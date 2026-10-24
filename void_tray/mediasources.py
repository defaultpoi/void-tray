"""Media source drivers: the built-in ones (archive.org, yt-dlp, RSS feeds) and the
plugins found in the plugin folder, which all speak the same small interface.

A driver is an object (a plugin is a Python module) with
    matches(source)      -> bool           which [media] lines it handles (optional)
    list_episodes(source) -> (title, [episode, ...])   required; oldest first
    resolve(episode)     -> {'video': {'url', 'ext'}, 'subs': [{'url', 'ext', 'lang'}]}   optional
    download(episode, base, progress, cancelled) -> {'video': path, 'subs': [paths]}   optional
and the optional NAME and ORDER attributes. See "Media plugins" in the README.
"""

import importlib.util
import os
import re
import sys
import threading
import types
import urllib.parse

from .config import load_media_settings
from .hls import DEFAULT_EXTENSION, FORMATS, is_hls
from .log import _log
from .media import archive_identifier, list_archive, list_feed
from .ytdlp import download_ytdlp, is_youtube_url, list_ytdlp


_NAME_RE = re.compile(r'[a-z][a-z0-9_-]*')
_DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')


def _list_archive_source(source):
    """An archive.org URL, or with the `archive:` prefix the bare identifier."""
    return list_archive(archive_identifier(source) or source)


def _list_ytdlp_source(source):
    return list_ytdlp(source, load_media_settings()['ytdlp'])


def _download_ytdlp_episode(episode, base, progress, cancelled):
    return download_ytdlp(episode, base, progress, cancelled, load_media_settings()['ytdlp'])


def _builtin_drivers():
    return [
        types.SimpleNamespace(
            NAME='archive', ORDER='oldest',
            matches=lambda source: archive_identifier(source) is not None,
            list_episodes=_list_archive_source),
        types.SimpleNamespace(
            NAME='yt-dlp', ORDER='newest',
            matches=lambda source: is_youtube_url(source),
            list_episodes=_list_ytdlp_source,
            download=_download_ytdlp_episode),
        types.SimpleNamespace(
            NAME='feed', ORDER='newest',
            matches=lambda source: urllib.parse.urlparse(source).scheme in ('http', 'https'),
            list_episodes=lambda source: list_feed(source)),
    ]


class NoDriver(Exception):
    """No driver handles a source, or an episode's driver is no longer there."""


def normalize_saved(saved):
    """What a driver's download() returned, checked: {'video': a file that exists,
    'subs': [paths]}. Raises ValueError for anything else."""
    video = saved.get('video') if isinstance(saved, dict) else None
    if not isinstance(video, str) or not os.path.isfile(video):
        raise ValueError('the download did not leave the video file where it said')
    subs = saved.get('subs')
    return {'video': video, 'subs': [s for s in subs if isinstance(s, str) and os.path.isfile(s)] if isinstance(subs, list) else []}


def _extension(url, fallback='.mp4'):
    extension = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    return extension if re.fullmatch(r'\.[a-z0-9]{1,5}', extension) else fallback


def _clean_headers(headers):
    """The request headers a driver asked for, as {str: str}; anything else is dropped."""
    if not isinstance(headers, dict):
        return {}
    return {k: v for k, v in headers.items() if isinstance(k, str) and isinstance(v, str) and k and '\n' not in k + v}


def _clean_subs(subs):
    clean = []
    for sub in subs if isinstance(subs, list) else []:
        if isinstance(sub, dict) and isinstance(sub.get('url'), str) and sub['url']:
            ext = sub.get('ext') if isinstance(sub.get('ext'), str) and sub['ext'].startswith('.') else _extension(sub['url'], '.srt')
            lang = sub.get('lang') if isinstance(sub.get('lang'), str) and sub['lang'] else None
            entry = {'url': sub['url'], 'ext': ext, 'lang': lang}
            headers = _clean_headers(sub.get('headers'))
            if headers:
                entry['headers'] = headers
            clean.append(entry)
    return clean


def normalize_files(files):
    """What a driver's resolve() returned, checked: {'video': {'url', 'ext', and
    'headers' if any}, 'subs': [...]}. An HLS video gets .mkv unless it asked for .ts
    or .mp4. Raises ValueError for anything without a video url."""
    video = files.get('video') if isinstance(files, dict) else None
    url = video.get('url') if isinstance(video, dict) else None
    if not isinstance(url, str) or not url:
        raise ValueError("resolve() must return {'video': {'url': ...}, 'subs': [...]}")
    ext = video.get('ext') if isinstance(video.get('ext'), str) and video['ext'].startswith('.') else None
    if is_hls(url):
        ext = ext if ext in FORMATS else DEFAULT_EXTENSION
    else:
        ext = ext or _extension(url)
    clean = {'url': url, 'ext': ext}
    headers = _clean_headers(video.get('headers'))
    if headers:
        clean['headers'] = headers
    return {'video': clean, 'subs': _clean_subs(files.get('subs'))}


