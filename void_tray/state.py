"""state.json: loading with validation and atomic saving."""

import json
import os

from .constants import CONFIG_DIR


STATE_FILE = os.path.join(CONFIG_DIR, 'state.json')


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

    return state


def save_state(state):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)
