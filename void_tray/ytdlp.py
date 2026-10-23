"""yt-dlp as a media source: YouTube channels, playlists and videos, and every
other site yt-dlp supports (add them as `yt-dlp:<url>` lines).

Listing runs `yt-dlp --flat-playlist --dump-single-json`, which does not touch the
videos themselves; each episode is downloaded by its own yt-dlp run, with its
subtitles, into the place the app chose.
"""

import json
import os
import re
import shutil
import subprocess
import urllib.parse
from datetime import datetime, timezone

from .media import DownloadCancelled, SUBTITLE_EXTENSIONS
from .updates import topgrade_env  # the environment for a tool run from the tray: UTF-8, plus ~/.local/bin on PATH


YOUTUBE_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'}
LIST_TIMEOUT_SECONDS = 300
MAX_PLAYLISTS = 10  # playlists opened from a channel's podcasts or playlists tab

# What every download gets; the `ytdlp=` setting comes after these and wins over them.
DOWNLOAD_ARGS = ['-S', 'res:1080', '--write-subs', '--sub-langs', 'en.*,-live_chat', '--sub-format', 'srt/vtt/best',
                 '--no-playlist', '--no-warnings']
LIST_ARGS = ['--flat-playlist', '--dump-single-json', '--no-warnings', '--extractor-args', 'youtubetab:approximate_date']

_PROGRESS_MARK = 'YTDLP_PROGRESS '
_FILE_MARK = 'YTDLP_FILE '
_PRIVATE_TITLES = ('[private video]', '[deleted video]')
_UNPLAYABLE = ('is_live', 'is_upcoming')
_CHANNEL_PATH = re.compile(r'^/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)')


def is_youtube_url(url):
    return urllib.parse.urlparse(url).netloc.lower() in YOUTUBE_HOSTS


def find_ytdlp(env=None):
    path = shutil.which('yt-dlp', path=(env or {}).get('PATH'))
    if not path:
        raise ValueError('yt-dlp is not installed (sudo xbps-install yt-dlp)')
    return path


