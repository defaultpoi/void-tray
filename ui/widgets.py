"""
Reusable GTK widgets for void-tray popup window.

Includes header rows, item rows, weather bars, timer controls, and launcher buttons.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, Pango, GLib
import logging
import time as time_module
from typing import Optional, Dict, Any, List
from core.state import get_state_manager
from core.event_bus import get_event_bus
from utils.logger import get_logger

logger = get_logger(__name__)


class HeaderRow(Gtk.Box):
    """Header row with title and controls."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.set_halign(Gtk.Align.FILL)

        # Application title
        self._title_label = Gtk.Label(label="void-tray")
        self._title_label.set_halign(Gtk.Align.START)
        self._title_label.get_style_context().add_class("title")
        self.pack_start(self._title_label, True, True, 0)

        # Control buttons (refresh, settings, etc.)
        self._controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.pack_start(self._controls_box, False, False, 0)

        # Refresh button
        self._refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        self._refresh_button.set_tooltip_text("Refresh all")
        self._refresh_button.connect("clicked", self._on_refresh_clicked)
        self._controls_box.pack_start(self._refresh_button, False, False, 0)

        # Settings button
        self._settings_button = Gtk.Button.new_from_icon_name("preferences-system-symbolic", Gtk.IconSize.BUTTON)
        self._settings_button.set_tooltip_text("Settings")
        self._settings_button.connect("clicked", self._on_settings_clicked)
        self._controls_box.pack_start(self._settings_button, False, False, 0)

        self.show_all()

    def update(self) -> None:
        """Update the header row based on current state."""
        # Update title or other elements if needed
        pass

    def _on_refresh_clicked(self, button: Gtk.Button) -> None:
        """Handle refresh button click."""
        logger.debug("Refresh button clicked")
        # Trigger refresh of all services
        event_bus = get_event_bus()
        event_bus.publish('manual_refresh', {})

    def _on_settings_clicked(self, button: Gtk.Button) -> None:
        """Handle settings button click."""
        logger.debug("Settings button clicked")
        # TODO: Open settings dialog


class ItemRow(Gtk.Box):
    """Row displaying a single feed item."""

    def __init__(self, item_data: Dict[str, Any]):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.set_halign(Gtk.Align.FILL)
        self.set_margin_top(2)
        self.set_margin_bottom(2)

        self._item_data = item_data
        self._setup_widgets()
        self._update_content()

    def _setup_widgets(self) -> None:
        """Set up the widgets for the item row."""
        # Icon indicator (for highlighted items, etc.)
        self._icon_image = Gtk.Image()
        self._icon_image.set_pixel_size(16)
        self.pack_start(self._icon_image, False, False, 0)

        # Main content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.pack_start(content_box, True, True, 0)

        # Title
        self._title_label = Gtk.Label()
        self._title_label.set_halign(Gtk.Align.START)
        self._title_label.set_line_wrap(True)
        self._title_label.set_max_width_chars(50)
        content_box.pack_start(self._title_label, False, False, 0)

        # Meta information (feed title, time, etc.)
        self._meta_label = Gtk.Label()
        self._meta_label.set_halign(Gtk.Align.START)
        self._meta_label.get_style_context().add_class("meta")
        content_box.pack_start(self._meta_label, False, False, 0)

        # Action buttons (open, mark as read, etc.)
        self._actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.pack_start(self._actions_box, False, False, 0)

        # Open button
        self._open_button = Gtk.Button.new_from_icon_name("web-browser-symbolic", Gtk.IconSize.BUTTON)
        self._open_button.set_tooltip_text("Open in browser")
        self._open_button.connect("clicked", self._on_open_clicked)
        self._actions_box.pack_start(self._open_button, False, False, 0)

        # Mark as read button
        self._mark_read_button = Gtk.Button.new_from_icon_name("mail-mark-as-read-symbolic", Gtk.IconSize.BUTTON)
        self._mark_read_button.set_tooltip_text("Mark as read")
        self._mark_read_button.connect("clicked", self._on_mark_read_clicked)
        self._actions_box.pack_start(self._mark_read_button, False, False, 0)

    def _update_content(self) -> None:
        """Update the row content based on item data."""
        # Title
        title = self._item_data.get('title', '(untitled)')
        self._title_label.set_text(title)

        # Highlighting
        if self._item_data.get('highlighted', False):
            self._title_label.get_style_context().add_class("highlighted")
            self._icon_image.set_from_icon_name("emblem-important-symbolic", Gtk.IconSize.MENU)
        else:
            self._title_label.get_style_context().remove_class("highlighted")
            self._icon_image.clear()

        # Meta information
        feed_title = self._item_data.get('feed_title', 'Unknown feed')
        published = self._item_data.get('published', '')
        if published:
            # Try to format the date nicely
            try:
                # Simple approach - just show it as-is for now
                meta_text = f"{feed_title} • {published}"
            except:
                meta_text = feed_title
        else:
            meta_text = feed_title

        self._meta_label.set_text(meta_text)

        # Tooltip with full description
        description = self._item_data.get('description', '')
        if description:
            # Truncate tooltip if too long
            tooltip = description[:200] + ("..." if len(description) > 200 else "")
            self.set_has_tooltip(True)
            self._open_button.set_tooltip_text(f"Open: {title}\n{tooltip}")
        else:
            self.set_has_tooltip(False)
            self._open_button.set_tooltip_text(f"Open: {title}")

    def _on_open_clicked(self, button: Gtk.Button) -> None:
        """Handle open button click."""
        url = self._item_data.get('link', '')
        if url:
            logger.debug(f"Opening URL: {url}")
            # TODO: Use utility function to open URL in browser
            import subprocess
            try:
                subprocess.Popen(['xdg-open', url])
            except Exception as e:
                logger.error(f"Failed to open URL: {e}")

    def _on_mark_read_clicked(self, button: Gtk.Button) -> None:
        """Handle mark as read button click."""
        # TODO: Implement mark as read functionality
        logger.debug("Mark as read clicked")
        # This would involve updating state and possibly removing from list


