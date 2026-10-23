"""config.conf: creating it, reading every section, and the single writer."""

import os
import re
import shlex
import shutil

from .constants import CONFIG_DIR, CONFIG_FILE, DEFAULT_HIGHLIGHT_WORDS, LEGACY_CONFIG_DIR
from .log import _log
from .textutil import _fold_text
from .windows import parse_schedule


CHECK_INTERVAL = 600  # default per-feed interval (seconds) when none is set in config.conf


PENDING_CHECK_INTERVAL_SECONDS = 12 * 3600  # default: twice a day, for system-wide package updates


TWITCH_CHECK_INTERVAL_SECONDS = 900  # how often to poll Twitch live status


WEATHER_REFRESH_SECONDS = 1800  # default: 30 minutes (weather and alerts share this cycle)


def migrate_legacy_config():
    """Copies config.conf, state.json and notification.wav from the single-file
    version's directory the first time this app starts without a config of its
    own, so feeds, read state and sound carry over. The old directory is left
    untouched. Returns True when something was copied."""
    if os.path.exists(CONFIG_FILE) or not os.path.isdir(LEGACY_CONFIG_DIR):
        return False
    copied = False
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        for name in ('config.conf', 'state.json', 'notification.wav'):
            source = os.path.join(LEGACY_CONFIG_DIR, name)
            if os.path.isfile(source):
                shutil.copy2(source, os.path.join(CONFIG_DIR, name))
                copied = True
    except OSError as error:
        _log(f'could not copy the old configuration: {error}')
    return copied