def normalize_episodes(driver_name, result):
    """(title, episodes) from whatever a driver's list_episodes returned, with every
    episode checked: a string id that is unique in the source, a title, a date as
    YYYY-MM-DD or empty, a media url with an extension and subtitles when given.
    Episodes that cannot be used are dropped; a result of the wrong shape raises."""
    try:
        title, episodes = result
        episodes = list(episodes)
    except (TypeError, ValueError):
        raise ValueError(f'{driver_name}: list_episodes must return (title, [episodes])') from None
    clean, seen = [], set()
    for entry in episodes:
        if not isinstance(entry, dict) or entry.get('id') in (None, ''):
            continue
        ident = str(entry['id'])
        if ident in seen:
            continue
        seen.add(ident)
        episode = dict(entry)
        episode['id'] = ident
        episode['title'] = str(entry.get('title') or ident)
        date = str(entry.get('date') or '')[:10]
        episode['date'] = date if _DATE_RE.fullmatch(date) else ''
        episode['driver'] = driver_name
        url = entry.get('url')
        if isinstance(url, str) and url:
            ext = entry.get('ext') if isinstance(entry.get('ext'), str) and entry['ext'].startswith('.') else _extension(url)
            episode['ext'] = ext
        else:
            episode.pop('url', None)
            episode.pop('ext', None)
        episode['subs'] = _clean_subs(entry.get('subs'))
        for key in ('season', 'episode'):  # whole numbers, or left for the title to say
            if not isinstance(entry.get(key), int) or isinstance(entry.get(key), bool):
                episode.pop(key, None)
        clean.append(episode)
    return str(title or ''), clean


class Driver:
    """A plugin module or built-in namespace with its name, order and optional parts."""

    def __init__(self, name, module):
        self.name = name
        self.module = module
        order = getattr(module, 'ORDER', None)
        self.order = order if order in ('oldest', 'newest') else 'newest'
        self.resolve = getattr(module, 'resolve', None)
        self.download = getattr(module, 'download', None)

    def matches(self, source):
        check = getattr(self.module, 'matches', None)
        if not check:
            return False
        try:
            return bool(check(source))
        except Exception as error:  # a plugin's bug must not stop the others from being tried
            _log(f'media plugin {self.name}: matches() failed: {error!r}')
            return False

    def list_episodes(self, source):
        return normalize_episodes(self.name, self.module.list_episodes(source))


class MediaSources:
    """The drivers in force: plugins first (so one can take over a site the built-in
    drivers handle), then archive.org, yt-dlp for YouTube, and RSS feeds last."""

    def __init__(self, plugin_dir):
        self.plugin_dir = plugin_dir
        self.errors = {}  # plugin file -> why it could not be loaded
        self._plugins = {}  # file name -> (mtime, Driver or None)
        self._builtins = [Driver(m.NAME, m) for m in _builtin_drivers()]
        self._lock = threading.Lock()

    # ---- the plugin folder ---------------------------------------------------

    def refresh(self):
        """Loads new plugin files and reloads changed ones; forgets removed ones."""
        with self._lock:
            try:
                names = sorted(n for n in os.listdir(self.plugin_dir) if n.endswith('.py') and not n.startswith(('_', '.'))
                               and os.path.isfile(os.path.join(self.plugin_dir, n)))
            except OSError:
                names = []
            for gone in set(self._plugins) - set(names):
                del self._plugins[gone]
                self.errors.pop(gone, None)
            for name in names:
                path = os.path.join(self.plugin_dir, name)
                try:
                    stamp = os.stat(path).st_mtime_ns
                except OSError:
                    continue
                if name in self._plugins and self._plugins[name][0] == stamp:
                    continue
                driver = self._load(name, path)
                self._plugins[name] = (stamp, driver)

    def _load(self, name, path):
        stem = name[:-3]
        module_name = 'void_tray_media_plugin_' + re.sub(r'\W', '_', stem)
        sys.modules.pop(module_name, None)
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            if not callable(getattr(module, 'list_episodes', None)):
                raise ValueError('it has no list_episodes(source) function')
            for optional in ('matches', 'resolve', 'download'):
                if hasattr(module, optional) and not callable(getattr(module, optional)):
                    raise ValueError(f'{optional} is not a function')
            declared = getattr(module, 'NAME', None)
            driver_name = declared if isinstance(declared, str) and _NAME_RE.fullmatch(declared) else stem.lower()
            if not _NAME_RE.fullmatch(driver_name):
                raise ValueError(f'{driver_name!r} cannot be used as a name: start with a letter, then letters, digits, - or _')
        except Exception as error:
            sys.modules.pop(module_name, None)
            self.errors[name] = f'{type(error).__name__}: {error}'
            _log(f'media plugin {name} not loaded: {self.errors[name]}')
            return None
        self.errors.pop(name, None)
        return Driver(driver_name, module)

    # ---- choosing a driver ---------------------------------------------------

    def drivers(self):
        plugins = [driver for _stamp, driver in (self._plugins[n] for n in sorted(self._plugins)) if driver]
        return plugins + self._builtins

    def by_name(self, name):
        return next((d for d in self.drivers() if d.name == name), None)

    def problems(self):
        """Why plugins failed to load, for an error message; '' when all loaded."""
        if not self.errors:
            return ''
        return ' (plugins not loaded: ' + '; '.join(f'{n}: {e}' for n, e in sorted(self.errors.items())) + ')'

    def driver_for(self, source):
        """(driver, what to give it) for a [media] source line: a `name:` prefix that
        is a driver's name selects it and is cut off; otherwise the first driver that
        matches the whole line. Raises NoDriver when none does."""
        prefix, colon, rest = source.partition(':')
        if colon and _NAME_RE.fullmatch(prefix.lower()) and prefix.lower() not in ('http', 'https'):
            driver = self.by_name(prefix.lower())
            if driver:
                return driver, rest
        for driver in self.drivers():
            if driver.matches(source):
                return driver, source
        raise NoDriver(f'no source handler for {source}' + self.problems())

    def driver_of(self, episode):
        """The driver an episode was listed by (episodes stored before drivers existed
        carry only their kind). Raises NoDriver when it is gone, e.g. a removed plugin."""
        name = episode.get('driver') or ('archive' if str(episode.get('kind', '')).startswith('archive') else 'feed')
        driver = self.by_name(name)
        if driver is None:
            raise NoDriver(f'the {name} source plugin is not loaded' + self.problems())
        return driver
