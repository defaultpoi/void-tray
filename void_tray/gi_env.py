"""The GObject-introspection imports, in one place."""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Gio, Pango
try:
    gi.require_version('Wnck', '3.0')
    from gi.repository import Wnck
except Exception:
    Wnck = None  # fullscreen detection just no-ops if this isn't available
try:
    gi.require_version('GLibUnix', '2.0')
    from gi.repository import GLibUnix  # GLib >= 2.78: where the unix signal helpers moved
except Exception:
    GLibUnix = None
import cairo

__all__ = ['Gtk', 'GLib', 'Gdk', 'Gio', 'Pango', 'Wnck', 'GLibUnix', 'cairo']
