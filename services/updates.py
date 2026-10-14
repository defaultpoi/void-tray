"""
Updates service for void-tray.

Handles Void Linux system package update detection using xbps-install
and xbps-query, with proper locking to prevent overlapping operations.
"""

import subprocess
import time as time_module
import threading
import logging
from typing import Dict, Any, Optional, Tuple, List
from ..config.loader import (
    load_update_settings, get_update_check_interval,
    get_update_auto_install, get_update_reboot_required
)
from ..core.state import get_state_manager
from ..core.event_bus import get_event_bus
from ..utils.logger import get_logger
from ..utils.network import is_online

logger = get_logger(__name__)
EVENT_UPDATE_CHECK = 'update_check'
EVENT_UPDATE_AVAILABLE = 'update_available'
EVENT_UPDATE_IN_PROGRESS = 'update_in_progress'
EVENT_UPDATE_COMPLETED = 'update_completed'
EVENT_UPDATE_FAILED = 'update_failed'
EVENT_REBOOT_REQUIRED = 'reboot_required'


class UpdatesService:
    """
    Service for checking and managing Void Linux system updates.

    Responsibilities:
    - Checking for available updates using xbps-query
    - Installing updates using xbps-install
    - Tracking reboot-required state
    - Proper locking to prevent overlapping operations
    - Error handling and retry logic
    - Background polling with operation-level locking
    - Notifying via event bus when update state changes
    """

    # xbps commands
    XBPS_QUERY = ['xbps-install', '-Mn', '-u']
    XBPS_INSTALL = ['xbps-install', '-Su', '-y']
    XBPS_CHECK_REBOOT = ['grep', '-q', 'reboot', '/var/run/xbps.reboot/required']

    def __init__(self):
        self.state_manager = get_state_manager()
        self.event_bus = get_event_bus()
        self._update_lock = threading.Lock()  # For preventing overlapping update checks/installs
        self._install_lock = threading.Lock()  # For preventing overlapping installations
        logger.debug("UpdatesService initialized")

    def start_update_check_thread(self, force: bool = False) -> None:
        """
        Start a background thread to check for updates.

        Args:
            force: If True, check for updates regardless of last check time
        """
        if not self._update_lock.acquire(blocking=False):
            logger.debug("Update check already in progress, skipping")
            return

        def _check_updates_guarded():
            try:
                self.check_for_updates(force)
            finally:
                self._update_lock.release()

        thread = threading.Thread(target=_check_updates_guarded, daemon=True)
        thread.start()
        logger.debug("Started update check thread")

    def check_for_updates(self, force: bool = False) -> None:
        """
        Check for available system updates.

        Args:
            force: If True, check for updates regardless of last check time
        """
        logger.debug(f"Checking for updates (force={force})")

        # Check if updates are enabled
        settings = load_update_settings()
        if not settings.get('enabled', True):
            logger.debug("Update service disabled")
            return

        # Check connectivity first (only for non-force checks)
        if not force and not is_online():
            logger.debug("Offline: skipping update check")
            return

        interval = get_update_check_interval()
        now = time_module.time()
        state = self.state_manager.get_state()
        last_checked = state.get('update_last_checked', 0)

        if not force and (now - last_checked) < interval:
            logger.debug(f"Updates not due for check yet (last checked {now - last_checked:.0f}s ago)")
            return

        logger.debug("Checking for available updates...")

        # Publish that we're starting a check
        self.event_bus.publish(EVENT_UPDATE_CHECK, {'status': 'checking'})

        try:
            # Run xbps-query to check for updates
            result = subprocess.run(
                self.XBPS_QUERY,
                capture_output=True,
                text=True,
                timeout=30  # 30 second timeout
            )

            if result.returncode not in (0, 1):  # xbps-query returns 1 when there are updates
                logger.warning(f"xbps-query failed with return code {result.returncode}: {result.stderr}")
                self._handle_update_error(f"xbps-query failed: {result.stderr}")
                return

            # Parse the output to get list of available updates
            updates = []
            if result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    line = line.strip()
                    if not line:
                        continue  # Skip empty lines

                    # Skip common header/status lines that don't represent actual updates
                    lower_line = line.lower()
                    if any(keyword in lower_line for keyword in [
                        'repository', 'available', 'up to date', 'up-to-date',
                        'cached', 'processing', 'triggered', 'verifying',
                        'downloading', 'installing', 'resolving', 'fetching',
                        'validating', 'verifying', 'loading', 'initializing',
                        'updates:', 'packages:', 'the following packages',
                        'will be updated', 'will be installed', 'will be upgraded',
                        'retrieving', 'downloading', 'resolving', 'conflicts',
                        'size:', 'total:', 'download:', 'will be', 'the following',
                        'packages have updates available', 'update available',
                        'updates are available', 'need to be updated', 'waiting for',
                        'triggered', 'processing:', 'processing', 'active triggers',
                        'waiting for triggers', 'smart-triggered', 'running triggers',
                        'fetching updates', 'checking repos', 'repo:', 'repositories',
                        'processing triggers', 'processing triggers', 'executing triggers',
                        'no updates available', 'system is up to date', 'all packages',
                        'are up to date', 'up to date', 'nothing to do', 'no packages',
                        'specified', 'missing argument', 'usage:', 'error:', 'warning:',
                        'debug:', 'info:', 'note:', 'hint:', 'tip:', 'usage'
                    ]):
                        continue

                    # Skip separator lines (just dashes, equals, etc.)
                    if len(line) > 0 and all(c in '-=' for c in line):
                        continue

                    # Skip lines that don't look like package specifications
                    # Package names typically contain alphanumerics, hyphens, underscores, periods
                    # and don't consist solely of special characters
                    if not line or len(line) > 100:
                        continue

                    # Check if line looks like a reasonable package specification
                    # Package names:
                    # - Must not be empty
                    # - Must not contain spaces (package names don't have spaces)
                    # - Must start and end with an alphanumeric character
                    # - Must contain only alphanumerics, hyphens, underscores, periods
                    # - Must contain at least one letter (to avoid pure version numbers)
                    # - Reasonable length
                    if not line:
                        continue
                    if ' ' in line:
                        continue
                    if len(line) < 1 or len(line) > 50:
                        continue
                    if not line[0].isalnum() or not line[-1].isalnum():
                        continue
                    if not all(c.isalnum() or c in '._-' for c in line):
                        continue
                    if not any(c.isalpha() for c in line):  # Must contain at least one letter
                        continue

                    # Should not consist only of special characters that are unlikely in package names
                    # Allow: alphanumeric, hyphen, underscore, period, plus
                    # Reject if consists only of: spaces, punctuation that's not in allowed set
                    allowed_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_+. ')
                    if not all(c in allowed_chars for c in line):
                        continue

                    # Should not be just whitespace or just special characters
                    if line.strip() in ('-', '+', '.', '=', '~', ':', ',', ';', '!', '@', '#', '$', '%', '^', '&', '*', '(', ')', '[', ']', '{', '}', '|', '\\', '/', '?', '"', "'"):
                        continue

                    # This looks like an actual package update
                    updates.append(line)

            update_count = len(updates)
            logger.debug(f"Found {update_count} available updates")

            # Check if reboot is required
            reboot_required = self._check_reboot_required()

            # Update state
            def updater(state_dict):
                state_dict['update_available'] = update_count > 0
                state_dict['update_count'] = update_count
                state_dict['update_list'] = updates
                state_dict['update_last_checked'] = now
                state_dict['update_error'] = None
                state_dict['reboot_required'] = reboot_required
                # Clear error flag on successful check
                if 'update_error' in state_dict:
                    del state_dict['update_error']

            self.state_manager.update_state(updater)
            self.state_manager.save_to_disk()

            # Publish events based on results
            if update_count > 0:
                self.event_bus.publish(EVENT_UPDATE_AVAILABLE, {
                    'count': update_count,
                    'updates': updates,
                    'reboot_required': reboot_required
                })
                logger.info(f"Found {update_count} available updates")
            else:
                self.event_bus.publish(EVENT_UPDATE_CHECK, {'status': 'none_available'})
                logger.debug("No updates available")

            if reboot_required:
                self.event_bus.publish(EVENT_REBOOT_REQUIRED, {})
                logger.info("Reboot required for pending updates")

        except subprocess.TimeoutExpired:
            logger.error("xbps-query timed out")
            self._handle_update_error("Update check timed out")
        except Exception as e:
            logger.error(f"Error checking for updates: {e}", exc_info=True)
            self._handle_update_error(str(e))

    def install_updates(self) -> bool:
        """
        Install available updates.

        Returns:
            True if installation started successfully, False otherwise
        """
        logger.info("Starting update installation...")

        # Prevent overlapping installations
        if not self._install_lock.acquire(blocking=False):
            logger.warning("Update installation already in progress")
            self.event_bus.publish(EVENT_UPDATE_IN_PROGRESS, {'status': 'already_running'})
            return False

        def _install_updates_guarded():
            try:
                self._perform_update_installation()
            finally:
                self._install_lock.release()

        thread = threading.Thread(target=_install_updates_guarded, daemon=True)
        thread.start()

        # Publish that installation is starting
        self.event_bus.publish(EVENT_UPDATE_IN_PROGRESS, {'status': 'starting'})
        return True

    def _perform_update_installation(self) -> None:
        """Perform the actual update installation."""
        logger.info("Performing update installation...")

        try:
            # Run xbps-install to install updates
            process = subprocess.Popen(
                self.XBPS_INSTALL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # Wait for completion with timeout
            try:
                stdout, stderr = process.communicate(timeout=300)  # 5 minute timeout
                returncode = process.returncode
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                logger.error("xbps-install timed out after 5 minutes")
                self._handle_update_error("Update installation timed out")
                return

            if returncode == 0:
                logger.info("Update installation completed successfully")

                # Check if reboot is required after installation
                reboot_required = self._check_reboot_required()

                # Update state
                def updater(state_dict):
                    state_dict['update_available'] = False
                    state_dict['update_count'] = 0
                    state_dict['update_list'] = []
                    state_dict['update_last_installed'] = time_module.time()
                    state_dict['update_error'] = None
                    state_dict['reboot_required'] = reboot_required
                    # Clear error flag on successful installation
                    if 'update_error' in state_dict:
                        del state_dict['update_error']

                self.state_manager.update_state(updater)
                self.state_manager.save_to_disk()

                # Publish completion event
                self.event_bus.publish(EVENT_UPDATE_COMPLETED, {
                    'reboot_required': reboot_required
                })

                if reboot_required:
                    self.event_bus.publish(EVENT_REBOOT_REQUIRED, {})

            else:
                logger.error(f"xbps-install failed with return code {returncode}: {stderr}")
                self._handle_update_error(f"Update installation failed: {stderr}")

        except Exception as e:
            logger.error(f"Error during update installation: {e}", exc_info=True)
            self._handle_update_error(str(e))

    def _check_reboot_required(self) -> bool:
        """
        Check if a reboot is required for pending updates.

        Returns:
            True if reboot is required, False otherwise
        """
        try:
            # Check if the reboot required file exists
            result = subprocess.run(
                ['test', '-f', '/var/run/xbps.reboot/required'],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except Exception:
            # If we can't check, assume no reboot required
            return False

    def _handle_update_error(self, error_message: str) -> None:
        """Handle update service errors."""
        logger.error(f"Update service error: {error_message}")

        def updater(state_dict):
            state_dict['update_error'] = error_message
            state_dict['update_last_checked'] = time_module.time()

        self.state_manager.update_state(updater)
        self.state_manager.save_to_disk()

        # Publish error event
        self.event_bus.publish(EVENT_UPDATE_FAILED, {'message': error_message})

    def shutdown(self) -> None:
        """Cleanup resources."""
        logger.debug("UpdatesService shutdown")


# Global service instance
_updates_service: Optional[UpdatesService] = None


def get_updates_service() -> UpdatesService:
    """Get the global updates service instance."""
    global _updates_service
    if _updates_service is None:
        _updates_service = UpdatesService()
    return _updates_service