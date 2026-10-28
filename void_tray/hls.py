"""Downloading HLS (.m3u8) streams with ffmpeg.

Many sites serve a video as an HLS playlist. The best version is picked from a
master playlist, the length is taken from the playlist itself (ffprobe fails on
some servers), and ffmpeg copies the streams without re-encoding into a file
that appears under its final name only when it is complete.
"""

import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from collections import deque

from .updates import topgrade_env  # the environment for a tool run from the tray: UTF-8, plus ~/.local/bin on PATH


class DownloadCancelled(Exception):
    pass


PLAYLIST_TIMEOUT_SECONDS = 30
PLAYLIST_MAX_BYTES = 5_000_000
STALL_SECONDS = 120  # without a byte of output for this long the stream is given up on
POLL_SECONDS = 0.5  # how often a running download looks at cancelled() and the stall clock
DEFAULT_USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
FORMATS = {'.ts': 'mpegts', '.mkv': 'matroska', '.mp4': 'mp4'}  # what ffmpeg writes, by the extension asked for
DEFAULT_EXTENSION = '.mkv'  # seeks properly and takes any codec; .ts is fine too

_ATTRIBUTE_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
_EXTINF_RE = re.compile(r'^#EXTINF:([\d.]+)', re.MULTILINE)
_PROGRESS_LINE_RE = re.compile(r'^[a-z_0-9]+=')


def is_hls(url):
    return urllib.parse.urlparse(url).path.lower().endswith(('.m3u8', '.m3u'))


def parse_master(text, base_url):
    """[{'url', 'bandwidth', 'width', 'height'}] for the variants of a master playlist (width and
    height 0 where it does not say); empty for a media playlist."""
    lines = text.splitlines()
    variants = []
    for number, line in enumerate(lines):
        if not line.startswith('#EXT-X-STREAM-INF:'):
            continue
        attributes = dict(_ATTRIBUTE_RE.findall(line.split(':', 1)[1]))
        uri = next((l.strip() for l in lines[number + 1:] if l.strip() and not l.startswith('#')), None)
        if not uri:
            continue
        size = re.match(r'(\d+)x(\d+)', attributes.get('RESOLUTION', ''))
        variants.append({'url': urllib.parse.urljoin(base_url, uri),
                         'bandwidth': int(attributes['BANDWIDTH']) if attributes.get('BANDWIDTH', '').isdigit() else 0,
                         'width': int(size.group(1)) if size else 0, 'height': int(size.group(2)) if size else 0})
    return variants


FIT_TOLERANCE = 1.15  # a variant up to this much larger than the screen still counts as fitting it


def pick_variant(variants, screen=None):
    """The variant to download. Without a screen (width, height), or when the variants do
    not say how large they are, the one with the most bandwidth. Otherwise the best one
    that fits the screen, measured as a player shows it, scaled to fit: 1440x720 fits
    1366x768, 1920x1080 does not, and a little more than the screen is fine (see
    FIT_TOLERANCE). When none fits, the smallest of them, so that a stream whose only
    version is larger than the screen is still downloaded."""
    def best(group):
        return max(group, key=lambda v: (v['bandwidth'], v['height']))
    sized = [v for v in variants if v['width'] and v['height']]
    if not screen or not sized:
        return best(variants)

    def oversize(v):  # how many times larger than the screen it is in the direction that limits it
        return max(v['width'] / screen[0], v['height'] / screen[1])
    fitting = [v for v in sized if oversize(v) <= FIT_TOLERANCE]
    return best(fitting) if fitting else min(sized, key=lambda v: (oversize(v), -v['bandwidth']))


def playlist_seconds(text):
    """The length in seconds of a media playlist, from its segments; 0 when it has none."""
    return sum(float(m) for m in _EXTINF_RE.findall(text))


def _fetch_text(url, headers):
    request = urllib.request.Request(url, headers={'User-Agent': DEFAULT_USER_AGENT, **headers})
    with urllib.request.urlopen(request, timeout=PLAYLIST_TIMEOUT_SECONDS) as response:
        return response.read(PLAYLIST_MAX_BYTES).decode('utf-8', 'replace')


