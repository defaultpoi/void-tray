"""Countdown timer and the recurring schedule."""

import math
import os
import time as time_module
from datetime import datetime

from ..gi_env import GLib, Gtk
from ..config import load_actions
from ..constants import CONFIG_FILE, NEW_SCHEDULE, TIMER_HEIGHT_COUNTDOWN_PX, _DAY_NAMES
from ..desktop import play_notification_sound, run_launcher_command
from ..log import _log
from ..schedule import (
    _schedule_occurrences,
    format_days,
    load_schedule_settings,
    save_schedules,
    schedule_key,
    schedule_pending,
    schedule_settings_for,
    schedule_snooze_override,
    schedule_status,
)
from ..state import save_state


SCHEDULE_RESUME_GAP_SECONDS = 10  # a bigger wall-clock jump between ticks means the machine slept


SCHEDULE_MISSING_ACTION_SUFFIX = ' (not in [actions])'


TIMER_HEIGHT_SCHEDULE_PX = 134


TIMER_SPACER_EXTRA_PX = 25  # gap kept above the slide


def format_timer_duration(total_seconds):
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h} Hours, {m:02d} Minutes and {s:02d} Seconds"


class TimerMixin:
    # ---- Schedule mode ------------------------------------------------------
    def _schedule_settings(self):
        """Settings, re-read only when config.conf changed (this runs every
        second). A change also re-syncs, so editing a time never runs an
        occurrence that is already in the past, and drops the state of
        schedules that no longer exist."""
        try:
            stamp = os.stat(CONFIG_FILE).st_mtime_ns
        except OSError:
            stamp = None
        if self._schedule_cache[1] is None or self._schedule_cache[0] != stamp:
            settings = load_schedule_settings()
            self._schedule_cache = (stamp, settings)
            self._schedule_resync = True
            self._prune_schedule_state({schedule_key(e) for e in settings['schedules']})
        return self._schedule_cache[1]

    def _prune_schedule_state(self, keys):
        with self.lock:
            changed = False
            for name in ('schedule_fired', 'schedule_override'):
                current = self.state.get(name)
                if not isinstance(current, dict):
                    if name in self.state:
                        del self.state[name]
                        changed = True
                    continue
                for key in [k for k in current if k not in keys]:
                    del current[key]
                    changed = True
            if changed:
                save_state(self.state)

    def _schedule_state(self):
        with self.lock:
            fired, override = self.state.get('schedule_fired'), self.state.get('schedule_override')
            return (dict(fired) if isinstance(fired, dict) else {},
                    dict(override) if isinstance(override, dict) else {})

    def _mark_schedule_handled(self, key, occurrence):
        with self.lock:
            fired = self.state.setdefault('schedule_fired', {})
            if not isinstance(fired, dict):
                fired = self.state['schedule_fired'] = {}
            fired[key] = max(fired.get(key, 0), occurrence)
            overrides = self.state.get('schedule_override')
            if isinstance(overrides, dict):
                overrides.pop(key, None)
            save_state(self.state)

    def _skip_missed_schedule(self, now_ts, key, settings):
        """App start, wake from sleep or a settings change: a time that has
        already passed is never acted on late (unless it was snoozed)."""
        previous = _schedule_occurrences(now_ts, settings['days'], settings['minute'])[0]
        fired, overrides = self._schedule_state()
        if previous is None or previous <= fired.get(key, 0):
            return
        override = overrides.get(key)
        if override and override.get('occ') == previous and override['fire'] > now_ts:
            return  # still snoozed
        self._mark_schedule_handled(key, previous)

    def _schedule_tick(self):
        now_ts = time_module.time()
        last, self._last_schedule_tick = self._last_schedule_tick, now_ts
        settings = self._schedule_settings()
        active = [(schedule_key(e), schedule_settings_for(settings, e))
                  for e in settings['schedules'] if e['enabled']]
        if not active:
            self._schedule_resync = True  # enabling later must not act on an older time
            self._set_schedule_warning(None)
            self._refresh_schedule_ui()
            return
        if self._schedule_resync or last is None or now_ts - last > SCHEDULE_RESUME_GAP_SECONDS:
            self._schedule_resync = False
            for key, entry in active:
                self._skip_missed_schedule(now_ts, key, entry)
        fired, overrides = self._schedule_state()
        warnings, to_fire = [], []
        for key, entry in active:
            kind, occurrence, left = schedule_status(now_ts, entry, fired.get(key, 0), overrides.get(key))
            if kind == 'warn':
                warnings.append({'key': key, 'occ': occurrence, 'left': math.ceil(left), 'action': entry['action']})
            elif kind == 'fire':
                to_fire.append((key, occurrence, entry))
            elif kind == 'skip':
                self._mark_schedule_handled(key, occurrence)
        for key, occurrence, entry in to_fire:
            self._fire_schedule(key, occurrence, entry)
        self._set_schedule_warning(min(warnings, key=lambda w: w['left']) if warnings else None)
        self._refresh_schedule_ui()

    def _fire_schedule(self, key, occurrence, settings):
        self._mark_schedule_handled(key, occurrence)
        actions = load_actions()
        command = next((c for label, c in actions if label == settings['action']), None) \
            or next((c for label, c in actions if label.lower() == settings['action'].lower()), None)
        if command:
            run_launcher_command(command)
        else:
            _log(f"schedule: no action named {settings['action']!r} in [actions]")

    def _set_schedule_warning(self, warning):
        previous = self.schedule_warning
        if warning is None and previous is None:
            return
        self.schedule_warning = warning
        if warning is not None:
            self._schedule_flash_on = not self._schedule_flash_on
            if previous is None:  # the warning just started: be hard to miss
                play_notification_sound()
                if not (self.popup and self.popup.get_visible()):
                    self.show_popup()
        else:
            self._schedule_flash_on = False
        self.update_icon()

    def on_schedule_cancel(self, _button=None):
        warning = self.schedule_warning
        if warning:
            self._mark_schedule_handled(warning['key'], warning['occ'])
            self._set_schedule_warning(None)
            self._refresh_schedule_ui()

    def _snooze_schedule(self, key):
        settings = self._schedule_settings()
        entry = next((schedule_settings_for(settings, e) for e in settings['schedules']
                      if schedule_key(e) == key), None)
        if entry is not None:
            fired, overrides = self._schedule_state()
            new = schedule_snooze_override(time_module.time(), entry, fired.get(key, 0), overrides.get(key))
            if new:
                with self.lock:
                    store = self.state.get('schedule_override')
                    if not isinstance(store, dict):
                        store = self.state['schedule_override'] = {}
                    store[key] = new
                    save_state(self.state)
        warning = self.schedule_warning
        if warning and warning['key'] == key:
            self._set_schedule_warning(None)
        self._refresh_schedule_ui()

    def on_schedule_snooze(self, _button=None):
        """Snooze button of the warning bar: postpones the schedule that is warning."""
        if self.schedule_warning:
            self._snooze_schedule(self.schedule_warning['key'])

    def on_schedule_snooze_selected(self, _button=None):
        """Snooze button of the slide: postpones the next run of the selected schedule."""
        entry = self._selected_schedule()
        if entry is not None:
            self._snooze_schedule(schedule_key(entry))

    def _selected_schedule(self):
        entries = self._schedule_settings()['schedules']
        if not entries:
            return None
        self.sched_index = min(max(self.sched_index, 0), len(entries) - 1)
        return entries[self.sched_index]

    def _schedule_summary(self, settings):
        warning = self.schedule_warning
        if warning:
            return f"{warning['action']} in {warning['left']} s"
        now_ts = time_module.time()
        fired, overrides = self._schedule_state()
        upcoming = []
        for entry in settings['schedules']:
            if not entry['enabled']:
                continue
            key = schedule_key(entry)
            for occurrence, fire in schedule_pending(now_ts, entry, fired.get(key, 0), overrides.get(key)):
                if fire > now_ts:
                    upcoming.append((fire, occurrence, entry['action']))
        if not upcoming:
            return 'Schedule on' if any(e['enabled'] for e in settings['schedules']) else 'Schedule off'
        fire, occurrence, action = min(upcoming)
        when = datetime.fromtimestamp(fire)
        text = f"Next: {_DAY_NAMES[when.weekday()][:3].title()} {when:%H:%M} {action}"
        return text + (' (snoozed)' if fire != occurrence else '')

    def _refresh_schedule_ui(self):
        warning = self.schedule_warning
        if self.schedule_banner is not None:
            self.schedule_banner.set_visible(warning is not None)
            if warning:
                self.schedule_banner_label.set_text(f"{warning['action']} in {warning['left']} s")
        if self.timer_label is not None and self.timer_mode == 'schedule':
            self.timer_label.set_text(self._schedule_summary(self._schedule_settings()))
        if self.sched is not None:
            entry = self._selected_schedule()
            self.sched['snooze_btn'].set_sensitive(entry is not None and entry['enabled'])

    def _apply_timer_mode(self):
        if self.timer_box is None:
            return
        schedule = self.timer_mode == 'schedule'
        self.countdown_box.set_visible(not schedule)
        self.schedule_box.set_visible(schedule)
        height = TIMER_HEIGHT_SCHEDULE_PX if schedule else TIMER_HEIGHT_COUNTDOWN_PX
        self.timer_box.set_size_request(-1, height)
        if self.list_spacer is not None:
            self.list_spacer.set_size_request(-1, height + TIMER_SPACER_EXTRA_PX)
        if schedule:
            self._refresh_schedule_ui()
        else:
            self.timer_label.set_text(format_timer_duration(self.timer_remaining_seconds))

    def on_timer_mode_toggled(self, button, mode):
        if self._mode_updating:
            return
        self._mode_updating = True
        if not button.get_active():
            button.set_active(True)  # clicking the active mode again keeps it selected
        else:
            self.timer_mode = mode
            for name, other in self.mode_buttons.items():
                other.set_active(name == mode)
        self._mode_updating = False
        self._apply_timer_mode()

    # -- the Schedule controls: a selector for the list, and the selected schedule's fields
    @staticmethod
    def _schedule_label(entry):
        text = f"{entry['minute'] // 60:02d}:{entry['minute'] % 60:02d} {format_days(entry['days'])} \u00b7 {entry['action']}"
        return text if entry['enabled'] else text + ' (off)'

    def _save_schedule_entries(self, entries):
        save_schedules(entries)
        self._schedule_cache = (None, None)  # re-read (and re-sync) on next use

    def _populate_schedule_selector(self):
        entries = self._schedule_settings()['schedules']
        select = self.sched['select']
        self._sched_updating = True
        select.remove_all()
        for entry in entries:
            select.append_text(self._schedule_label(entry))
        if entries:
            self.sched_index = min(max(self.sched_index, 0), len(entries) - 1)
            select.set_active(self.sched_index)
        self._sched_updating = False
        self._load_selected_schedule_into_controls()

    def _load_selected_schedule_into_controls(self):
        entry = self._selected_schedule()
        sched = self.sched
        sched['fields'].set_sensitive(entry is not None)
        sched['remove'].set_sensitive(entry is not None)
        self._sched_updating = True
        if entry is not None:
            sched['enabled'].set_active(entry['enabled'])
            sched['hour'].set_value(entry['minute'] // 60)
            sched['minute'].set_value(entry['minute'] % 60)
            for index, button in enumerate(sched['days']):
                button.set_active(index in entry['days'])
            action = sched['action']
            action.remove_all()
            labels = [label for label, _command in load_actions()]
            for label in labels:
                action.append_text(label)
            if entry['action'] in labels:
                action.set_active(labels.index(entry['action']))
            else:  # the action no longer exists: show it, flagged, so nothing silently changes
                action.append_text(entry['action'] + SCHEDULE_MISSING_ACTION_SUFFIX)
                action.set_active(len(labels))
        self._sched_updating = False
        self._refresh_schedule_ui()

    def _on_schedule_selected(self, select):
        if self._sched_updating:
            return
        self.sched_index = max(select.get_active(), 0)
        self._load_selected_schedule_into_controls()

    def _on_schedule_add(self, _button):
        entries = [dict(e) for e in self._schedule_settings()['schedules']]
        new = dict(NEW_SCHEDULE)
        while any(schedule_key(e) == schedule_key(new) for e in entries):
            new['minute'] = (new['minute'] + 30) % (24 * 60)  # identities must differ
        entries.append(new)
        self._save_schedule_entries(entries)
        self.sched_index = len(entries) - 1
        self._populate_schedule_selector()

    def _on_schedule_remove(self, _button):
        entries = [dict(e) for e in self._schedule_settings()['schedules']]
        if entries:
            del entries[min(self.sched_index, len(entries) - 1)]
            self._save_schedule_entries(entries)
            self.sched_index = max(0, min(self.sched_index, len(entries) - 1))
            self._populate_schedule_selector()

    def _on_schedule_control_changed(self, widget=None):
        if self._sched_updating or self.sched is None:
            return
        days = frozenset(i for i, b in enumerate(self.sched['days']) if b.get_active())
        if not days:  # at least one day must stay selected
            self._sched_updating = True
            widget.set_active(True)
            self._sched_updating = False
            return
        entries = [dict(e) for e in self._schedule_settings()['schedules']]
        if not entries:
            return
        index = min(self.sched_index, len(entries) - 1)
        entry = entries[index]
        entry['enabled'] = self.sched['enabled'].get_active()
        entry['minute'] = int(self.sched['hour'].get_value()) * 60 + int(self.sched['minute'].get_value())
        entry['days'] = days
        action = self.sched['action'].get_active_text() or ''
        if action and not action.endswith(SCHEDULE_MISSING_ACTION_SUFFIX):
            entry['action'] = action
        if any(i != index and schedule_key(e) == schedule_key(entry) for i, e in enumerate(entries)):
            return  # would duplicate another schedule; ignore this change
        self._save_schedule_entries(entries)
        select = self.sched['select']
        self._sched_updating = True
        select.remove(index)
        select.insert_text(index, self._schedule_label(entry))
        select.set_active(index)
        self._sched_updating = False
        self._refresh_schedule_ui()

    def _build_schedule_box(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)

        row0 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        select = Gtk.ComboBoxText()
        add_btn = Gtk.Button(label='+')
        add_btn.set_tooltip_text('Add a schedule')
        remove_btn = Gtk.Button(label='\u2212')
        remove_btn.set_tooltip_text('Remove the selected schedule')
        row0.pack_start(select, True, True, 0)
        row0.pack_start(add_btn, False, False, 0)
        row0.pack_start(remove_btn, False, False, 0)

        fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        enabled = Gtk.CheckButton(label='On')
        hour = Gtk.SpinButton.new_with_range(0, 23, 1)
        minute = Gtk.SpinButton.new_with_range(0, 59, 1)
        for spin in (hour, minute):
            spin.set_wrap(True)
            spin.set_numeric(True)
            spin.set_width_chars(2)
            spin.connect('output', lambda s: (s.set_text(f'{int(s.get_value()):02d}'), True)[1])
        action = Gtk.ComboBoxText()
        row1.pack_start(enabled, False, False, 0)
        row1.pack_start(Gtk.Label(label='at'), False, False, 0)
        row1.pack_start(hour, False, False, 0)
        row1.pack_start(Gtk.Label(label=':'), False, False, 0)
        row1.pack_start(minute, False, False, 0)
        row1.pack_start(action, True, True, 0)

        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        day_buttons = []
        for index, name in enumerate(_DAY_NAMES):
            button = Gtk.ToggleButton(label=name[:2].title())
            button.set_tooltip_text(name.title())
            day_buttons.append(button)
            row2.pack_start(button, False, False, 0)
        snooze_btn = Gtk.Button(label=f"Snooze {self._schedule_settings()['snooze']} min")
        snooze_btn.set_tooltip_text('Postpone the next run of this schedule')
        snooze_btn.connect('clicked', self.on_schedule_snooze_selected)
        row2.pack_end(snooze_btn, False, False, 0)

        fields.pack_start(row1, False, False, 0)
        fields.pack_start(row2, False, False, 0)
        box.pack_start(row0, False, False, 0)
        box.pack_start(fields, False, False, 0)

        self.sched = {'select': select, 'add': add_btn, 'remove': remove_btn, 'fields': fields,
                      'enabled': enabled, 'hour': hour, 'minute': minute, 'action': action,
                      'days': day_buttons, 'snooze_btn': snooze_btn}
        select.connect('changed', self._on_schedule_selected)
        add_btn.connect('clicked', self._on_schedule_add)
        remove_btn.connect('clicked', self._on_schedule_remove)
        enabled.connect('toggled', self._on_schedule_control_changed)
        hour.connect('value-changed', self._on_schedule_control_changed)
        minute.connect('value-changed', self._on_schedule_control_changed)
        action.connect('changed', self._on_schedule_control_changed)
        for button in day_buttons:
            button.connect('toggled', self._on_schedule_control_changed)
        self._populate_schedule_selector()
        return box

    def on_timer_toggle_clicked(self, _button):
        self.timer_visible = not self.timer_visible
        if self.timer_box is not None:
            self.timer_box.set_visible(self.timer_visible)

    def _timer_tick(self):
        if self.timer_running and self.timer_deadline is not None:
            left = self.timer_deadline - time_module.monotonic()
            self.timer_remaining_seconds = max(0, int(-(-left // 1)))  # ceil
            if self.timer_remaining_seconds <= 0:
                self.timer_running = False
                self.timer_deadline = None
                self._fire_timer_done()
            self._update_timer_widgets()
        self._schedule_tick()
        return True

    def _fire_timer_done(self):
        play_notification_sound()
        GLib.timeout_add(700, self._play_second_beep)

    def _play_second_beep(self):
        play_notification_sound()
        return False

    def _update_timer_widgets(self):
        if self.timer_scale is None or self.timer_label is None:
            return
        self._timer_updating_ui = True
        self.timer_scale.set_value(self.timer_remaining_seconds)
        self._timer_updating_ui = False
        if self.timer_mode == 'countdown':  # in Schedule mode the label shows the schedule summary
            self.timer_label.set_text(format_timer_duration(self.timer_remaining_seconds))

    def on_timer_slider_changed(self, scale):
        if self._timer_updating_ui:
            return
        value = int(scale.get_value())
        self.timer_remaining_seconds = value
        self.timer_running = value > 0
        self.timer_deadline = (time_module.monotonic() + value) if value > 0 else None
        if self.timer_label is not None:
            self.timer_label.set_text(format_timer_duration(value))
