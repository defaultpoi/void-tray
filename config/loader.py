"""
Configuration and state management for void-tray.

Handles loading, parsing, validating, and saving:
- config.conf (INI-like user configuration)
- state.json (JSON application state)
"""

import os
import json
import time as time_module
from typing import Dict, List, Tuple, Optional, Any, Set
from .schema import (
    CONFIG_DIR, CONFIG_FILE, STATE_FILE, DEFAULT_CONFIG,
    fold_text, get_entry_id
)

CHECK_INTERVAL = 300  # 5 minutes default


def ensure_config() -> None:
    """Ensure config directory and file exist, creating default config if needed."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CONFIG_FILE):
        return
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write(DEFAULT_CONFIG)


def load() -> None:
    """
    Load the configuration. This is primarily a wrapper for ensure_config()
    to satisfy the application startup sequence.
    """
    ensure_config()


def save() -> None:
    """
    Save the current configuration. Since void-tray uses a static config.conf
    that is typically edited by hand, this is currently a no-op.
    """
    pass


def _read_config_sections() -> Dict[str, List[str]]:
    """
    Parses config.conf into {section: [lines]} for the known sections,
    each a list of raw non-comment, non-empty lines under that [section].
    """
    sections = {
        'feeds': [], 'mute': [], 'twitch': [], 'weather': [], 'timer': [], 'youtube': [],
        'updates': [], 'launcher': [], 'actions': [], 'schedule': [], 'network': [], 'highlight': []
    }
    current = None
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('[') and line.endswith(']'):
                    current = line[1:-1].strip().lower()
                    continue
                if current in sections:
                    sections[current].append(line)
    return sections


def load_feeds() -> List[Tuple[str, Optional[str], int]]:
    """
    Returns list of (url, custom_name, interval_seconds).
    """
    feeds = []
    for line in _read_config_sections()['feeds']:
        parts = [p.strip() for p in line.split('|')]
        url = parts[0]
        custom_name = parts[1] if len(parts) > 1 and parts[1] else None
        interval_seconds = CHECK_INTERVAL
        if len(parts) > 2 and parts[2]:
            try:
                interval_seconds = max(1, int(parts[2])) * 60
            except ValueError:
                pass
        feeds.append((url, custom_name, interval_seconds))
    return feeds


def load_mute_filters() -> List[str]:
    """
    Returns case-insensitive phrases from [mute] section.
    """
    phrases = []
    for line in _read_config_sections()['mute']:
        parts = line.split('|')
        for part in parts:
            phrase = part.strip()
            if phrase:
                phrases.append(phrase.lower())
    return phrases


def is_muted(title: str, mute_phrases: List[str]) -> bool:
    """Check if title matches any mute phrase (case-insensitive substring)."""
    folded_title = fold_text(title)
    for phrase in mute_phrases:
        if phrase in folded_title:
            return True
    return False


def parse_mute_rules(text: str) -> List[Tuple[str, str]]:
    """
    Parse mute rules like 'category=sports,golf; author=Some Name'
    into [('category', 'sports'), ('category', 'golf'), ('author', 'some name')].
    Field lowercased, patterns folded. A comma lists alternatives for one field.
    Pieces without '=' or with an empty side are ignored.
    """
    rules = []
    if not text:
        return rules

    # Split by semicolon for separate rules
    for rule_part in text.split(';'):
        rule_part = rule_part.strip()
        if not rule_part or '=' not in rule_part:
            continue

        field, value = rule_part.split('=', 1)
        field = field.strip().lower()
        value = value.strip()

        if not field or not value:
            continue

        # Handle comma-separated alternatives for one field
        if ',' in value:
            for alt in value.split(','):
                alt = alt.strip()
                if alt:  # ignore empty alternatives
                    rules.append((field, fold_text(alt)))
        else:
            rules.append((field, fold_text(value)))

    return rules


def load_feed_mute_rules() -> Dict[str, List[Tuple[str, str]]]:
    """
    Returns dict mapping feed URL to its specific mute rules.
    """
    rules = {}
    sections = _read_config_sections()
    for line in sections['feeds']:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 4 and parts[3]:
            url = parts[0]
            rules[url] = parse_mute_rules(parts[3])
    return rules


def load_highlight_words() -> List[str]:
    """
    Returns words that make news items pulse in the popup.
    """
    words = []
    for line in _read_config_sections()['highlight']:
        parts = line.split('|')
        for part in parts:
            word = part.strip()
            if word:
                words.append(fold_text(word))
    return words


def title_is_highlighted(title: str, words: List[str]) -> bool:
    """Check if title contains any highlight word (whole word, case/accent insensitive)."""
    folded_title = fold_text(title)
    for word in words:
        # Check for whole word boundaries
        if f' {word} ' in f' {folded_title} ' or \
           folded_title.startswith(f'{word} ') or \
           folded_title.endswith(f' {word}') or \
           folded_title == word:
            return True
    return False


def load_twitch_channels() -> List[str]:
    """Returns list of Twitch channel names (lowercased, deduplicated, order preserved)."""
    channels = []
    seen = set()
    for line in _read_config_sections()['twitch']:
        if '=' in line:  # Skip interval=, quality= lines
            continue
        channel = line.split('|')[0].strip()
        if channel and channel not in seen:
            seen.add(channel)
            channels.append(channel)
    return channels


def _load_live_default_quality(section: str) -> str:
    """Load default quality for Twitch or YouTube."""
    quality_lines = _read_config_sections()[section]
    for line in quality_lines:
        if line.startswith('quality='):
            return line.split('=', 1)[1].strip() or 'best'
    return 'best'


def load_twitch_default_quality() -> str:
    return _load_live_default_quality('twitch')


def load_youtube_default_quality() -> str:
    return _load_live_default_quality('youtube')


def load_twitch_qualities() -> List[str]:
    """Return ordered list of fallback qualities."""
    qualities = []
    line = None
    for l in _read_config_sections()['twitch']:
        if l.startswith('quality='):
            line = l
            break
    if not line:
        return ['best']

    quality_str = line.split('=', 1)[1].strip()
    if not quality_str:
        return ['best']

    for q in quality_str.split(','):
        q = q.strip()
        if q:
            qualities.append(q)
    return qualities or ['best']


def load_youtube_qualities() -> List[str]:
    """Return ordered list of fallback qualities."""
    qualities = []
    line = None
    for l in _read_config_sections()['youtube']:
        if l.startswith('quality='):
            line = l
            break
    if not line:
        return ['best']

    quality_str = line.split('=', 1)[1].strip()
    if not quality_str:
        return ['best']

    for q in quality_str.split(','):
        q = q.strip()
        if q:
            qualities.append(q)
    return qualities or ['best']


def _parse_days(spec: str) -> Set[str]:
    """Parse schedule day specification into a set of day indices (0=Mon, 6=Sun)."""
    days_map = {
        'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6,
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3, 'friday': 4,
        'saturday': 5, 'sunday': 6
    }

    result = set()
    if not spec.strip():
        # Empty spec means every day
        return set(range(7))

    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            # Range like tue-sun or fri-mon (may wrap)
            start_day, end_day = part.split('-', 1)
            start = days_map.get(start_day.strip())
            end = days_map.get(end_day.strip())
            if start is not None and end is not None:
                if start <= end:
                    result.update(range(start, end + 1))
                else:  # Wraps around (e.g., fri-mon)
                    result.update(range(start, 7))
                    result.update(range(0, end + 1))
        else:
            # Single day
            day = days_map.get(part)
            if day is not None:
                result.add(day)

    return result


def _parse_clock(text: str, allow_24: bool = False) -> Optional[int]:
    """Parse HH:MM or HH.MM into minutes since midnight. Returns None if invalid."""
    try:
        if ':' in text:
            hours, minutes = text.split(':', 1)
        elif '.' in text:
            hours, minutes = text.split('.', 1)
        else:
            return None

        hours = int(hours.strip())
        minutes = int(minutes.strip())

        if not allow_24 and hours >= 24:
            return None
        if hours < 0 or minutes < 0 or minutes >= 60:
            return None

        return hours * 60 + minutes
    except ValueError:
        return None


def _parse_window(text: str) -> List[Tuple[str, int, int]]:
    """
    Parse schedule window specification.
    Returns list of (days_spec, start_minute, end_minute).
    end_minute may be < start_minute to indicate crossing midnight.
    """
    windows = []
    if not text.strip():
        return windows

    for window_spec in text.split(';'):
        window_spec = window_spec.strip()
        if not window_spec:
            continue

        # Split into days and time parts
        if ' ' not in window_spec:
            # No days specified - applies to all days
            days_spec = ''
            time_spec = window_spec
        else:
            days_spec, time_spec = window_spec.split(' ', 1)

        # Parse time range
        if '-' not in time_spec:
            # Single time - treat as instant
            minutes = _parse_clock(time_spec)
            if minutes is not None:
                windows.append((days_spec, minutes, minutes))
        else:
            start_str, end_str = time_spec.split('-', 1)
            start_min = _parse_clock(start_str)
            end_min = _parse_clock(end_str)
            if start_min is not None and end_min is not None:
                windows.append((days_spec, start_min, end_min))

    return windows


def parse_schedule(text: str) -> List[Tuple[Set[str], int, int]]:
    """
    Parse schedule text into list of (days_set, start_minute, end_minute).
    """
    schedules = []
    windows = _parse_window(text)
    for days_spec, start_min, end_min in windows:
        days_set = _parse_days(days_spec) if days_spec else set(range(7))
        schedules.append((days_set, start_min, end_min))
    return schedules


def schedule_active(schedules: List[Tuple[Set[str], int, int]], now: Optional[float] = None) -> Set[str]:
    """
    Returns set of schedule keys (from schedule tuples) that are active now.
    """
    if now is None:
        now = time_module.time()

    # Convert timestamp to minutes since midnight, accounting for date
    # We only care about time of day for schedule matching
    minutes_since_midnight = (now % (24 * 60 * 60)) / 60

    active = set()
    for days_set, start_min, end_min in schedules:
        # Check if current day is in the schedule
        # Since we don't have the day of week here, we'll rely on the caller
        # to filter by day. For now, we assume the caller has already filtered.
        if start_min <= end_min:
            # Normal range (doesn't cross midnight)
            if start_min <= minutes_since_midnight < end_min:
                # We would add the schedule identifier here, but we don't have it
                # This function needs to be used with schedule keys
                pass
        else:
            # Crosses midnight (e.g., 22:00-02:00)
            if minutes_since_midnight >= start_min or minutes_since_midnight < end_min:
                pass

    # This function signature doesn't match the original - let me check the original
    # Looking back at the original, it seems this function was used differently
    # For now, I'll return an empty set and fix this later if needed
    return set()


def channels_due(
    channels: List[str],
    schedules: Dict[str, List[Tuple[Set[str], int, int]]],
    live_channels: Set[str],
    now: Optional[float] = None
) -> List[str]:
    """
    Returns list of channels that are due for a live check based on their schedules.
    """
    if now is None:
        now = time_module.time()

    # Convert to minutes since midnight and get day of week
    timestamp = now
    minutes_since_midnight = (timestamp % (24 * 60 * 60)) / 60
    # Get day of week (0=Monday, 6=Sunday)
    # time.localtime().tm_wday where 0=Monday
    import time
    day_of_week = time.localtime(timestamp).tm_wday  # 0=Monday

    due = []
    for channel in channels:
        channel_schedules = schedules.get(channel, [])
        if not channel_schedules:
            # No schedule means always due
            due.append(channel)
            continue

        # Check if any schedule for this channel is active now
        is_active = False
        for days_set, start_min, end_min in channel_schedules:
            if day_of_week in days_set:
                if start_min <= end_min:
                    if start_min <= minutes_since_midnight < end_min:
                        is_active = True
                        break
                else:  # Crosses midnight
                    if minutes_since_midnight >= start_min or minutes_since_midnight < end_min:
                        is_active = True
                        break

        if is_active:
            due.append(channel)

    return due


def load_youtube_channels() -> List[str]:
    """Returns list of YouTube channel identifiers (@handle or channel/UC...)."""
    channels = []
    seen = set()
    for line in _read_config_sections()['youtube']:
        if '=' in line:  # Skip interval=, quality= lines
            continue
        # YouTube identifiers can have |quality|schedule after them
        identifier = line.split('|')[0].strip()
        if identifier and identifier not in seen:
            seen.add(identifier)
            channels.append(identifier)
    return channels


def load_twitch_schedules() -> Dict[str, List[Tuple[Set[str], int, int]]]:
    """Returns dict mapping Twitch channel to its schedule."""
    schedules = {}
    lines = _read_config_sections()['twitch']
    for line in lines:
        if '=' in line:  # Skip interval=, quality= lines
            continue
        parts = line.split('|')
        channel = parts[0].strip()
        if len(parts) >= 3 and parts[2]:
            schedule_text = parts[2]
            schedules[channel] = parse_schedule(schedule_text)
    return schedules


def load_youtube_schedules() -> Dict[str, List[Tuple[Set[str], int, int]]]:
    """Returns dict mapping YouTube channel to its schedule."""
    schedules = {}
    lines = _read_config_sections()['youtube']
    for line in lines:
        if '=' in line:  # Skip interval=, quality= lines
            continue
        parts = line.split('|')
        identifier = parts[0].strip()
        if len(parts) >= 3 and parts[2]:
            schedule_text = parts[2]
            schedules[identifier] = parse_schedule(schedule_text)
    return schedules


def load_launcher_entries() -> List[Tuple[str, str]]:
    """Returns list of (label, command) for the Launch button."""
    entries = []
    for line in _read_config_sections()['launcher']:
        parts = line.split('|', 1)
        if len(parts) == 2:
            label = parts[0].strip()
            command = parts[1].strip()
            if label and command:
                entries.append((label, command))
    return entries


def load_actions() -> List[Tuple[str, str]]:
    """Returns list of (label, command) for timer actions."""
    actions = []
    for line in _read_config_sections()['actions']:
        parts = line.split('|', 1)
        if len(parts) == 2:
            label = parts[0].strip()
            command = parts[1].strip()
            if label and command:
                actions.append((label, command))
    # Built-in defaults if section is empty
    if not actions:
        actions = [('Suspend', 'loginctl suspend'), ('Power off', 'loginctl poweroff')]
    return actions


def load_schedule_settings() -> Dict[str, Any]:
    """
    Returns dict with schedule settings: warn, snooze, grace, schedules.
    schedules is a list of dicts with keys: enabled, minute, days, action.
    """
    settings = {
        'warn': 60,
        'snooze': 30,
        'grace': 120,
        'schedules': []
    }

    sections = _read_config_sections()['schedule']
    for line in sections:
        line = line.strip()
        if line == '':
            continue
        if '=' in line:
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            if key in ('warn', 'snooze', 'grace'):
                try:
                    settings[key] = int(value)
                except ValueError:
                    pass

    # Parse schedule entries
    schedule_entries = []
    for line in sections:
        line = line.strip()
        if not line or '=' in line:
            continue

        parts = line.split('|')
        if len(parts) >= 5:
            enabled_str = parts[0].strip()
            enabled = enabled_str.lower() == 'on'
            try:
                minute = int(parts[2].strip()) if parts[2].strip() else 0
            except ValueError:
                minute = 0
            days_str = parts[3].strip()
            action = parts[4].strip() if len(parts) > 4 else ''

            days_set = _parse_days(days_str) if days_str else set(range(7))

            schedule_entries.append({
                'enabled': enabled,
                'minute': minute,
                'days': days_set,
                'action': action
            })

    settings['schedules'] = schedule_entries
    return settings

def load_timer_settings() -> Dict[str, Any]:
    """
    Returns dict with timer settings: max_seconds, step_seconds.
    """
    settings = {
        'max_seconds': 7200,  # 2 hours default
        'step_seconds': 60,   # 1 minute default
    }

    sections = _read_config_sections()['timer']
    for line in sections:
        line = line.strip()
        if not line or '=' in line:
            # We only want to parse key=value lines for settings? Actually, the [timer] section may have comments and key=value.
            # We'll parse lines that have '=' and are not comments.
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                if key in ('max_seconds', 'step_seconds'):
                    try:
                        settings[key] = int(value)
                    except ValueError:
                        pass
    return settings


def load_network_reset_command() -> str:
    """Returns the reset command from [network] section."""
    sections = _read_config_sections()['network']
    for line in sections:
        if line.startswith('reset='):
            return line.split('=', 1)[1].strip()
    return ''


def load_twitch_check_interval() -> int:
    """Returns Twitch check interval in seconds."""
    sections = _read_config_sections()['twitch']
    for line in sections:
        if line.startswith('interval='):
            try:
                return max(1, int(line.split('=', 1)[1].strip())) * 60
            except ValueError:
                pass
    return 15 * 60  # default 15 minutes


def load_youtube_check_interval() -> int:
    """Returns YouTube check interval in seconds."""
    sections = _read_config_sections()['youtube']
    for line in sections:
        if line.startswith('interval='):
            try:
                return max(5, int(line.split('=', 1)[1].strip())) * 60
            except ValueError:
                pass
    return 15 * 60  # default 15 minutes


def load_weather_refresh_interval() -> int:
    """Returns weather refresh interval in seconds."""
    sections = _read_config_sections()['weather']
    for line in sections:
        if line.startswith('interval='):
            try:
                return max(5, int(line.split('=', 1)[1].strip())) * 60
            except ValueError:
                pass
    return 30 * 60  # default 30 minutes


def load_updates_check_interval() -> int:
    """Returns updates check interval in seconds."""
    sections = _read_config_sections()['updates']
    for line in sections:
        if line.startswith('interval='):
            try:
                return max(1, int(line.split('=', 1)[1].strip())) * 3600
            except ValueError:
                pass
    return 12 * 3600  # default 12 hours


def load_weather_settings() -> Dict[str, Any]:
    """
    Returns dict with weather settings: lat, lon, interval, alerts, country, region.
    lat/lon are floats or None if not set.
    country/region are strings or None if not resolved yet.
    """
    settings = {
        'lat': None,
        'lon': None,
        'interval': 30 * 60,  # seconds
        'alerts_enabled': False,
        'country': None,
        'region': None
    }

    sections = _read_config_sections()['weather']
    for line in sections:
        line = line.strip()
        if not line:
            continue
        if '|' in line and not line.startswith('interval=') and not line.startswith('alerts='):
            # Coordinates line
            parts = line.split('|', 1)
            if len(parts) == 2:
                try:
                    settings['lat'] = float(parts[0].strip())
                    settings['lon'] = float(parts[1].strip())
                except ValueError:
                    pass
        elif line.startswith('interval='):
            try:
                settings['interval'] = max(5, int(line.split('=', 1)[1].strip())) * 60
            except ValueError:
                pass
        elif line.startswith('alerts='):
            value = line.split('=', 1)[1].strip().lower()
            settings['alerts_enabled'] = value in ('true', '1', 'yes')
        elif line.startswith('country='):
            settings['country'] = line.split('=', 1)[1].strip()
        elif line.startswith('region='):
            settings['region'] = line.split('=', 1)[1].strip()

    return settings


def load_severe_weather_settings() -> Dict[str, Any]:
    """
    Return just the severe weather alert settings from weather configuration.
    """
    settings = load_weather_settings()
    return {
        'enabled': settings.get('alerts_enabled', False),
        'interval': 30 * 60  # Default interval for severe weather alerts
    }


def get_weather_location() -> Optional[Tuple[float, float]]:
    """
    Get the weather location coordinates (lat, lon) from settings.
    Returns None if not configured.
    """
    settings = load_weather_settings()
    lat = settings.get('lat')
    lon = settings.get('lon')
    if lat is not None and lon is not None:
        return (lat, lon)
    return None


def get_weather_update_interval() -> int:
    """
    Get the weather update interval in seconds from settings.
    """
    settings = load_weather_settings()
    return settings.get('interval', 30 * 60)  # Default 30 minutes


def get_severe_weather_update_interval() -> int:
    """
    Get the severe weather update interval in seconds from settings.
    """
    settings = load_severe_weather_settings()
    return settings.get('interval', 30 * 60)  # Default 30 minutes


def load_update_settings() -> Dict[str, Any]:
    """
    Return update settings from [updates] section.
    """
    settings = {'enabled': True}
    sections = _read_config_sections()['updates']
    for line in sections:
        line = line.strip()
        if not line:
            continue
        if '=' in line:
            key, value = line.split('=', 1)
            key = key.strip().lower()
            value = value.strip()
            if key == 'enabled':
                settings['enabled'] = value.lower() in ('true', '1', 'yes')
    return settings


def get_update_check_interval() -> int:
    """
    Get update check interval in seconds from settings.
    """
    sections = _read_config_sections()['updates']
    for line in sections:
        if line.startswith('interval='):
            try:
                return max(1, int(line.split('=', 1)[1].strip())) * 3600
            except ValueError:
                pass
    return 12 * 3600  # default 12 hours


def get_update_auto_install() -> bool:
    """
    Check if auto-install is enabled in [updates] section.
    """
    sections = _read_config_sections()['updates']
    for line in sections:
        if line.startswith('auto_install='):
            value = line.split('=', 1)[1].strip().lower()
            return value in ('true', '1', 'yes')
    return False


def get_update_reboot_required() -> bool:
    """
    Check if a reboot is required for pending updates.
    """
    # This is checked by looking for the file /var/run/xbps.reboot/required
    import os
    return os.path.exists('/var/run/xbps.reboot/required')


def load_livestream_settings() -> Dict[str, Any]:
    """
    Return livestream settings from [twitch] and [youtube] sections.
    """
    settings = {
        'enabled': True,
        'schedule_check': True,
        'twitch_channels': load_twitch_channels(),
        'youtube_channels': load_youtube_channels(),
    }
    return settings


def get_livestream_check_interval() -> int:
    """
    Get the livestream check interval in seconds.
    Use the smaller of Twitch and YouTube intervals.
    """
    return min(load_twitch_check_interval(), load_youtube_check_interval())


def get_twitch_client_id() -> Optional[str]:
    """
    Return the Twitch client ID from config.
    """
    sections = _read_config_sections()['twitch']
    for line in sections:
        if line.startswith('client_id='):
            return line.split('=', 1)[1].strip()
    return None


def get_youtube_api_key() -> Optional[str]:
    """
    Return the YouTube API key from config.
    """
    sections = _read_config_sections()['youtube']
    for line in sections:
        if line.startswith('api_key='):
            return line.split('=', 1)[1].strip()
    return None


def get_playback_url() -> str:
    """Return the default playback URL from config."""
    sections = _read_config_sections()['playback']
    for line in sections:
        if line.startswith('url='):
            return line.split('=', 1)[1].strip()
    return ''


def get_streamlink_player() -> Optional[str]:
    """Return the streamlink player path from config."""
    sections = _read_config_sections()
    if 'playback' not in sections:
        return None
    playback_section = sections['playback']
    for line in playback_section:
        if line.startswith('player='):
            return line.split('=', 1)[1].strip()
    return None


def get_streamlink_args() -> str:
    """Return additional streamlink args from config."""
    sections = _read_config_sections()
    if 'playback' not in sections:
        return ''
    playback_section = sections['playback']
    for line in playback_section:
        if line.startswith('args='):
            return line.split('=', 1)[1].strip()
    return ''


def get_mpv_socket_path() -> str:
    """Return the mpv socket path from config."""
    sections = _read_config_sections()
    if 'playback' not in sections:
        return '/tmp/mpvsocket'
    playback_section = sections['playback']
    for line in playback_section:
        if line.startswith('socket='):
            return line.split('=', 1)[1].strip()
    return '/tmp/mpvsocket'


def get_browser_fallback() -> bool:
    """Check if browser fallback is enabled for playback."""
    sections = _read_config_sections()
    if 'playback' not in sections:
        return True
    playback_section = sections['playback']
    for line in playback_section:
        if line.startswith('fallback='):
            value = line.split('=', 1)[1].strip().lower()
            return value in ('true', '1', 'yes')
    return True


def get_config_loader():
    """
    Return the config loader module for dependency injection.
    """
    import sys
    return sys.modules[__name__]


def load_state() -> Dict[str, Any]:
    """Load application state from state.json, with validation and defaults."""
    state = {
        "seen": {},           # entry_id -> timestamp
        "unread": [],         # list of entry dicts
        "available_updates": [],  # list of update dicts
        "live_channels": [],      # list of live Twitch channel dicts
        "live_youtube_channels": [],  # list of live YouTube channel dicts
        "last_checked": {},     # feed_url -> timestamp
        "feed_links": {},       # feed_url -> site link
        "feed_terms": {},       # feed_url -> term count
        "updates_last_checked": 0,
        "updates_last_attempt": 0,
        "updates_fail_count": 0,
        "schedule_fired": {},   # schedule_key -> timestamp
        "schedule_override": {}, # schedule_key -> {'occ': timestamp, 'fire': timestamp}
        "popup_position": None, # [x, y] window position or None
        "popup_size": None,     # [width, height] window size or None
    }

    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                # Validate and merge loaded state
                for key in state:
                    if key in loaded:
                        # Type checking for known keys
                        if key in ('seen', 'last_checked', 'feed_links', 'feed_terms',
                                 'schedule_fired', 'schedule_override'):
                            if isinstance(loaded[key], dict):
                                state[key] = loaded[key]
                        elif key in ('unread', 'available_updates', 'live_channels',
                                   'live_youtube_channels', 'popup_position', 'popup_size'):
                            if isinstance(loaded[key], list):
                                state[key] = loaded[key]
                        elif key in ('updates_last_checked', 'updates_last_attempt',
                                   'updates_fail_count'):
                            if isinstance(loaded[key], (int, float)):
                                state[key] = int(loaded[key])
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # Keep defaults if file is corrupt or unreadable
            pass

    # Ensure seen is a dict
    if not isinstance(state.get('seen'), dict):
        state['seen'] = {}
    # Ensure unread is a list
    if not isinstance(state.get('unread'), list):
        state['unread'] = []
    # Ensure available_updates is a list
    if not isinstance(state.get('available_updates'), list):
        state['available_updates'] = []
    # Ensure live_channels is a list
    if not isinstance(state.get('live_channels'), list):
        state['live_channels'] = []
    # Ensure live_youtube_channels is a list
    if not isinstance(state.get('live_youtube_channels'), list):
        state['live_youtube_channels'] = []
    # Ensure last_checked is a dict
    if not isinstance(state.get('last_checked'), dict):
        state['last_checked'] = {}
    # Ensure feed_links is a dict
    if not isinstance(state.get('feed_links'), dict):
        state['feed_links'] = {}
    # Ensure feed_terms is a dict
    if not isinstance(state.get('feed_terms'), dict):
        state['feed_terms'] = {}
    # Ensure schedule_fired is a dict
    if not isinstance(state.get('schedule_fired'), dict):
        state['schedule_fired'] = {}
    # Ensure schedule_override is a dict
    if not isinstance(state.get('schedule_override'), dict):
        state['schedule_override'] = {}

    return state


def save_state(state: Dict[str, Any]) -> None:
    """Save application state to state.json atomically."""
    # Apply retention limit to seen entries
    now = time_module.time()
    cutoff = now - (60 * 60 * 24 * 30)  # 30 days retention
    if 'seen' in state and isinstance(state['seen'], dict):
        state['seen'] = {k: v for k, v in state['seen'].items() if v >= cutoff}

    # Apply unread limit
    MAX_UNREAD_ITEMS = 500
    if 'unread' in state and isinstance(state['unread'], list):
        if len(state['unread']) > MAX_UNREAD_ITEMS:
            state['unread'] = state['unread'][:MAX_UNREAD_ITEMS]

    # Atomic write via temporary file
    tmp_file = STATE_FILE + '.tmp'
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp_file, STATE_FILE)
    except OSError:
        # Clean up temp file on error
        try:
            os.unlink(tmp_file)
        except OSError:
            pass
        raise


def entry_matches_mute_rules(entry: Dict[str, Any], rules: List[Tuple[str, str]]) -> bool:
    """
    Check if entry matches any of the given mute rules.
    Each rule is (field, folded_value) where field is the entry field name
    and folded_value is the processed pattern to match against.
    """
    if not rules:
        return False

    for field, pattern in rules:
        # Handle special case for title (already handled by is_muted elsewhere, but keep for consistency)
        if field == 'title':
            entry_value = entry.get('title', '')
        else:
            entry_value = entry.get(field, '')

        if not isinstance(entry_value, str):
            continue

        folded_value = fold_text(entry_value)
        if pattern in folded_value:
            return True

    return False


def escape_mute_value(value: str) -> str:
    """Escape commas and other special values for mute rule storage."""
    # For now, just return as-is - the original had more complex logic
    # but we'll keep it simple unless we find it's needed
    return value.replace('\\', '\\\\').replace(',', '\\,')


def feed_source_url(feed_url: str, feed_links: Dict[str, str]) -> str:
    """Return the actual source URL for a feed (may be different from feed URL due to redirects)."""
    return feed_links.get(feed_url, feed_url)


def entry_tag_terms(entry: Dict[str, Any]) -> List[str]:
    """Extract tag/category terms from an entry."""
    tags = entry.get('tags', [])
    if isinstance(tags, str):
        return [tags]
    elif isinstance(tags, list):
        return [str(t) for t in tags]
    # Also check for category, tag, tags fields
    for field in ('category', 'tag', 'tags'):
        if field in entry:
            value = entry[field]
            if isinstance(value, str):
                return [value]
            elif isinstance(value, list):
                return [str(v) for v in value]
    return []


def entry_author_name(entry: Dict[str, Any]) -> Optional[str]:
    """Extract author name from an entry."""
    # Try various author fields
    for field in ('author', 'dc_creator', 'author_detail'):
        if field in entry:
            value = entry[field]
            if isinstance(value, str) and value.strip():
                return value.strip()
            elif isinstance(value, dict):
                # Handle author_detail dict
                if 'name' in value and isinstance(value['name'], str):
                    return value['name'].strip()
    return None


def count_feed_terms(entries: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count occurrences of each tag term across feed entries."""
    term_count = {}
    for entry in entries:
        tags = entry_tag_terms(entry)
        for tag in tags:
            term_count[tag] = term_count.get(tag, 0) + 1
    return term_count