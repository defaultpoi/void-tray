"""Podcasts, series and movies: reading sources, episode bookkeeping, downloads.

A source is an archive.org collection or item, or any RSS/Atom feed whose
entries carry audio or video enclosures. Every source becomes a list of
episodes; the app keeps the next few unwatched ones on disk, with subtitles.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

import feedparser

from .hls import DownloadCancelled, download_hls, is_hls


ARCHIVE_BASE = 'https://archive.org'
USER_AGENT = 'void-tray (+https://github.com/defaultpoi/void-tray)'
HTTP_TIMEOUT_SECONDS = 30
ARCHIVE_MAX_ITEMS = 1000  # episodes read from one archive.org collection

VIDEO_EXTENSIONS = ('.mp4', '.m4v', '.mkv', '.webm', '.avi', '.mov', '.mpg', '.mpeg', '.ogv')
AUDIO_EXTENSIONS = ('.mp3', '.m4a', '.ogg', '.opus', '.flac', '.wav')
SUBTITLE_EXTENSIONS = ('.srt', '.vtt', '.ass')


# ---- sources ---------------------------------------------------------------

def archive_identifier(url):
    """The identifier in an archive.org details/download/metadata URL, else None."""
    parsed = urllib.parse.urlparse(url)
    hosts = {'archive.org', 'www.archive.org', urllib.parse.urlparse(ARCHIVE_BASE).netloc}
    if parsed.netloc not in hosts:
        return None
    parts = [p for p in parsed.path.split('/') if p]
    if len(parts) >= 2 and parts[0] in ('details', 'download', 'metadata'):
        return urllib.parse.unquote(parts[1])
    return None


def _first(value):
    """archive.org gives some fields as a string, others as a list of them."""
    if isinstance(value, list):
        value = value[0] if value else None
    return value if isinstance(value, str) and value.strip() else None


def fetch_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.load(response)


# ---- titles, numbering, order ---------------------------------------------

_EPISODE_RE = re.compile(r'\bS(\d{1,2})\s?E(\d{1,3})\b|\b(\d{1,2})x(\d{2,3})\b', re.IGNORECASE)


def season_episode(title):
    """(season, episode) read from 'S01E05' or '1x05' in a title, else (None, None)."""
    match = _EPISODE_RE.search(title or '')
    if not match:
        return None, None
    season, episode = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
    return int(season), int(episode)


def clean_title(title, names=()):
    """The episode's own title: archive.org and many feeds put the series name in
    front and the number in the middle ('Show S01E05 Pilot'), which the row and the
    file name already carry. Falls back to the full title when nothing is left."""
    text = title or ''
    for name in names:
        if name and text.lower().startswith(name.lower()):
            text = text[len(name):]
            break
    text = _EPISODE_RE.sub('', text, count=1)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\s*[\-\u2013\u2014:\u00b7|]\s*[\-\u2013\u2014:\u00b7|]+\s*', ' - ', text)  # what the number leaves between two separators
    text = re.sub(r'^[\s\-\u2013\u2014:\u00b7.,|]+|[\s\-\u2013\u2014:\u00b7.,|]+$', '', text)
    return text or (title or '')


def episode_label(episode):
    """'S01E05' when the title carried a number, else the date, else ''."""
    if episode.get('season') is not None and episode.get('episode') is not None:
        return f"S{episode['season']:02d}E{episode['episode']:02d}"
    return episode.get('date') or ''


def order_episodes(episodes, order):
    """The watching order: season and episode where titles carry them, then date;
    episodes the site gives no way to tell apart stay in the order the driver listed
    them, which is oldest first. 'newest' turns it all around."""
    def key(e):
        return (e.get('season') or 0, e.get('episode') or 0, e.get('date') or '')
    ordered = sorted(episodes, key=key)  # stable
    return ordered[::-1] if order == 'newest' else ordered


def upcoming(episodes, watched, order, count=1):
    """The next `count` episodes not watched yet, in watching order."""
    watched = set(watched)
    return [e for e in order_episodes(episodes, order) if e['id'] not in watched][:count]


def merge_episodes(entry, title, episodes, order):
    """Folds a fresh episode list into a source's stored entry and returns the
    episodes that are new since the last check. The first check only sets the
    baseline: a series starts at its first episode, a podcast at its latest."""
    for episode in episodes:
        given = (episode.get('season'), episode.get('episode'))
        if not all(isinstance(n, int) and not isinstance(n, bool) for n in given):  # else read them from the title
            episode['season'], episode['episode'] = season_episode(episode.get('title'))
    known = set(entry.get('known', []))
    entry['title'] = title
    entry['episodes'] = episodes
    ids = [e['id'] for e in episodes]
    if not entry.get('baselined'):
        entry['baselined'] = True
        entry['known'] = ids
        if order == 'newest':  # skip the backlog: only the latest episode is waiting
            entry['watched'] = [e['id'] for e in order_episodes(episodes, order)[1:]]
        return []
    new = [e for e in episodes if e['id'] not in known]
    entry['known'] = sorted(known | set(ids))
    entry['new'] = [i for i in entry.get('new', []) if i in set(ids)] + [e['id'] for e in new]
    return new


def new_unwatched(entry):
    """How many episodes appeared since the baseline and are still unwatched."""
    watched = set(entry.get('watched', []))
    return sum(1 for i in entry.get('new', []) if i not in watched)


def unwatched_count(entry):
    watched = set(entry.get('watched', []))
    return sum(1 for e in entry.get('episodes', []) if e['id'] not in watched)


# ---- reading a source ------------------------------------------------------

def _media_rank(file):
    """Sort key for choosing which of an archive.org item's files to download,
    None for files that are not media: the h.264 derivative first (plays
    everywhere), then originals, then the small derivatives."""
    if file.get('source') == 'metadata' or not isinstance(file.get('name'), str):
        return None
    extension = os.path.splitext(file['name'])[1].lower()
    extensions = VIDEO_EXTENSIONS + AUDIO_EXTENSIONS
    if extension not in extensions:
        return None
    fmt = (file.get('format') or '').lower()
    quality = 0 if 'h.264' in fmt else 1 if file.get('source') == 'original' else 2
    return (quality, extensions.index(extension))


def _subtitle_language(name):
    stem = os.path.splitext(name)[0]
    match = re.search(r'[._-]([a-z]{2,3}(?:-[A-Za-z]{2,4})?)$', stem)
    return match.group(1) if match else None


def archive_files(identifier, files):
    """(media_file, [subtitle files]) among an archive.org item's files; each
    file is {'url', 'name', 'ext', 'lang'}. media_file is None without media."""
    def entry(file):
        name = file['name']
        return {'url': f'{ARCHIVE_BASE}/download/{urllib.parse.quote(identifier)}/{urllib.parse.quote(name)}',
                'name': name, 'ext': os.path.splitext(name)[1].lower(), 'lang': _subtitle_language(name)}
    ranked = [(rank, f) for f in files if isinstance(f, dict) for rank in [_media_rank(f)] if rank is not None]
    media = entry(min(ranked, key=lambda pair: pair[0])[1]) if ranked else None
    subtitles = [entry(f) for f in files if isinstance(f, dict) and isinstance(f.get('name'), str)
                 and os.path.splitext(f['name'])[1].lower() in SUBTITLE_EXTENSIONS]
    return media, subtitles


def _archive_collection_episodes(identifier):
    params = [('q', f'collection:{identifier} AND NOT mediatype:collection'),
              ('fl[]', 'identifier'), ('fl[]', 'title'), ('fl[]', 'date'), ('fl[]', 'publicdate'),
              ('sort[]', 'date asc'), ('rows', str(ARCHIVE_MAX_ITEMS)), ('output', 'json')]
    data = fetch_json(f'{ARCHIVE_BASE}/advancedsearch.php?' + urllib.parse.urlencode(params))
    episodes = []
    for doc in (data.get('response') or {}).get('docs') or []:
        item = _first(doc.get('identifier'))
        if item:
            date = _first(doc.get('date')) or _first(doc.get('publicdate')) or ''
            episodes.append({'id': item, 'title': _first(doc.get('title')) or item, 'date': date[:10],
                             'kind': 'archive', 'ref': item})
    return episodes


def _group_key(file):
    """What a media file is a version of: its derivatives (h.264, 512Kb, Ogg) belong
    with the original they were made from, named in `original` or else by suffix."""
    original = file.get('original')
    base = original if isinstance(original, str) and original else file['name']
    return re.sub(r'_(?:512kb|64kb|ia)$', '', os.path.splitext(base)[0])


def _archive_item_episodes(identifier, files, title):
    """A single item: one episode per media file (a movie has one), the versions of
    a file counting as one. Items that hold a whole series list every file."""
    stems = {}
    for f in files:
        if isinstance(f, dict) and isinstance(f.get('name'), str) and _media_rank(f) is not None:
            stems.setdefault(_group_key(f), []).append(f)
    episodes = []
    for stem, group in sorted(stems.items()):
        media, _subs = archive_files(identifier, group)
        subs = [s for s in archive_files(identifier, files)[1]
                if len(stems) == 1 or s['name'].startswith(stem)]
        name = title if len(stems) == 1 else stem.replace('_', ' ')
        episodes.append({'id': f"{identifier}/{media['name']}", 'title': name, 'date': '',
                         'kind': 'archive-file', 'ref': identifier, 'url': media['url'], 'ext': media['ext'],
                         'subs': subs})
    return episodes


def list_archive(identifier):
    """(title, episodes) of an archive.org collection (each item an episode) or
    of a single item (each media file an episode)."""
    meta = fetch_json(f'{ARCHIVE_BASE}/metadata/{urllib.parse.quote(identifier)}')
    info = meta.get('metadata') or {}
    title = _first(info.get('title')) or identifier
    if _first(info.get('mediatype')) == 'collection':
        return title, _archive_collection_episodes(identifier)
    return title, _archive_item_episodes(identifier, meta.get('files') or [], title)


_MIME_EXTENSIONS = {'audio/mpeg': '.mp3', 'audio/mp3': '.mp3', 'audio/mp4': '.m4a', 'audio/x-m4a': '.m4a',
                    'audio/ogg': '.ogg', 'audio/opus': '.opus', 'video/mp4': '.mp4', 'video/webm': '.webm',
                    'video/x-matroska': '.mkv', 'video/quicktime': '.mov'}


def _enclosure_extension(href, mime):
    extension = os.path.splitext(urllib.parse.urlparse(href).path)[1].lower()
    if extension in VIDEO_EXTENSIONS + AUDIO_EXTENSIONS:
        return extension
    return _MIME_EXTENSIONS.get((mime or '').lower(), '.mp4' if (mime or '').startswith('video') else '.mp3')


def list_feed(url):
    """(title, episodes) from an RSS/Atom feed: its entries with an audio or
    video enclosure. Raises ValueError for a feed that could not be read."""
    parsed = feedparser.parse(url, agent=USER_AGENT)
    if not parsed.entries and getattr(parsed, 'bozo', False):
        raise ValueError(f'could not read {url}')
    episodes = []
    for entry in parsed.entries:
        enclosure = next((e for e in entry.get('enclosures', [])
                          if (e.get('type') or '').startswith(('audio', 'video'))
                          or os.path.splitext(urllib.parse.urlparse(e.get('href', '')).path)[1].lower()
                          in VIDEO_EXTENSIONS + AUDIO_EXTENSIONS), None)
        if not enclosure or not enclosure.get('href'):
            continue
        published = entry.get('published_parsed') or entry.get('updated_parsed')
        date = f'{published[0]:04d}-{published[1]:02d}-{published[2]:02d}' if published else ''
        episodes.append({'id': entry.get('id') or enclosure['href'], 'title': entry.get('title') or enclosure['href'],
                         'date': date, 'kind': 'feed', 'ref': enclosure['href'], 'url': enclosure['href'],
                         'ext': _enclosure_extension(enclosure['href'], enclosure.get('type')), 'subs': []})
    return (parsed.feed.get('title') or url), episodes


def list_episodes(url):
    """(title, episodes) for a source URL."""
    identifier = archive_identifier(url)
    return list_archive(identifier) if identifier else list_feed(url)


def resolve_files(episode):
    """{'video': {'url','ext'}, 'subs': [{'url','ext','lang'}]} for an episode;
    the files of an archive.org collection item are only looked up here, when the
    episode is about to be downloaded, not for every item at every check."""
    if episode.get('url'):
        return {'video': {'url': episode['url'], 'ext': episode.get('ext') or '.mp4'}, 'subs': episode.get('subs') or []}
    meta = fetch_json(f"{ARCHIVE_BASE}/metadata/{urllib.parse.quote(episode['ref'])}")
    media, subs = archive_files(episode['ref'], meta.get('files') or [])
    if not media:
        raise ValueError('no media file in this item')
    return {'video': {'url': media['url'], 'ext': media['ext']}, 'subs': subs}


# ---- files on disk ---------------------------------------------------------

def safe_name(text, limit=90):
    """A file or folder name made of `text`: no path separators or characters
    that trip up filesystems, no leading dots."""
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', ' ', text or '')
    text = re.sub(r'\s+', ' ', text).strip(' .')
    return text[:limit].rstrip(' .') or 'untitled'


def episode_basename(source_title, episode, names=()):
    """'Show - S01E05 - Title' (the label is the date when there is no number);
    `names` are other names the source goes by, to cut from the front of the title."""
    title = clean_title(episode.get('title'), (source_title, *names))
    parts = [safe_name(source_title), episode_label(episode), safe_name(title)]
    return ' - '.join(p for p in parts if p)


def episode_basepath(root, source_title, episode, taken=(), names=()):
    """Where an episode's files go, without the extension: in a folder named after
    the source. A name another episode's files already use (`taken` are their paths,
    with or without extension) gets ' (2)'."""
    directory = os.path.join(os.path.expanduser(root), safe_name(source_title))
    stem = episode_basename(source_title, episode, names)
    used = {os.path.splitext(p)[0] for p in taken}
    base, number = os.path.join(directory, stem), 2
    while base in used:
        base = os.path.join(directory, f'{stem} ({number})')
        number += 1
    return base


def episode_paths(root, source_title, episode, files, taken=(), names=()):
    """Where an episode's files go: {'video': path, 'subs': [path...]}."""
    base = episode_basepath(root, source_title, episode, taken, names)
    directory = os.path.dirname(base)
    video = base + files['video']['ext']
    base = os.path.basename(base)
    subs, used = [], set()
    for sub in files['subs']:
        lang = sub.get('lang') or ''
        name = f"{base}.{lang}{sub['ext']}" if lang else f"{base}{sub['ext']}"
        while name in used:
            name = f"{base}.{len(used)}{sub['ext']}"
        used.add(name)
        subs.append(os.path.join(directory, name))
    return {'video': video, 'subs': subs}


