"""
Playback service for void-tray.

Handles media playback coordination using streamlink and mpv.
Manages the shared mpv window via IPC socket approach, handles streamlink
process lifecycle, and provides fallback to browser playback when needed.
"""

import subprocess
import time as time_module
import threading
import socket
import json
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional, Tuple, List
import logging
from ..config.loader import (
    get_playback_url, get_streamlink_player, get_streamlink_args,
    get_mpv_socket_path, get_browser_fallback
)
from ..core.state import get_state_manager
from ..core.event_bus import get_event_bus
from ..utils.logger import get_logger
from ..utils.network import is_online, _free_local_port

logger = get_logger(__name__)
EVENT_PLAYBACK_START = 'playback_start'
EVENT_PLAYBACK_STOP = 'playback_stop'
EVENT_PLAYBACK_ERROR = 'playback_error'
EVENT_PLAYBACK_STREAM_CHANGED = 'playback_stream_changed'

# MPV constants (matching rss-tray approach)
MPV_START_TIMEOUT_SECONDS = 10
STREAMLINK_READY_TIMEOUT_SECONDS = 30


class PlaybackService:
    """
    Service for managing media playback.

    Responsibilities:
    - Launching streamlink processes to fetch streams
    - Managing shared mpv instance via IPC socket control
    - Handling stream switching without restarting mpv
    - Providing browser fallback when streamlink/mpv unavailable
    - Proper process lifecycle management
    - Notifying via event bus when playback state changes
    """

    def __init__(self):
        self.state_manager = get_state_manager()
        self.event_bus = get_event_bus()
        self._playback_lock = threading.RLock()  # Reentrant lock for playback operations
        self._streamlink_proc: Optional[subprocess.Popen] = None
        self._mpv_proc: Optional[subprocess.Popen] = None
        self._current_url: Optional[str] = None
        logger.debug("PlaybackService initialized")

    def _get_platform_from_url(self, url: str) -> str:
        """Determine the platform (twitch, youtube, or other) from a URL."""
        url_lower = url.lower()
        if 'twitch.tv' in url_lower:
            return 'twitch'
        elif 'youtube.com' in url_lower or 'youtu.be' in url_lower:
            return 'youtube'
        else:
            return 'other'

    def _mpv_ipc(self, commands, timeout=2.0):
        """Sends mpv JSON-IPC commands (each a list, e.g. ['loadfile', url,
        'replace']) and waits for one reply per command. True only if mpv is
        reachable and every command answered 'success'."""
        socket_path = get_mpv_socket_path()
        if not socket_path:
            logger.error("No MPV socket path configured")
            return False

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(socket_path)
            for command in commands:
                sock.sendall((json.dumps({'command': command}) + '\n').encode())
            pending = len(commands)
            ok = True
            buf = b''
            while pending > 0:
                chunk = sock.recv(4096)
                if not chunk:
                    return False
                buf += chunk
                *lines, buf = buf.split(b'\n')
                for line in lines:
                    try:
                        reply = json.loads(line)
                    except ValueError:
                        continue
                    if 'error' in reply:  # events have no 'error' key
                        pending -= 1
                        if reply.get('error') != 'success':
                            ok = False
            return ok
        except Exception as e:
            logger.debug(f"MPV IPC error: {e}")
            return False
        finally:
            sock.close()

    def _ensure_mpv(self) -> bool:
        """Ensure mpv process is running, start it if not."""
        if self._is_mpv_running():
            # Check if we can actually communicate with it
            if self._mpv_ipc([['get_property', 'pid']]):
                return True
            # If we can't communicate, it might be hung - restart it
            logger.debug("MPV not responding, restarting")

        # Stop any existing process (only if not responsive)
        if self._mpv_proc:
            try:
                self._mpv_proc.terminate()
                self._mpv_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._mpv_proc.kill()
                self._mpv_proc.wait()
            except:
                pass
            self._mpv_proc = None

        # Start fresh mpv process
        return self._start_mpv()

    def start_playback(self, url: str) -> bool:
        """
        Start playback of a stream URL.

        Args:
            url: Stream URL to play

        Returns:
            True if playback started successfully, False otherwise
        """
        logger.info(f"Starting playback for URL: {url}")

        with self._playback_lock:
            # Stop any existing streamlink first (but keep mpv running)
            self._stop_streamlink_only()

            self._current_url = url

            try:
                # Use the actual stream URL for streamlink
                streamlink_url = url

                # Get quality settings from config
                from ..config.loader import load_twitch_qualities, load_youtube_qualities
                platform = self._get_platform_from_url(url)
                if platform == 'youtube':
                    qualities = load_youtube_qualities()
                elif platform == 'twitch':
                    qualities = load_twitch_qualities()
                else:
                    qualities = ['best']  # default fallback
                quality_spec = ','.join(qualities)

                # Get a free local port for streamlink's HTTP server
                http_port = _free_local_port()
                if http_port is None:
                    logger.error("Could not get free local port for streamlink")
                    self._handle_playback_error("Could not get free local port")
                    return False

                # Start streamlink process with --player-external-http
                streamlink_cmd = [
                    "streamlink",
                    "--player-external-http",
                    "--player-external-http-interface", "127.0.0.1",
                    "--player-external-http-port", str(http_port),
                    streamlink_url,
                    quality_spec,
                ]

                # Add any additional streamlink args
                streamlink_args = get_streamlink_args()
                if streamlink_args:
                    streamlink_cmd.extend(streamlink_args.split())

                logger.debug(f"Starting streamlink with command: {' '.join(streamlink_cmd)}")

                self._streamlink_proc = subprocess.Popen(
                    streamlink_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )

                # Wait for streamlink to report it's ready (local HTTP server ready)
                ready = False
                recent_output = []  # last few log lines, for error reporting
                start_time = time_module.time()

                while time_module.time() - start_time < STREAMLINK_READY_TIMEOUT_SECONDS:
                    # Check if process died
                    if self._streamlink_proc.poll() is not None:
                        stdout, stderr = self._streamlink_proc.communicate()
                        logger.error(f"Streamlink failed to start: {stderr}")
                        self._handle_playback_error("Streamlink failed to start")
                        return False

                    # Read output line by line (non-blocking)
                    line = self._streamlink_proc.stdout.readline()
                    if line:
                        recent_output.append(line.strip())
                        # Keep only last 10 lines for error reporting
                        if len(recent_output) > 10:
                            recent_output = recent_output[-10:]

                        # Check if streamlink reports it's ready
                        if 'access with one of' in line:
                            ready = True
                            break

                    # Small sleep to prevent busy loop
                    time_module.sleep(0.1)

                if not ready:
                    # Timeout or other issue
                    stdout, stderr = self._streamlink_proc.communicate()
                    logger.error(f"Streamlink timed out or failed: stdout={stdout}, stderr={stderr}")
                    logger.debug(f"Recent streamlink output: {recent_output}")
                    self._handle_playback_error("Streamlink failed to become ready")
                    return False

                # Ensure mpv is running (start it if needed)
                if not self._ensure_mpv():
                    logger.error("Failed to start or ensure mpv is running")
                    self._handle_playback_error("Failed to start mpv")
                    return False

                # Tell mpv to load the stream from streamlink's HTTP server
                stream_url = f'http://127.0.0.1:{http_port}/'
                # Get a title for the stream (could be enhanced later)
                title = f"{platform} stream"

                if not self._mpv_ipc([
                        ['set_property', 'force-media-title', title],
                        ['loadfile', stream_url, 'replace']
                    ]):
                    logger.error("Failed to command mpv to load stream")
                    self._handle_playback_error("Failed to control mpv")
                    return False

                # Update state
                def updater(state_dict):
                    state_dict['playing'] = True
                    state_dict['current_stream'] = url
                    state_dict['playback_error'] = None

                self.state_manager.update_state(updater)
                self.state_manager.save_to_disk()

                # Publish event
                self.event_bus.publish(EVENT_PLAYBACK_START, {'url': url})
                logger.info("Playback started successfully")
                return True

            except Exception as e:
                logger.error(f"Error starting playback: {e}", exc_info=True)
                self._handle_playback_error(str(e))
                return False

    def stop_playback(self) -> None:
        """Stop playback and clean up processes."""
        logger.info("Stopping playback")

        with self._playback_lock:
            self._stop_streamlink_only()
            self._stop_mpv_only()
            self._current_url = None

            # Update state
            def updater(state_dict):
                state_dict['playing'] = False
                state_dict['current_stream'] = None
                state_dict['playback_error'] = None

            self.state_manager.update_state(updater)
            self.state_manager.save_to_disk()

            # Publish event
            self.event_bus.publish(EVENT_PLAYBACK_STOP, {})
            logger.info("Playback stopped")

    def switch_stream(self, url: str) -> bool:
        """
        Switch to a different stream without restarting mpv.

        Args:
            url: New stream URL to play

        Returns:
            True if switch initiated successfully, False otherwise
        """
        logger.info(f"Switching stream to: {url}")

        if not self._is_mpv_running():
            logger.warning("MPV not running, falling back to start_playback")
            return self.start_playback(url)

        with self._playback_lock:
            try:
                self._current_url = url

                # Stop existing streamlink process (but keep mpv running)
                self._stop_streamlink_only()

                # Get quality settings from config
                from ..config.loader import load_twitch_qualities, load_youtube_qualities
                platform = self._get_platform_from_url(url)
                if platform == 'youtube':
                    qualities = load_youtube_qualities()
                elif platform == 'twitch':
                    qualities = load_twitch_qualities()
                else:
                    qualities = ['best']  # default fallback
                quality_spec = ','.join(qualities)

                # Get a free local port for streamlink's HTTP server
                http_port = _free_local_port()
                if http_port is None:
                    logger.error("Could not get free local port for streamlink")
                    self._handle_playback_error("Could not get free local port")
                    return False

                # Start streamlink process with --player-external-http
                streamlink_cmd = [
                    "streamlink",
                    "--player-external-http",
                    "--player-external-http-interface", "127.0.0.1",
                    "--player-external-http-port", str(http_port),
                    url,
                    quality_spec,
                ]

                # Add any additional streamlink args
                streamlink_args = get_streamlink_args()
                if streamlink_args:
                    streamlink_cmd.extend(streamlink_args.split())

                logger.debug(f"Starting streamlink with command: {' '.join(streamlink_cmd)}")

                self._streamlink_proc = subprocess.Popen(
                    streamlink_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )

                # Wait for streamlink to report it's ready (local HTTP server ready)
                ready = False
                recent_output = []  # last few log lines, for error reporting
                start_time = time_module.time()

                while time_module.time() - start_time < STREAMLINK_READY_TIMEOUT_SECONDS:
                    # Check if process died
                    if self._streamlink_proc.poll() is not None:
                        stdout, stderr = self._streamlink_proc.communicate()
                        logger.error(f"Streamlink failed to start: {stderr}")
                        self._handle_playback_error("Streamlink failed to start")
                        return False

                    # Read output line by line (non-blocking)
                    line = self._streamlink_proc.stdout.readline()
                    if line:
                        recent_output.append(line.strip())
                        # Keep only last 10 lines for error reporting
                        if len(recent_output) > 10:
                            recent_output = recent_output[-10:]

                        # Check if streamlink reports it's ready
                        if 'access with one of' in line:
                            ready = True
                            break

                    # Small sleep to prevent busy loop
                    time_module.sleep(0.1)

                if not ready:
                    # Timeout or other issue
                    stdout, stderr = self._streamlink_proc.communicate()
                    logger.error(f"Streamlink timed out or failed: stdout={stdout}, stderr={stderr}")
                    logger.debug(f"Recent streamlink output: {recent_output}")
                    self._handle_playback_error("Streamlink failed to become ready")
                    return False

                # Tell mpv to load the new stream from streamlink's HTTP server
                stream_url = f'http://127.0.0.1:{http_port}/'
                # Get a title for the stream (could be enhanced later)
                title = f"{platform} stream"

                if not self._mpv_ipc([
                        ['set_property', 'force-media-title', title],
                        ['loadfile', stream_url, 'replace']
                    ]):
                    logger.error("Failed to command mpv to load stream")
                    self._handle_playback_error("Failed to control mpv")
                    return False

                # Update state
                def updater(state_dict):
                    state_dict['current_stream'] = url
                    state_dict['playback_error'] = None

                self.state_manager.update_state(updater)
                self.state_manager.save_to_disk()

                # Publish event
                self.event_bus.publish(EVENT_PLAYBACK_STREAM_CHANGED, {'url': url})
                logger.info("Stream switched successfully")
                return True

            except Exception as e:
                logger.error(f"Error switching stream: {e}", exc_info=True)
                self._handle_playback_error(str(e))
                return False

    def _stop_streamlink_only(self) -> None:
        """Stop only the streamlink process (keep mpv running)."""
        if self._streamlink_proc:
            try:
                self._streamlink_proc.terminate()
                self._streamlink_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._streamlink_proc.kill()
                self._streamlink_proc.wait()
            except:
                pass
            self._streamlink_proc = None

    def _stop_mpv_only(self) -> None:
        """Stop only the mpv process."""
        if self._mpv_proc:
            try:
                self._mpv_proc.terminate()
                self._mpv_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._mpv_proc.kill()
                self._mpv_proc.wait()
            except:
                pass
            self._mpv_proc = None

    def _start_mpv(self) -> bool:
        """Start mpv process with IPC socket for control."""
        try:
            socket_path = get_mpv_socket_path()
            if not socket_path:
                logger.error("No MPV socket path configured")
                return False

            # Remove existing socket file if it exists
            import os
            try:
                os.unlink(socket_path)
            except FileNotFoundError:
                pass

            # Start mpv
            mpv_cmd = [
                'mpv',
                f'--input-ipc-server={socket_path}',
                '--idle=once',  # Start idle and quit after one file (we'll restart as needed)
                '--force-window=yes',
                '--window-maximized=yes',  # Start maximized
                '--video-sync=display-resample',
                '--hwdec=auto'
            ]

            logger.debug(f"Starting mpv with command: {' '.join(mpv_cmd)}")

            self._mpv_proc = subprocess.Popen(
                mpv_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True  # Detach mpv from parent process group
            )

            # Give mpv a moment to start
            time_module.sleep(1)

            # Check if mpv started successfully
            if self._mpv_proc.poll() is not None:
                stdout, stderr = self._mpv_proc.communicate()
                logger.error(f"MPV failed to start: {stderr.decode()}")
                return False

            logger.debug("MPV started successfully")
            return True

        except Exception as e:
            logger.error(f"Error starting MPV: {e}", exc_info=True)
            return False

    def _is_mpv_running(self) -> bool:
        """Check if mpv process is still running."""
        return self._mpv_proc is not None and self._mpv_proc.poll() is None

    def _handle_playback_error(self, error_message: str) -> None:
        """Handle playback service errors."""
        logger.error(f"Playback service error: {error_message}")

        def updater(state_dict):
            state_dict['playback_error'] = error_message
            state_dict['playing'] = False

        self.state_manager.update_state(updater)
        self.state_manager.save_to_disk()

        # Publish error event
        self.event_bus.publish(EVENT_PLAYBACK_ERROR, {'message': error_message})

    def shutdown(self) -> None:
        """Cleanup resources."""
        logger.info("Shutting down playback service")
        # Stop only streamlink process, leave mpv running so it can continue
        # after the application closes if desired
        self._stop_streamlink_only()

        # Update state to reflect stopped playback
        def updater(state_dict):
            state_dict['playing'] = False
            state_dict['current_stream'] = None
            state_dict['playback_error'] = None

        self.state_manager.update_state(updater)
        self.state_manager.save_to_disk()

        # Publish event
        self.event_bus.publish(EVENT_PLAYBACK_STOP, {})
        logger.debug("PlaybackService shutdown")


# Global service instance
_playback_service: Optional[PlaybackService] = None


def get_playback_service() -> PlaybackService:
    """Get the global playback service instance."""
    global _playback_service
    if _playback_service is None:
        _playback_service = PlaybackService()
    return _playback_service