def ensure_config():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CONFIG_FILE):
        return
    with open(CONFIG_FILE, 'w') as f:
        f.write(
            "[feeds]\n"
            "# Format: URL|custom display name|check interval in minutes|mute rules|read-later rules\n"
            "# All fields after the URL are optional but positional — leave a field empty\n"
            "# to skip it while still setting a later one, e.g. URL||5\n"
            "# Mute rules are 'field=value' separated by ;, matched against the item's\n"
            "# fields (category, author, title, summary, ...); a comma lists several\n"
            "# values (category=a,b,c); * and ? are wildcards, case and accents are\n"
            "# ignored. Matching items are marked read, never shown. Read-later rules (the\n"
            "# fifth field) have the same form; matching items go straight to the Read later list.\n"
            "# https://example.com/feed.xml\n"
            "# https://example.com/feed.xml|My Blog\n"
            "# https://example.com/feed.xml|My Blog|5\n"
            "# https://example.com/feed.xml|My Blog|5|category=sports; author=Some Name\n"
            "# https://example.com/feed.xml|My Blog|5||category=long reads\n"
            "\n"
            "[mute]\n"
            "# Items whose title contains any of these phrases (ignoring case and accents,\n"
            "# substring match) are auto-marked as read and never shown as unread.\n"
            "# One phrase per line, or several separated by | on the same line.\n"
            "# Example:\n"
            "# (P)|Fashion week|Another item\n"
            "\n"
            "[twitch]\n"
            "# Twitch channel login names to watch for live status, one per line:\n"
            "#   channel|display name (optional)|quality (optional)|schedule (optional)\n"
            "# Uses Twitch's own internal (unofficial) API — no account/app needed.\n"
            "# Clicking a live channel plays it (streamlink + mpv, one shared maximized window).\n"
            "# interval=<minutes> sets how often to check (default 15, minimum 1).\n"
            "# quality=<streamlink quality> for channels without their own, e.g.\n"
            "# quality=720p60,720p,best (fallbacks, first available wins; default best).\n"
            "# A schedule limits checking to when the channel is expected to stream\n"
            "# (system local time): [days] HH:MM[-HH:MM], several separated by ;\n"
            "# Days: mon..sun, ranges (tue-sun), lists (mon,wed), daily, weekdays,\n"
            "# weekends; no end time means until midnight. Examples:\n"
            "# The display name is what the popup row and the player window call the\n"
            "# channel (default: the channel name). Leave a field empty to skip it:\n"
            "# examplechannel\n"
            "# namedchannel|Some Name\n"
            "# weeklychannel|||wed 18:00-23:00\n"
            "# dailychannel|Daily Show|720p60|tue-sun 13:00-; sat 10:00-12:00\n"
            "\n"
            "[youtube]\n"
            "# Same format as [twitch]; identifiers are @handle or channel/UC... and\n"
            "# case-sensitive. interval=<minutes>: default 15, minimum 5; quality= as above.\n"
            "\n"
            "[updates]\n"
            "# interval=<hours> between package update scans (default 12, minimum 1).\n"
            "# installer= what Install all runs: auto (topgrade if it is installed, else\n"
            "# xbps-install; the default), topgrade or xbps. Scanning always uses\n"
            "# `xbps-install -Mn -u`, because topgrade cannot list pending updates.\n"
            "# steps= topgrade steps to run, e.g. system, flatpak (default: system, which\n"
            "# is xbps-install -Su xbps then -u). `all` runs every step topgrade finds;\n"
            "# a step that needs a sudo password fails instead of asking. Names are the\n"
            "# ones `topgrade --help` lists under --only: lowercase with underscores.\n"
            "# orphans=false hides the row that offers to remove orphaned packages (those\n"
            "# installed as dependencies that nothing needs any more, `xbps-query -O`).\n"
            "\n"
            "[media]\n"
            "# Podcasts, series and movies: the app checks these sources, keeps the next\n"
            "# unwatched episodes downloaded (with subtitles) and plays them in mpv.\n"
            "# path=<folder> downloads go to (default ~/videos)\n"
            "# interval=<minutes> between checks (default 60, minimum 10)\n"
            "# ahead=<n> unwatched episodes kept downloaded per source (default 2; 0 downloads\n"
            "# only when you click)\n"
            "# ytdlp=<arguments> added to every yt-dlp run, e.g. -S res:720 --cookies-from-browser firefox\n"
            "# One source per line: url|display name (optional)|order (optional)\n"
            "#   url: an archive.org collection or item (https://archive.org/details/...),\n"
            "#        a YouTube channel, playlist or video, or any podcast / video RSS or Atom feed.\n"
            "#        yt-dlp:<url> reads any other site yt-dlp supports, and a plugin in\n"
            "#        ~/.config/void-tray/media-plugins can add more (see the README)\n"
            "#   order: oldest (a series, from its first episode; the default for archive.org)\n"
            "#          or newest (a podcast, from the latest; the default for feeds)\n"
            "# https://archive.org/details/some-collection|Some Series\n"
            "# https://example.com/podcast.xml|Some Podcast|newest\n"
            "\n"
            "[highlight]\n"
            "# News items whose title or categories contain one of these words pulse in\n"
            "# the popup (one per line, or several separated by |; default: breaking).\n"
            "# Case and accents are ignored, whole words only.\n"
            "# breaking|urgent\n"
            "\n"
            "[network]\n"
            "# reset=<command>: shows a \"Reset network\" button on the Offline banner,\n"
            "# e.g. reset=~/.local/bin/reset-network.sh (runs only when you click it).\n"
            "\n"
            "[actions]\n"
            "# Commands the timer's Schedule mode can run, one per line: Label|command\n"
            "# (built-in defaults if this section is empty: Suspend and Power off).\n"
            "Suspend|loginctl suspend\n"
            "Power off|loginctl poweroff\n"
            "\n"
            "[schedule]\n"
            "# Recurring actions, managed from the popup's Timer > Schedule (or here),\n"
            "# one per line: on|off | HH:MM | days | action label from [actions]\n"
            "# days: daily, weekdays, weekends or e.g. mon,wed,fri. Examples:\n"
            "# on|00:30|daily|Suspend\n"
            "# off|07:00|weekdays|Lock screen\n"
            "# Shared settings: warn=<seconds of warning, 0 = none>, snooze=<minutes the\n"
            "# Snooze button postpones>, grace=<seconds: a run missed by more than this,\n"
            "# e.g. while the machine was asleep, is skipped instead of run late>.\n"
            "warn=60\n"
            "snooze=30\n"
            "grace=120\n"
            "\n"
            "[launcher]\n"
            "# Commands for the popup's Launch button, one per line: Label|command\n"
            "# (run through the shell, so ~, quotes and && work). Examples:\n"
            "# Files|thunar ~\n"
            "# Update system|xfce4-terminal -e \"sudo xbps-install -Su\"\n"
            "\n"
            "[weather]\n"
            "# Coordinates for the weather bar: lat|lon (one line, decimal degrees).\n"
            "# Uses Open-Meteo, no account/key needed. The weather bar stays hidden\n"
            "# until this line is set, e.g.:\n"
            "# <latitude>|<longitude>\n"
            "# interval=<minutes> between weather (and alert) refreshes (default 30, minimum 5).\n"
            "#\n"
            "# alerts=true enables MeteoAlarm severe-weather alerts (European\n"
            "# countries covered by MeteoAlarm) for the country/region matching the\n"
            "# coordinates above (reverse-geocoded automatically via OpenStreetMap).\n"
            "# When an alert is active, the matching weather value and the tray badge\n"
            "# pulse in the alert's color.\n"
            "# The app writes country=/region= back into this section once resolved\n"
            "# and never looks them up again; delete both lines to resolve afresh\n"
            "# (e.g. after changing the coordinates).\n"
            "alerts=false\n"
        )