class WeatherBar(Gtk.Box):
    """Weather information display bar."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.set_halign(Gtk.Align.FILL)

        # Weather icon
        self._icon_image = Gtk.Image()
        self._icon_image.set_pixel_size(24)
        self.pack_start(self._icon_image, False, False, 0)

        # Temperature
        self._temp_label = Gtk.Label()
        self._temp_label.set_halign(Gtk.Align.START)
        self._temp_label.get_style_context().add_class("temperature")
        self.pack_start(self._temp_label, False, False, 0)

        # Conditions
        self._conditions_label = Gtk.Label()
        self._conditions_label.set_halign(Gtk.Align.START)
        self.pack_start(self._conditions_label, False, False, 0)

        # Additional info (humidity, wind, etc.)
        self._details_label = Gtk.Label()
        self._details_label.set_halign(Gtk.Align.START)
        self._details_label.get_style_context().add_class("details")
        self.pack_start(self._details_label, False, False, 0)

        self.show_all()

    def update(self) -> None:
        """Update the weather bar based on current state."""
        state = get_state_manager().get_state()
        weather_data = state.get('weather_data', {})

        if not weather_data or weather_data.get('temperature') is None:
            # No weather data available
            self._icon_image.clear()
            self._temp_label.set_text("--°")
            self._conditions_label.set_text("Weather unavailable")
            self._details_label.set_text("")
            return

        # Temperature
        temp = weather_data.get('temperature')
        if temp is not None:
            self._temp_label.set_text(f"{temp:.0f}°")
        else:
            self._temp_label.set_text("--°")

        # Conditions (would need to map weather code to text)
        conditions = self._get_conditions_text(weather_data.get('weathercode'))
        self._conditions_label.set_text(conditions)

        # Details
        details_parts = []
        humidity = weather_data.get('humidity')
        if humidity is not None:
            details_parts.append(f"{humidity}% humidity")

        windspeed = weather_data.get('windspeed')
        if windspeed is not None:
            details_parts.append(f"{windspeed} km/h wind")

        self._details_label.set_text(" • ".join(details_parts))

        # Icon (would need to map weather code to icon)
        icon_name = self._get_weather_icon(weather_data.get('weathercode'))
        try:
            self._icon_image.set_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
        except:
            self._icon_image.clear()

    def _get_conditions_text(self, weather_code: Optional[int]) -> str:
        """Convert weather code to human-readable conditions."""
        if weather_code is None:
            return "Unknown"

        # Simplified mapping - in reality this would be more comprehensive
        condition_map = {
            0: "Clear sky",
            1: "Mainly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Fog",
            48: "Depositing rime fog",
            51: "Light drizzle",
            53: "Moderate drizzle",
            55: "Dense drizzle",
            56: "Light freezing drizzle",
            57: "Dense freezing drizzle",
            61: "Slight rain",
            63: "Moderate rain",
            65: "Heavy rain",
            66: "Light freezing rain",
            67: "Heavy freezing rain",
            71: "Slight snow fall",
            73: "Moderate snow fall",
            75: "Heavy snow fall",
            77: "Snow grains",
            80: "Slight rain showers",
            81: "Moderate rain showers",
            82: "Violent rain showers",
            85: "Slight snow showers",
            86: "Heavy snow showers",
            95: "Thunderstorm",
            96: "Thunderstorm with slight hail",
            99: "Thunderstorm with heavy hail"
        }

        return condition_map.get(weather_code, f"Weather code {weather_code}")

    def _get_weather_icon(self, weather_code: Optional[int]) -> str:
        """Get icon name for weather code."""
        if weather_code is None:
            return "weather-severe-alert-symbolic"

        # Simplified mapping to symbolic icon names
        icon_map = {
            0: "weather-clear-symbolic",           # Clear sky
            1: "weather-few-clouds-symbolic",      # Mainly clear
            2: "weather-scattered-clouds-symbolic", # Partly cloudy
            3: "weather-overcast-symbolic",        # Overcast
            45: "weather-fog-symbolic",            # Fog
            48: "weather-fog-symbolic",            # Depositing rime fog
            51: "weather-showers-scattered-symbolic", # Light drizzle
            53: "weather-showers-scattered-symbolic", # Moderate drizzle
            55: "weather-showers-scattered-symbolic", # Dense drizzle
            56: "weather-showers-scattered-symbolic", # Light freezing drizzle
            57: "weather-showers-scattered-symbolic", # Dense freezing drizzle
            61: "weather-showers-symbolic",        # Slight rain
            63: "weather-showers-symbolic",        # Moderate rain
            65: "weather-showers-symbolic",        # Heavy rain
            66: "weather-showers-symbolic",        # Light freezing rain
            67: "weather-showers-symbolic",        # Heavy freezing rain
            71: "weather-snow-symbolic",           # Slight snow fall
            73: "weather-snow-symbolic",           # Moderate snow fall
            75: "weather-snow-symbolic",           # Heavy snow fall
            77: "weather-snow-symbolic",           # Snow grains
            80: "weather-showers-symbolic",        # Slight rain showers
            81: "weather-showers-symbolic",        # Moderate rain showers
            82: "weather-showers-symbolic",        # Violent rain showers
            85: "weather-snow-symbolic",           # Slight snow showers
            86: "weather-snow-symbolic",           # Heavy snow showers
            95: "weather-storm-symbolic",          # Thunderstorm
            96: "weather-storm-symbolic",          # Thunderstorm with slight hail
            99: "weather-storm-symbolic"           # Thunderstorm with heavy hail
        }

        return icon_map.get(weather_code, "weather-severe-alert-symbolic")


class TimerControls(Gtk.Box):
    """Timer and schedule controls."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_halign(Gtk.Align.FILL)

        # Load configuration for timer defaults
        from config.loader import load_timer_settings
        timer_settings = load_timer_settings()
        self._max_seconds = timer_settings.get('max_seconds', 7200)  # 2 hours default
        self._step_seconds = timer_settings.get('step_seconds', 60)

        # Load schedules from config
        from config.loader import load_schedule_settings
        schedule_settings = load_schedule_settings()
        self._schedules = schedule_settings.get('schedules', [])

        # Load actions from config
        from config.loader import load_actions
        actions = load_actions()
        self._action_labels = [label for label, command in actions]

        # Timer state
        self._remaining_time = 0  # seconds remaining in countdown
        self._timer_running = False
        self._timer_id = None  # for GLib timeout
        self._duration_value = 30 * 60  # default duration in seconds (30 minutes)

        # Event bus for state updates
        self._event_bus = get_event_bus()
        self._event_bus.subscribe('state_change', self._on_state_change)

        # Create notebook for tabs
        self._notebook = Gtk.Notebook()
        self._notebook.set_tab_pos(Gtk.PositionType.TOP)
        self.pack_start(self._notebook, True, True, 0)

        # Countdown tab
        countdown_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        countdown_box.set_halign(Gtk.Align.FILL)
        countdown_box.set_valign(Gtk.Align.FILL)

        # Duration slider
        slider_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        slider_label = Gtk.Label(label="Duration:")
        slider_label.set_halign(Gtk.Align.START)
        slider_box.pack_start(slider_label, False, False, 0)
        self._duration_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 120, 1)
        self._duration_scale.set_value(30)  # default 30 minutes
        self._duration_scale.set_digits(0)
        self._duration_scale.set_draw_value(True)
        self._duration_scale.set_hexpand(True)
        slider_box.pack_start(self._duration_scale, True, True, 0)
        countdown_box.pack_start(slider_box, False, False, 0)

        # Remaining time label
        self._remaining_label = Gtk.Label(label="00:00")
        self._remaining_label.set_halign(Gtk.Align.CENTER)
        self._remaining_label.set_markup('<span size="xx-large">00:00</span>')
        countdown_box.pack_start(self._remaining_label, True, True, 0)

        # Control buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._start_button = Gtk.Button(label="Start")
        self._start_button.connect("clicked", self._on_start_clicked)
        button_box.pack_start(self._start_button, False, False, 0)
        self._pause_button = Gtk.Button(label="Pause")
        self._pause_button.connect("clicked", self._on_pause_clicked)
        button_box.pack_start(self._pause_button, False, False, 0)
        self._reset_button = Gtk.Button(label="Reset")
        self._reset_button.connect("clicked", self._on_reset_clicked)
        button_box.pack_start(self._reset_button, False, False, 0)
        countdown_box.pack_start(button_box, False, False, 0)

        self._notebook.append_page(countdown_box, Gtk.Label(label="Countdown"))

        # Schedule tab
        schedule_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        schedule_box.set_halign(Gtk.Align.FILL)
        schedule_box.set_valign(Gtk.Align.FILL)

        # Schedule list container
        self._schedule_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        schedule_box.pack_start(self._schedule_list_box, True, True, 0)

        # Add schedule button
        add_button = Gtk.Button(label="Add Schedule")
        add_button.connect("clicked", self._on_add_schedule_clicked)
        schedule_box.pack_start(add_button, False, False, 0)

        self._notebook.append_page(schedule_box, Gtk.Label(label="Schedule"))

        # Load existing schedules into the UI
        self._load_schedules_into_ui()

        # Do not show automatically; visibility will be controlled by popup window

    def _on_state_change(self, data: Any = None) -> None:
        """Handle state change events."""
        self.update()

    def update(self) -> None:
        """Update the timer controls based on current state."""
        state = get_state_manager().get_state()
        remaining = state.get('timer_remaining', 0)
        running = state.get('timer_running', False)

        # Update remaining time label
        minutes = int(remaining // 60)
        seconds = int(remaining % 60)
        self._remaining_label.set_text(f"{minutes:02d}:{seconds:02d}")

        # Update button states
        self._start_button.set_sensitive(not running)
        self._pause_button.set_sensitive(running)
        self._reset_button.set_sensitive(not running and remaining > 0)

    def _on_start_clicked(self, button: Gtk.Button) -> None:
        """Handle start button click."""
        # Get duration from slider (in minutes) and convert to seconds
        duration_minutes = self._duration_scale.get_value()
        self._remaining_time = int(duration_minutes * 60)
        # Update state
        state_manager = get_state_manager()
        state_manager.update_state(lambda state: {
            **state,
            'timer_remaining': self._remaining_time,
            'timer_running': True
        })
        self._timer_running = True
        # Start timeout
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._timer_id = GLib.timeout_add_seconds(1, self._on_timer_tick)
        logger.debug(f"Timer started for {duration_minutes} minutes")

    def _on_pause_clicked(self, button: Gtk.Button) -> None:
        """Handle pause button click."""
        self._timer_running = False
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        # Update state
        state_manager = get_state_manager()
        state_manager.update_state(lambda state: {
            **state,
            'timer_running': False
        })
        logger.debug("Timer paused")

    def _on_reset_clicked(self, button: Gtk.Button) -> None:
        """Handle reset button click."""
        self._timer_running = False
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self._remaining_time = 0
        # Update state
        state_manager = get_state_manager()
        state_manager.update_state(lambda state: {
            **state,
            'timer_remaining': 0,
            'timer_running': False
        })
        logger.debug("Timer reset")

    def _on_timer_tick(self) -> bool:
        """Called every second to update the timer."""
        if self._remaining_time > 0:
            self._remaining_time -= 1
        else:
            self._remaining_time = 0
            self._timer_running = False
            if self._timer_id is not None:
                GLib.source_remove(self._timer_id)
                self._timer_id = None
            # Timer finished - show notification?
            logger.debug("Timer finished")
        # Update state
        state_manager = get_state_manager()
        state_manager.update_state(lambda state: {
            **state,
            'timer_remaining': self._remaining_time,
            'timer_running': self._timer_running
        })
        return self._timer_running  # Continue if still running

    def _load_schedules_into_ui(self) -> None:
        """Load schedules from self._schedules into the UI."""
        # Clear existing rows
        for child in self._schedule_list_box.get_children():
            self._schedule_list_box.remove(child)

        # Add each schedule as a row
        for index, schedule in enumerate(self._schedules):
            row = self._create_schedule_row(schedule, index)
            self._schedule_list_box.pack_start(row, False, False, 0)

        self._schedule_list_box.show_all()

    def _create_schedule_row(self, schedule: Dict[str, Any], index: int) -> Gtk.Widget:
        """Create a row widget for a schedule entry."""
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # Enabled checkbox
        enabled_cb = Gtk.CheckButton()
        enabled_cb.set_active(schedule.get('enabled', False))
        enabled_cb.connect("toggled", self._on_enabled_toggled, index)
        row_box.pack_start(enabled_cb, False, False, 0)

        # Time entry
        time_entry = Gtk.Entry()
        time_entry.set_text(schedule.get('time', '00:00'))
        time_entry.set_width_chars(5)
        time_entry.connect("changed", self._on_time_changed, index)
        row_box.pack_start(time_entry, False, False, 0)

        # Days checkboxes
        days_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        for day in day_names:
            cb = Gtk.CheckButton(label=day.capitalize()[:2])  # Mo, Tu, etc.
            cb.set_active(day in schedule.get('days', []))
            cb.connect("toggled", self._on_day_toggled, index, day)
            days_box.pack_start(cb, False, False, 0)
        row_box.pack_start(days_box, True, True, 0)

        # Action combo box
        action_combo = Gtk.ComboBoxText()
        for action_label in self._action_labels:
            action_combo.append_text(action_label)
        action_text = schedule.get('action', self._action_labels[0])
        if action_text in self._action_labels:
            action_combo.set_active(self._action_labels.index(action_text))
        else:
            action_combo.set_active(0)
        action_combo.connect("changed", self._on_action_changed, index)
        row_box.pack_start(action_combo, False, False, 0)

        # Remove button
        remove_button = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        remove_button.connect("clicked", self._on_remove_schedule_clicked, index)
        row_box.pack_start(remove_button, False, False, 0)

        return row_box

    def _on_enabled_toggled(self, check_button: Gtk.CheckButton, index: int) -> None:
        """Handle enabled checkbox toggle."""
        enabled = check_button.get_active()
        self._schedules[index]['enabled'] = enabled
        self._save_schedules()
        logger.debug(f"Schedule {index} enabled set to {enabled}")

    def _on_time_changed(self, entry: Gtk.Entry, index: int) -> None:
        """Handle time entry change."""
        time_str = entry.get_text()
        # Validate time format HH:MM
        import re
        if re.match(r'^[0-2][0-9]:[0-5][0-9]$', time_str):
            self._schedules[index]['time'] = time_str
            self._save_schedules()
        else:
            # Revert to previous valid time? For simplicity, we'll just not save invalid.
            pass

    def _on_day_toggled(self, check_button: Gtk.CheckButton, index: int, day: str) -> None:
        """Handle day checkbox toggle."""
        active = check_button.get_active()
        days = self._schedules[index].get('days', [])
        if active and day not in days:
            days.append(day)
        elif not active and day in days:
            days.remove(day)
        self._schedules[index]['days'] = days
        self._save_schedules()
        logger.debug(f"Schedule {index} day {day} set to {active}")

    def _on_action_changed(self, combo: Gtk.ComboBoxText, index: int) -> None:
        """Handle action combo box change."""
        active = combo.get_active()
        if 0 <= active < len(self._action_labels):
            action = self._action_labels[active]
            self._schedules[index]['action'] = action
            self._save_schedules()
            logger.debug(f"Schedule {index} action set to {action}")

    def _on_remove_schedule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle remove schedule button click."""
        del self._schedules[index]
        self._save_schedules()
        self._load_schedules_into_ui()
        logger.debug(f"Schedule {index} removed")

    def _on_add_schedule_clicked(self, button: Gtk.Button) -> None:
        """Handle add schedule button click."""
        # Show a dialog to add a new schedule
        dialog = Gtk.Dialog(title="Add Schedule",
                            transient_for=self.get_toplevel(),
                            flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dialog.set_default_size(400, 200)

        content_area = dialog.get_content_area()
        # Create a box for the dialog content
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_border_width(6)
        content_area.add(vbox)

        # Enabled checkbox
        enabled_cb = Gtk.CheckButton(label="Enabled")
        enabled_cb.set_active(True)
        vbox.pack_start(enabled_cb, False, False, 0)

        # Time entry
        hbox_time = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vbox.pack_start(hbox_time, False, False, 0)
        label_time = Gtk.Label(label="Time (HH:MM):")
        label_time.set_halign(Gtk.Align.START)
        hbox_time.pack_start(label_time, False, False, 0)
        time_entry = Gtk.Entry()
        time_entry.set_text("00:00")
        time_entry.set_width_chars(5)
        hbox_time.pack_start(time_entry, True, True, 0)

        # Days checkboxes
        vbox.pack_start(Gtk.Label(label="Days:"), False, False, 0)
        days_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        day_checkboxes = {}
        for day in day_names:
            cb = Gtk.CheckButton(label=day.capitalize()[:2])
            day_checkboxes[day] = cb
            days_box.pack_start(cb, False, False, 0)
        vbox.pack_start(days_box, False, False, 0)

        # Action combo box
        hbox_action = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vbox.pack_start(hbox_action, False, False, 0)
        label_action = Gtk.Label(label="Action:")
        label_action.set_halign(Gtk.Align.START)
        hbox_action.pack_start(label_action, False, False, 0)
        action_combo = Gtk.ComboBoxText()
        for action_label in self._action_labels:
            action_combo.append_text(action_label)
        if self._action_labels:
            action_combo.set_active(0)
        hbox_action.pack_start(action_combo, True, True, 0)

        vbox.show_all()

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            enabled = enabled_cb.get_active()
            time_str = time_entry.get_text()
            # Validate time
            import re
            if not re.match(r'^[0-2][0-9]:[0-5][0-9]$', time_str):
                time_str = "00:00"
            days = [day for day, cb in day_checkboxes.items() if cb.get_active()]
            action_index = action_combo.get_active()
            action = self._action_labels[action_index] if 0 <= action_index < len(self._action_labels) else self._action_labels[0]
            new_schedule = {
                'enabled': enabled,
                'time': time_str,
                'days': days,
                'action': action
            }
            self._schedules.append(new_schedule)
            self._save_schedules()
            self._load_schedules_into_ui()
            logger.debug(f"Added new schedule: {new_schedule}")
        dialog.destroy()

    def _save_schedules(self) -> None:
        """Save schedules to config file."""
        from config.loader import get_config_loader
        loader = get_config_loader()
        config = loader.load()
        # Build schedule lines
        lines = []
        for schedule in self._schedules:
            enabled_str = "on" if schedule['enabled'] else "off"
            time_str = schedule['time']
            days_str = ','.join(schedule['days']) if schedule['days'] else 'daily'  # default to daily if none
            action_str = schedule['action']
            line = f"{enabled_str}|{time_str}|{days_str}|{action_str}"
            lines.append(line)
        config['schedule'] = '\n'.join(lines)
        loader.save(config)
        logger.debug("Schedules saved to config")




class LauncherPanel(Gtk.Box):
    """Panel showing a list of launcher commands."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.set_halign(Gtk.Align.FILL)
        self.get_style_context().add_class("launcher-panel")

        self._build_launcher_list()
        # Do not show automatically; visibility will be controlled by popup window

    def _build_launcher_list(self):
        from config.loader import get_config_loader
        loader = get_config_loader()
        entries = loader.load_launcher_entries()

        if not entries:
            label = Gtk.Label(label="No commands yet — add them under [launcher]\nin the config (Label|command).")
            label.set_halign(Gtk.Align.CENTER)
            label.set_valign(Gtk.Align.CENTER)
            self.pack_start(label, True, True, 0)
            return

        for label, command in entries:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", self._on_launch_clicked, command)
            self.pack_start(btn, False, False, 0)

    def _on_launch_clicked(self, button, command):
        import subprocess
        import logging
        from utils.logger import get_logger
        logger = get_logger(__name__)

        logger.debug(f"Launching command: {command}")
        try:
            subprocess.Popen(command, shell=True, start_new_session=True)
        except Exception as e:
            logger.error(f"Failed to run launcher command {command!r}: {e}")

class LauncherButton(Gtk.Button):
    """Application launcher button."""

    def __init__(self):
        super().__init__()
        self.set_label("Launch Application")
        self.set_halign(Gtk.Align.FILL)
        self.connect("clicked", self._on_clicked)
        self._update_icon()

    def update(self) -> None:
        """Update the launcher button based on current state."""
        self._update_icon()

    def _update_icon(self) -> None:
        """Update the button icon based on current state."""
        state = get_state_manager().get_state()

        # Change icon based on what would be launched
        if state.get('playing', False):
            icon_name = "media-playback-pause-symbolic"
            self.set_label("Pause Playback")
        elif len(state.get('online_streams', [])) > 0:
            icon_name = "media-playback-start-symbolic"
            self.set_label("Launch Stream")
        else:
            icon_name = "application-default-icon"
            self.set_label("Launch Application")

        self.set_image(Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON))

    def _on_clicked(self, button: Gtk.Button) -> None:
        """Handle button click."""
        state = get_state_manager().get_state()

        if state.get('playing', False):
            # Toggle playback pause/resume
            logger.debug("Toggle playback pause/resume")
            # TODO: Implement playback toggle
        elif len(state.get('online_streams', [])) > 0:
            # Launch the first available stream
            logger.debug("Launching stream")
            # TODO: Implement stream launching
        else:
            # Launch default application
            logger.debug("Launching default application")
            # TODO: Implement application launching
