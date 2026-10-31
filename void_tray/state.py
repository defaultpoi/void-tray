"""state.json: loading with validation and atomic saving."""

import json
import math
import os
import uuid

from .constants import CONFIG_DIR
from .log import _log


STATE_FILE = os.path.join(CONFIG_DIR, 'state.json')
MEDIA_FILE = os.path.join(CONFIG_DIR, 'media.json')  # kept apart: episode lists are long


def _set_aside(path, why):
    """Moves a data file the app cannot use to `path`.corrupt before the empty
    default that replaces it is saved over it, so what was in it can still be
    repaired by hand. One copy is kept: a later corrupt file replaces it."""
    name = os.path.basename(path)
    try:
        os.replace(path, path + '.corrupt')
    except OSError as e:
        _log(f'{name} is {why} and could not be set aside: {e}')
        return
    _log(f'{name} is {why}; moved to {name}.corrupt and started afresh')


def load_state():
    state = {"seen": {}, "unread": []}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                loaded = json.load(f)
        except OSError:
            pass  # unreadable right now, which does not make it corrupt: leave it alone
        except ValueError:  # not JSON (JSONDecodeError) or not text (UnicodeDecodeError)
            _set_aside(STATE_FILE, 'not valid JSON')
        else:
            if isinstance(loaded, dict):
                state = loaded
            else:
                _set_aside(STATE_FILE, 'not a JSON object')

    # Validate shape — a corrupted/hand-edited state.json shouldn't crash
    # the app elsewhere; reset just the offending key to a sane default.
    if not isinstance(state.get('seen'), dict):
        state['seen'] = {}
    if not isinstance(state.get('unread'), list):
        state['unread'] = []
    if not isinstance(state.get('available_updates'), list):
        state['available_updates'] = []
    # saved items need an id, a title and a link to be listed and opened
    if isinstance(state.get('read_later'), list):
        state['read_later'] = [e for e in state['read_later']
                               if isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ('id', 'title', 'link'))]
    else:
        state['read_later'] = []
    if isinstance(state.get('orphans'), list):
        state['orphans'] = [name for name in state['orphans'] if isinstance(name, str)]
    else:
        state['orphans'] = []
    if not isinstance(state.get('live_channels'), list):
        state['live_channels'] = []
    if not isinstance(state.get('live_youtube_channels'), list):
        state['live_youtube_channels'] = []
    if not isinstance(state.get('last_checked'), dict):
        state['last_checked'] = {}
    # the running countdown's end as a wall-clock timestamp; anything else is dropped
    end = state.get('timer_end')
    if isinstance(end, bool) or not isinstance(end, (int, float)) or not math.isfinite(end):
        state.pop('timer_end', None)

    return state


def _write_json(path, data):
    """Writes `data` to `path` through a temp file of its own, renamed into
    place, so a reader never sees a half-written file and two writers (two
    threads, or a new instance starting while the old one exits) never share
    a temp file. The temp file is removed if anything fails."""
    tmp = f'{path}.{uuid.uuid4().hex}.tmp'
    try:
        with open(tmp, 'x') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_state(state):
    _write_json(STATE_FILE, state)


def _clean_media_entry(entry):
    """One source's stored entry with every field the code reads present and of
    the right type; entries of the wrong shape inside lists are dropped."""
    def strings(value):
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []
    files = entry.get('files')
    last = entry.get('last_watched')
    return {
        'title': entry['title'] if isinstance(entry.get('title'), str) else '',
        'episodes': [e for e in entry.get('episodes', []) if isinstance(e, dict) and isinstance(e.get('id'), str)]
        if isinstance(entry.get('episodes'), list) else [],
        'known': strings(entry.get('known')),
        'new': strings(entry.get('new')),
        'watched': strings(entry.get('watched')),
        'files': {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, dict)
                  and isinstance(v.get('video'), str)} if isinstance(files, dict) else {},
        'last_watched': last if isinstance(last, dict) and isinstance(last.get('id'), str) else None,
        'baselined': entry.get('baselined') is True,
    }


def new_media_entry():
    return _clean_media_entry({})


def load_media():
    """media.json as {'sources': {url: entry}}; a missing or unreadable file
    starts empty, and a file that is not JSON or not the expected shape is set
    aside as media.json.corrupt first (see _set_aside)."""
    media = {'sources': {}}
    try:
        with open(MEDIA_FILE) as f:
            loaded = json.load(f)
    except OSError:
        return media  # the first run has no file; an unreadable one is left alone
    except ValueError:
        _set_aside(MEDIA_FILE, 'not valid JSON')
        return media
    if not isinstance(loaded, dict) or not isinstance(loaded.get('sources', {}), dict):
        _set_aside(MEDIA_FILE, 'not the expected shape')
        return media
    for url, entry in loaded.get('sources', {}).items():
        if isinstance(url, str) and isinstance(entry, dict):
            media['sources'][url] = _clean_media_entry(entry)
    return media


def save_media(media):
    _write_json(MEDIA_FILE, media)