def _read_config_sections():
    """Parses config.conf into {section: [lines]} for the known sections,
    each a list of raw non-comment, non-empty lines under that [section]."""
    sections = {'feeds': [], 'mute': [], 'twitch': [], 'weather': [], 'timer': [], 'youtube': [],
                'updates': [], 'launcher': [], 'actions': [], 'schedule': [], 'network': [], 'highlight': [],
                'media': []}
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


def load_feeds():
    """Returns list of (url, custom_name, interval_seconds)."""
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


def load_highlight_words():
    """Words (folded) from [highlight] -- one per line or '|'-separated -- that
    make a news item pulse; 'breaking' if the section has none."""
    words = []
    for line in _read_config_sections()['highlight']:
        for word in line.split('|'):
            word = _fold_text(word)
            if word:
                words.append(word)
    return tuple(words) or DEFAULT_HIGHLIGHT_WORDS


def load_mute_filters():
    """Returns the phrases folded to lowercase without accents; a title is muted
    if it contains any of them (see is_muted)."""
    phrases = []
    for line in _read_config_sections()['mute']:
        for phrase in line.split('|'):
            phrase = _fold_text(phrase)
            if phrase:
                phrases.append(phrase)
    return phrases


def _load_live_entries(section, fold_case):
    """Parses the channel lines of [twitch] / [youtube]:
    'channel|display name|quality|schedule', the last three optional (each may
    be left empty). Returns [(channel, name_or_None, quality_or_None,
    schedule_text_or_None)], de-duplicated case-insensitively (first line wins)
    in config order. Twitch logins are lowercased (fold_case); YouTube
    identifiers keep the casing as typed."""
    entries, seen = [], set()
    for line in _read_config_sections()[section]:
        if '=' in line:
            continue  # a setting (e.g. interval=10), not a channel
        parts = [p.strip() for p in line.split('|', 3)]
        channel = parts[0].lower() if fold_case else parts[0]
        if not channel or channel.lower() in seen:
            continue
        seen.add(channel.lower())
        name = parts[1] if len(parts) > 1 and parts[1] else None
        quality = parts[2] if len(parts) > 2 and parts[2] else None
        schedule = parts[3] if len(parts) > 3 and parts[3] else None
        entries.append((channel, name, quality, schedule))
    return entries


def _entry_schedules(entries):
    """{channel: windows} for the entries that have a valid schedule; an
    invalid one is reported (RSS_TRAY_DEBUG) and ignored, i.e. that channel is
    then checked all the time rather than never."""
    schedules = {}
    for channel, _name, _quality, text in entries:
        if not text:
            continue
        try:
            schedules[channel] = parse_schedule(text)
        except ValueError as e:
            _log(f'ignoring schedule for {channel!r}: {e}')
    return schedules


def load_twitch_channels():
    """Lowercase Twitch login names to watch, in config order."""
    return [e[0] for e in _load_live_entries('twitch', True)]


DEFAULT_STREAM_QUALITY = 'best'


def _load_live_default_quality(section):
    """The section's 'quality=<streamlink quality>' line: what channels
    without their own quality use (a comma-separated fallback list such as
    720p60,720p,best works); 'best' if there is none."""
    for line in _read_config_sections()[section]:
        key, sep, val = line.partition('=')
        if sep and key.strip().lower() == 'quality' and val.split('#', 1)[0].strip():
            return val.split('#', 1)[0].strip()
    return DEFAULT_STREAM_QUALITY


def load_twitch_default_quality():
    return _load_live_default_quality('twitch')


def load_youtube_default_quality():
    return _load_live_default_quality('youtube')


