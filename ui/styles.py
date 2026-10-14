"""
CSS styling for void-tray GTK widgets.

Defines the visual appearance of the popup window, weather bar, timer controls,
and status indicators.
"""

# CSS styles for void-tray application
CSS_STYLES = """
/* General window styling */
.window {
    background-color: @theme_bg_color;
    color: @theme_fg_color;
    font-family: sans-serif;
    font-size: 9pt;
}

/* Title styling */
.title {
    font-weight: bold;
    font-size: 10pt;
    color: @theme_selected_fg_color;
}

/* Highlighted item styling */
.item-row .highlighted {
    font-weight: bold;
    color: #ff6600;
    background-color: rgba(255, 102, 0, 0.1);
    border-radius: 3px;
    padding: 2px;
}

/* Meta information styling */
.meta {
    font-size: 8pt;
    color: @theme_fg_color;
    opacity: 0.7;
}

/* Temperature styling */
.temperature {
    font-weight: bold;
    font-size: 10pt;
    color: #ff6600;
}

/* Details styling */
.details {
    font-size: 8pt;
    color: @theme_fg_color;
    opacity: 0.6;
}

/* Update banner styling */
.update-banner {
    background-color: #ffeb3b;
    color: #000000;
    border-radius: 3px;
    padding: 4px;
    font-weight: bold;
    margin: 4px 0;
}

/* Livestream banner styling */
.livestream-banner {
    background-color: #4caf50;
    color: #ffffff;
    border-radius: 3px;
    padding: 4px;
    font-weight: bold;
    margin: 4px 0;
}

/* Button styling */
.button {
    border-radius: 3px;
    padding: 4px 8px;
}

/* Entry field styling */
.entry {
    border-radius: 3px;
    padding: 4px;
}

/* Frame styling */
.frame {
    border: 1px solid shade(@theme_bg_color, 1.2);
    border-radius: 3px;
    padding: 6px;
}

/* Section header styling */
.section-header {
    font-weight: bold;
    font-size: 10pt;
    margin: 8px 0 4px 0;
    text-align: center;
}

/* Footer bar styling */
.footer-bar {
    background-color: shade(@theme_bg_color, 1.1);
    border-top: 1px solid shade(@theme_bg_color, 1.2);
    padding: 4px;
}

/* Weather header styling */
.weather-header {
    font-weight: normal;
    font-size: 10pt;
    text-align: center;
    padding: 4px;
}

/* Feed item styling */
.feed-item {
    font-size: 9pt;
    padding: 2px 0;
}

/* Indicator styling */
.indicator-dot {
    width: 8px;
    height: 8px;
    border-radius: 4px;
}

/* Header bar styling */
.header-bar {
    background: shade(@theme_bg_color, 1.1);
    border-bottom: 1px solid shade(@theme_bg_color, 1.2);
}

/* Status icon styling */
.status-icon {
    padding: 4px;
}

/* Tooltip styling */
.tooltip {
    background-color: #ffffe0;
    color: #000000;
    border: 1px solid #d4d4d4;
    border-radius: 3px;
    padding: 4px;
    font-size: 8pt;
}
"""


def get_css_provider() -> str:
    """Return the CSS styles as a string for GTK CSS provider."""
    return CSS_STYLES


def apply_css_styles() -> None:
    """Apply CSS styles to the GTK application."""
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk, Gdk

        # Create CSS provider
        css_provider = Gtk.CssProvider()

        # Load CSS from string
        css_provider.load_from_data(CSS_STYLES.encode('utf-8'))

        # Add provider to default screen
        screen = Gdk.Screen.get_default()
        style_context = Gtk.StyleContext()
        style_context.add_provider_for_screen(
            screen,
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Also apply to the default display for GTK 3.22+
        try:
            display = Gdk.Display.get_default()
            style_context.add_provider_for_display(
                display,
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        except:
            pass  # Fallback for older GTK versions

        logger = __import__('logging').getLogger(__name__)
        logger.debug("CSS styles applied successfully")

    except Exception as e:
        logger = __import__('logging').getLogger(__name__)
        logger.warning(f"Failed to apply CSS styles: {e}")


# Alternative: Inline style application for individual widgets
INLINE_STYLES = {
    'update_banner': """
        background-color: #ffeb3b;
        color: #000000;
        border-radius: 3px;
        padding: 4px;
        font-weight: bold;
        text-align: center;
    """,
    'livestream_banner': """
        background-color: #4caf50;
        color: #ffffff;
        border-radius: 3px;
        padding: 4px;
        font-weight: bold;
        text-align: center;
    """,
    'temperature': """
        font-weight: bold;
        font-size: 10pt;
        color: #ff6600;
    """,
    'meta': """
        font-size: 8pt;
        color: @theme_fg_color;
        opacity: 0.7;
    """,
    'details': """
        font-size: 8pt;
        color: @theme_fg_color;
        opacity: 0.6;
    """
}