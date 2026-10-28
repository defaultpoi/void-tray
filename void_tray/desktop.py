"""Desktop integration: sounds, fullscreen detection, editor and launcher."""

import os
import shutil
import subprocess
import threading

from .gi_env import GLib, Gdk, Gio, Wnck
from .constants import CONFIG_DIR
from .log import _log


NOTIFICATION_SOUND_FILE = os.path.join(CONFIG_DIR, 'notification.wav')  # no sound if absent


def run_launcher_command(command):
    """Runs `command` through the shell, fully detached from the tray app
    (own session, no stdio), from the home directory. True if it started."""
    try:
        proc = subprocess.Popen(
            ['sh', '-c', command], cwd=os.path.expanduser('~'), start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        _log(f'could not run launcher command {command!r}: {e}')
        return False
    threading.Thread(target=proc.wait, daemon=True).start()  # reap it, no zombie
    return True


def play_notification_sound():
    """Best-effort: plays notification.wav from the config directory with
    whichever player is available. Silently does nothing if the file or every
    player is missing."""
    if not os.path.exists(NOTIFICATION_SOUND_FILE):
        return
    for player in (['paplay'], ['aplay', '-q'],
                   ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet']):
        if shutil.which(player[0]):
            try:
                subprocess.Popen(player + [NOTIFICATION_SOUND_FILE],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                continue


def primary_monitor_size():
    """(width, height) in pixels of the primary monitor, at its scale factor (a 1366x768
    panel set to 2x is 2732x1536 pixels of picture); None when there is no display to ask.
    Main thread only."""
    display = Gdk.Display.get_default()
    if display is None:
        return None
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    if monitor is None:
        return None
    geometry, scale = monitor.get_geometry(), monitor.get_scale_factor() or 1
    return (geometry.width * scale, geometry.height * scale)


def is_fullscreen_active():
    """Best-effort check for whether the currently focused window is fullscreen,
    so the popup doesn't interrupt a video/game/presentation. Returns False
    (never suppresses) if the Wnck library isn't available."""
    if Wnck is None:
        return False
    try:
        screen = Wnck.Screen.get_default()
        screen.force_update()
        active = screen.get_active_window()
        return bool(active and active.is_fullscreen())
    except Exception:
        return False


def edit_file_externally(path):
    """Opens `path` in the user's default application for its type -- the one
    set in the desktop's settings (e.g. XFCE's Default Applications). Tried in
    order: GIO (in-process, needs nothing beyond GTK itself), xdg-open, and
    finally $EDITOR in a terminal."""
    try:
        if Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None):
            return
    except GLib.Error as e:
        _log(f'GIO could not open {path}: {e.message}')
    if shutil.which('xdg-open'):
        try:
            subprocess.Popen(['xdg-open', path])
            return
        except OSError as e:
            _log(f'xdg-open failed: {e}')
    editor = os.environ.get('EDITOR', 'vi')
    if shutil.which('xfce4-terminal'):
        subprocess.Popen(['xfce4-terminal', '-e', f'{editor} "{path}"'])
    else:
        try:
            subprocess.Popen([editor, path])
        except Exception:
            print(f"Couldn't open an editor — edit manually: {path}")
