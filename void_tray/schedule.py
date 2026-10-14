"""Recurring timer actions: parsing, saving and occurrence maths."""

from datetime import datetime, timedelta

from .config import _edit_config_section, _read_config_sections
from .constants import NEW_SCHEDULE, _ALL_DAYS, _DAY_NAMES
from .windows import _parse_clock, _parse_days


SCHEDULE_GLOBAL_DEFAULTS = {'warn': 60, 'snooze': 30, 'grace': 120}


def format_days(days):
    """Inverse of _parse_days for the config file: daily, weekdays, weekends
    or a 'mon,wed,fri' list."""
    days = frozenset(days)
    if days == _ALL_DAYS:
        return 'daily'
    if days == frozenset(range(5)):
        return 'weekdays'
    if days == frozenset((5, 6)):
        return 'weekends'
    return ','.join(_DAY_NAMES[d][:3] for d in sorted(days))


def schedule_key(entry):
    """Stable identity of a schedule (its state -- last handled run, snooze --
    is filed under it); editing a schedule therefore starts it afresh."""
    return f"{entry['minute']:04d}|{format_days(entry['days'])}|{entry['action']}"


def format_schedule_line(entry):
    return (f"{'on' if entry['enabled'] else 'off'}|{entry['minute'] // 60:02d}:{entry['minute'] % 60:02d}"
            f"|{format_days(entry['days'])}|{entry['action']}\n")


def _parse_schedule_line(line):
    """'on|00:30|weekdays|Suspend' -> entry dict, or None if unreadable."""
    fields = [f.strip() for f in line.split('|', 3)]
    if len(fields) < 2:
        return None
    try:
        minute = _parse_clock(fields[1])
        days = _parse_days(fields[2]) if len(fields) > 2 and fields[2] else _ALL_DAYS
    except ValueError:
        return None
    if minute >= 24 * 60:
        return None
    return {
        'enabled': fields[0].lower() in ('on', 'true', 'yes', '1'),
        'minute': minute,
        'days': days,
        'action': fields[3] if len(fields) > 3 and fields[3] else NEW_SCHEDULE['action'],
    }


def _is_schedule_entry_line(line):
    return '|' in line and '=' not in line.split('|', 1)[0]


def load_schedule_settings():
    """{'warn' (s), 'snooze' (min), 'grace' (s), 'schedules': [entry, ...]}
    from [schedule]. Entries are 'on|HH:MM|days|action' lines, key=value lines
    hold the shared warn/snooze/grace. A section that still uses the original
    single-schedule keys (enabled=/time=/days=/action=) is read as one entry."""
    settings = dict(SCHEDULE_GLOBAL_DEFAULTS)
    entries, legacy, legacy_seen = [], dict(NEW_SCHEDULE), False
    for line in _read_config_sections()['schedule']:
        if _is_schedule_entry_line(line):
            entry = _parse_schedule_line(line)
            if entry is not None:
                entries.append(entry)
            continue
        key, sep, val = line.partition('=')
        if not sep:
            continue
        key, val = key.strip().lower(), val.split('#', 1)[0].strip()
        try:
            if key in ('warn', 'grace'):
                settings[key] = max(0, int(val))
            elif key == 'snooze':
                settings['snooze'] = max(1, int(val))
            elif key == 'enabled':
                legacy['enabled'], legacy_seen = val.lower() in ('1', 'true', 'yes', 'on'), True
            elif key == 'time':
                minute = _parse_clock(val)
                if minute < 24 * 60:
                    legacy['minute'] = minute
                legacy_seen = True
            elif key == 'days':
                legacy['days'], legacy_seen = _parse_days(val), True
            elif key == 'action' and val:
                legacy['action'], legacy_seen = val, True
        except ValueError:
            pass
    if not entries and legacy_seen:
        entries.append(legacy)
    seen, unique = set(), []
    for entry in entries:  # identical lines would share one identity: keep the first
        if schedule_key(entry) not in seen:
            seen.add(schedule_key(entry))
            unique.append(entry)
    settings['schedules'] = unique
    return settings


def save_schedules(entries):
    """Rewrites the schedule entry lines of [schedule] (and drops the
    original single-schedule keys); comments and the warn/snooze/grace
    settings are kept."""
    def transform(section):
        kept = [l for l in section
                if not _is_schedule_entry_line(l.strip())
                and l.partition('=')[0].strip().lower() not in ('enabled', 'time', 'days', 'action')]
        return kept + [format_schedule_line(e) for e in entries]
    _edit_config_section('schedule', transform)


def schedule_settings_for(settings, entry):
    """One schedule's entry merged with the shared warn/snooze/grace, the
    shape the scheduling functions below take."""
    return dict(entry, warn=settings['warn'], snooze=settings['snooze'], grace=settings['grace'])


def _schedule_occurrences(now_ts, days, minute):
    """(latest occurrence <= now, first occurrence > now) of 'HH:MM on these
    weekdays', as timestamps in local time (DST handled by the C library)."""
    base = datetime.fromtimestamp(now_ts).replace(hour=0, minute=0, second=0, microsecond=0)
    previous = upcoming = None
    for offset in range(-8, 9):
        day = base + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        occurrence = day.replace(hour=minute // 60, minute=minute % 60).timestamp()
        if occurrence <= now_ts:
            previous = occurrence
        elif upcoming is None:
            upcoming = occurrence
    return previous, upcoming


def schedule_pending(now_ts, settings, fired_ts, override):
    """[(occurrence, fire_time)] for the occurrences not handled yet (the
    latest past one -- it may be snoozed -- and the next one). `override` is
    {'occ', 'fire'}: a snooze that moved one occurrence's fire time."""
    pending = []
    for occurrence in _schedule_occurrences(now_ts, settings['days'], settings['minute']):
        if occurrence is None or occurrence <= fired_ts:
            continue
        fire = override['fire'] if override and override.get('occ') == occurrence else occurrence
        pending.append((occurrence, fire))
    return pending


def schedule_status(now_ts, settings, fired_ts, override):
    """('idle'|'warn'|'fire'|'skip', occurrence, seconds_left): what the
    scheduler should do right now. 'skip' = the fire time passed by more than
    `grace` (e.g. the machine was asleep): drop it, don't run it late."""
    for occurrence, fire in schedule_pending(now_ts, settings, fired_ts, override):
        if fire <= now_ts:
            if now_ts - fire <= settings['grace']:
                return 'fire', occurrence, 0
            return 'skip', occurrence, 0
        if settings['warn'] > 0 and fire - now_ts <= settings['warn']:
            return 'warn', occurrence, fire - now_ts
    return 'idle', None, 0


def schedule_snooze_override(now_ts, settings, fired_ts, override):
    """The override that postpones the pending occurrence by `snooze` minutes
    (counted from its current fire time, or from now if that has passed);
    None if nothing is pending."""
    # a stale past occurrence the tick hasn't dropped yet is not worth snoozing
    pending = [p for p in schedule_pending(now_ts, settings, fired_ts, override)
               if p[1] >= now_ts - settings['grace']]
    if not pending:
        return None
    occurrence, fire = pending[0]
    return {'occ': occurrence, 'fire': max(fire, now_ts) + settings['snooze'] * 60}
