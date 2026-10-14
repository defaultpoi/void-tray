"""Open-Meteo weather, MeteoAlarm alerts and glyph helpers."""

import feedparser
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .config import load_weather_settings
from .constants import WEATHER_GLYPH_COLOR


def build_weather_api_url(lat, lon):
    return (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,wind_speed_10m,weather_code"
        "&hourly=wind_speed_10m"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum"
        "&forecast_days=6&timezone=auto"
    )


METEOALARM_FEED_URL_TEMPLATE = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-{country}"


NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"


def reverse_geocode_region(lat, lon):
    """Best-effort reverse geocode via Nominatim (English names); returns
    (country, region, country_code) -- region being the county, falling back
    to the state, and None if neither exists; country_code is the lowercase
    ISO code Nominatim reports. Returns None on any failure or if no country
    was found. The caller only asks when country/region aren't in the config."""
    url = (
        f"{NOMINATIM_REVERSE_URL}?lat={lat}&lon={lon}"
        "&format=jsonv2&zoom=8&accept-language=en"
    )
    req = urllib.request.Request(url, headers={'User-Agent': 'void-tray/1.0 (personal use)'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        address = data.get('address') or {}
        country = address.get('country')
        if not country:
            return None
        region = address.get('county') or address.get('state')
        return country, region, (address.get('country_code') or '').upper() or None
    except Exception:
        return None


def meteoalarm_feed_url(country):
    """MeteoAlarm's per-country legacy Atom feed, e.g. 'United Kingdom' ->
    .../meteoalarm-legacy-atom-united-kingdom."""
    slug = re.sub(r'[^a-z0-9]+', '-', (country or '').lower()).strip('-')
    return METEOALARM_FEED_URL_TEMPLATE.format(country=slug)


ALERT_HAZARD_SEGMENT_MAP = (
    # (substring to look for in the hazard text, lowercased) -> segment key.
    # Checked in order; first match wins. 'glyph' recolors the weather-icon
    # glyph in the temp segment; the others recolor that value's own label.
    ('wind', 'wind'),
    ('temperature', 'hilo'),
    ('snow', 'glyph'),
    ('ice', 'glyph'),
    ('rain', 'rain'),
    ('thunderstorm', 'rain'),
    ('flood', 'rain'),
    ('coastal', 'rain'),
)


def _hazard_to_segment(hazard_text):
    h = hazard_text.lower()
    for needle, segment in ALERT_HAZARD_SEGMENT_MAP:
        if needle in h:
            return segment
    return None  # e.g. Fog, Forest fire, Avalanches -- badge-only, no matching value


_METEOALARM_TITLE_RE = re.compile(
    r'^\s*\S+\s+(.+?)\s+Warning\s+issued\s+for\s+.+?\s+-\s+(.+?)\s*$', re.IGNORECASE
)


def parse_meteoalarm_title(title):
    """Splits a MeteoAlarm entry title like 'Yellow Wind Warning issued for
    <country> - <region>' into (hazard, region). Returns (None, None) if the
    title doesn't match the expected shape."""
    m = _METEOALARM_TITLE_RE.match(title or '')
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _severity_to_color(cap_severity):
    """Maps a CAP severity string to MeteoAlarm's own awareness-level color:
    Moderate=yellow, Severe=orange, Extreme=red. Minor or an unrecognized
    value defaults to yellow (the least visually urgent), since overstating
    urgency for an unrecognized value is worse than understating it."""
    s = (cap_severity or '').strip().lower()
    if s == 'extreme':
        return 'red'
    if s == 'severe':
        return 'orange'
    return 'yellow'


def _parse_meteoalarm_time(value):
    """Parses a MeteoAlarm CAP timestamp like '2026-09-28T17:00:00+00:00'.
    Returns None if missing or unparsable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def fetch_meteoalarm_alerts(region, country, now=None):
    """Fetches `country`'s MeteoAlarm feed and returns the entries whose area
    matches `region` (case-insensitive) AND whose time window currently
    covers `now` (defaults to the real current time), as a list of
    {'hazard', 'segment', 'title', 'expires', 'severity_color'} dicts.
    Returns None on a feed fetch failure (distinct from [] = feed fetched
    fine, nothing active for this region right now).

    The feed can retain entries past their own expiry (observed in
    practice), so onset/expires are checked here rather than trusting
    presence in the feed alone. Missing or unparsable timestamps err on the
    side of showing the alert rather than hiding a possibly-real one."""
    try:
        parsed = feedparser.parse(meteoalarm_feed_url(country))
    except Exception:
        return None
    if getattr(parsed, 'bozo', False) and not parsed.entries:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    region_lower = (region or '').strip().lower()
    alerts = []
    for entry in parsed.entries:
        if entry.get('cap_areadesc', '').strip().lower() != region_lower:
            continue
        hazard, _area = parse_meteoalarm_title(entry.get('title', ''))
        if not hazard:
            continue
        onset = (
            _parse_meteoalarm_time(entry.get('cap_onset'))
            or _parse_meteoalarm_time(entry.get('cap_effective'))
        )
        if onset is not None and now < onset:
            continue  # not started yet
        expires = _parse_meteoalarm_time(entry.get('cap_expires'))
        if expires is not None and now > expires:
            continue  # expired
        alerts.append({
            'hazard': hazard,
            'segment': _hazard_to_segment(hazard),
            'title': entry.get('title', ''),
            'expires': entry.get('cap_expires', ''),
            'severity_color': _severity_to_color(entry.get('cap_severity')),
        })
    return alerts


def fetch_weather():
    """Best-effort fetch from Open-Meteo. Returns a dict or None on any failure."""
    settings = load_weather_settings()
    lat, lon = settings['lat'], settings['lon']
    if lat is None or lon is None:
        return None  # no coordinates configured: weather bar stays hidden
    url = build_weather_api_url(lat, lon)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None
    try:
        current = data.get('current', {})
        hourly = data.get('hourly', {})
        daily = data.get('daily', {})
        winds = hourly.get('wind_speed_10m', [])
        hourly_times = hourly.get('time', [])
        current_time = current.get('time')

        current_hour_idx = hourly_times.index(current_time) if current_time in hourly_times else 0
        remaining_winds = [w for w in winds[current_hour_idx:24] if w is not None]
        today_max_wind = max(remaining_winds) if remaining_winds else None

        daily_max = daily.get('temperature_2m_max', [])
        daily_min = daily.get('temperature_2m_min', [])
        daily_rain_prob = daily.get('precipitation_probability_max', [])
        daily_precip_sum = daily.get('precipitation_sum', [])

        forecast_days = []
        for i in range(1, 6):
            if i < len(daily_max) and i < len(daily_min):
                forecast_days.append({
                    'max_temp': daily_max[i],
                    'min_temp': daily_min[i],
                    'rain_prob': daily_rain_prob[i] if i < len(daily_rain_prob) else None,
                })

        return {
            'temp': current.get('temperature_2m'),
            'weather_code': current.get('weather_code'),
            'wind': current.get('wind_speed_10m'),
            'today_max_wind': today_max_wind,
            'today_max_temp': daily_max[0] if len(daily_max) > 0 else None,
            'today_min_temp': daily_min[0] if len(daily_min) > 0 else None,
            'today_rain_prob': daily_rain_prob[0] if len(daily_rain_prob) > 0 else None,
            'today_precip_sum': daily_precip_sum[0] if len(daily_precip_sum) > 0 else None,
            'forecast_days': forecast_days,
        }
    except Exception:
        return None


def weather_code_glyph(code):
    """Maps a WMO weather_code to a plain Unicode glyph (rendered via
    mono_glyph_markup, never as a color emoji).
    Returns None for codes without a good simple symbol (e.g. fog), so the
    icon is just omitted rather than showing something misleading."""
    if code is None:
        return None
    if code == 0:
        return '\u2600'  # clear sky
    if code in (1, 2, 3):
        return '\u2601'  # mainly clear / partly cloudy / overcast
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return '\u2614'  # drizzle / rain / rain showers
    if code in (71, 73, 75, 77, 85, 86):
        return '\u2744'  # snow
    if code in (95, 96, 99):
        return '\u26a1'  # thunderstorm
    return None  # e.g. fog (45, 48) — no reliable simple glyph


# DejaVu Sans' cloud is small and sits low (its top is ~0.36em above the
# baseline, vs ~0.74em for the digits and the sun/snow/bolt glyphs), so it's
# lifted by this many em to line up with the others (value tuned against a
# real screenshot of the bar).
GLYPH_RISE_EM = {'\u2601': 0.52}


def glyph_rise_units(glyph, widget=None):
    """Pango `rise` (1/1024 pt) that top-aligns `glyph`, or 0 if it needs none.
    The glyph's span is size="large" (1.2x), so the base font size of
    `widget` (10pt if unknown) is scaled accordingly."""
    em = GLYPH_RISE_EM.get(glyph)
    if not em:
        return 0
    size = 10 * 1024
    try:
        size = widget.get_pango_context().get_font_description().get_size() or size
    except Exception:
        pass
    return int(em * 1.2 * size)


def rise_spacer_markup(rise):
    """Zero-width run with the same font and rise as a raised glyph. Appended
    to the other values so every label gets the exact same line height (and so
    the same text baseline) as the one holding the raised glyph."""
    if not rise:
        return ''
    return f'<span font_family="DejaVu Sans" rise="{rise}">\u200b</span>'


def weather_row_pad(widget=None):
    """Zero-width raised run appended to every value on both weather slides,
    so all labels (and both slides) get identical line height and therefore
    identical text position, whether or not a raised glyph is on screen."""
    return rise_spacer_markup(glyph_rise_units('\u2601', widget))


def mono_glyph_markup(glyph, color=WEATHER_GLYPH_COLOR, rise=0):
    """Weather glyph forced to the monochrome text form (U+FE0E variation
    selector + a text font), so it's drawn in `color` and stays readable on
    the light weather bar instead of becoming a pale color emoji."""
    rise_attr = f' rise="{rise}"' if rise else ''
    return (f'<span foreground="{color}" font_family="DejaVu Sans"{rise_attr}>'
            f'{glyph}\ufe0e</span>')
