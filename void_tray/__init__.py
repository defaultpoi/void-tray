"""Minimal tray RSS/Atom reader with system-wide Void package-update detection
and Twitch live-channel notifications."""
import warnings

# Gtk.StatusIcon is deprecated upstream but is what the XEMBED tray needs here;
# silence the per-call warnings it would otherwise print on every icon update.
warnings.filterwarnings('ignore', message=r'Gtk\.StatusIcon')
warnings.filterwarnings('ignore', message=r'GLib\.unix_signal_add')  # older-GLib fallback path
