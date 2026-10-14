"""
Event bus for void-tray.

Provides publish/subscribe mechanism for decoupled communication
between services, state manager, and UI components.
"""

import threading
import logging
from typing import Dict, List, Callable, Any
from collections import defaultdict

logger = logging.getLogger(__name__)


class EventBus:
    """
    Simple event bus for publish/subscribe communication.

    Allows components to subscribe to event types and publish events
    without needing direct references to each other.
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._lock = threading.RLock()
        logger.debug("EventBus initialized")

    def subscribe(self, event_type: str, callback: Callable[[Any], None]) -> None:
        """
        Subscribe to an event type.

        Args:
            event_type: String identifier for the event (e.g., 'feed_update', 'weather_change')
            callback: Function to call when event is published, taking one argument (event data)
        """
        with self._lock:
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)
                logger.debug(f"Subscribed to event '{event_type}'")

    def unsubscribe(self, event_type: str, callback: Callable[[Any], None]) -> None:
        """
        Unsubscribe from an event type.

        Args:
            event_type: String identifier for the event
            callback: Callback function to remove
        """
        with self._lock:
            if event_type in self._subscribers and callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)
                logger.debug(f"Unsubscribed from event '{event_type}'")

    def publish(self, event_type: str, data: Any = None) -> None:
        """
        Publish an event to all subscribers.

        Args:
            event_type: String identifier for the event
            data: Optional data to pass to subscribers
        """
        subscribers = []
        with self._lock:
            # Make a copy of subscribers to avoid issues if they unsubscribe during callback
            if event_type in self._subscribers:
                subscribers = list(self._subscribers[event_type])

        if subscribers:
            logger.debug(f"Publishing event '{event_type}' to {len(subscribers)} subscribers")
            for callback in subscribers:
                try:
                    callback(data)
                except Exception as e:
                    logger.error(f"Error in event callback for '{event_type}': {e}", exc_info=True)
        else:
            logger.debug(f"Event '{event_type}' published but no subscribers")


# Global event bus instance
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus