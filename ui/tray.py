"""
System tray icon implementation for void-tray.

Provides GTK StatusIcon with left-click (toggle popup), right-click (context menu),
and tooltip updates based on application state.
"""

import gi
import math
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk
import logging
import threading
from typing import Optional, Callable
from core.state import get_state_manager
from core.event_bus import get_event_bus
from utils.logger import get_logger

logger = get_logger(__name__)


class TrayIcon:
    """
    System tray icon for void-tray application.

    Features:
    - Left-click: Toggle popup window visibility
    - Right-click: Context menu with quit option
    - Tooltip: Shows unread count, update status, weather, etc.
    - Icons: Changes based on application state (normal, updates available, etc.)
    """

    def __init__(self):
        self.state_manager = get_state_manager()
        self.event_bus = get_event_bus()
        self._icon: Optional[Gtk.StatusIcon] = None
        self._popup_visible = False
        _popup_window: Optional['PopupWindow'] = None  # Forward reference
        self._menu: Optional[Gtk.Menu] = None
        self._tooltip_text = ""
        self._app: Optional['VoidTrayApplication'] = None  # Reference to application instance

        logger.debug("TrayIcon initialized")

        # Subscribe to relevant events
        self._setup_event_subscriptions()

        # Create the tray icon
        self._create_icon()

    def _setup_event_subscriptions(self) -> None:
        """Subscribe to state change events that affect the tray icon."""
        self.event_bus.subscribe('state_change', self._on_state_change)
        self.event_bus.subscribe('feed_update', self._on_feed_update)
        self.event_bus.subscribe('update_available', self._on_update_available)
        self.event_bus.subscribe('weather_update', self._on_weather_update)
        self.event_bus.subscribe('severe_weather_update', self._on_severe_weather_update)
        self.event_bus.subscribe('livestream_online', self._on_livestream_online)
        self.event_bus.subscribe('livestream_offline', self._on_livestream_offline)
        self.event_bus.subscribe('playback_start', self._on_playback_start)
        self.event_bus.subscribe('playback_stop', self._on_playback_stop)

    def _create_icon(self) -> None:
        """Create the GTK status icon."""
        self._icon = Gtk.StatusIcon()

        # Set initial icon and tooltip
        self._update_icon()
        self._update_tooltip()

        # Connect event handlers
        self._icon.connect('activate', self._on_left_click)
        self._icon.connect('popup-menu', self._on_right_click)
        self._icon.connect('query-tooltip', self._on_query_tooltip)

        # Enable tooltip
        self._icon.set_has_tooltip(True)

        logger.debug("Tray icon created")

    def _on_left_click(self, icon: Gtk.StatusIcon) -> None:
        """Handle left-click on tray icon."""
        logger.debug("Tray icon left-clicked")
        self.toggle_popup()

    def _on_right_click(self, icon: Gtk.StatusIcon, button: int, time: int) -> None:
        """Handle right-click on tray icon."""
        logger.debug("Tray icon right-clicked")
        self._show_context_menu(button, time)

    def _on_query_tooltip(self, icon: Gtk.StatusIcon, x: int, y: int,
                         keyboard_tooltip: bool, tooltip: Gtk.Tooltip) -> bool:
        """Handle tooltip query."""
        tooltip.set_text(self._tooltip_text)
        return True  # Indicate that we handled the tooltip

    def _show_context_menu(self, button: int, time: int) -> None:
        """Show the context menu."""
        if self._menu is None:
            self._menu = self._create_context_menu()

        self._menu.popup(None, None, None, None, button, time)

    def _create_context_menu(self) -> Gtk.Menu:
        """Create the context menu for the tray icon."""
        menu = Gtk.Menu()

        # Popup menu item
        popup_item = Gtk.MenuItem(label="Show Popup")
        popup_item.connect("activate", lambda _: self.toggle_popup())
        menu.append(popup_item)

        # Separator
        separator = Gtk.SeparatorMenuItem()
        menu.append(separator)

        # Quit menu item
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: self._app.stop())
        menu.append(quit_item)

        menu.show_all()
        return menu

    def toggle_popup(self) -> None:
        """Toggle the popup window visibility."""
        if self._popup_visible:
            self.hide_popup()
        else:
            self.show_popup()

    def show_popup(self) -> None:
        """Show the popup window."""
        if self._popup_visible:
            return

        # Import here to avoid circular imports
        from .popup_window import get_popup_window

        if not hasattr(self, '_popup_window') or self._popup_window is None:
            self._popup_window = get_popup_window()

        self._popup_window.show()
        self._popup_visible = True
        logger.debug("Popup window shown")

    def hide_popup(self) -> None:
        """Hide the popup window."""
        if not self._popup_visible:
            return

        if hasattr(self, '_popup_window') and self._popup_window is not None:
            self._popup_window.hide()

        self._popup_visible = False
        logger.debug("Popup window hidden")

    def _update_icon(self) -> None:
        """Update the tray icon based on current state."""
        if self._icon is None:
            return

        try:
            # Determine which icon to show based on state
            icon_name = self._get_icon_name()

            # Try to load from theme first
            try:
                base_pixbuf = Gtk.IconTheme.get_default().load_icon(
                    icon_name, 22, 0
                )

                # Create a composite pixbuf with temperature overlay
                pixbuf = self._create_composite_icon(base_pixbuf)
                self._icon.set_from_pixbuf(pixbuf)
                return
            except:
                pass

            # Fallback to built-in icons or stock icons
            base_pixbuf = self._icon.render_icon_pixbuf(Gtk.STOCK_HOME, Gtk.IconSize.MENU)
            pixbuf = self._create_composite_icon(base_pixbuf)
            self._icon.set_from_pixbuf(pixbuf)

        except Exception as e:
            logger.debug(f"Error updating tray icon: {e}")
            # Fallback to a simple icon
            try:
                base_pixbuf = self._icon.render_icon_pixbuf(Gtk.STOCK_HOME, Gtk.IconSize.MENU)
                pixbuf = self._create_composite_icon(base_pixbuf)
                self._icon.set_from_pixbuf(pixbuf)
            except:
                pass  # If all else fails, keep existing icon

    def _create_composite_icon(self, base_pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf:
        """Create a composite icon with temperature overlay."""
        # Get temperature from state
        state = self.state_manager.get_state()
        weather_data = state.get('weather_data', {})
        temperature = weather_data.get('temperature')

        # If no temperature data, return base icon as-is
        if temperature is None:
            return base_pixbuf if base_pixbuf else self._create_fallback_icon()

        # Format temperature string with Pango markup to make degree symbol smaller
        if isinstance(temperature, float):
            # Round half up: 8.4 -> 8, 8.5 -> 9, 8.6 -> 9
            if temperature >= 0:
                rounded_temp = math.floor(temperature + 0.5)
            else:
                rounded_temp = math.ceil(temperature - 0.5)
            # Use markup: number at normal size, degree symbol as small as possible
            temp_str = f"{int(rounded_temp)}<span size='xx-small'>°</span>"
        else:
            temp_str = f"{int(temperature)}<span size='xx-small'>°</span>"

        # Try to render the overlay
        try:
            composite_pixbuf = self._render_text_overlay(base_pixbuf, temp_str)
            if composite_pixbuf:
                return composite_pixbuf
        except Exception as e:
            logger.debug(f"Failed to create composite icon: {e}")

        if base_pixbuf:
            return base_pixbuf.copy()
        else:
            # Create a visible fallback icon when base_pixbuf is None
            return self._create_fallback_icon()

    def _create_fallback_icon(self) -> GdkPixbuf.Pixbuf:
        """Create a visible fallback icon when base_pixbuf is None."""
        pixbuf = GdkPixbuf.Pixbuf(GdkPixbuf.Colorspace.RGB, True, 8, 22, 22)
        # Fill with gray so it's visible (ARGB: 0xFF808080)
        pixbuf.fill(0xff808080)
        return pixbuf

    def _create_icon_with_overlay(self, base_pixbuf: GdkPixbuf.Pixbuf, temperature_text: str) -> GdkPixbuf.Pixbuf:
        """Create an icon showing only the temperature text (no background icon)."""
        # Import Pango and Cairo inside the method to avoid issues if not available
        import gi
        gi.require_version('Pango', '1.0')
        gi.require_version('PangoCairo', '1.0')
        from gi.repository import Pango, PangoCairo
        import cairo

        # Create a surface for the final icon
        width = 22  # Standard tray icon size
        height = 22
        surface = cairo.ImageSurface(cairo.Format.ARGB32, width, height)
        context = cairo.Context(surface)

        # Start with fully transparent background
        context.set_source_rgba(0, 0, 0, 0)
        context.rectangle(0, 0, width, height)
        context.fill()

        # Separate the number and the degree symbol
        import re
        match = re.match(r"([^<]+)(.*)", temperature_text)
        if match:
            number_text = match.group(1)
            degree_text = "°"
        else:
            number_text = temperature_text
            degree_text = ""

        # Prepare layouts
        num_layout = PangoCairo.create_layout(context)
        num_layout.set_text(number_text, -1)
        deg_layout = PangoCairo.create_layout(context)
        deg_layout.set_text(degree_text, -1)

        # Adaptive font sizing for the number, considering the degree symbol too
        font_size = 18  # Lower starting point to avoid "too big" look
        min_font_size = 8
        padding = 4     # Increased padding for more breathing room
        max_width = width - 2 * padding

        while font_size >= min_font_size:
            # Set number font
            num_font_desc = Pango.FontDescription(f"Sans Bold {font_size}")
            num_layout.set_font_description(num_font_desc)
            _, num_rect = num_layout.get_pixel_extents()

            # Degree symbol font size is proportional to number size
            deg_size = max(6, int(font_size * 0.5))
            deg_font_desc = Pango.FontDescription(f"Sans Bold {deg_size}")
            deg_layout.set_font_description(deg_font_desc)
            _, deg_rect = deg_layout.get_pixel_extents()

            if (num_rect.width + deg_rect.width) <= max_width:
                break
            font_size -= 1

        num_w, num_h = num_rect.width, num_rect.height
        deg_w, deg_h = deg_rect.width, deg_rect.height

        # Total width for right-alignment
        total_w = num_w + deg_w
        x_start = width - total_w - padding

        # Draw text with White fill (no outline)
        context.set_source_rgb(1, 1, 1)

        # Draw number - vertically centered
        context.save()
        context.translate(x_start, (height - num_h) / 2)
        PangoCairo.show_layout(context, num_layout)
        context.restore()

        # Draw degree symbol - top aligned
        context.save()
        deg_y = (height - num_h) / 2
        context.translate(x_start + num_w, deg_y)
        PangoCairo.show_layout(context, deg_layout)
        context.restore()

        # Convert surface back to GdkPixbuf
        pb = Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)
        return pb

    def _render_text_overlay(self, base_pixbuf: GdkPixbuf.Pixbuf, text: str) -> Optional[GdkPixbuf.Pixbuf]:
        """Render text overlay and return a new pixbuf."""
        try:
            return self._create_icon_with_overlay(base_pixbuf, text)
        except Exception as e:
            logger.error(f"Text overlay rendering failed: {e}", exc_info=True)
            return None

    def _get_icon_name(self) -> str:
        """Determine which icon name to use based on current state."""
        state = self.state_manager.get_state()

        # Check for updates
        if state.get('update_available', False):
            return "indicator-updates"  # Updates available icon

        # Check for livestreams
        online_streams = state.get('online_streams', [])
        if online_streams:
            return "video-x-generic"  # Media icon for streams

        # Check for severe weather alerts
        severe_weather = state.get('severe_weather_data', {})
        alerts = severe_weather.get('alerts', [])
        if alerts:
            return "weather-severe-alert"  # Severe weather icon

        # Check for unread items
        unread_count = len(state.get('unread', []))
        if unread_count > 0:
            return "mail-unread"  # Mail icon for unread items

        # Default icon
        return "application-default-icon"  # Fallback to default app icon

    def _update_tooltip(self) -> None:
        """Update the tooltip text based on current state."""
        try:
            state = self.state_manager.get_state()
            tooltip_parts = []

            # Application name
            tooltip_parts.append("void-tray")

            # Unread items count
            unread_count = len(state.get('unread', []))
            if unread_count > 0:
                tooltip_parts.append(f"{unread_count} unread item{'s' if unread_count != 1 else ''}")

            # Updates available
            if state.get('update_available', False):
                count = state.get('update_count', 0)
                tooltip_parts.append(f"{count} update{'s' if count != 1 else ''} available")

            # Weather
            weather_data = state.get('weather_data', {})
            if weather_data.get('temperature') is not None:
                temp = weather_data['temperature']
                tooltip_parts.append(f"{temp}°C")

            # Livestreams
            online_streams = state.get('online_streams', [])
            if online_streams:
                stream_names = [s.get('channel', 'unknown') for s in online_streams[:2]]
                if len(online_streams) > 2:
                    stream_names.append(f"+{len(online_streams)-2} more")
                tooltip_parts.append(f"Live: {', '.join(stream_names)}")

            # Playback status
            if state.get('playing', False):
                current_stream = state.get('current_stream', '')
                if current_stream:
                    tooltip_parts.append(f"Playing: {current_stream}")

            # Set tooltip
            self._tooltip_text = " • ".join(tooltip_parts)
            if self._icon:
                self._icon.set_tooltip_text(self._tooltip_text)

        except Exception as e:
            logger.debug(f"Error updating tooltip: {e}")
            self._tooltip_text = "void-tray"
            if self._icon:
                self._icon.set_tooltip_text(self._tooltip_text)

    def _on_state_change(self, data: Any = None) -> None:
        """Handle general state change events."""
        # Update icon and tooltip when state changes significantly
        GLib.idle_add(self._update_icon)
        GLib.idle_add(self._update_tooltip)

    def _on_feed_update(self, data: Any = None) -> None:
        """Handle feed update events."""
        GLib.idle_add(self._update_icon)
        GLib.idle_add(self._update_tooltip)

    def _on_update_available(self, data: Any = None) -> None:
        """Handle update available events."""
        GLib.idle_add(self._update_icon)
        GLib.idle_add(self._update_tooltip)

    def _on_weather_update(self, data: Any = None) -> None:
        """Handle weather update events."""
        GLib.idle_add(self._update_icon)
        GLib.idle_add(self._update_tooltip)

    def _on_severe_weather_update(self, data: Any = None) -> None:
        """Handle severe weather update events."""
        GLib.idle_add(self._update_icon)
        GLib.idle_add(self._update_tooltip)

    def _on_livestream_online(self, data: Any = None) -> None:
        """Handle livestream online events."""
        GLib.idle_add(self._update_icon)
        GLib.idle_add(self._update_tooltip)

    def _on_livestream_offline(self, data: Any = None) -> None:
        """Handle livestream offline events."""
        GLib.idle_add(self._update_icon)
        GLib.idle_add(self._update_tooltip)

    def _on_playback_start(self, data: Any = None) -> None:
        """Handle playback start events."""
        GLib.idle_add(self._update_icon)
        GLib.idle_add(self._update_tooltip)

    def _on_playback_stop(self, data: Any = None) -> None:
        """Handle playback stop events."""
        GLib.idle_add(self._update_icon)
        GLib.idle_add(self._update_tooltip)

    def shutdown(self) -> None:
        """Clean up resources."""
        logger.debug("Shutting down tray icon")
        if self._icon:
            self._icon.set_visible(False)
            self._icon = None

        if self._menu:
            self._menu = None

        # Unsubscribe from events
        # In a more complete implementation, we'd track subscriptions and unsubscribe


# Global tray icon instance
_tray_icon: Optional[TrayIcon] = None


def get_tray_icon() -> TrayIcon:
    """Get the global tray icon instance."""
    global _tray_icon
    if _tray_icon is None:
        _tray_icon = TrayIcon()
    return _tray_icon