def download_file(url, dest, progress=None, cancelled=None, block_size=1 << 18, headers=None):
    """Downloads url to dest through dest + '.part', carrying on from a partial
    file when the server allows. `headers` are sent with the request (a Referer, a
    cookie). progress(done, total_or_None) after each block. Raises on failure,
    keeping the partial file for the next try."""
    part = dest + '.part'
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    start = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {'User-Agent': USER_AGENT, **(headers or {})}
    if start:
        headers['Range'] = f'bytes={start}-'
    try:
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=HTTP_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as error:
        if error.code == 416 and start:  # the partial file is all of it
            os.replace(part, dest)
            return dest
        raise
    with response:
        resumed = bool(start) and response.status == 206
        if not resumed:
            start = 0
        length = response.headers.get('Content-Length')
        total = start + int(length) if length and length.isdigit() else None
        done = start
        with open(part, 'ab' if resumed else 'wb') as f:
            while True:
                if cancelled and cancelled():
                    raise DownloadCancelled()
                block = response.read(block_size)
                if not block:
                    break
                f.write(block)
                done += len(block)
                if progress:
                    progress(done, total)
        if total is not None and done < total:
            raise OSError('the connection closed before the end of the file')
    os.replace(part, dest)
    return dest


def download_episode(paths, files, progress=None, cancelled=None):
    """Downloads an episode's subtitles (a failing one is skipped) and then its
    video to `paths`; an HLS (.m3u8) video is fetched with ffmpeg, anything else
    as a file. Each file is asked for with its own `headers`, if it has any.
    Returns {'video': path, 'subs': [the subtitle paths saved]}."""
    saved = []
    for sub, path in zip(files['subs'], paths['subs']):
        try:
            download_file(sub['url'], path, cancelled=cancelled, headers=sub.get('headers'))
            saved.append(path)
        except DownloadCancelled:
            raise
        except (OSError, ValueError):
            pass  # an episode without its subtitles is better than none
    video = files['video']
    if is_hls(video['url']):
        download_hls(video['url'], paths['video'], headers=video.get('headers'), progress=progress, cancelled=cancelled)
    else:
        download_file(video['url'], paths['video'], progress=progress, cancelled=cancelled, headers=video.get('headers'))
    return {'video': paths['video'], 'subs': saved}
