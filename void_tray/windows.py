"""Day/time windows used by per-channel live schedules."""

import re

from .constants import _ALL_DAYS, _DAY_NAMES


_CLOCK_RE = re.compile(r'^(\d{1,2})[:.](\d{2})$')


def _day_index(token):
    """Index (Monday=0) for 'mon'..'sun' or a longer prefix of the full name."""
    if len(token) >= 3:
        for index, name in enumerate(_DAY_NAMES):
            if name.startswith(token):
                return index
    raise ValueError(f'unknown day {token!r}')


def _parse_days(spec):
    spec = spec.lower().replace(' ', '')
    if spec in ('', 'daily', 'everyday', 'every', '*'):
        return _ALL_DAYS
    if spec == 'weekdays':
        return frozenset(range(5))
    if spec in ('weekend', 'weekends'):
        return frozenset((5, 6))
    days = set()
    for part in spec.split(','):
        if not part:
            raise ValueError(f'bad day list {spec!r}')
        first, dash, last = part.partition('-')
        start = _day_index(first)
        end = _day_index(last) if dash else start
        day = start
        while True:  # ranges may wrap, e.g. fri-mon
            days.add(day)
            if day == end:
                break
            day = (day + 1) % 7
    return frozenset(days)


def _parse_clock(text, allow_24=False):
    m = _CLOCK_RE.match(text.strip())
    if not m:
        raise ValueError(f'bad time {text!r}')
    hours, minutes = int(m.group(1)), int(m.group(2))
    if minutes > 59 or hours > 24 or (hours == 24 and (minutes or not allow_24)):
        raise ValueError(f'bad time {text!r}')
    return hours * 60 + minutes


def _parse_window(text):
    tokens = text.split()
    if not tokens:
        raise ValueError('empty window')
    last = tokens[-1]
    if re.match(r'^\d', last):
        days_spec = ''.join(tokens[:-1])
        start_text, dash, end_text = last.partition('-')
        start = _parse_clock(start_text)
        end = _parse_clock(end_text, allow_24=True) if end_text.strip() else 24 * 60
    else:  # days only: the whole day
        days_spec, start, end = ''.join(tokens), 0, 24 * 60
    return _parse_days(days_spec), start, end


def parse_schedule(text):
    """Parses 'WINDOW; WINDOW; ...' into [(days, start_minute, end_minute)];
    each WINDOW is '[days] HH:MM[-HH:MM]' (':' or '.' as the separator). Days:
    mon..sun (or full names), ranges like tue-sun, lists like mon,wed, daily,
    weekdays, weekends -- omitted means every day. No end time = until the end
    of that day; an end earlier than the start runs past midnight into the
    next day. Raises ValueError on anything it can't read."""
    windows = [_parse_window(part) for part in text.split(';') if part.strip()]
    if not windows:
        raise ValueError('empty schedule')
    return windows


def schedule_active(windows, now):
    """True if the datetime `now` (local time) falls inside any window."""
    minute = now.hour * 60 + now.minute
    weekday = now.weekday()
    for days, start, end in windows:
        if end > start:
            if weekday in days and start <= minute < end:
                return True
        elif (weekday in days and minute >= start) or ((weekday - 1) % 7 in days and minute < end):
            return True  # window runs past midnight
    return False


def channels_due(channels, schedules, live_channels, now):
    """The channels worth checking right now: those without a schedule, those
    inside one of their windows, and any that are currently live (so the end
    of a stream that outlasts its window is still noticed)."""
    return [c for c in channels
            if c not in schedules or c in live_channels or schedule_active(schedules[c], now)]


def scheduled_active_channels(schedules, now):
    """The channels that have a schedule and are inside a window at `now`."""
    return {c for c, windows in schedules.items() if schedule_active(windows, now)}