class WeatherHeader(Gtk.Box):
    """Dynamic weather information bar with sliding views."""
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.set_halign(Gtk.Align.FILL)
        self.set_margin_start(6)
        self.set_margin_end(6)
        self.set_margin_top(4)
        self.set_margin_bottom(4)
        self.get_style_context().add_class("weather-header")

        # Stack for sliding between Today and Forecast
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_transition_duration(300)
        self.pack_start(self._stack, True, True, 0)

        # Today's View
        self._today_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._today_box.set_homogeneous(True)

        self._today_current = Gtk.Label()
        self._today_range = Gtk.Label()
        self._today_wind = Gtk.Label()
        self._today_precip = Gtk.Label()

        for lbl in [self._today_current, self._today_range, self._today_wind, self._today_precip]:
            lbl.set_halign(Gtk.Align.CENTER)
            lbl.set_valign(Gtk.Align.CENTER)
            self._today_box.pack_start(lbl, True, True, 0)

        self._stack.add_named(self._today_box, "today")

        # Forecast View
        self._forecast_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._forecast_box.set_homogeneous(True)
        self._forecast_labels = []
        for i in range(5):
            lbl = Gtk.Label()
            lbl.set_halign(Gtk.Align.CENTER)
            lbl.set_valign(Gtk.Align.CENTER)
            self._forecast_box.pack_start(lbl, True, True, 0)
            self._forecast_labels.append(lbl)

        self._stack.add_named(self._forecast_box, "forecast")

        # Toggle Button
        self._toggle_btn = Gtk.Button.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
        self._toggle_btn.set_relief(Gtk.ReliefStyle.NONE)
        self._toggle_btn.connect("clicked", self._on_toggle)
        self.pack_start(self._toggle_btn, False, False, 0)

        self._current_view = "today"
        self.show_all()

    def _on_toggle(self, _):
        if self._current_view == "today":
            self._stack.set_visible_child_name("forecast")
            self._current_view = "forecast"
            image = self._toggle_btn.get_child()
            if image and isinstance(image, Gtk.Image):
                image.set_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        else:
            self._stack.set_visible_child_name("today")
            self._current_view = "today"
            image = self._toggle_btn.get_child()
            if image and isinstance(image, Gtk.Image):
                image.set_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)

    def _get_condition_glyph(self, code: Optional[int]) -> str:
        """Map weather code to a sky-related glyph."""
        if code is None: return "🌡️"

        # Open-Meteo WMO Codes
        if code == 0: return "☀️" # Clear sky
        if code in (1, 2, 3): return "⛅" # Partly cloudy
        if code in (45, 48): return "🌫️" # Fog
        if code in (51, 53, 55, 56, 57): return "🌦️" # Drizzle
        if code in (61, 63, 65, 66, 67): return "🌧️" # Rain
        if code in (71, 73, 75, 77): return "❄️" # Snow
        if code in (80, 81, 82): return "🌦️" # Rain showers
        if code in (85, 86): return "🌨️" # Snow showers
        if code in (95, 96, 99): return "⛈️" # Thunderstorm
        return "🌡️"

    def update(self) -> None:
        state = get_state_manager().get_state()
        weather = state.get('weather_data', {})
        if not weather or weather.get('temperature') is None:
            self._today_current.set_text("N/A")
            return

        # Today's data
        temp = weather.get('temperature', 0)
        code = weather.get('weathercode')
        glyph = self._get_condition_glyph(code)

        max_temp = weather.get('temp_max')
        if max_temp is None:
            max_temp = temp + 2

        min_temp = weather.get('temp_min')
        if min_temp is None:
            min_temp = temp - 2
        wind_curr = weather.get('windspeed', 0)
        wind_max = weather.get('windspeed_max')
        if wind_max is None:
            wind_max = wind_curr + 5
        precip_chance = weather.get('precip_chance')
        if precip_chance is None:
            precip_chance = 0

        precip_mm = weather.get('precip_mm')
        if precip_mm is None:
            precip_mm = 0

        self._today_current.set_text(f"{glyph} {temp:.0f}°C")
        self._today_range.set_text(f"{max_temp:.0f}/{min_temp:.0f}°C")
        self._today_wind.set_text(f"{wind_curr:.0f}/{wind_max:.0f} km/h")
        self._today_precip.set_text(f"{precip_chance}% / {precip_mm}mm")

        # Forecast data (excluding today)
        forecast = weather.get('daily', [])
        for i in range(5):
            # Shift index by 1 to exclude today
            forecast_idx = i + 1
            if forecast_idx < len(forecast):
                day_data = forecast[forecast_idx]
                f_max = day_data.get('temperature_max', 15)
                f_min = day_data.get('temperature_min', 10)
                f_code = day_data.get('weathercode')
                f_glyph = self._get_condition_glyph(f_code)
                self._forecast_labels[i].set_text(f"{f_glyph} {f_max:.0f}/{f_min:.0f}°C")
            else:
                self._forecast_labels[i].set_text("--")