def load_twitch_qualities():
    """{channel: quality} for lines with a quality; callers default the rest
    to 'best'."""
    return {c: q for c, _n, q, _s in _load_live_entries('twitch', True) if q}


def load_twitch_names():
    """{channel: display name} for lines with one; the rest show the channel name."""
    return {c: n for c, n, _q, _s in _load_live_entries('twitch', True) if n}


def load_twitch_schedules():
    return _entry_schedules(_load_live_entries('twitch', True))


def load_youtube_channels():
    """YouTube channel identifiers to watch, as typed (case-sensitive), e.g.
    '@somehandle' or 'channel/UCxxxxxxxxxxxxxxxxxxxxxx'."""
    return [e[0] for e in _load_live_entries('youtube', False)]


def load_youtube_qualities():
    return {c: q for c, _n, q, _s in _load_live_entries('youtube', False) if q}


def load_youtube_names():
    return {c: n for c, n, _q, _s in _load_live_entries('youtube', False) if n}


def load_youtube_schedules():
    return _entry_schedules(_load_live_entries('youtube', False))


MIN_TWITCH_CHECK_MINUTES = 1


MIN_YOUTUBE_CHECK_MINUTES = 5  # each channel costs a full streamlink run


MIN_UPDATES_CHECK_HOURS = 1


MIN_WEATHER_REFRESH_MINUTES = 5


def load_launcher_entries():
    """[(label, command)] from the [launcher] section, config order. A line is
    'Label|command' (the command may itself contain '|'); a line without '|'
    is used as both label and command."""
    entries = []
    for line in _read_config_sections()['launcher']:
        label, sep, command = line.partition('|')
        label, command = label.strip(), command.strip()
        if not sep:
            command = label
        if command:
            entries.append((label or command, command))
    return entries


def _load_interval(section, default_seconds, minimum, unit_seconds):
    """Seconds from an 'interval=<n>' line in the given section, n counted in
    units of `unit_seconds`. Missing or unparsable -> default; values below
    `minimum` are raised to it (each check hits the network)."""
    for line in _read_config_sections()[section]:
        key, sep, val = line.partition('=')
        if sep and key.strip().lower() == 'interval':
            try:
                return max(minimum, int(val.split('#', 1)[0].strip())) * unit_seconds
            except ValueError:
                return default_seconds
    return default_seconds


def load_twitch_check_interval():
    return _load_interval('twitch', TWITCH_CHECK_INTERVAL_SECONDS, MIN_TWITCH_CHECK_MINUTES, 60)


def load_youtube_check_interval():
    return _load_interval('youtube', YOUTUBE_CHECK_INTERVAL_SECONDS, MIN_YOUTUBE_CHECK_MINUTES, 60)


def load_weather_refresh_interval():
    """Seconds between weather (and alert) refreshes: 'interval=<minutes>' in [weather]."""
    return _load_interval('weather', WEATHER_REFRESH_SECONDS, MIN_WEATHER_REFRESH_MINUTES, 60)


def load_updates_check_interval():
    """Seconds between package update scans: 'interval=<hours>' in [updates]."""
    return _load_interval('updates', PENDING_CHECK_INTERVAL_SECONDS, MIN_UPDATES_CHECK_HOURS, 3600)


UPDATES_INSTALLERS = ('auto', 'topgrade', 'xbps')
DEFAULT_TOPGRADE_STEPS = ('system',)
TOPGRADE_STEP_RE = re.compile(r'^[a-z0-9_]+$')


def load_updates_installer():
    """(installer, steps) from [updates]. `installer=` is what Install all runs:
    auto (topgrade when it is installed, else xbps-install; the default),
    topgrade or xbps. `steps=` lists the topgrade steps to run, separated by
    commas, '|' or spaces: names that are not plain lowercase identifiers are
    dropped, nothing usable gives ('system',), and 'all' gives None, meaning
    every step topgrade knows (no --only)."""
    installer, steps = 'auto', DEFAULT_TOPGRADE_STEPS
    for line in _read_config_sections()['updates']:
        key, sep, val = line.partition('=')
        if not sep:
            continue
        key, val = key.strip().lower(), val.split('#', 1)[0].strip().lower()
        if key == 'installer':
            installer = val if val in UPDATES_INSTALLERS else 'auto'
        elif key == 'steps':
            names = [n for n in re.split(r'[,|\s]+', val) if n]
            if 'all' in names:
                steps = None
            else:
                steps = tuple(dict.fromkeys(n for n in names if TOPGRADE_STEP_RE.match(n))) or DEFAULT_TOPGRADE_STEPS
    return installer, steps


