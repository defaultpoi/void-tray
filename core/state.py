"""
Thread-safe application state management for void-tray.

Provides atomic access to application state with proper locking,
persistence to disk, and validation.
"""

import threading
import time as time_module
from typing import Dict, List, Tuple, Any, Optional, Set
from ..config.loader import (
    load_state, save_state, get_entry_id, entry_matches_mute_rules,
    fold_text, is_muted, load_feeds, load_mute_filters, load_highlight_words,
    load_feed_mute_rules, load_twitch_channels, load_youtube_channels,
    load_twitch_schedules, load_youtube_schedules, schedule_active,
    channels_due, load_twitch_check_interval, load_youtube_check_interval,
    load_weather_refresh_interval, load_updates_check_interval,
    load_weather_settings, load_schedule_settings, load_network_reset_command,
    load_actions, load_launcher_entries,
    entry_tag_terms, entry_author_name, count_feed_terms,
    entry_matches_mute_rules as entry_matches_mute_rules_func,
    escape_mute_value, feed_source_url
)
from ..utils.logger import get_logger

logger = get_logger(__name__)


class StateManager:
    """
    Thread-safe state manager for void-tray application.

    Manages:
    - Feed state (seen entries, unread items)
    - Update state (available system updates)
    - Live stream state (Twitch/YouTube)
    - Weather state
    - Timer/schedule state
    - Network connectivity state
    """

    def __init__(self):
        self._lock = threading.RLock()  # Reentrant lock for state access
        self._state = load_state()
        self._last_save = time_module.time()
        self._save_delay = 5.0  # seconds to wait before saving after change

        # Operation locks to prevent overlapping checks (matching original behavior)
        self._check_lock = threading.Lock()      # guards RSS + update checks
        self._xbps_lock = threading.Lock()       # guards xbps operations
        self._twitch_lock = threading.Lock()     # guards Twitch checks
        self._youtube_lock = threading.Lock()    # guards YouTube checks
        self._weather_lock = threading.Lock()    # guards weather checks

        logger.debug("StateManager initialized")

    def load_from_disk(self) -> None:
        """Load state from disk."""
        with self._lock:
            self._state = load_state()
        logger.debug("State loaded from disk")

    def get_state(self) -> Dict[str, Any]:
        """Get a copy of the current state."""
        with self._lock:
            return dict(self._state)

    def update_state(self, updater: callable) -> None:
        """
        Update state using a modifier function.

        Args:
            updater: Function that takes current state dict and modifies it in-place
        """
        with self._lock:
            updater(self._state)
            self._maybe_save()

    def _maybe_save(self) -> None:
        """Save state to disk if enough time has passed since last save."""
        now = time_module.time()
        if now - self._last_save >= self._save_delay:
            self.save_to_disk()
            self._last_save = now

    def save_to_disk(self) -> None:
        """Save current state to disk immediately."""
        with self._lock:
            save_state(self._state)
        logger.debug("State saved to disk")

    # Convenience methods for specific state domains

    def get_feeds(self) -> List[Tuple[str, Optional[str], int]]:
        """Get feeds from config (not state)."""
        return load_feeds()

    def get_mute_phrases(self) -> List[str]:
        """Get mute phrases from config."""
        return load_mute_filters()

    def get_highlight_words(self) -> List[str]:
        """Get highlight words from config."""
        return load_highlight_words()

    def get_feed_mute_rules(self) -> Dict[str, List[Tuple[str, str]]]:
        """Get feed-specific mute rules from config."""
        return load_feed_mute_rules()

    def get_twitch_channels(self) -> List[str]:
        """Get Twitch channels from config."""
        return load_twitch_channels()

    def get_youtube_channels(self) -> List[str]:
        """Get YouTube channels from config."""
        return load_youtube_channels()

    def get_twitch_schedules(self) -> Dict[str, List[Tuple[Set[str], int, int]]]:
        """Get Twitch schedules from config."""
        return load_twitch_schedules()

    def get_youtube_schedules(self) -> Dict[str, List[Tuple[Set[str], int, int]]]:
        """Get YouTube schedules from config."""
        return load_youtube_schedules()

    def get_twitch_check_interval(self) -> int:
        """Get Twitch check interval in seconds."""
        return load_twitch_check_interval()

    def get_youtube_check_interval(self) -> int:
        """Get YouTube check interval in seconds."""
        return load_youtube_check_interval()

    def get_weather_refresh_interval(self) -> int:
        """Get weather refresh interval in seconds."""
        return load_weather_refresh_interval()

    def get_updates_check_interval(self) -> int:
        """Get updates check interval in seconds."""
        return load_updates_check_interval()

    def get_weather_settings(self) -> Dict[str, Any]:
        """Get weather settings from config."""
        return load_weather_settings()

    def get_schedule_settings(self) -> Dict[str, Any]:
        """Get schedule settings from config."""
        return load_schedule_settings()

    def get_network_reset_command(self) -> str:
        """Get network reset command from config."""
        return load_network_reset_command()

    def get_actions(self) -> List[Tuple[str, str]]:
        """Get timer actions from config."""
        return load_actions()

    def get_launcher_entries(self) -> List[Tuple[str, str]]:
        """Get launcher entries from config."""
        return load_launcher_entries()

    # State-specific getters and setters

    def get_seen_entries(self) -> Dict[str, float]:
        """Get seen entries map (entry_id -> timestamp)."""
        with self._lock:
            return dict(self._state.get('seen', {}))

    def get_unread_items(self) -> List[Dict[str, Any]]:
        """Get unread items list."""
        with self._lock:
            return list(self._state.get('unread', []))

    def get_available_updates(self) -> List[Dict[str, Any]]:
        """Get available updates list."""
        with self._lock:
            return list(self._state.get('available_updates', []))

    def get_live_channels(self) -> List[Dict[str, Any]]:
        """Get live Twitch channels list."""
        with self._lock:
            return list(self._state.get('live_channels', []))

    def get_live_youtube_channels(self) -> List[Dict[str, Any]]:
        """Get live YouTube channels list."""
        with self._lock:
            return list(self._state.get('live_youtube_channels', []))

    def get_last_checked(self) -> Dict[str, float]:
        """Get last checked timestamps for feeds."""
        with self._lock:
            return dict(self._state.get('last_checked', {}))

    def get_feed_links(self) -> Dict[str, str]:
        """Get feed links (source URLs) map."""
        with self._lock:
            return dict(self._state.get('feed_links', {}))

    def get_feed_terms(self) -> Dict[str, int]:
        """Get feed term counts map."""
        with self._lock:
            return dict(self._state.get('feed_terms', {}))

    def get_updates_timestamps(self) -> Tuple[float, float, int]:
        """Get (last_checked, last_attempt, fail_count) for updates."""
        with self._lock:
            state = self._state
            return (
                state.get('updates_last_checked', 0),
                state.get('updates_last_attempt', 0),
                state.get('updates_fail_count', 0)
            )

    def get_schedule_state(self) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
        """Get (schedule_fired, schedule_override) state."""
        with self._lock:
            state = self._state
            return (
                dict(state.get('schedule_fired', {})),
                dict(state.get('schedule_override', {}))
            )

    # Update methods for specific domains

    def update_seen_entries(self, entry_ids: Set[str], timestamp: Optional[float] = None) -> None:
        """Mark entry IDs as seen at given timestamp."""
        if timestamp is None:
            timestamp = time_module.time()

        def updater(state):
            seen = state.get('seen', {})
            for entry_id in entry_ids:
                seen[entry_id] = timestamp
            state['seen'] = seen

        self.update_state(updater)

    def add_unread_items(self, items: List[Dict[str, Any]]) -> None:
        """Add new unread items, respecting limits and deduplication."""
        def updater(state):
            unread = state.get('unread', [])
            seen_ids = {item.get('id') for item in unread if item.get('id')}

            # Filter out duplicates and items already seen
            new_items = []
            for item in items:
                item_id = item.get('id')
                if item_id and item_id not in seen_ids:
                    new_items.append(item)
                    seen_ids.add(item_id)

            # Prepend new items and apply limit
            if new_items:
                combined = new_items + unread
                state['unread'] = combined[:500]  # MAX_UNREAD_ITEMS

        self.update_state(updater)

    def set_available_updates(self, updates: List[Dict[str, Any]]) -> None:
        """Set the available updates list."""
        def updater(state):
            state['available_updates'] = list(updates)
        self.update_state(updater)

    def set_live_channels(self, channels: List[Dict[str, Any]]) -> None:
        """Set the live Twitch channels list."""
        def updater(state):
            state['live_channels'] = list(channels)
        self.update_state(updater)

    def set_live_youtube_channels(self, channels: List[Dict[str, Any]]) -> None:
        """Set the live YouTube channels list."""
        def updater(state):
            state['live_youtube_channels'] = list(channels)
        self.update_state(updater)

    def update_last_checked(self, feed_url: str, timestamp: Optional[float] = None) -> None:
        """Update last checked timestamp for a feed."""
        if timestamp is None:
            timestamp = time_module.time()

        def updater(state):
            last_checked = state.get('last_checked', {})
            last_checked[feed_url] = timestamp
            state['last_checked'] = last_checked
        self.update_state(updater)

    def update_feed_links(self, links: Dict[str, str]) -> None:
        """Update feed links (source URLs) map."""
        def updater(state):
            feed_links = state.get('feed_links', {})
            feed_links.update(links)
            state['feed_links'] = feed_links
        self.update_state(updater)

    def update_feed_terms(self, terms: Dict[str, int]) -> None:
        """Update feed term counts map."""
        def updater(state):
            feed_terms = state.get('feed_terms', {})
            feed_terms.update(terms)
            state['feed_terms'] = feed_terms
        self.update_state(updater)

    def update_updates_timestamps(self, last_checked: float, last_attempt: float, fail_count: int) -> None:
        """Update update check timestamps and failure count."""
        def updater(state):
            state['updates_last_checked'] = last_checked
            state['updates_last_attempt'] = last_attempt
            state['updates_fail_count'] = fail_count
        self.update_state(updater)

    def update_schedule_state(self, fired: Dict[str, float], override: Dict[str, Dict[str, float]]) -> None:
        """Update schedule firing and override state."""
        def updater(state):
            state['schedule_fired'] = dict(fired)
            state['schedule_override'] = dict(override)
        self.update_state(updater)

    # Operation locks (context managers)

    def check_lock(self):
        """Context manager for RSS/update check lock."""
        return self._check_lock

    def xbps_lock(self):
        """Context manager for xbps operations lock."""
        return self._xbps_lock

    def twitch_lock(self):
        """Context manager for Twitch check lock."""
        return self._twitch_lock

    def youtube_lock(self):
        """Context manager for YouTube check lock."""
        return self._youtube_lock

    def weather_lock(self):
        """Context manager for weather check lock."""
        return self._weather_lock

    # Cleanup

    def shutdown(self) -> None:
        """Save state and cleanup resources."""
        self.save_to_disk()
        logger.debug("StateManager shutdown")


# Global state manager instance (singleton pattern)
_state_manager: Optional[StateManager] = None


def get_state_manager() -> StateManager:
    """Get the global state manager instance."""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager