"""
RSS/Atom feed service for void-tray.

Handles feed loading, parsing, muting, highlighting, age filtering,
and unread item tracking using feedparser.
"""

import feedparser
import time as time_module
import threading
import hashlib
import logging
from typing import List, Tuple, Dict, Any, Optional, Set
from ..config.loader import (
    load_feeds, load_mute_filters, is_muted, load_highlight_words,
    title_is_highlighted, load_feed_mute_rules, entry_matches_mute_rules,
    fold_text, get_entry_id, entry_tag_terms, entry_author_name,
    count_feed_terms, escape_mute_value, feed_source_url, CHECK_INTERVAL
)
from ..utils.network import (
    MAX_UNREAD_ITEMS, MAX_ITEM_AGE_SECONDS, SEEN_RETENTION_SECONDS
)
from ..core.state import get_state_manager
from ..core.event_bus import get_event_bus
from ..utils.logger import get_logger
from ..utils.network import fetch_url_with_retry

logger = get_logger(__name__)
EVENT_FEED_UPDATE = 'feed_update'
EVENT_NEW_ITEMS = 'new_items'
FEED_RETRY_SECONDS = 60  # Retry failed feeds after 60 seconds


class FeedService:
    """
    Service for monitoring RSS/Atom feeds and managing unread items.

    Responsibilities:
    - Loading feed configurations from config.conf
    - Periodically checking feeds for new entries
    - Applying muting rules (global and per-feed)
    - Applying highlighting rules
    - Filtering entries by age
    - Tracking seen/unread state
    - Notifying via event bus when new items arrive
    """

    def __init__(self):
        self.state_manager = get_state_manager()
        self.event_bus = get_event_bus()
        self._check_lock = threading.Lock()  # For preventing overlapping checks
        logger.debug("FeedService initialized")

    def start_check_thread(self, force: bool = False) -> None:
        """
        Start a background thread to check for feed updates.

        Args:
            force: If True, check feeds regardless of last check time
        """
        if not self._check_lock.acquire(blocking=False):
            logger.debug("Feed check already in progress, skipping")
            return  # a check is already in flight — skip this tick rather than overlap

        def _check_feeds_guarded():
            try:
                self.check_feeds(force)
                self.check_updates_if_due(force=force)  # Also check updates
            finally:
                self._check_lock.release()

        thread = threading.Thread(target=_check_feeds_guarded, daemon=True)
        thread.start()
        logger.debug("Started feed check thread")

    def check_feeds(self, force: bool = False) -> None:
        """
        Check all configured feeds for new entries.

        Args:
            force: If True, check feeds regardless of last check time
        """
        logger.debug(f"Checking feeds (force={force})")

        # Check connectivity first
        from ..utils.network import is_online
        if not force and not is_online():
            logger.debug("Offline: skipping feed check")
            return

        feeds = load_feeds()
        if not feeds:
            logger.debug("No feeds configured")
            return

        mute_phrases = load_mute_filters()
        highlight_words = load_highlight_words()
        feed_mute_rules = load_feed_mute_rules()

        now = time_module.time()

        # Get current state
        state = self.state_manager.get_state()
        seen_ids = set(state.get('seen', {}).keys())
        last_checked = dict(state.get('last_checked', {}))

        newly_seen_ids = set()
        feed_links = {}
        feed_terms = {}
        due_urls = []
        intervals = {}

        # Determine which feeds are due for checking
        for url, custom_name, interval_seconds in feeds:
            intervals[url] = interval_seconds
            last = last_checked.get(url, 0)
            if force or (now - last) >= interval_seconds:
                due_urls.append(url)

        logger.debug(f"Checking {len(due_urls)} feeds due for update")

        new_items = []
        # Check each due feed
        for url in due_urls:
            custom_name = None
            interval_seconds = intervals[url]
            # Find custom_name for this URL
            for feed_url, name, interval in feeds:
                if feed_url == url:
                    custom_name = name
                    break

            retry_soon = now - intervals[url] + min(intervals[url], FEED_RETRY_SECONDS)
            try:
                parsed = feedparser.parse(url)
            except Exception as e:
                logger.warning(f"Failed to parse feed {url}: {e}")
                last_checked[url] = retry_soon
                self._note_request_failure()
                continue

            if getattr(parsed, 'bozo', False) and not parsed.entries:
                logger.warning(f"Feed {url} appears to be malformed: {getattr(parsed, 'bozo_exception', 'Unknown error')}")
                last_checked[url] = retry_soon
                self._note_request_failure()
                continue

            last_checked[url] = now
            site_link = (getattr(parsed, 'feed', None) or {}).get('link')
            if isinstance(site_link, str) and site_link:
                feed_links[url] = site_link
            feed_terms[url] = count_feed_terms(parsed.entries)

            for entry in parsed.entries:
                eid = get_entry_id(entry, url)
                if eid in seen_ids:
                    continue  # Already seen

                seen_ids.add(eid)
                newly_seen_ids.add(eid)

                title = entry.get('title', '(untitled)')
                if is_muted(title, mute_phrases) or entry_matches_mute_rules(entry, feed_mute_rules.get(url, ())):
                    logger.debug(f"Item muted: {title[:50]}...")
                    continue  # matches mute filters — mark as seen, don't surface

                age = self._entry_age_seconds(entry)
                if age is not None and age > MAX_ITEM_AGE_SECONDS:
                    logger.debug(f"Item too old (age={age}s): {title[:50]}...")
                    continue  # too old — mark as seen, don't surface

                item = {
                    'id': eid,
                    'title': title,
                    'link': entry.get('link', ''),
                    'feed_url': url,
                    'feed_title': custom_name or url,
                    'published': entry.get('published', ''),
                    'updated': entry.get('updated', ''),
                }

                # Add tags if present
                tags = entry_tag_terms(entry)
                if tags:
                    item['tags'] = tags

                # Add author if present
                author = entry_author_name(entry)
                if author:
                    item['author'] = author

                # Check if this item should be highlighted
                if title_is_highlighted(title, highlight_words):
                    item['highlighted'] = True

                new_items.append(item)

            logger.debug(f"Feed {url}: found {len(new_items)} new items")

        # Update state with new information
        def updater(state_dict):
            # Merge seen entries
            seen = state_dict.get('seen', {})
            for eid in newly_seen_ids:
                seen.setdefault(eid, now)
            # Apply retention limit
            cutoff = now - SEEN_RETENTION_SECONDS
            seen = {eid: ts for eid, ts in seen.items() if ts >= cutoff}
            state_dict['seen'] = seen

            # Update last checked
            last_checked_dict = state_dict.get('last_checked', {})
            last_checked_dict.update(last_checked)
            state_dict['last_checked'] = last_checked_dict

            # Update feed links
            feed_links_dict = state_dict.get('feed_links', {})
            feed_links_dict.update(feed_links)
            state_dict['feed_links'] = feed_links_dict

            # Update feed terms
            feed_terms_dict = state_dict.get('feed_terms', {})
            feed_terms_dict.update(feed_terms)
            state_dict['feed_terms'] = feed_terms_dict

            # Handle new items
            if new_items:
                existing_ids = {e['id'] for e in state_dict.get('unread', [])}
                # Filter out duplicates
                truly_new_items = [e for e in new_items if e['id'] not in existing_ids]
                if truly_new_items:
                    # Prepend new items and apply limit
                    combined = truly_new_items + state_dict.get('unread', [])
                    state_dict['unread'] = combined[:MAX_UNREAD_ITEMS]
                    logger.info(f"Added {len(truly_new_items)} new unread items")

                    # Publish events
                    self.event_bus.publish(EVENT_NEW_ITEMS, truly_new_items)
                    self.event_bus.publish(EVENT_FEED_UPDATE, {
                        'feeds_checked': len(due_urls),
                        'new_items': len(truly_new_items)
                    })
                else:
                    # Still publish update event even if no new items
                    self.event_bus.publish(EVENT_FEED_UPDATE, {
                        'feeds_checked': len(due_urls),
                        'new_items': 0
                    })
            else:
                # No new items at all
                self.event_bus.publish(EVENT_FEED_UPDATE, {
                    'feeds_checked': len(due_urls),
                    'new_items': 0
                })

        self.state_manager.update_state(updater)

        # Save state to disk
        self.state_manager.save_to_disk()

        # Play notification sound if we had new items
        if newly_seen_ids:
            self._play_notification_sound()

    def _note_request_failure(self) -> None:
        """Note a failed request for connectivity diagnostics."""
        # This would typically trigger a network probe
        # For now, we'll just log it
        logger.debug("Request failure noted")
        # In the full implementation, this would call self.state_manager.note_request_failure()
        # which would trigger a connectivity probe

    def _play_notification_sound(self) -> None:
        """Play notification sound for new items."""
        try:
            from ..utils.network import play_notification_sound
            play_notification_sound()
        except ImportError:
            logger.debug("Notification sound utility not available")

    def _entry_age_seconds(self, entry: Dict[str, Any]) -> Optional[float]:
        """
        Returns seconds since publish/update time, or None if no date info available.
        """
        parsed = entry.get('published_parsed') or entry.get('updated_parsed')
        if parsed:
            return time_module.time() - time_module.mktime(parsed)
        return None

    def check_updates_if_due(self, force: bool = False) -> None:
        """
        Check for system updates if due.
        Delegates to the UpdatesService to avoid duplication.
        """
        # This would be implemented by delegating to UpdatesService
        # For now, we'll just call a placeholder
        logger.debug("Update check delegated to UpdatesService")
        # In practice, we'd have a reference to UpdatesService and call its method

    def shutdown(self) -> None:
        """Cleanup resources."""
        logger.debug("FeedService shutdown")


# Global service instance
_feed_service: Optional[FeedService] = None


def get_feed_service() -> FeedService:
    """Get the global feed service instance."""
    global _feed_service
    if _feed_service is None:
        _feed_service = FeedService()
    return _feed_service