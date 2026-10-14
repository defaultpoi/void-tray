"""
Main entry point for void-tray application.

Initializes and wires together all components using dependency injection.
Sets up the configuration loader, state manager, event bus, services, and UI components.
Starts the GTK main loop and handles graceful shutdown.
"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Notify', '0.7')
from gi.repository import Gtk, GLib, Gio, Notify
import signal
import logging
import threading
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.loader import get_config_loader
from core.state import get_state_manager
from core.event_bus import get_event_bus
from services.rss import get_feed_service
from services.weather import get_weather_service
from services.updates import get_updates_service
from services.livestreams import get_livestreams_service
from services.playback import get_playback_service
from ui.tray import get_tray_icon
from ui.popup_window import get_popup_window
from ui.styles import apply_css_styles
from utils.logger import get_logger
from utils.network import is_online

logger = get_logger(__name__)

_app: Optional[VoidTrayApplication] = None


class VoidTrayApplication:
    """
    Main application class for void-tray.

    Responsibilities:
    - Initialize all services and UI components
    - Wire up dependencies between components
    - Start background services
    - Handle application lifecycle (startup, shutdown)
    - Manage GTK main loop
    """

    def __init__(self):
        self._running = False
        self._main_loop: Optional[GLib.MainLoop] = None
        self._shutdown_handlers_registered = False

        # Component references (will be initialized in setup)
        self.config_loader = None
        self.state_manager = None
        self.event_bus = None
        self.feed_service = None
        self.weather_service = None
        self.updates_service = None
        self.livestreams_service = None
        self.playback_service = None
        self.tray_icon = None
        self.popup_window = None

        logger.info("VoidTrayApplication initialized")

    def setup(self) -> None:
        """Set up all application components and dependencies."""
        logger.info("Setting up application components...")

        # Initialize core components
        self.config_loader = get_config_loader()
        self.state_manager = get_state_manager()
        self.event_bus = get_event_bus()

        # Initialize services
        self.feed_service = get_feed_service()
        self.weather_service = get_weather_service()
        self.updates_service = get_updates_service()
        self.livestreams_service = get_livestreams_service()
        self.playback_service = get_playback_service()

        # Initialize UI components
        self.popup_window = get_popup_window()
        self.popup_window.set_app(self)
        self.tray_icon = get_tray_icon()
        self.tray_icon._app = self

        # Apply CSS styling
        apply_css_styles()

        # Initialize notification system
        try:
            Notify.init("void-tray")
            logger.debug("Notification system initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize notifications: {e}")

        # Set up signal handlers for graceful shutdown
        self._setup_signal_handlers()

        logger.info("Application setup complete")

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        if self._shutdown_handlers_registered:
            return

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        self._shutdown_handlers_registered = True
        logger.debug("Signal handlers registered")

    def _signal_handler(self, signum, frame) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        GLib.idle_add(self.stop)

    def start(self) -> int:
        """
        Start the application.

        Returns:
            Exit code (0 for success, non-zero for error)
        """
        if self._running:
            logger.warning("Application already running")
            return 1

        try:
            logger.info("Starting void-tray application...")

            # Load configuration and state
            self.config_loader.load()
            self.state_manager.load_from_disk()

            # Start background services
            self._start_services()

            # Show tray icon
            self.tray_icon._create_icon()  # Ensure icon is created

            # Show popup window on startup
            self.popup_window.show()

            # Start GTK main loop
            self._running = True
            self._main_loop = GLib.MainLoop()

            logger.info("Entering GTK main loop")
            self._main_loop.run()

            return 0

        except Exception as e:
            logger.error(f"Error starting application: {e}", exc_info=True)
            return 1
        finally:
            self._cleanup()

    def _start_services(self) -> None:
        """Start all background services."""
        logger.info("Starting background services...")

        # Start feed service
        self.feed_service.start_check_thread(force=True)

        # Start weather service
        self.weather_service.start_weather_check_thread(force=True)
        self.weather_service.start_severe_weather_check_thread(force=True)

        # Start updates service
        self.updates_service.start_update_check_thread(force=True)

        # Start livestreams service
        self.livestreams_service.start_check_thread(force=True)

        # Playback service starts on demand

        logger.info("All background services started")

    def stop(self) -> None:
        """Stop the application and initiate shutdown."""
        if not self._running:
            return

        logger.info("Stopping void-tray application...")
        self._running = False

        if self._main_loop and self._main_loop.is_running():
            self._main_loop.quit()

    def _cleanup(self) -> None:
        """Clean up resources during shutdown."""
        logger.info("Cleaning up application resources...")

        # Stop all services
        try:
            if self.feed_service:
                self.feed_service.shutdown()
            if self.weather_service:
                self.weather_service.shutdown()
            if self.updates_service:
                self.updates_service.shutdown()
            if self.livestreams_service:
                self.livestreams_service.shutdown()
            if self.playback_service:
                self.playback_service.shutdown()
        except Exception as e:
            logger.error(f"Error stopping services: {e}")

        # Hide UI components
        try:
            if self.popup_window:
                self.popup_window.hide()
            if self.tray_icon:
                self.tray_icon.shutdown()
        except Exception as e:
            logger.error(f"Error hiding UI components: {e}")

        # Save state
        try:
            if self.state_manager:
                self.state_manager.save_to_disk()
        except Exception as e:
            logger.error(f"Error saving state: {e}")

        # Save configuration
        try:
            if self.config_loader:
                self.config_loader.save()
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")

        # Clean up notifications
        try:
            Notify.uninit()
        except:
            pass

        logger.info("Application cleanup complete")


def main() -> int:
    """Main entry point."""
    global _app
    app = VoidTrayApplication()
    _app = app
    app.setup()
    return app.start()


if __name__ == "__main__":
    sys.exit(main())