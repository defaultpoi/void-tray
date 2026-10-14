from typing import Any, Dict, List, Optional, Tuple, Union
import os
import json
import hashlib
import unicodedata

# Constants from the original app
CONFIG_DIR = os.path.expanduser('~/.config/void-tray')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.conf')
STATE_FILE = os.path.join(CONFIG_DIR, 'state.json')

DEFAULT_CONFIG = (
    "[feeds]\n"
    "# Format: URL|custom display name|check interval in minutes|mute rules\n"
    "# All fields after the URL are optional but positional — leave a field empty\n"
    "# to skip it while still setting a later one, e.g. URL||5\n"
    "# Mute rules are 'field=value' separated by ;, matched against the item's\n"
    "# fields (category, author, title, summary, ...); a comma lists several\n"
    "# values (category=a,b,c); * and ? are wildcards, case and accents are\n"
    "# ignored. Matching items are marked read, never shown.\n"
    "# https://example.com/feed.xml\n"
    "# https://example.com/feed.xml|My Blog\n"
    "# https://example.com/feed.xml|My Blog|5\n"
    "# https://example.com/feed.xml|My Blog|5|category=sports; author=Some Name\n"
    "\n"
    "[mute]\n"
    "# Items whose title contains any of these phrases (case-insensitive,\n"
    "# substring match) are auto-marked as read and never shown as unread.\n"
    "# One phrase per line, or several separated by | on the same line.\n"
    "# Example:\n"
    "# (P)|Fashion week|Another item\n"
    "\n"
    "[twitch]\n"
    "# Twitch channel login names to watch for live status, one per line:\n"
    "#   channel|quality (optional)|schedule (optional)\n"
    "# Uses Twitch's own internal (unofficial) API — no account/app needed.\n"
    "# Clicking a live channel plays it (streamlink + mpv, one shared maximized window).\n"
    "# interval=<minutes> sets how often to check (default 15, minimum 1).\n"
    "# quality=<streamlink quality> for channels without their own, e.g.\n"
    "# quality=720p60,720p,best (fallbacks, first available wins; default best).\n"
    "# A schedule limits checking to when the channel is expected to stream\n"
    "# (system local time): [days] HH:MM[-HH:MM], several separated by ;\n"
    "# Days: mon..sun, ranges (tue-sun), lists (mon,wed), daily, weekdays,\n"
    "# weekends; no end time means until midnight. Examples:\n"
    "# examplechannel\n"
    "# weeklychannel||wed 18:00-23:00\n"
    "# dailychannel|720p60|tue-sun 13:00-; sat 10:00-12:00\n"
    "\n"
    "[youtube]\n"
    "# Same format as [twitch]; identifiers are @handle or channel/UC... and\n"
    "# case-sensitive. interval=<minutes> between live checks (default 15, minimum 5; each\n"
    "# channel costs a full streamlink run).\n"
    "# quality= as above.\n"
    "# @somehandle\n"
    "# channel/UCxxxxxxxxxxxxxxxxxxxxxx|720p60\n"
    "\n"
    "[updates]\n"
    "# interval=<hours> between package update scans (default 12, minimum 1).\n"
    "\n"
    "[highlight]\n"
    "# News items whose title contains one of these words pulse in the popup\n"
    "# (one per line, or several separated by |; default: breaking). Case and\n"
    "# accents are ignored, whole words only.\n"
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
    "[timer]\n"
    "# Timer settings\n"
    "# max_seconds=<seconds>: maximum timer duration (default 7200 = 2 hours)\n"
    "# step_seconds=<seconds>: slider step size (default 60 = 1 minute)\n"
    "max_seconds=7200\n"
    "step_seconds=60\n"
    "\n"
    "[launcher]\n"
    "# Commands for the popup's Launch button, one per line: Label|command\n"
    "# (run through the shell, so ~, quotes and && work), detached, from my home directory.\n"
    "# Files|thunar ~\n"
    "# Update system|xfce4-terminal -e \"sudo xbps-install -Su\"\n"
    "\n"
    "[weather]\n"
    "# Coordinates for the weather bar: lat|lon (one line, decimal degrees).\n"
    "# Uses Open-Meteo, no account/key needed. The weather bar stays hidden\n"
    "# until this line is set, e.g.:\n"
    "# <latitude>|<longitude>\n"
    "# interval=<minutes> between weather (and alert) refreshes (default 30, minimum 5).\n"
    "# alerts=true enables MeteoAlarm severe weather alerts (European\n"
    "# countries covered by MeteoAlarm) for the country/region matching the\n"
    "# coordinates above (reverse-geocoded automatically via OpenStreetMap).\n"
    "# When an alert is active, the matching weather value and the tray badge\n"
    "# pulse in the alert's color.\n"
    "# The app writes country=/region= back into this section once resolved\n"
    "# and never looks them up again; delete both lines to resolve afresh\n"
    "# (e.g. after changing the coordinates).\n"
    "alerts=false\n"
)

def fold_text(text: str) -> str:
    """Lowercase, accents and extra spaces removed, so 'Café' matches 'cafe'."""
    decomposed = unicodedata.normalize('NFKD', str(text))
    stripped = ''.join(ch for ch in decomposed if not unicodedata.combining(ch))
    return ' '.join(stripped.casefold().split())

def get_entry_id(entry: Dict[str, Any], feed_url: str) -> str:
    """A stable id for entries with a real id/link. Entries lacking both
    (the fallback: title+published) are hashed together with feed_url, so the
    same title+date on two different feeds doesn't collide."""
    raw = entry.get('id') or entry.get('link')
    if raw:
        return hashlib.sha1(raw.encode('utf-8', 'ignore')).hexdigest()
    fallback = feed_url + '\x00' + entry.get('title', '') + entry.get('published', '')
    return hashlib.sha1(fallback.encode('utf-8', 'ignore')).hexdigest()
