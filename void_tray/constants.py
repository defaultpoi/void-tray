"""Constants shared by several modules."""

import os


CONFIG_DIR = os.path.expanduser('~/.config/void-tray')


# Where the single-file rss-tray kept its files; copied once on first start.
LEGACY_CONFIG_DIR = os.path.expanduser('~/.config/rss-tray')


CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.conf')


DEFAULT_HIGHLIGHT_WORDS = ('breaking',)


# ---- per-channel check schedules -------------------------------------------
# 'wed 18:00-23:00', 'tue-sun 13:00-', 'mon,wed,fri 20:00-02:00; sat 10:00-12:00'
_DAY_NAMES = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


_ALL_DAYS = frozenset(range(7))


NEW_SCHEDULE = {'enabled': False, 'minute': 30, 'days': _ALL_DAYS, 'action': 'Suspend'}


YOUTUBE_CHECK_TIMEOUT_SECONDS = 20  # per-channel; this check shells out to


WEATHER_GLYPH_COLOR = '#2b2b2b'


# `background:` (the shorthand), not `background-color:`, on everything that
# makes up the list area: many themes paint a background-image (gradient or
# solid) on lists/viewports, and that image is drawn over a plain
# background-color -- the whole news area then turns black/dark with the rows
# sitting on top of it. The shorthand also resets the image. Text colour is
# forced too, since a dark theme's light foreground would be invisible on white.
OFFLINE_BANNER_TEXT = "Offline"


TIMER_HEIGHT_COUNTDOWN_PX = 60