def _ffmpeg_headers(headers):
    """The -user_agent and -headers arguments that make ffmpeg ask the way the plugin says."""
    arguments = []
    agent = next((v for k, v in headers.items() if k.lower() == 'user-agent'), DEFAULT_USER_AGENT)
    arguments += ['-user_agent', agent]
    others = ''.join(f'{k}: {v}\r\n' for k, v in headers.items() if k.lower() != 'user-agent')
    if others:
        arguments += ['-headers', others]
    return arguments


def _ffmpeg_error_text(lines):
    """Why ffmpeg failed, from the lines it printed: the last one that is not a progress line."""
    useful = [l for l in lines if l and not _PROGRESS_LINE_RE.match(l)]
    return (useful[-1] if useful else 'ffmpeg failed')[:300]


def download_hls(url, dest, headers=None, progress=None, cancelled=None, stall_seconds=None, screen=None):
    """Downloads the HLS stream at `url` to `dest` (.mkv, .ts or .mp4) and returns dest.
    `headers` are sent with every request (a Referer, a User-Agent). Of the versions a master
    playlist offers, the one that suits `screen` (width, height) is taken, see pick_variant. progress(seconds,
    total_seconds_or_None) is called as it goes. Raises ValueError for a stream that is
    not finished (live), or when ffmpeg fails or stalls, DownloadCancelled when
    cancelled() turns true. The file is written as dest + '.part' and renamed at the end;
    a failed download leaves nothing, since an HLS download cannot be carried on."""
    headers = dict(headers or {})
    stall_seconds = STALL_SECONDS if stall_seconds is None else stall_seconds
    extension = os.path.splitext(dest)[1].lower()
    if extension not in FORMATS:
        raise ValueError(f'cannot save a stream as {extension or "a file without an extension"}')
    env = topgrade_env()
    ffmpeg = shutil.which('ffmpeg', path=env.get('PATH'))
    if not ffmpeg:
        raise ValueError('ffmpeg is not installed (sudo xbps-install ffmpeg)')

    text = _fetch_text(url, headers)
    variants = parse_master(text, url)
    if variants:
        url = pick_variant(variants, screen)['url']
        text = _fetch_text(url, headers)
    if '#EXT-X-ENDLIST' not in text:
        raise ValueError('this stream is live or not finished yet')
    total = playlist_seconds(text) or None
    if cancelled and cancelled():  # asked to stop while the playlists were being read
        raise DownloadCancelled()

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + '.part'
    command = [ffmpeg, '-nostdin', '-y', '-loglevel', 'error', *_ffmpeg_headers(headers), '-i', url,
               '-map', '0:v:0?', '-map', '0:a?', '-c', 'copy']
    if extension == '.mp4':
        command += ['-bsf:a', 'aac_adtstoasc']
    command += ['-f', FORMATS[extension], '-progress', 'pipe:1', '-nostats', part]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)

    state = {'seconds': 0.0, 'size': 0}
    tail = deque(maxlen=20)

    def read():  # ffmpeg writes about a dozen lines per update and stops when nobody reads them
        for line in proc.stdout:
            line = line.strip()
            key, _, value = line.partition('=')
            if key in ('out_time_us', 'out_time_ms') and value.lstrip('-').isdigit():  # both are microseconds
                state['seconds'] = max(state['seconds'], int(value) / 1_000_000)
            elif key == 'total_size' and value.isdigit():
                state['size'] = int(value)
            else:
                tail.append(line)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        last_change, last_seen = time.monotonic(), (0.0, 0)
        reported = None
        while True:
            try:
                proc.wait(timeout=POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancelled and cancelled():
                raise DownloadCancelled()
            seen = (state['seconds'], state['size'])
            if seen != last_seen:
                last_seen, last_change = seen, time.monotonic()
            elif time.monotonic() - last_change > stall_seconds:
                raise ValueError('the stream stopped delivering data')
            if progress and int(seen[0]) != reported:
                reported = int(seen[0])
                progress(reported, int(total) if total else None)
        reader.join(timeout=5)
        if proc.returncode != 0:
            raise ValueError(_ffmpeg_error_text(list(tail)))
        if not os.path.exists(part) or os.path.getsize(part) == 0:
            raise ValueError('ffmpeg finished without saving anything')
        os.replace(part, dest)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if os.path.exists(part):
            os.remove(part)
        raise
    if progress and total:
        progress(int(total), int(total))
    return dest