def load_orphan_nudge():
    """[updates] orphans= switch for the orphaned-packages row; on unless it is
    0, false, no or off."""
    for line in _read_config_sections()['updates']:
        key, sep, val = line.partition('=')
        if sep and key.strip().lower() == 'orphans':
            return val.split('#', 1)[0].strip().lower() not in ('0', 'false', 'no', 'off')
    return True


DEFAULT_TIMER_MAX_MINUTES = 120


DEFAULT_TIMER_STEP_SECONDS = 60


# ---- timer "Schedule" mode: a recurring action (suspend, power off, ...) --------
DEFAULT_ACTIONS = [('Suspend', 'loginctl suspend'), ('Power off', 'loginctl poweroff')]


def load_network_reset_command():
    """The command behind the Offline banner's "Reset network" button
    ('reset=' in [network]); '' if none is configured (then there is no button)."""
    for line in _read_config_sections()['network']:
        key, sep, val = line.partition('=')
        if sep and key.strip().lower() == 'reset':
            return val.strip()
    return ''


def load_actions():
    """[(label, command)] from [actions] (same 'Label|command' lines as the
    launcher), or the built-in Suspend / Power off if the section is empty."""
    entries = []
    for line in _read_config_sections()['actions']:
        label, sep, command = line.partition('|')
        label, command = label.strip(), command.strip()
        if not sep:
            command = label
        if command:
            entries.append((label or command, command))
    return entries or list(DEFAULT_ACTIONS)


def load_timer_settings():
    """Returns {'max_seconds', 'step_seconds'} from config.conf's [timer]
    section: 'max=<minutes>' and 'step=<seconds>' key=value lines, in any
    order/combination. Missing or unparsable values fall back to the
    built-in defaults (120 minutes, 60 second steps)."""
    max_minutes = DEFAULT_TIMER_MAX_MINUTES
    step_seconds = DEFAULT_TIMER_STEP_SECONDS
    for line in _read_config_sections()['timer']:
        if '=' not in line:
            continue
        key, _, val = line.partition('=')
        key = key.strip().lower()
        val = val.split('#', 1)[0].strip()  # allow a trailing '#comment'
        if key == 'max':
            try:
                parsed = int(val)
                if parsed > 0:
                    max_minutes = parsed
            except ValueError:
                pass
        elif key == 'step':
            try:
                parsed = int(val)
                if parsed > 0:
                    step_seconds = parsed
            except ValueError:
                pass
    return {'max_seconds': max_minutes * 60, 'step_seconds': step_seconds}


def load_weather_settings():
    """Returns {'lat', 'lon', 'alerts_enabled', 'country', 'region'} from
    config.conf's [weather] section. lat/lon are None until a coordinates line
    is configured. The coordinates line has no '=' ('lat|lon'); everything else
    is a 'key=value' line. 'country' and 'region' are written back by the app
    itself (see _update_weather_region_cache) the first time alerts are
    enabled and the location has been resolved from the coordinates; they are
    never looked up again until the user deletes them."""
    lat = lon = None
    alerts_enabled = False
    country = None
    region = None
    for line in _read_config_sections()['weather']:
        if '=' in line:
            key, _, val = line.partition('=')
            key = key.strip().lower()
            val = val.strip()
            if key == 'alerts':
                alerts_enabled = val.lower() in ('1', 'true', 'yes', 'on')
            elif key == 'country':
                country = val or None
            elif key == 'region':
                region = val or None
        else:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 2:
                try:
                    lat, lon = float(parts[0]), float(parts[1])
                except ValueError:
                    pass
    return {
        'lat': lat, 'lon': lon, 'alerts_enabled': alerts_enabled,
        'country': country, 'region': region,
    }


