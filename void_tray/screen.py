"""The size of the screen, which decides how large a video is worth downloading.

The monitor is measured on the main thread (desktop.primary_monitor_size) and
remembered here, so that the download threads can read it without touching GTK.
"""

from .config import load_media_settings


_detected_screen = None  # (width, height) in pixels of the primary monitor


def remember_screen_size(size):
    """Keeps the monitor's size, (width, height) in pixels, or None when it is not known."""
    global _detected_screen
    _detected_screen = size


def media_target_size():
    """(width, height) that videos are chosen for: `screen=` in [media] if it says so,
    else the monitor's own, None if neither is known. Safe from any thread."""
    return load_media_settings()['screen'] or _detected_screen