class SectionHeader(Gtk.Label):
    """Centered bold section header."""
    def __init__(self, title: str):
        super().__init__(label=title)
        self.set_halign(Gtk.Align.CENTER)
        self.get_style_context().add_class("section-header")
        self.set_margin_top(10)
        self.set_margin_bottom(5)

class LiveNowRow(Gtk.EventBox):
    """Row for a livestream item with indicator."""
    def __init__(self, stream_data: Dict[str, Any]):
        super().__init__()
        self.set_margin_start(6)
        self.set_margin_end(6)
        self.set_margin_top(2)
        self.set_margin_bottom(2)

        self._stream_data = stream_data

        # Create a horizontal box to hold the content
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add(hbox)

        self._indicator = Gtk.Image()
        self._indicator.set_pixel_size(10)
        hbox.pack_start(self._indicator, False, False, 0)

        self._label = Gtk.Label()
        self._label.set_halign(Gtk.Align.START)
        hbox.pack_start(self._label, True, True, 0)

        self._update_content(stream_data)

        # Connect to button release event (more reliable than press for clicks)
        self.connect("button-release-event", self._on_button_release)

    def _update_content(self, data: Dict[str, Any]) -> None:
        channel = data.get('channel', 'unknown')
        title = data.get('title', 'unknown')
        duration = data.get('duration', 'unknown')
        viewers = data.get('viewers', 0)
        self._indicator.set_from_icon_name("media-playback-start-symbolic", Gtk.IconSize.MENU)
        self._label.set_text(f"{channel} - {title} - {viewers} - {duration}")

    def _on_button_release(self, widget, event):
        """Handle mouse button releases on the livestream row."""
        if event.button == Gdk.BUTTON_PRIMARY:  # Left click
            self._on_left_click()
        elif event.button == Gdk.BUTTON_SECONDARY:  # Right click
            self._on_right_click()
        # Let the event propagate so the window can handle dragging/etc.
        return False

    def _on_left_click(self):
        """Handle left-click - open stream with streamlink and mpv."""
        from services.playback import get_playback_service
        from utils.logger import get_logger

        logger = get_logger(__name__)

        # Determine the correct URL for streamlink based on platform
        platform = self._stream_data.get('platform', '')
        url = self._stream_data.get('url', '')

        if not url:
            logger.warning("No URL available for livestream")
            return

        # For YouTube, we need to add /live to get the actual stream URL
        if platform == 'youtube':
            stream_url = f"{url}/live"
        else:
            # For Twitch and other platforms, the URL is already the stream URL
            stream_url = url

        logger.info(f"Opening livestream for playback: {stream_url}")

        # Start playback using the playback service
        playback_service = get_playback_service()
        if not playback_service.start_playback(stream_url):
            logger.error(f"Failed to start playback for URL: {stream_url}")

    def _on_right_click(self):
        """Handle right-click - do nothing (user does not want to remove from live list)."""
        from utils.logger import get_logger
        logger = get_logger(__name__)
        logger.debug(f"Right-clicked on livestream: {self._stream_data.get('platform', 'unknown')}://{self._stream_data.get('channel', 'unknown')} - no action taken")