def _edit_config_section(name, transform):
    """Rewrites config.conf in place: `transform` receives the lines of the
    [name] section (without the header; the section is created at the end of
    the file if it's missing) and returns the replacement lines. Every other
    line -- including comments and other sections -- is left untouched.
    Best-effort: failures are swallowed (callers just redo the work later)."""
    try:
        with open(CONFIG_FILE, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except OSError:
        lines = []

    def is_section(line, wanted=None):
        stripped = line.strip()
        if not (stripped.startswith('[') and stripped.endswith(']')):
            return False
        return wanted is None or stripped[1:-1].strip().lower() == wanted

    start = next((i for i, l in enumerate(lines) if is_section(l, name)), None)
    if start is None:
        if lines and not lines[-1].endswith('\n'):
            lines.append('\n')
        lines.append(f'[{name}]\n')
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if is_section(lines[i])), len(lines))
    lines[start + 1:end] = transform(lines[start + 1:end])

    try:
        tmp = CONFIG_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        os.replace(tmp, CONFIG_FILE)
    except OSError:
        pass


def _edit_weather_section(transform):
    _edit_config_section('weather', transform)


_RESOLVED_LOCATION_KEYS = ('country=', 'region=')


def _update_weather_region_cache(region, country):
    """Stores the reverse-geocoded country=/region= in [weather]; they are
    only ever looked up again after the user deletes them."""
    def transform(section):
        kept = [l for l in section if not l.strip().lower().startswith(_RESOLVED_LOCATION_KEYS)]
        return kept + [f'country={country}\n', f'region={region}\n']
    _edit_weather_section(transform)


def _disable_weather_alerts():
    """Writes alerts=false into [weather] (and drops the cached location),
    used when the coordinates resolve to a country MeteoAlarm doesn't cover."""
    def transform(section):
        kept = [l for l in section
                if not l.strip().lower().startswith(_RESOLVED_LOCATION_KEYS)
                and not l.strip().lower().startswith('alerts=')]
        return kept + ['alerts=false\n']
    _edit_weather_section(transform)


YOUTUBE_CHECK_INTERVAL_SECONDS = 900  # how often to poll YouTube live status


DEFAULT_MEDIA_PATH = '~/videos'
DEFAULT_MEDIA_CHECK_MINUTES = 60
MIN_MEDIA_CHECK_MINUTES = 10
DEFAULT_MEDIA_AHEAD = 2
MAX_MEDIA_AHEAD = 20
_MEDIA_SETTINGS = ('path', 'interval', 'ahead', 'ytdlp')
_MEDIA_SOURCE_RE = re.compile(r'[A-Za-z][A-Za-z0-9+.\-_]*:\S')  # https://..., yt-dlp:https://..., mysite:show-1


def _media_setting(name):
    for line in _read_config_sections()['media']:
        key, sep, val = line.partition('=')
        if sep and key.strip().lower() == name:
            return val.split('#', 1)[0].strip()
    return None


def load_media_settings():
    """{'path', 'interval' (seconds), 'ahead', 'ytdlp' (extra yt-dlp arguments, a
    list)} from [media], defaults for anything missing or unusable."""
    path = _media_setting('path') or DEFAULT_MEDIA_PATH
    try:
        minutes = max(MIN_MEDIA_CHECK_MINUTES, int(_media_setting('interval')))
    except (TypeError, ValueError):
        minutes = DEFAULT_MEDIA_CHECK_MINUTES
    try:
        ahead = min(MAX_MEDIA_AHEAD, max(0, int(_media_setting('ahead'))))
    except (TypeError, ValueError):
        ahead = DEFAULT_MEDIA_AHEAD
    try:
        ytdlp = shlex.split(_media_setting('ytdlp') or '')
    except ValueError:  # an unclosed quote
        ytdlp = []
    return {'path': os.path.expanduser(path), 'interval': minutes * 60, 'ahead': ahead, 'ytdlp': ytdlp}


def load_media_sources():
    """[(source, display name or None, order or None)] from [media], de-duplicated
    (first line wins). A source is an http(s) URL or a `name:...` line for a driver
    or plugin; the settings and anything that does not start like that are not."""
    sources, seen = [], set()
    for line in _read_config_sections()['media']:
        key, sep, _val = line.partition('=')
        if sep and key.strip().lower() in _MEDIA_SETTINGS:
            continue
        parts = [p.strip() for p in line.split('|', 2)]
        url = parts[0]
        if not _MEDIA_SOURCE_RE.match(url) or url in seen:
            continue
        seen.add(url)
        name = parts[1] if len(parts) > 1 and parts[1] else None
        order = parts[2].lower() if len(parts) > 2 and parts[2].lower() in ('oldest', 'newest') else None
        sources.append((url, name, order))
    return sources