def _error_text(output):
    """The reason in yt-dlp's output: its last ERROR line, else its last line."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith('ERROR')]
    text = (errors or lines or ['yt-dlp failed'])[-1]
    return re.sub(r'^ERROR:\s*(\[[^\]]*\]\s*)?', '', text)[:300]


def _dump(url, extra_args):
    env = topgrade_env()
    command = [find_ytdlp(env), *LIST_ARGS, *extra_args, '--', url]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=LIST_TIMEOUT_SECONDS, env=env)
    except subprocess.TimeoutExpired:
        raise ValueError('yt-dlp took too long to list the source') from None
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError(_error_text(result.stderr or result.stdout))
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise ValueError('yt-dlp gave an answer that is not JSON') from None


def _is_playlist(entry):
    """An entry that is a playlist (or channel tab) rather than a video."""
    url = entry.get('url') or ''
    return (entry.get('_type') == 'playlist' or entry.get('ie_key') in ('YoutubeTab', 'YoutubePlaylist')
            or '/playlist?list=' in url)


def _date(entry):
    stamp = entry.get('upload_date')
    if isinstance(stamp, str) and re.fullmatch(r'\d{8}', stamp):
        return f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}'
    timestamp = entry.get('timestamp') or entry.get('release_timestamp')
    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime('%Y-%m-%d')
    return ''


def _episode(entry):
    """An episode from one entry of a listing, None for one that cannot be downloaded."""
    ident = entry.get('id')
    url = entry.get('webpage_url') or entry.get('url')
    if not isinstance(ident, str) or not ident or not isinstance(url, str):
        return None
    if not url.startswith(('http://', 'https://')):
        if entry.get('ie_key') != 'Youtube':
            return None
        url = f'https://www.youtube.com/watch?v={ident}'
    title = entry.get('title') or ident
    if title.strip().lower() in _PRIVATE_TITLES or entry.get('live_status') in _UNPLAYABLE:
        return None
    if entry.get('availability') in ('private', 'needs_auth', 'subscriber_only', 'premium_only'):
        return None
    return {'id': ident, 'title': title, 'date': _date(entry), 'url': url}


def list_ytdlp(url, extra_args=()):
    """(title, episodes oldest first) for a YouTube channel tab, playlist or video,
    or whatever else yt-dlp can list. Playlists found in the listing (a channel's
    podcasts tab is a list of shows) are opened and their videos used instead."""
    data = _dump(url, extra_args)
    title = data.get('title') or data.get('uploader') or data.get('channel') or url
    entries = data.get('entries')
    if entries is None:  # one video: a movie
        episode = _episode(data)
        return title, [episode] if episode else []
    entries = [e for e in entries if isinstance(e, dict)]
    videos, opened = [], 0
    for entry in entries:
        if not _is_playlist(entry):
            videos.append(entry)
            continue
        link = entry.get('url') or entry.get('webpage_url')
        if opened >= MAX_PLAYLISTS or not isinstance(link, str):
            continue
        opened += 1
        try:
            inner = _dump(link, extra_args)
        except ValueError:
            continue  # one show that cannot be read does not lose the others
        videos.extend(e for e in inner.get('entries') or [] if isinstance(e, dict) and not _is_playlist(e))
    episodes, seen = [], set()
    for entry in videos:
        episode = _episode(entry)
        if episode and episode['id'] not in seen:
            seen.add(episode['id'])
            episodes.append(episode)
    # A channel tab lists its newest video first; a playlist is in its own order.
    if _CHANNEL_PATH.match(urllib.parse.urlparse(url).path) and not any(_is_playlist(e) for e in entries):
        episodes.reverse()
    return title, episodes


def download_ytdlp(episode, base, progress=None, cancelled=None, extra_args=()):
    """Downloads an episode with yt-dlp to `base` + its extension, with the
    subtitles it has as `base`.<lang>.<ext>. Returns {'video': path, 'subs': [paths]}.
    progress(done, total_or_None) is reported per stream (video, then audio)."""
    env = topgrade_env()
    directory = os.path.dirname(base)
    os.makedirs(directory, exist_ok=True)
    # --print makes yt-dlp quiet, which hides the progress lines unless --progress is given
    command = [find_ytdlp(env), *DOWNLOAD_ARGS, *extra_args, '--newline', '--no-simulate', '--progress',
               '--progress-template', f'download:{_PROGRESS_MARK}%(progress.downloaded_bytes)s %(progress.total_bytes)s '
                                      '%(progress.total_bytes_estimate)s %(progress.filename)s',
               '--print', f'after_move:{_FILE_MARK}%(filepath)s', '-o', base + '.%(ext)s', '--', episode['url']]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    video, tail = None, []
    try:
        for line in proc.stdout:
            line = line.rstrip('\n')
            if cancelled and cancelled():
                raise DownloadCancelled()
            if line.startswith(_PROGRESS_MARK):
                if progress:
                    fields = line[len(_PROGRESS_MARK):].split(None, 3)  # the file name is last and may hold spaces
                    numbers = [int(n) if n.isdigit() else None for n in fields[:3]]
                    numbers += [None] * (3 - len(numbers))
                    done, exact, estimate = numbers
                    named = fields[3] if len(fields) > 3 else ''
                    if done is not None and os.path.splitext(named)[1].lower() not in SUBTITLE_EXTENSIONS:
                        progress(done, exact or estimate)  # a subtitle's own 100 % would flash before the video starts
            elif line.startswith(_FILE_MARK):
                video = line[len(_FILE_MARK):]
            elif line.strip():
                tail.append(line)
                del tail[:-20]
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if proc.returncode != 0:
        raise ValueError(_error_text('\n'.join(tail)))
    if not video or not os.path.exists(video):
        raise ValueError('yt-dlp finished without saving a file')
    stem = os.path.basename(base) + '.'
    subs = sorted(os.path.join(directory, name) for name in os.listdir(directory)
                  if name.startswith(stem) and os.path.splitext(name)[1].lower() in SUBTITLE_EXTENSIONS)
    return {'video': video, 'subs': subs}