class FeedItemRow(Gtk.EventBox):
    """Row for a feed item."""
    def __init__(self, item_data: Dict[str, Any]):
        super().__init__()
        self.set_margin_start(6)
        self.set_margin_end(6)
        self.set_margin_top(2)
        self.set_margin_bottom(2)

        self._item_data = item_data

        # Create a horizontal box to hold the content
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add(hbox)

        self._label = Gtk.Label()
        self._label.set_halign(Gtk.Align.START)
        self._label.set_ellipsize(Pango.EllipsizeMode.END)
        hbox.pack_start(self._label, True, True, 0)

        title = item_data.get('title', '(untitled)')
        self._label.set_text(title)

        # Connect to button release event (more reliable than press for clicks)
        self.connect("button-release-event", self._on_button_release)

    def _on_button_release(self, widget, event):
        """Handle mouse button releases on the feed item row."""
        if event.button == Gdk.BUTTON_PRIMARY:  # Left click
            self._on_left_click()
        elif event.button == Gdk.BUTTON_SECONDARY:  # Right click
            self._on_right_click()
        # Let the event propagate so the window can handle dragging/etc.
        return False

    def _on_left_click(self):
        """Handle left-click - open article in browser and mark as read."""
        import webbrowser
        url = self._item_data.get('link', '')
        if url:
            webbrowser.open(url)
            logger.debug(f"Opened URL in browser: {url}")
        # Also mark as read when opening article
        self._mark_item_as_read()

    def _on_right_click(self):
        """Handle right-click - mark item as read."""
        self._mark_item_as_read()

    def _mark_item_as_read(self):
        """Mark the item as read (update state and trigger UI refresh)."""
        from core.state import get_state_manager
        from core.event_bus import get_event_bus

        item_id = self._item_data.get('id')
        if not item_id:
            return

        state_manager = get_state_manager()
        event_bus = get_event_bus()

        def updater(state_dict):
            # Mark item as seen (which effectively marks it as read)
            seen = state_dict.get('seen', {})
            seen[item_id] = time_module.time()
            state_dict['seen'] = seen

            # Remove from unread items
            unread = state_dict.get('unread', [])
            unread = [item for item in unread if item.get('id') != item_id]
            state_dict['unread'] = unread

            return state_dict

        state_manager.update_state(updater)
        state_manager.save_to_disk()

        # Notify that items have been updated
        event_bus.publish('feed_update', {})
        logger.debug(f"Marked item as read: {self._item_data.get('title', 'Unknown')}")

