"""
Livestreams service for void-tray.

Handles live stream monitoring for Twitch (via unofficial GQL API) and YouTube
(via streamlink), with schedule-aware checking and proper error handling.
"""

import time as time_module
import threading
import subprocess
import socket
import logging
import re
import json
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
from ..config.loader import (
    load_livestream_settings, get_livestream_check_interval,
    get_twitch_client_id, get_youtube_api_key
)
from ..core.state import get_state_manager
from ..core.event_bus import get_event_bus
from ..utils.logger import get_logger
from ..utils.network import fetch_json_with_retry, is_online

logger = get_logger(__name__)
EVENT_LIVESTREAM_UPDATE = 'livestream_update'
EVENT_LIVESTREAM_ERROR = 'livestream_error'
EVENT_LIVESTREAM_ONLINE = 'livestream_online'
EVENT_LIVESTREAM_OFFLINE = 'livestream_offline'


class LivestreamsService:
    """
    Service for monitoring Twitch and YouTube live streams.

    Responsibilities:
    - Monitoring Twitch streams via unofficial GQL API
    - Monitoring YouTube streams via streamlink
    - Schedule-aware checking to limit polling to expected streaming hours
    - Proper error handling and fallback to last-known state
    - Background polling with operation-level locking
    - Notifying via event bus when stream status changes
    """

    # Twitch GQL endpoint
    TWITCH_GQL_URL = "https://gql.twitch.tv/gql"
    # Twitch batch size for GQL requests
    TWITCH_BATCH_SIZE = 20

    def __init__(self):
        self.state_manager = get_state_manager()
        self.event_bus = get_event_bus()
        self._check_lock = threading.Lock()  # For preventing overlapping checks
        logger.debug("LivestreamsService initialized")

    def start_check_thread(self, force: bool = False) -> None:
        """
        Start a background thread to check for livestream updates.

        Args:
            force: If True, check livestreams regardless of last check time
        """
        if not self._check_lock.acquire(blocking=False):
            logger.debug("Livestream check already in progress, skipping")
            return

        def _check_livestreams_guarded():
            try:
                self.check_livestreams(force)
            finally:
                self._check_lock.release()

        thread = threading.Thread(target=_check_livestreams_guarded, daemon=True)
        thread.start()
        logger.debug("Started livestream check thread")

    def check_livestreams(self, force: bool = False) -> None:
        """
        Check for livestream status updates.

        Args:
            force: If True, check livestreams regardless of last check time
        """
        logger.debug(f"Checking livestreams (force={force})")

        # Check if livestreams are enabled
        settings = load_livestream_settings()
        if not settings.get('enabled', True):
            logger.debug("Livestream service disabled in config")
            return

        # Check connectivity first (only for non-force checks)
        if not force and not is_online():
            logger.debug("Offline: skipping livestream check")
            return

        # Check if we're within scheduled hours (if schedule checking is enabled)
        if settings.get('schedule_check', True) and not self._is_within_scheduled_hours(settings):
            logger.debug("Outside scheduled hours for livestream checking")
            return

        interval = get_livestream_check_interval()
        now = time_module.time()
        state = self.state_manager.get_state()
        last_checked = state.get('livestream_last_checked', 0)

        if not force and (now - last_checked) < interval:
            logger.debug(f"Livestreams not due for check yet (last checked {now - last_checked:.0f}s ago, interval {interval}s)")
            return

        logger.info("Proceeding with livestream status check...")

        # Publish that we're starting a check
        self.event_bus.publish(EVENT_LIVESTREAM_UPDATE, {'status': 'checking'})

        try:
            # Get configured channels
            twitch_channels = settings.get('twitch_channels', [])
            youtube_channels = settings.get('youtube_channels', [])
            logger.info(f"Configured channels - Twitch: {len(twitch_channels)} {twitch_channels}, YouTube: {len(youtube_channels)} {youtube_channels}")

            # Get current state for comparison (what we had before this check)
            state = self.state_manager.get_state()
            previous_online_streams = state.get('online_streams', [])

            # Check Twitch streams first (faster check)
            twitch_online = []
            temp_state_after_twitch = previous_online_streams  # Default to previous state if no Twitch check
            if twitch_channels:
                logger.info(f"Starting Twitch check for {len(twitch_channels)} channels...")
                twitch_online = self._check_twitch_streams(twitch_channels)
                logger.info(f"Twitch check found {len(twitch_online)} online streams")

                # Calculate what state should be after Twitch check
                # (Keep YouTube streams from previous state, update Twitch streams)
                non_twitch_previous = [s for s in previous_online_streams if s.get('platform') != 'twitch']
                temp_state_after_twitch = non_twitch_previous + twitch_online

                # Publish events for Twitch stream status changes (compares temp state with previous state)
                self._publish_stream_status_changes(temp_state_after_twitch)

                # Note: We don't publish Twitch-only update events to avoid confusing the UI
                # The individual livestream_online/offline events are sufficient for immediate updates

            # Check YouTube streams
            youtube_online = []
            if youtube_channels:
                logger.info(f"Starting YouTube check for {len(youtube_channels)} channels...")
                youtube_online = self._check_youtube_streams(youtube_channels)
                logger.info(f"YouTube check found {len(youtube_online)} online streams")

                # Calculate what state should be after YouTube check
                # (Keep Twitch streams from temp state after Twitch check, update YouTube streams)
                current_twitch_streams = [s for s in temp_state_after_twitch if s.get('platform') == 'twitch']
                non_youtube_current = [s for s in temp_state_after_twitch if s.get('platform') != 'youtube']
                temp_state_after_youtube = non_youtube_current + youtube_online

                # Publish events for YouTube stream status changes (compares temp state with state after Twitch check)
                self._publish_stream_status_changes(temp_state_after_youtube)

                # Note: We don't publish YouTube-only update events to avoid confusing the UI
                # The individual livestream_online/offline events are sufficient for immediate updates

            # Final state: combine Twitch and YouTube results
            final_online_streams = []
            if twitch_channels:
                final_online_streams.extend(twitch_online)
            if youtube_channels:
                final_online_streams.extend(youtube_online)

            def updater_final(state_dict):
                state_dict['livestream_last_checked'] = now
                state_dict['livestream_error'] = None
                state_dict['online_streams'] = final_online_streams
                # Clear error flag on successful check
                if 'livestream_error' in state_dict:
                    del state_dict['livestream_error']

            self.state_manager.update_state(updater_final)
            self.state_manager.save_to_disk()

            # Publish events for final stream status changes (this ensures consistency)
            # Compares final state with the state after YouTube check
            self._publish_stream_status_changes(final_online_streams)

            # Publish overall update event
            self.event_bus.publish(EVENT_LIVESTREAM_UPDATE, {
                'status': 'updated',
                'online_count': len(final_online_streams),
                'streams': final_online_streams
            })

            logger.info(f"Livestream check completed: {len(final_online_streams)} streams online ({len(twitch_online) if twitch_channels else 0} Twitch, {len(youtube_online) if youtube_channels else 0} YouTube)")

        except Exception as e:
            logger.error(f"Error checking livestreams: {e}", exc_info=True)
            self._handle_livestream_error(str(e))

    def _is_within_scheduled_hours(self, settings: Dict[str, Any]) -> bool:
        """
        Check if current time is within scheduled hours for livestream checking.

        Args:
            settings: Livestream settings dictionary

        Returns:
            True if within scheduled hours, False otherwise
        """
        # For now, we'll always return True to keep it simple
        # In a full implementation, we'd check start/end times and days of week
        return True

    def _format_duration(self, started_at: str) -> str:
        """Convert ISO 8601 timestamp to human-readable duration (e.g., 6h 22min)."""
        if not started_at:
            return "Live"
        try:
            # Twitch startedAt is usually ISO 8601: 2023-10-13T12:34:56Z
            # Replace Z with +00:00 for fromisoformat
            start_dt = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            delta = now - start_dt

            seconds = int(delta.total_seconds())
            if seconds < 0:
                return "Live"

            hours = seconds // 3600
            minutes = (seconds % 3600) // 60

            if hours > 0:
                return f"{hours}h {minutes}min"
            return f"{minutes}min"
        except Exception as e:
            logger.debug(f"Error formatting duration: {e}")
            return "Live"

    def _parse_viewer_count(self, text: str) -> Optional[int]:
        """'283 watching now' -> 283, '1,234 watching' -> 1234, '1.2K watching' ->
        1200; None if the text doesn't start with a number."""
        if not text:
            return None
        text = str(text).replace(' ', ' ').strip()
        suffixed = re.match(r'(\d+(?:[.,]\d+)?)\s*([kKmM])\b', text)
        if suffixed:
            number = float(suffixed.group(1).replace(',', '.'))
            return int(round(number * (1000 if suffixed.group(2) in 'kK' else 1000000)))
        plain = re.match(r'\d[\d.,\s]*', text)
        if not plain:
            return None
        digits = re.sub(r'\D', '', plain.group(0))
        return int(digits) if digits else None

    def _check_twitch_streams(self, channels: List[str]) -> List[Dict[str, Any]]:
        """
        Check Twitch stream status using the unofficial GQL API.

        Args:
            channels: List of Twitch channel names to check

        Returns:
            List of online stream information dictionaries
        """
        online_streams = []

        if not channels:
            return online_streams

        try:
            # Process channels in batches to match rss-tray implementation
            for start in range(0, len(channels), self.TWITCH_BATCH_SIZE):
                batch = channels[start:start + self.TWITCH_BATCH_SIZE]
                batch_result = self._check_twitch_batch(batch)

                if batch_result is None:
                    # Batch request failed, skip this batch
                    logger.warning(f"Twitch batch request failed for channels {start}-{start+len(batch)}")
                    continue

                # Process successful batch results
                for channel, stream_data in batch_result.items():
                    if not stream_data:
                        continue

                    # Extract stream information
                    title = stream_data.get('title', '') or f'Live stream on {channel}'
                    game_name = stream_data.get('category', '') or 'Live stream'
                    viewers = stream_data.get('viewers', 0)
                    started_at = stream_data.get('started', '')

                    stream_item = {
                        'platform': 'twitch',
                        'channel': channel,
                        'url': f"https://www.twitch.tv/{channel}",
                        'title': title,
                        'duration': self._format_duration(started_at),
                        'viewers': viewers,
                        'thumbnail': '',  # We don't get thumbnail from this method
                        'game_name': game_name,
                        'started_at': started_at,
                    }
                    online_streams.append(stream_item)
                    logger.info(f"Twitch stream found: {channel} - {game_name} ({viewers} viewers)")

        except Exception as e:
            logger.error(f"Error checking Twitch streams: {e}", exc_info=True)

        return online_streams

    def _check_twitch_batch(self, channels: List[str]) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        One batched GQL POST for channels; returns {channel: stream_data} for live ones,
        or None if the request failed.
        """
        if not channels:
            return {}

        try:
            # Build the batched GQL payload like rss-tray
            payload_list = []
            for channel in channels:
                payload_list.append({
                    "operationName": "StreamMetadata",
                    "query": "query StreamMetadata($channelLogin: String!) { "
                             "user(login: $channelLogin) { stream { type title viewersCount createdAt game { name } } } }",
                    "variables": {"channelLogin": channel},
                })

            payload = json.dumps(payload_list).encode('utf-8')
            request = urllib.request.Request(
                self.TWITCH_GQL_URL,
                data=payload,
                headers={
                    'Client-Id': get_twitch_client_id() or "kimne78kx3ncx6brgo4mv6wki5h1ko",
                    'Content-Type': 'application/json',
                    'User-Agent': 'Mozilla/5.0',
                },
                method='POST',
            )

            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    response_data = json.loads(response.read().decode('utf-8'))
            except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, ValueError) as e:
                logger.error(f"Failed to fetch from Twitch API: {e}")
                return None

            # Process results like rss-tray
            if not isinstance(response_data, list):
                logger.error("Unexpected Twitch API response format")
                return None

            live_streams = {}
            for channel, result in zip(channels, response_data):
                user = (result.get('data') or {}).get('user')
                if not user:
                    continue

                stream = user.get('stream')
                if stream and stream.get('type') == 'live':
                    viewers = stream.get('viewersCount')
                    live_streams[channel] = {
                        'title': stream.get('title') or '',
                        'category': (stream.get('game') or {}).get('name') or '',
                        'viewers': viewers if isinstance(viewers, int) else 0,
                        'started': stream.get('createdAt', ''),
                    }

            return live_streams

        except Exception as e:
            logger.error(f"Error in Twitch batch request: {e}", exc_info=True)
            return None

    def _check_youtube_streams(self, channels: List[str]) -> List[Dict[str, Any]]:
        """
        Check YouTube stream status using streamlink (matching rss-tray approach).

        Args:
            channels: List of YouTube channel names/IDs to check

        Returns:
            List of online stream information dictionaries
        """
        online_streams = []

        if not channels:
            return online_streams

        try:
            logger.debug(f"Checking YouTube streams for {len(channels)} channels")

            # For each channel, check if there's a live stream using streamlink --json
            for channel in channels:
                # YouTube channel URL formats:
                # - https://www.youtube.com/channel/UC...
                # - https://www.youtube.com/c/ChannelName
                # - https://www.youtube.com/user/Username
                # - https://www.youtube.com/@Username

                # Handle YouTube channel identifier formats
                if not channel.startswith(('http://', 'https://')):
                    # Check if it looks like a YouTube channel ID (UC followed by alphanumeric)
                    # If so, add the 'channel/' prefix as expected by YouTube URLs
                    if re.match(r'^UC[A-Za-z0-9_-]+$', channel):
                        url = f"https://www.youtube.com/channel/{channel}"
                    else:
                        # Assume it's already a valid identifier (@handle, channel/UC..., etc.)
                        url = f"https://www.youtube.com/{channel}"
                else:
                    url = channel

                try:
                    # Use streamlink --json to get detailed stream information
                    # Add /live to the URL to check for live streams specifically
                    live_url = f"{url}/live"
                    result = subprocess.run(
                        ['streamlink', '--json', live_url],
                        capture_output=True,
                        text=True,
                        timeout=20  # Timeout for JSON fetching
                    )

                    # Check if streamlink found any streams
                    if result.returncode == 0 and result.stdout.strip():
                        # Parse the JSON output (tolerating log lines before the object)
                        start = result.stdout.find('{')
                        if start >= 0:
                            try:
                                parsed = json.loads(result.stdout[start:])

                                # Check if there's an error (not live)
                                if parsed.get('error'):
                                    logger.debug(f"YouTube {channel}: not live ({str(parsed['error'])[:100]})")
                                    continue

                                # Check if we got streams or metadata
                                metadata = parsed.get('metadata') or {}
                                streams = parsed.get('streams')

                                # A channel is live if streamlink found playable streams or metadata has title
                                if streams or metadata.get('title'):
                                    # Get basic stream info from metadata
                                    title = metadata.get('title') or f'Live stream on {channel}'
                                    category = metadata.get('category') or ''

                                    # Fetch additional details (viewer count, start time) from YouTube page
                                    # This matches the rss-tray approach
                                    try:
                                        # Fetch the YouTube live page for additional details
                                        live_page_url = f"https://www.youtube.com/{channel}/live"
                                        page_req = urllib.request.Request(
                                            live_page_url,
                                            headers={
                                                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0',
                                                'Accept-Language': 'en-US,en;q=0.9',
                                                'Cookie': 'SOCS=CAI'  # Accepts consent page for EU visitors
                                            }
                                        )
                                        with urllib.request.urlopen(page_req, timeout=15) as resp:
                                            html = resp.read(4 * 1024 * 1024).decode('utf-8', 'replace')

                                        # Parse viewer count and start time from the page
                                        viewers = None
                                        started = None

                                        # Look for ytInitialPlayerResponse (contains start time)
                                        player_match = re.search(r'ytInitialPlayerResponse\s*=\s*', html)
                                        if player_match:
                                            try:
                                                # Find the JSON object
                                                json_decoder = json.JSONDecoder()
                                                player_json, _ = json_decoder.raw_decode(html, player_match.end())
                                                details = player_json.get('liveBroadcastDetails')
                                                if isinstance(details, dict):
                                                    started_str = details.get('startTimestamp')
                                                    if started_str:
                                                        # Convert ISO timestamp to datetime
                                                        started = datetime.fromisoformat(started_str.replace('Z', '+00:00'))
                                            except (ValueError, json.JSONDecodeError, KeyError):
                                                pass  # Keep started as None

                                        # Look for ytInitialData (contains viewer count)
                                        data_match = re.search(r'ytInitialData\s*=\s*', html)
                                        if data_match:
                                            try:
                                                json_decoder = json.JSONDecoder()
                                                data_json, _ = json_decoder.raw_decode(html, data_match.end())
                                                # Navigate to videoViewCountRenderer
                                                video_renderer = None
                                                obj_stack = [data_json]
                                                while obj_stack and not video_renderer:
                                                    current = obj_stack.pop()
                                                    if isinstance(current, dict):
                                                        if 'videoViewCountRenderer' in current:
                                                            video_renderer = current['videoViewCountRenderer']
                                                            break
                                                        obj_stack.extend(current.values())
                                                    elif isinstance(current, list):
                                                        obj_stack.extend(current)

                                                if video_renderer and isinstance(video_renderer, dict):
                                                    # Check if it's marked as live
                                                    if video_renderer.get('isLive', True):
                                                        view_count = video_renderer.get('viewCount') or {}
                                                        # Get text from either simpleText or runs (YouTube uses both formats)
                                                        text = view_count.get('simpleText') or ''
                                                        if not text:
                                                            # Try to construct text from runs array
                                                            runs = view_count.get('runs', [])
                                                            if isinstance(runs, list):
                                                                text = ''.join(
                                                                    run.get('text', '') for run in runs
                                                                    if isinstance(run, dict))
                                                                # Parse viewer count using the same logic as rss-tray
                                                                viewers = self._parse_viewer_count(text)
                                            except (ValueError, json.JSONDecodeError, KeyError):
                                                pass  # Keep viewers as None

                                    except Exception as page_error:
                                        logger.debug(f"Could not fetch YouTube page details for {channel}: {page_error}")
                                        # Continue with just the basic stream info

                                    # Calculate duration if we have start time
                                    duration = 'Live'
                                    if started:
                                        now = datetime.now(timezone.utc)
                                        delta = now - started
                                        seconds = int(delta.total_seconds())
                                        if seconds >= 0:
                                            hours = seconds // 3600
                                            minutes = (seconds % 3600) // 60
                                            if hours > 0:
                                                duration = f"{hours}h {minutes}min"
                                            else:
                                                duration = f"{minutes}min"

                                    stream_info = {
                                        'platform': 'youtube',
                                        'channel': channel,
                                        'url': url,
                                        'title': title,
                                        'duration': duration,
                                        'viewers': viewers if viewers is not None else 0,
                                        'thumbnail': '',  # We don't get thumbnail from this method
                                        'game_name': category or duration,
                                        'started_at': started.isoformat() if started else '',
                                    }
                                    online_streams.append(stream_info)
                                    logger.info(f"YouTube stream found: {channel} - {category or duration} ({viewers or 0} viewers)")
                                else:
                                    logger.debug(f"No streams or metadata found for YouTube channel {channel}")
                            except json.JSONDecodeError as json_error:
                                logger.debug(f"Could not parse streamlink JSON output for {channel}: {json_error}")
                        else:
                            logger.debug(f"No JSON object found in streamlink output for {channel}")
                    else:
                        # Log stderr for debugging if no streams found
                        if result.stderr:
                            logger.debug(f"Streamlink stderr for YouTube channel {channel}: {result.stderr.strip()}")

                except subprocess.TimeoutExpired:
                    logger.debug(f"Streamlink timeout for YouTube channel {channel}")
                except FileNotFoundError:
                    logger.warning("streamlink not found - cannot check YouTube streams")
                    break  # No point checking other channels if streamlink isn't installed
                except Exception as e:
                    logger.debug(f"Error checking YouTube channel {channel}: {e}")

        except Exception as e:
            logger.error(f"Error checking YouTube streams: {e}", exc_info=True)

        return online_streams

    def _publish_stream_status_changes(self, current_online_streams: List[Dict[str, Any]]) -> None:
        """
        Publish events for stream status changes.

        Args:
            current_online_streams: List of currently online stream information
        """
        # Get previous state
        state = self.state_manager.get_state()
        previous_online = state.get('online_streams', [])

        # Create sets of stream identifiers for comparison
        def stream_identifier(stream):
            return f"{stream.get('platform', '')}:{stream.get('channel', '')}"

        previous_ids = set(stream_identifier(s) for s in previous_online)
        current_ids = set(stream_identifier(s) for s in current_online_streams)

        # Find newly online streams
        newly_online = current_ids - previous_ids
        for stream_id in newly_online:
            # Find the stream object
            stream_obj = next((s for s in current_online_streams if stream_identifier(s) == stream_id), None)
            if stream_obj:
                self.event_bus.publish(EVENT_LIVESTREAM_ONLINE, stream_obj)
                logger.info(f"Stream went online: {stream_obj.get('channel')} on {stream_obj.get('platform')}")

        # Find newly offline streams
        newly_offline = previous_ids - current_ids
        for stream_id in newly_offline:
            # Find the stream object from previous state
            stream_obj = next((s for s in previous_online if stream_identifier(s) == stream_id), None)
            if stream_obj:
                self.event_bus.publish(EVENT_LIVESTREAM_OFFLINE, stream_obj)
                logger.info(f"Stream went offline: {stream_obj.get('channel')} on {stream_obj.get('platform')}")

    def _handle_livestream_error(self, error_message: str) -> None:
        """Handle livestream service errors."""
        logger.error(f"Livestream service error: {error_message}")

        def updater(state_dict):
            state_dict['livestream_error'] = error_message
            state_dict['livestream_last_checked'] = time_module.time()

        self.state_manager.update_state(updater)
        self.state_manager.save_to_disk()

        # Publish error event
        self.event_bus.publish(EVENT_LIVESTREAM_ERROR, {'message': error_message})

    def shutdown(self) -> None:
        """Cleanup resources."""
        logger.debug("LivestreamsService shutdown")


# Global service instance
_livestreams_service: Optional[LivestreamsService] = None


def get_livestreams_service() -> LivestreamsService:
    """Get the global livestreams service instance."""
    global _livestreams_service
    if _livestreams_service is None:
        _livestreams_service = LivestreamsService()
    return _livestreams_service