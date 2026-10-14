"""
Popup window implementation for void-tray.

Displays unread feed items, weather information, update notifications,
livestream status, and timer controls in a GTK window.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Pango
import logging
import threading
from typing import Optional, Callable, List, Dict, Any, Tuple
from core.state import get_state_manager
from core.event_bus import get_event_bus
from utils.logger import get_logger
from .widgets import (
    WeatherHeader, SectionHeader, LiveNowRow,
    FeedItemRow, FooterBar, TimerControls, LauncherButton
)

logger = get_logger(__name__)

# Resize threshold in pixels
_RESIZE_THRESHOLD = 10


def _get_resize_direction(x: int, y: int, width: int, height: int) -> Optional[str]:
    """Determine which edge/corner the point is near for resizing.

    Returns a string indicating the direction, or None if not near any edge.
    """
    # Check if point is near horizontal edges
    near_top = y <= _RESIZE_THRESHOLD
    near_bottom = y >= (height - _RESIZE_THRESHOLD)

    # Check if point is near vertical edges
    near_left = x <= _RESIZE_THRESHOLD
    near_right = x >= (width - _RESIZE_THRESHOLD)

    # Determine resize direction
    if near_top and near_left:
        return 'top-left'
    elif near_top and near_right:
        return 'top-right'
    elif near_bottom and near_left:
        return 'bottom-left'
    elif near_bottom and near_right:
        return 'bottom-right'
    elif near_top:
        return 'top'
    elif near_bottom:
        return 'bottom'
    elif near_left:
        return 'left'
    elif near_right:
        return 'right'
    else:
        return None


class PopupWindow:
    """
    Popup window for void-tray application.

    Displays:
    - Header with application title and controls
    - Unread feed items with rich formatting
    - Weather information bar
    - Livestream status indicators
    - Update notifications
    - Timer controls
    - Application launcher
    """

    def __init__(self):
        self.state_manager = get_state_manager()
        self.event_bus = get_event_bus()
        self._app = None  # Reference to application instance
        self._window: Optional[Gtk.Window] = None
        self._main_box: Optional[Gtk.Box] = None
        self._scrolled_window: Optional[Gtk.ScrolledWindow] = None
        self._item_list_box: Optional[Gtk.Box] = None
        self._header_row: Optional[HeaderRow] = None
        self._weather_bar: Optional[WeatherBar] = None
        self._timer_controls: Optional[TimerControls] = None
        self._launcher_button: Optional[LauncherButton] = None
        self._update_banner: Optional[Gtk.Label] = None
        self._livestream_banner: Optional[Gtk.Label] = None

        # Dragging state
        self._dragging = False
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._window_offset_x = 0
        self._window_offset_y = 0

        # Resizing state
        self._resizing = False
        self._resize_start_x = 0
        self._resize_start_y = 0
        self._resize_start_width = 0
        self._resize_start_height = 0
        self._resize_direction = None  # None, 'left', 'right', 'top', 'bottom', 'top-left', etc.

        logger.debug("PopupWindow initialized")

        # Subscribe to relevant events
        self._setup_event_subscriptions()

        # Don't create window until needed
        self._window_created = False

    def _setup_event_subscriptions(self) -> None:
        """Subscribe to state change events that affect the popup window."""
        self.event_bus.subscribe('state_change', self._on_state_change)
        self.event_bus.subscribe('feed_update', self._on_feed_update)
        self.event_bus.subscribe('new_items', self._on_new_items)
        self.event_bus.subscribe('update_available', self._on_update_available)
        self.event_bus.subscribe('weather_update', self._on_weather_update)
        self.event_bus.subscribe('severe_weather_update', self._on_severe_weather_update)
        self.event_bus.subscribe('livestream_online', self._on_livestream_online)
        self.event_bus.subscribe('livestream_offline', self._on_livestream_offline)
        self.event_bus.subscribe('playback_start', self._on_playback_start)
        self.event_bus.subscribe('playback_stop', self._on_playback_stop)
        self.event_bus.subscribe('timer_tick', self._on_timer_tick)
        self.event_bus.subscribe('toggle_timer_controls', self._on_toggle_timer)
        self.event_bus.subscribe('toggle_launcher_controls', self._on_toggle_launcher)

    def show(self) -> None:
        """Show the popup window."""
        if not self._window_created:
            self._create_window()

        # Ensure footer has the app reference if it was set after window creation
        if hasattr(self, '_footer_bar') and self._footer_bar and hasattr(self, '_app'):
            self._footer_bar.set_app(self._app)

        if self._window:
            self._window.present()
            self._update_content()
            logger.debug("Popup window shown")

    def hide(self) -> None:
        """Hide the popup window."""
        if self._window:
            self._window.hide()
            logger.debug("Popup window hidden")

    def _create_window(self) -> None:
        """Create the GTK popup window."""
        self._window = Gtk.Window(Gtk.WindowType.POPUP)
        self._window.set_title("void-tray")
        self._window.set_resizable(False)
        self._window.set_destroy_with_parent(True)

        # Set window properties for popup behavior
        self._window.set_decorated(False)  # No title bar
        self._window.set_skip_taskbar_hint(True)
        self._window.set_skip_pager_hint(True)
        # Make window resizable
        self._window.set_resizable(True)

        # Connect events
        self._window.connect("focus-out-event", self._on_focus_out)
        self._window.connect("key-press-event", self._on_key_press)
        self._window.connect("configure-event", self._on_configure_event)
        # Enable dragging
        self._window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                                Gdk.EventMask.BUTTON_RELEASE_MASK |
                                Gdk.EventMask.POINTER_MOTION_MASK)
        self._window.connect("button-press-event", self._on_button_press)
        self._window.connect("button-release-event", self._on_button_release)
        self._window.connect("motion-notify-event", self._on_motion_notify)

        # Restore saved position
        saved_pos = self._get_saved_popup_position()
        if saved_pos:
            self._window.move(*saved_pos)

        # Restore saved size
        saved_size = self._get_saved_popup_size()
        if saved_size:
            self._window.resize(*saved_size)

        # Create main container
        self._main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._main_box.set_border_width(0)
        self._window.add(self._main_box)

        # Create header
        self._weather_header = WeatherHeader()
        self._main_box.pack_start(self._weather_header, False, False, 0)

        # Create scrolled window for items
        self._scrolled_window = Gtk.ScrolledWindow()
        self._scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled_window.set_max_content_height(400)
        self._main_box.pack_start(self._scrolled_window, True, True, 0)

        # Create item list container
        self._item_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._scrolled_window.add(self._item_list_box)

        # Create timer controls
        self._timer_controls = TimerControls()
        self._main_box.pack_start(self._timer_controls, False, False, 0)

        # Create launcher panel
        from .widgets import LauncherPanel
        self._launcher_panel = LauncherPanel()
        self._main_box.pack_start(self._launcher_panel, False, False, 0)

        # Create footer
        self._footer_bar = FooterBar(self._app if hasattr(self, '_app') else None)
        # Since self._app is set later or handled by the app instance,
        # we might need to pass it properly. In current code, the app
        # instance is usually passed to the popup or it's a singleton.
        # For now, let's just create it.
        self._main_box.pack_start(self._footer_bar, False, False, 0)

        # Show all widgets
        self._window.show_all()
        # Ensure launcher panel starts hidden
        self._launcher_panel.set_visible(False)
        # Ensure timer controls start hidden
        self._timer_controls.set_visible(False)
        self._window_created = True

        logger.debug("Popup window created")

    def _on_toggle_timer(self, data: Any = None) -> None:
        """Toggle visibility of timer controls."""
        if self._timer_controls:
            if self._timer_controls.get_visible():
                self._timer_controls.hide()
                new_visible = False
            else:
                self._timer_controls.show_all()
                new_visible = True
            logger.debug(f"Timer controls visibility toggled to: {new_visible}")

    def _on_toggle_launcher(self, data: Any = None) -> None:
        """Toggle visibility of launcher panel."""
        if self._launcher_panel:
            if self._launcher_panel.get_visible():
                self._launcher_panel.hide()
                new_visible = False
            else:
                self._launcher_panel.show_all()
                new_visible = True
            logger.debug(f"Launcher panel visibility toggled to: {new_visible}")


    def _on_focus_out(self, widget: Gtk.Widget, event: Gdk.EventFocus) -> bool:
        """Handle focus out event - hide popup when focus is lost."""
        self.hide()
        return True  # Event handled

    def _on_key_press(self, widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        """Handle key press events."""
        # Escape key hides popup
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def _on_button_press(self, widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        """Handle mouse button press to start dragging or resizing."""
        if event.button == Gdk.BUTTON_PRIMARY:  # Left mouse button
            # Get window size and position
            window_x, window_y = self._window.get_position()
            width, height = self._window.get_size()

            # Check if point is near an edge for resizing
            direction = _get_resize_direction(event.x, event.y, width, height)
            if direction is not None:
                # Start resizing
                self._resizing = True
                self._resize_direction = direction
                self._resize_start_x = event.x_root
                self._resize_start_y = event.y_root
                self._resize_start_width = width
                self._resize_start_height = height
                return True  # Event handled
            else:
                # Start dragging
                self._dragging = True
                self._drag_start_x = event.x_root
                self._drag_start_y = event.y_root
                self._window_offset_x = window_x - self._drag_start_x
                self._window_offset_y = window_y - self._drag_start_y
                return True  # Event handled
        return False

    def _on_button_release(self, widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        """Handle mouse button release to stop dragging or resizing."""
        if event.button == Gdk.BUTTON_PRIMARY:
            # Save position/size when drag/resize ends
            self._save_popup_position()
            self._save_popup_size()
            self._dragging = False
            self._resizing = False
            self._resize_direction = None
            return True
        return False

    def _on_configure_event(self, widget: Gtk.Widget, event: Gdk.EventConfigure) -> bool:
        """Handle window configure event (moved or resized)."""
        self._save_popup_position()
        self._save_popup_size()
        return True  # Event handled

    def _update_window_cursor(self, direction: Optional[str]) -> None:
        """Update the window cursor based on resize direction."""
        cursor_name = 'default'
        if direction == 'left':
            cursor_name = 'nw-resize'
        elif direction == 'right':
            cursor_name = 'ne-resize'
        elif direction == 'top':
            cursor_name = 'n-resize'
        elif direction == 'bottom':
            cursor_name = 's-resize'
        elif direction == 'top-left':
            cursor_name = 'nw-resize'
        elif direction == 'top-right':
            cursor_name = 'ne-resize'
        elif direction == 'bottom-left':
            cursor_name = 'sw-resize'
        elif direction == 'bottom-right':
            cursor_name = 'se-resize'

        # In GTK 3, you set the cursor on the window's GDK window
        window = self._window
        if window:
            display = window.get_display()
            cursor = Gdk.Cursor.new_from_name(display, cursor_name)
            gdk_window = window.get_window()
            if gdk_window:
                gdk_window.set_cursor(cursor)

    def _on_motion_notify(self, widget: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        """Handle mouse motion to move or resize window."""
        if self._dragging:
            # Handle dragging (move window)
            x_root = event.x_root
            y_root = event.y_root
            self._window.move(x_root + self._window_offset_x, y_root + self._window_offset_y)
            return True
        elif self._resizing:
            # Handle resizing
            x_root = event.x_root
            y_root = event.y_root

            # Calculate delta from start position
            dx = x_root - self._resize_start_x
            dy = y_root - self._resize_start_y

            # Calculate new size based on direction
            new_width = self._resize_start_width
            new_height = self._resize_start_height

            if 'left' in self._resize_direction:
                new_width = self._resize_start_width - dx
            if 'right' in self._resize_direction:
                new_width = self._resize_start_width + dx
            if 'top' in self._resize_direction:
                new_height = self._resize_start_height - dy
            if 'bottom' in self._resize_direction:
                new_height = self._resize_start_height + dy

            # Apply minimum size constraints
            new_width = max(200, new_width)  # Minimum width
            new_height = max(150, new_height)  # Minimum height

            # Resize the window
            self._window.resize(new_width, new_height)
            return True
        else:
            # Update cursor based on position when not dragging/resizing
            width, height = self._window.get_size()
            direction = _get_resize_direction(event.x, event.y, width, height)
            self._update_window_cursor(direction)
            return False

    def _update_content(self) -> None:
        """Update all content in the popup window based on current state."""
        if not self._window_created:
            return

        # Update weather header
        if self._weather_header:
            self._weather_header.update()

        # Update feed items and sections
        self._update_feed_items()

        logger.debug("Popup window content updated")

    def _update_feed_items(self) -> None:
        """Update the list of feed items and sections."""
        if not self._item_list_box:
            return

        # Clear existing items
        for child in self._item_list_box.get_children():
            self._item_list_box.remove(child)

        state = self.state_manager.get_state()

        # 1. "Live now" section
        online_streams = state.get('online_streams', [])
        if online_streams:
            header = SectionHeader("Live now")
            self._item_list_box.pack_start(header, False, False, 0)

            for stream in online_streams:
                row = LiveNowRow(stream)
                self._item_list_box.pack_start(row, False, False, 0)

            # Add a divider after Live now
            separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            self._item_list_box.pack_start(separator, False, False, 0)

        # 2. Grouped RSS feeds
        unread_items = state.get('unread', [])
        if unread_items:
            # Group items by feed title
            feeds_dict = {}
            for item in unread_items:
                feed_title = item.get('feed_title', 'Unknown feed')
                feeds_dict.setdefault(feed_title, []).append(item)

            for feed_title, items in feeds_dict.items():
                # Create clickable section header for the feed
                event_box = Gtk.EventBox()
                event_box.set_visible_window(False)  # Not necessary but good practice
                header = SectionHeader(feed_title)
                event_box.add(header)
                # Connect left-click event to mark all items of this feed as seen
                event_box.connect("button-release-event", self._on_feed_title_clicked, feed_title)
                self._item_list_box.pack_start(event_box, False, False, 0)

                # Items for this feed
                for item in items[:20]:  # Limit items per feed
                    row = FeedItemRow(item)
                    self._item_list_box.pack_start(row, False, False, 0)

                # Add a divider between feeds
                separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                self._item_list_box.pack_start(separator, False, False, 0)

        elif not online_streams:
            # Placeholder if absolutely nothing is showing
            placeholder = Gtk.Label(label="No updates available")
            placeholder.set_halign(Gtk.Align.CENTER)
            placeholder.set_valign(Gtk.Align.CENTER)
            self._item_list_box.pack_start(placeholder, True, True, 0)

        self._item_list_box.show_all()
        logger.debug("Updated feed items with sections")

    def _update_update_banner(self) -> None:
        """Update the update notification banner."""
        if not self._update_banner:
            return

        state = self.state_manager.get_state()
        if state.get('update_available', False):
            count = state.get('update_count', 0)
            reboot_required = state.get('reboot_required', False)

            text = f"{count} update{'s' if count != 1 else ''} available"
            if reboot_required:
                text += " (reboot required)"

            self._update_banner.set_text(text)
            self._update_banner.set_visible(True)
            self._update_banner.get_style_context().add_class("update-banner")
        else:
            self._update_banner.set_visible(False)
            self._update_banner.get_style_context().remove_class("update-banner")

    def _update_livestream_banner(self) -> None:
        """Update the livestream status banner."""
        if not self._livestream_banner:
            return

        state = self.state_manager.get_state()
        online_streams = state.get('online_streams', [])

        if online_streams:
            stream_names = [s.get('channel', 'unknown') for s in online_streams[:3]]
            if len(online_streams) > 3:
                stream_names.append(f"+{len(online_streams)-3} more")

            text = f"Live: {', '.join(stream_names)}"
            self._livestream_banner.set_text(text)
            self._livestream_banner.set_visible(True)
            self._livestream_banner.get_style_context().add_class("livestream-banner")
        else:
            self._livestream_banner.set_visible(False)
            self._livestream_banner.get_style_context().remove_class("livestream-banner")

    def _on_state_change(self, data: Any = None) -> None:
        """Handle general state change events."""
        GLib.idle_add(self._update_content)

    def _on_feed_update(self, data: Any = None) -> None:
        """Handle feed update events."""
        GLib.idle_add(self._update_feed_items)
        GLib.idle_add(self._update_content)  # Update header/badge as well

    def _on_new_items(self, data: Any = None) -> None:
        """Handle new items events."""
        GLib.idle_add(self._update_feed_items)
        # Flash or highlight new items if needed

    def _on_update_available(self, data: Any = None) -> None:
        """Handle update available events."""
        GLib.idle_add(self._update_update_banner)

    def _on_weather_update(self, data: Any = None) -> None:
        """Handle weather update events."""
        if self._weather_bar:
            GLib.idle_add(self._weather_bar.update)

    def _on_severe_weather_update(self, data: Any = None) -> None:
        """Handle severe weather update events."""
        if self._weather_bar:
            GLib.idle_add(self._weather_bar.update)

    def _on_feed_title_clicked(self, widget, event, feed_title):
        """Handle left-click on a feed title to mark all its items as seen."""
        if event.button == Gdk.BUTTON_PRIMARY:  # Left click
            self._mark_feed_items_as_seen(feed_title)
            return False  # Allow event to propagate for drag/resize handling
        return False

    def _mark_feed_items_as_seen(self, feed_title: str) -> None:
        """Mark all items of the specified feed as seen."""
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

            # Find items belonging to the specified feed
            feed_items = [item for item in unread if item.get('feed_title') == feed_title]
            if not feed_items:
                return state_dict

            # Mark these items as seen
            seen = state_dict.get('seen', {})
            now = time_module.time()
            for item in feed_items:
                item_id = item.get('id')
                if item_id:
                    seen[item_id] = now

            # Remove these items from unread list
            unread = [item for item in unread if item.get('feed_title') != feed_title]
            state_dict['unread'] = unread
            state_dict['seen'] = seen

            return state_dict

        state_manager.update_state(updater)
        state_manager.save_to_disk()

        # Notify that items have been updated to refresh the popup
        event_bus.publish('feed_update', {})
        logger.debug(f"Marked all items as seen for feed: {feed_title}")

    def _on_livestream_online(self, data: Any = None) -> None:
        """Handle livestream online events."""
        GLib.idle_add(self._update_content)

    def _on_livestream_offline(self, data: Any = None) -> None:
        """Handle livestream offline events."""
        GLib.idle_add(self._update_content)

    def _on_playback_start(self, data: Any = None) -> None:
        """Handle playback start events."""
        # Update header or other elements if needed
        pass

    def _on_playback_stop(self, data: Any = None) -> None:
        """Handle playback stop events."""
        # Update header or other elements if needed
        pass

    def _on_timer_tick(self, data: Any = None) -> None:
        """Handle timer tick events."""
        if self._timer_controls:
            GLib.idle_add(self._timer_controls.update)

    def _get_saved_popup_position(self) -> Optional[Tuple[int, int]]:
        """Get the saved popup position from state."""
        state = self.state_manager.get_state()
        pos = state.get('popup_position')
        if pos and isinstance(pos, list) and len(pos) == 2:
            return tuple(pos)  # type: ignore
        return None

    def _save_popup_position(self) -> None:
        """Save the current popup window position to state."""
        if self._window:
            x, y = self._window.get_position()
            def updater(state_dict):
                state_dict['popup_position'] = [x, y]
            self.state_manager.update_state(updater)

    def _get_saved_popup_size(self) -> Optional[Tuple[int, int]]:
        """Get the saved popup size from state."""
        state = self.state_manager.get_state()
        size = state.get('popup_size')
        if size and isinstance(size, list) and len(size) == 2:
            return tuple(size)  # type: ignore
        return None

    def _save_popup_size(self) -> None:
        """Save the current popup window size to state."""
        if self._window:
            width, height = self._window.get_size()
            def updater(state_dict):
                state_dict['popup_size'] = [width, height]
            self.state_manager.update_state(updater)

    def set_app(self, app) -> None:
        """Set the application reference and propagate it to children."""
        self._app = app
        # We can't propagate to footer_bar yet if window isn't created.
        # Instead, we'll ensure it's set during _create_window.
        if hasattr(self, '_footer_bar') and self._footer_bar:
            self._footer_bar.set_app(app)


# Global popup window instance
_popup_window: Optional[PopupWindow] = None


def get_popup_window() -> PopupWindow:
    """Get the global popup window instance."""
    global _popup_window
    if _popup_window is None:
        _popup_window = PopupWindow()
    return _popup_window