"""One persistent mpv fed by streamlink for live streams."""

import atexit
import json
import os
import shutil
import socket
import subprocess
import threading
import time as time_module
import webbrowser

from .constants import YOUTUBE_CHECK_TIMEOUT_SECONDS
from .log import _log


# --- Live-stream playback -------------------------------------------------
# One persistent mpv window is shared by every live channel. mpv is started
# idle + maximized with an IPC socket; each click starts `streamlink
# --player-external-http` (serves the stream on a local port, no player of its
# own) and tells the running mpv to `loadfile` that URL, so a new channel
# replaces whatever is playing in the *same* window instead of opening another.
MPV_IPC_SOCKET = os.path.join(
    os.environ.get('XDG_RUNTIME_DIR') or '/tmp', f'void-tray-mpv-{os.getuid()}.sock')


STREAMLINK_READY_TIMEOUT_SECONDS = 30


MPV_START_TIMEOUT_SECONDS = 10


_player_lock = threading.Lock()  # guards _streamlink_proc


_mpv_lock = threading.Lock()     # serialises "is mpv up? if not, start it"


_streamlink_proc = None


_mpv_proc = None


_playing = None  # (streamlink_proc, site, channel) of the stream mpv is showing


def is_stream_playing(site, channel):
    """True while `channel` on `site` ('Twitch'/'YouTube') is what the shared
    mpv is playing (its streamlink is still running)."""
    with _player_lock:
        playing = _playing
    return bool(playing and playing[1:] == (site, channel) and playing[0].poll() is None)


def live_marker_markup(color, playing):
    """The colored bullet in front of a live row; a play triangle (forced to
    its text form, not the emoji one) while that stream is playing."""
    symbol = '\u25b6\ufe0e' if playing else '\u25cf'
    return f'<span foreground="{color}"><b>{symbol}</b></span>'


def stream_window_title(site, channel, title=None):
    """mpv window title: site, user and stream title separated by bullets."""
    return ' \u00b7 '.join(p for p in (site, channel, title) if p)


