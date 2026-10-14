"""
Background task scheduler for void-tray.

Manages periodic tasks using GLib timeouts, replacing the multiple
GLib.timeout_add calls in the original RssTray.__init__.
"""

import threading
import time as time_module
from typing import Dict, Callable, Optional
try:
    from gi.repository import GLib
except ImportError:
    GLib = None
    logger = None
    # This will be handled gracefully in methods

import logging

logger = logging.getLogger(__name__)


class Scheduler:
    """
    Manages scheduled background tasks using GLib timeouts.

    Provides centralized scheduling and cancellation of periodic tasks,
    replacing the scattered GLib.timeout_add calls in the original code.
    """

    def __init__(self):
        self._scheduled_tasks: Dict[str, int] = {}  # tag -> timeout_id
        self._lock = threading.Lock()
        logger.debug("Scheduler initialized")

    def schedule_timeout(
        self,
        interval_seconds: float,
        callback: Callable[[], bool],
        tag: str,
        first_delay_seconds: Optional[float] = None
    ) -> bool:
        """
        Schedule a repeating timeout callback.

        Args:
            interval_seconds: Interval between calls in seconds
            callback: Function to call (should return True to continue, False to stop)
            tag: Identifier for this task (used for cancellation)
            first_delay_seconds: Delay before first call (defaults to interval_seconds)

        Returns:
            True if scheduled successfully, False otherwise
        """
        if GLib is None:
            logger.error("GLib not available - cannot schedule timeout")
            return False

        if first_delay_seconds is None:
            first_delay_seconds = interval_seconds

        def wrapper():
            try:
                return callback()
            except Exception as e:
                logger.error(f"Error in scheduled callback '{tag}': {e}", exc_info=True)
                return False  # Stop the timer on error

        with self._lock:
            # Cancel existing task with this tag if any
            self.cancel_timeout(tag)

            # Schedule new timeout
            timeout_id = GLib.timeout_add_seconds(
                int(first_delay_seconds),
                wrapper
            )

            # For repeating intervals, we need to reschedule manually
            # GLib.timeout_add_seconds doesn't support changing interval after first call
            # So we'll use a wrapper that reschedules itself
            def rescheduling_wrapper():
                try:
                    result = callback()
                    if result:
                        # Reschedule with the regular interval
                        GLib.timeout_add_seconds(
                            int(interval_seconds),
                            rescheduling_wrapper
                        )
                    return result
                except Exception as e:
                    logger.error(f"Error in scheduled callback '{tag}': {e}", exc_info=True)
                    return False

            # Actually, let's use GLib.timeout_add which accepts milliseconds
            # and allows us to specify the interval precisely
            timeout_id = GLib.timeout_add(
                int(first_delay_seconds * 1000),  # Convert to milliseconds
                rescheduling_wrapper
            )

            self._scheduled_tasks[tag] = timeout_id
            logger.debug(f"Scheduled task '{tag}' with {interval_seconds}s interval")
            return True

    def schedule_timeout_ms(
        self,
        interval_ms: int,
        callback: Callable[[], bool],
        tag: str,
        first_delay_ms: Optional[int] = None
    ) -> bool:
        """
        Schedule a repeating timeout callback with millisecond precision.

        Args:
            interval_ms: Interval between calls in milliseconds
            callback: Function to call (should return True to continue, False to stop)
            tag: Identifier for this task
            first_delay_ms: Delay before first call (defaults to interval_ms)

        Returns:
            True if scheduled successfully, False otherwise
        """
        if GLib is None:
            logger.error("GLib not available - cannot schedule timeout")
            return False

        if first_delay_ms is None:
            first_delay_ms = interval_ms

        def wrapper():
            try:
                return callback()
            except Exception as e:
                logger.error(f"Error in scheduled callback '{tag}': {e}", exc_info=True)
                return False  # Stop the timer on error

        with self._lock:
            # Cancel existing task with this tag if any
            self.cancel_timeout(tag)

            # Schedule new timeout
            timeout_id = GLib.timeout_add(first_delay_ms, wrapper)

            # Store the interval for rescheduling
            # We'll need to modify our approach - let's use a closure that captures the interval
            def rescheduling_wrapper(interval=interval_ms):
                try:
                    result = callback()
                    if result:
                        # Reschedule with the regular interval
                        GLib.timeout_add(interval, rescheduling_wrapper)
                    return result
                except Exception as e:
                    logger.error(f"Error in scheduled callback '{tag}': {e}", exc_info=True)
                    return False

            # Actually replace the wrapper with the rescheduling version
            GLib.source_remove(timeout_id)  # Remove the initial one
            timeout_id = GLib.timeout_add(first_delay_ms, rescheduling_wrapper)

            self._scheduled_tasks[tag] = timeout_id
            logger.debug(f"Scheduled task '{tag}' with {interval_ms}ms interval")
            return True

    def cancel_timeout(self, tag: str) -> bool:
        """
        Cancel a scheduled timeout by its tag.

        Args:
            tag: Identifier of the task to cancel

        Returns:
            True if task was found and cancelled, False otherwise
        """
        if GLib is None:
            return False

        with self._lock:
            if tag in self._scheduled_tasks:
                timeout_id = self._scheduled_tasks[tag]
                try:
                    GLib.source_remove(timeout_id)
                    del self._scheduled_tasks[tag]
                    logger.debug(f"Cancelled timeout '{tag}'")
                    return True
                except (ValueError, AttributeError) as e:
                    logger.debug(f"Error cancelling timeout '{tag}': {e}")
                    # Remove from dict anyway to prevent memory leak
                    if tag in self._scheduled_tasks:
                        del self._scheduled_tasks[tag]
                    return False
            return False

    def cancel_all(self) -> None:
        """Cancel all scheduled timeouts."""
        if GLib is None:
            return

        with self._lock:
            tags = list(self._scheduled_tasks.keys())
            for tag in tags:
                self.cancel_timeout(tag)
            logger.debug("Cancelled all scheduled timeouts")

    def is_scheduled(self, tag: str) -> bool:
        """Check if a task with the given tag is currently scheduled."""
        with self._lock:
            return tag in self._scheduled_tasks

    def get_scheduled_tags(self) -> list:
        """Get list of all currently scheduled task tags."""
        with self._lock:
            return list(self._scheduled_tasks.keys())


# Global scheduler instance
_scheduler: Optional[Scheduler] = None


def get_scheduler() -> Scheduler:
    """Get the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler