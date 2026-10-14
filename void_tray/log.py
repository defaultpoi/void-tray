"""Debug logging, enabled by the VOID_TRAY_DEBUG environment variable."""

import os
import sys


def _log(message):
    """Diagnostics to stderr, silent unless VOID_TRAY_DEBUG is set (e.g.
    `VOID_TRAY_DEBUG=1 void-tray`). RSS_TRAY_DEBUG, the name the single-file
    version used, still works."""
    if os.environ.get('VOID_TRAY_DEBUG') or os.environ.get('RSS_TRAY_DEBUG'):
        print(f'void-tray: {message}', file=sys.stderr, flush=True)
