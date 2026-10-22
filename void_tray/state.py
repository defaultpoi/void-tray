"""state.json: loading with validation and atomic saving."""

import json
import math
import os

from .constants import CONFIG_DIR


STATE_FILE = os.path.join(CONFIG_DIR, 'state.json')
MEDIA_FILE = os.path.join(CONFIG_DIR, 'media.json')  # kept apart: episode lists are long


def load_state():
    state = {"seen": {}, "unread": []}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state = loaded
        except (json.JSONDecodeError, OSError):
            pass

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


def save_state(state):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


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
    starts empty."""
    media = {'sources': {}}
    try:
        with open(MEDIA_FILE) as f:
            loaded = json.load(f)
    except (OSError, ValueError):
        return media
    if isinstance(loaded, dict) and isinstance(loaded.get('sources'), dict):
        for url, entry in loaded['sources'].items():
            if isinstance(url, str) and isinstance(entry, dict):
                media['sources'][url] = _clean_media_entry(entry)
    return media


def save_media(media):
    tmp = MEDIA_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(media, f)
    os.replace(tmp, MEDIA_FILE)