def _mpv_ipc(commands, timeout=2.0):
    """Sends mpv JSON-IPC commands (each a list, e.g. ['loadfile', url,
    'replace']) and waits for one reply per command. True only if mpv is
    reachable and every command answered 'success'."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(MPV_IPC_SOCKET)
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
                    if reply['error'] != 'success':
                        ok = False
        return ok
    except OSError:
        return False
    finally:
        sock.close()


def _ensure_mpv():
    """True if an mpv with our IPC socket is running, starting one (idle,
    maximized) if not. --idle=once: it sits empty until the first stream,
    and quits when that playback ends, so the next click starts a fresh one."""
    global _mpv_proc
    with _mpv_lock:
        if _mpv_ipc([['get_property', 'pid']]):
            return True
        try:
            os.unlink(MPV_IPC_SOCKET)  # stale socket from a dead mpv
        except OSError:
            pass
        try:
            _mpv_proc = subprocess.Popen(
                ['mpv', '--idle=once', '--force-window=yes',
                 '--window-maximized=yes', f'--input-ipc-server={MPV_IPC_SOCKET}'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return False
        deadline = time_module.monotonic() + MPV_START_TIMEOUT_SECONDS
        while time_module.monotonic() < deadline:
            if _mpv_proc.poll() is not None:
                return False
            if _mpv_ipc([['get_property', 'pid']], timeout=0.5):
                return True
            time_module.sleep(0.2)
        return False


def _load_in_mpv(stream_url, title):
    """Plays stream_url in the shared mpv window (replacing what's there)."""
    for _attempt in range(2):  # 2nd try covers mpv quitting right after the probe
        if not _ensure_mpv():
            return False
        if _mpv_ipc([['set_property', 'force-media-title', title],
                     ['loadfile', stream_url, 'replace']]):
            return True
        time_module.sleep(0.5)
    return False


def _stop_streamlink():
    global _streamlink_proc
    with _player_lock:
        proc, _streamlink_proc = _streamlink_proc, None
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass


atexit.register(_stop_streamlink)


def _is_current_streamlink(proc):
    with _player_lock:
        return _streamlink_proc is proc


def _free_local_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _apply_stream_title(proc, url, site, channel):
    """Refines the window title with the author's display name and stream title
    from `streamlink --json` (external-http mode has no {author}/{title}
    substitution of its own): '<site> \u00b7 <author> \u00b7 <title>'."""
    try:
        result = subprocess.run(['streamlink', '--json', url],
                                capture_output=True, text=True,
                                timeout=YOUTUBE_CHECK_TIMEOUT_SECONDS)
        meta = json.loads(result.stdout).get('metadata') or {}
    except Exception:
        return
    title = stream_window_title(site, meta.get('author') or channel, meta.get('title'))
    if meta and _is_current_streamlink(proc):
        _mpv_ipc([['set_property', 'force-media-title', title]])


def _stop_streamlink_when_mpv_exits(proc):
    """The external-http server would otherwise serve forever after the mpv
    window is closed; stop it once mpv has been gone for two checks."""
    misses = 0
    while proc.poll() is None:
        time_module.sleep(3)
        if _mpv_ipc([['get_property', 'pid']], timeout=1):
            misses = 0
            continue
        misses += 1
        if misses >= 2:
            try:
                proc.terminate()
            except OSError:
                pass
            return


def _play_in_shared_mpv(url, quality, site, channel, fallback_url, title=None):
    """Worker thread: serve the stream via streamlink, load it into the
    shared mpv. Falls back to the browser if anything fails (unless a newer
    click has superseded this one).

    The previous stream's streamlink is deliberately left running until mpv
    has been told to load the new one: killing it first would end the file
    mpv is playing, and with --idle=once mpv quits when playback ends -- the
    window would close and reopen instead of being reused."""
    port = _free_local_port()
    try:
        proc = subprocess.Popen(
            ['streamlink', '--player-external-http',
             '--player-external-http-interface', '127.0.0.1',
             '--player-external-http-port', str(port), url, quality],
            # streamlink logs to stdout in some versions and stderr in others;
            # read both through one pipe
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors='replace')
    except Exception as e:
        _log(f'could not start streamlink: {e}')
        webbrowser.open(fallback_url)
        return
    global _streamlink_proc
    with _player_lock:
        previous, _streamlink_proc = _streamlink_proc, proc
    # streamlink logs this line once its local server is listening; mpv can
    # connect from then on (it fetches the actual stream on first request).
    watchdog = threading.Timer(STREAMLINK_READY_TIMEOUT_SECONDS, proc.kill)
    watchdog.start()
    ready = False
    recent = []  # last few log lines, for the failure message
    try:
        for line in proc.stdout:
            recent = (recent + [line.strip()])[-4:]
            if 'access with one of' in line:
                ready = True
                break
    finally:
        watchdog.cancel()
    loaded = ready and _load_in_mpv(f'http://127.0.0.1:{port}/',
                                    stream_window_title(site, channel, title))
    if not loaded:
        superseded = not _is_current_streamlink(proc)
        if not superseded:
            with _player_lock:  # the previous stream (if still alive) stays current
                _streamlink_proc = previous if previous is not None and previous.poll() is None else None
            if not ready:
                _log('streamlink never reported its local server; last output: ' + ' | '.join(recent))
            else:
                _log('streamlink is serving, but mpv could not be started or controlled')
        try:
            proc.terminate()
        except OSError:
            pass
        if not superseded:
            webbrowser.open(fallback_url)
        return
    global _playing
    with _player_lock:
        _playing = (proc, site, channel)
    if previous is not None and previous.poll() is None:
        try:
            previous.terminate()  # mpv has already switched over to the new stream
        except OSError:
            pass
    threading.Thread(target=_apply_stream_title, args=(proc, url, site, channel), daemon=True).start()
    threading.Thread(target=_stop_streamlink_when_mpv_exits, args=(proc,), daemon=True).start()
    for _line in proc.stdout:  # drain so streamlink never blocks on a full pipe
        pass


def _open_live_stream(url, quality, site, channel, fallback_url, title=None):
    if shutil.which('streamlink') and shutil.which('mpv'):
        try:
            threading.Thread(
                target=_play_in_shared_mpv,
                args=(url, quality, site, channel, fallback_url, title),
                daemon=True).start()
            return
        except Exception:
            pass
    webbrowser.open(fallback_url)


def open_twitch_stream(channel, quality='best', site='Twitch', title=None):
    """Plays the stream in the shared, maximized mpv window (see above) if
    streamlink and mpv are installed; falls back to opening the channel in
    the browser otherwise, or if starting playback fails."""
    _open_live_stream(f'twitch.tv/{channel}', quality, site, channel,
                      f'https://twitch.tv/{channel}', title)


def open_youtube_stream(channel, quality='best', title=None):
    """Same as open_twitch_stream (shared mpv window); falls back to the
    channel's page in the browser."""
    _open_live_stream(f'https://www.youtube.com/{channel}/live', quality,
                      'YouTube', channel, f'https://www.youtube.com/{channel}', title)
