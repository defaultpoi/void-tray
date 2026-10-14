"""Tray icon drawing and badge state."""

import math

from ..gi_env import Gdk, cairo


ALERT_BADGE_COLORS = {
    'yellow': ((0.90, 0.75, 0.0, 1), (0.55, 0.46, 0.0, 1)),
    'orange': ((0.90, 0.45, 0.0, 1), (0.55, 0.28, 0.0, 1)),
    'red': ((0.90, 0.05, 0.05, 1), (0.50, 0.05, 0.05, 1)),
}


# The tray icon draws the degree sign as a small stroked ring (a text glyph
# would be too tiny/blurry at 24px and would eat into the digits' width).
TEMP_RING_RADIUS = 1.0


TEMP_RING_LINE_WIDTH = 1.0


TEMP_RING_GAP = 0.4


TEMP_RING_RESERVE = TEMP_RING_GAP + 2 * TEMP_RING_RADIUS + TEMP_RING_LINE_WIDTH


def temp_icon_layout(size, text_width, text_height):
    """Horizontal start of the digits and centre of the degree ring so that
    digits + ring are centred together in a `size`-px icon, ring top level
    with the digits' top. Returns (digits_left, ring_cx, ring_cy)."""
    total = text_width + TEMP_RING_RESERVE
    digits_left = (size - total) / 2
    ring_outer = 2 * TEMP_RING_RADIUS + TEMP_RING_LINE_WIDTH
    ring_cx = digits_left + text_width + TEMP_RING_GAP + ring_outer / 2
    ring_cy = max(size / 2 - text_height / 2 + ring_outer / 2, ring_outer / 2)
    return digits_left, ring_cx, ring_cy


class IconMixin:
    def update_icon(self):
        count = self.total_badge_count()
        self.status_icon.set_from_pixbuf(self.render_icon(count))
        if self.schedule_warning:
            tooltip = (f"{self.schedule_warning['action']} in {self.schedule_warning['left']} s "
                       "\u2014 open the popup to cancel or snooze")
        elif self.has_active_alerts():
            hazards = ', '.join(sorted({a['hazard'] for a in self.active_alerts}))
            tooltip = f"\u26a0 {hazards} warning"
        elif self._should_show_weather_icon(count):
            tooltip = self.format_weather_tooltip_text()
        elif self.has_pkg_update():
            tooltip = f"{count} unread — package update available"
        elif count:
            tooltip = f"{count} unread"
        else:
            tooltip = "No unread items"
        self.status_icon.set_tooltip_text(tooltip)
        return False

    def total_badge_count(self):
        # Live channels intentionally excluded — they show in the popup list
        # and still trigger the sound/auto-popup notification, but shouldn't
        # affect the tray icon's badge number or color.
        with self.lock:
            return (
                len(self.state.get('unread', []))
                + len(self.state.get('available_updates', []))
            )

    def has_anything_to_show(self):
        with self.lock:
            return (
                bool(self.state.get('unread'))
                or bool(self.state.get('available_updates'))
                or bool(self.state.get('live_channels'))
                or bool(self.state.get('live_youtube_channels'))
            )

    def has_pkg_update(self):
        with self.lock:
            return bool(self.state.get('available_updates'))

    def render_icon(self, count):
        size = 24
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        ctx = cairo.Context(surface)

        if self.schedule_warning is not None and self._schedule_flash_on:
            # pending scheduled action: flash a red "!" every other second
            ctx.set_source_rgba(0.82, 0.12, 0.12, 1)
            ctx.arc(size / 2, size / 2, size / 2 - 1, 0, 2 * 3.14159265)
            ctx.fill()
            ctx.set_source_rgba(1, 1, 1, 1)
            ctx.select_font_face('Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            ctx.set_font_size(18)
            xb, yb, w, h, _dx, _dy = ctx.text_extents('!')
            ctx.move_to(size / 2 - w / 2 - xb, size / 2 - h / 2 - yb)
            ctx.show_text('!')
            surface.flush()
            return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

        show_weather = self._should_show_weather_icon(count)

        if show_weather:
            # Temperature only, no glyph — the icon is a fixed-size XEMBED
            # tray slot with no way to make it bigger overall, so dropping
            # the glyph here lets the digits alone claim the full icon
            # instead of splitting the space with it. The glyph still shows
            # in the popup's weather bar, where space isn't constrained.
            ctx.set_source_rgba(1, 1, 1, 1)
            temp_text = f"{self.weather_data['temp']:.0f}"
            padding = 1
            max_width = size - 2 * padding - TEMP_RING_RESERVE  # room for the ring

            ctx.select_font_face('Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            temp_size = 20
            min_temp_size = 10
            while temp_size > min_temp_size:
                ctx.set_font_size(temp_size)
                if ctx.text_extents(temp_text)[4] <= max_width:
                    break
                temp_size -= 1

            xb, yb, tw, th, _dx, _dy = ctx.text_extents(temp_text)
            digits_left, ring_cx, ring_cy = temp_icon_layout(size, tw, th)
            ctx.move_to(digits_left - xb, size / 2 - th / 2 - yb)
            ctx.show_text(temp_text)
            ctx.set_line_width(TEMP_RING_LINE_WIDTH)
            ctx.new_sub_path()
            # snap the centre to a pixel centre so the tiny ring is drawn
            # crisp (full-intensity pixels) instead of a blurry grey dot
            ctx.arc(math.floor(ring_cx) + 0.5, math.floor(ring_cy) + 0.5,
                    TEMP_RING_RADIUS, 0, 2 * 3.14159265)
            ctx.stroke()
        else:
            worst_alert_color = self._worst_alert_color()
            if worst_alert_color:
                bright, dim = ALERT_BADGE_COLORS[worst_alert_color]
                ctx.set_source_rgba(*(bright if self._pulse_on_for(worst_alert_color) else dim))
            elif self.has_pkg_update():
                ctx.set_source_rgba(0.82, 0.18, 0.18, 1)   # red: update available
            elif count > 0:
                ctx.set_source_rgba(0.92, 0.55, 0.10, 1)   # orange: unread news / live channel
            else:
                ctx.set_source_rgba(0.20, 0.65, 0.30, 1)   # green: nothing unread, weather not loaded yet
            ctx.arc(size / 2, size / 2, size / 2 - 1, 0, 2 * 3.14159265)
            ctx.fill()

            ctx.set_source_rgba(1, 1, 1, 1)
            text = str(count) if count < 100 else '99+'
            ctx.select_font_face('Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            ctx.set_font_size(12 if len(text) <= 2 else 8)
            xb, yb, w, h, dx, dy = ctx.text_extents(text)
            ctx.move_to(size / 2 - w / 2 - xb, size / 2 - h / 2 - yb)
            ctx.show_text(text)

        surface.flush()
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