class FooterBar(Gtk.Box):
    """Bottom button bar."""
    def __init__(self, app_instance):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.set_halign(Gtk.Align.FILL)
        self.set_border_width(4)
        self.get_style_context().add_class("footer-bar")
        self.app = app_instance

        buttons = [
            ("Launch", self._on_launch),
            ("Timer", self._on_timer),
            ("Edit config", self._on_config),
            ("Refresh", self._on_refresh),
            ("Mark all read", self._on_mark_read),
            ("Quit", self._on_quit),
        ]

        for label, callback in buttons:
            btn = Gtk.Button(label=label)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.connect("clicked", callback)
            self.pack_start(btn, True, True, 0)

        self.show_all()

    def set_app(self, app_instance):
        """Update the application reference."""
        self.app = app_instance

    def _on_launch(self, _):
        get_event_bus().publish('toggle_launcher_controls', {})
    def _on_timer(self, _):
        get_event_bus().publish('toggle_timer_controls', {})
    def _on_config(self, _):
        import subprocess
        from config.schema import CONFIG_FILE
        logger.debug(f"Footer: Opening config file: {CONFIG_FILE}")
        try:
            subprocess.Popen(['mousepad', CONFIG_FILE])
        except Exception as e:
            logger.error(f"Footer: Failed to open config file: {e}")
    def _on_refresh(self, _): 
        get_event_bus().publish('manual_refresh', {})
    def _on_mark_read(self, _):
        """Mark all feed items as read (update state and trigger UI refresh)."""
        from core.state import get_state_manager
        from core.event_bus import get_event_bus
        import time as time_module

        state_manager = get_state_manager()
        event_bus = get_event_bus()

        def updater(state_dict):
            # Get all currently unread items
            unread = state_dict.get('unread', [])
            if not unread:
                return state_dict

            # Mark all currently unread items as seen
            seen = state_dict.get('seen', {})
            now = time_module.time()
            for item in unread:
                item_id = item.get('id')
                if item_id:
                    seen[item_id] = now
            state_dict['seen'] = seen

            # Clear all unread items
            state_dict['unread'] = []

            return state_dict

        state_manager.update_state(updater)
        state_manager.save_to_disk()

        # Notify that items have been updated to refresh the popup
        event_bus.publish('feed_update', {})
        logger.debug("Footer: Marked all feed items as read")
    def _on_quit(self, _):
        logger.debug(f"Footer: Quit clicked, app is {self.app}")
        if self.app:
            logger.debug("Footer: Calling app.stop()")
            self.app.stop()
        else:
            logger.warning("Footer: Quit clicked but app reference is missing")
