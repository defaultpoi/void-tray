"""
Weather service for void-tray.

Handles weather data fetching from Open-Meteo and severe weather alerts
from MeteoAlarm, including reverse geocoding for alert filtering.
"""

import time as time_module
import threading
import json
import logging
from typing import Dict, Any, Optional, Tuple, List
import feedparser
from ..config.loader import (
    load_weather_settings, load_severe_weather_settings,
    get_weather_location, get_weather_update_interval,
    get_severe_weather_update_interval
)
from ..core.state import get_state_manager
from ..core.event_bus import get_event_bus
from ..utils.logger import get_logger
from ..utils.network import fetch_json_with_retry, fetch_url_with_retry, is_online, diagnose_connectivity

logger = get_logger(__name__)
EVENT_WEATHER_UPDATE = 'weather_update'
EVENT_SEVERE_WEATHER_UPDATE = 'severe_weather_update'
EVENT_SEVERE_WEATHER_ERROR = 'severe_weather_error'
EVENT_WEATHER_ERROR = 'weather_error'


class WeatherService:
    """
    Service for fetching weather data and severe weather alerts.

    Responsibilities:
    - Fetching weather data from Open-Meteo API
    - Fetching severe weather alerts from MeteoAlarm API
    - Reverse geocoding via OpenStreetMap Nominatim for alert filtering
    - Error handling and retry logic
    - Background polling with operation-level locking
    - Notifying via event bus when weather data updates
    """

    # API endpoints
    OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
    METEOALARM_API = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-{country}"
    NOMINATIM_API = "https://nominatim.openstreetmap.org/reverse"

    def __init__(self):
        self.state_manager = get_state_manager()
        self.event_bus = get_event_bus()
        self._weather_lock = threading.Lock()  # For preventing overlapping weather checks
        self._severe_weather_lock = threading.Lock()  # For preventing overlapping severe weather checks
        logger.debug("WeatherService initialized")

    def start_weather_check_thread(self, force: bool = False) -> None:
        """
        Start a background thread to check for weather updates.

        Args:
            force: If True, check weather regardless of last check time
        """
        if not self._weather_lock.acquire(blocking=False):
            logger.debug("Weather check already in progress, skipping")
            return

        def _check_weather_guarded():
            try:
                self.check_weather(force)
            finally:
                self._weather_lock.release()

        thread = threading.Thread(target=_check_weather_guarded, daemon=True)
        thread.start()
        logger.debug("Started weather check thread")

    def start_severe_weather_check_thread(self, force: bool = False) -> None:
        """
        Start a background thread to check for severe weather updates.

        Args:
            force: If True, check severe weather regardless of last check time
        """
        if not self._severe_weather_lock.acquire(blocking=False):
            logger.debug("Severe weather check already in progress, skipping")
            return

        def _check_severe_weather_guarded():
            try:
                self.check_severe_weather(force)
            finally:
                self._severe_weather_lock.release()

        thread = threading.Thread(target=_check_severe_weather_guarded, daemon=True)
        thread.start()
        logger.debug("Started severe weather check thread")

    def check_weather(self, force: bool = False) -> None:
        """
        Check for weather data updates.

        Args:
            force: If True, check weather regardless of last check time
        """
        logger.debug(f"Checking weather (force={force})")

        # Check if weather is enabled
        settings = load_weather_settings()
        if not settings.get('enabled', True):
            logger.debug("Weather service disabled")
            return

        # Check connectivity first (only for non-force checks)
        if not force and not is_online():
            logger.debug("Offline: skipping weather check")
            return

        location = get_weather_location()
        if not location:
            logger.debug("No weather location configured")
            return

        latitude, longitude = location

        # Info log to confirm we're processing coordinates
        logger.info(f"Processing weather location: {latitude}, {longitude}")

        # Debug: log raw coordinates for troubleshooting
        logger.debug(f"Raw weather coordinates received: latitude='{latitude}' (type: {type(latitude)}), longitude='{longitude}' (type: {type(longitude)})")

        # Validate coordinates
        if latitude is None or longitude is None:
            logger.debug(f"Invalid weather location coordinates: {latitude}, {longitude}")
            return

        try:
            lat_float = float(latitude)
            lon_float = float(longitude)
            if not (-90 <= lat_float <= 90) or not (-180 <= lon_float <= 180):
                logger.debug(f"Weather location coordinates out of valid range: {latitude}, {longitude}")
                return
        except (ValueError, TypeError):
            logger.debug(f"Weather location coordinates are not valid numbers: {latitude}, {longitude}")
            return

        interval = get_weather_update_interval()

        now = time_module.time()
        state = self.state_manager.get_state()
        last_checked = state.get('weather_last_checked', 0)

        if not force and (now - last_checked) < interval:
            logger.debug(f"Weather not due for check yet (last checked {now - last_checked:.0f}s ago)")
            return

        logger.debug(f"Fetching weather for coordinates {latitude}, {longitude}")

        # Build Open-Meteo API request
        params = {
            'latitude': lat_float,
            'longitude': lon_float,
            'current_weather': 'true',
            'hourly': 'temperature_2m,relativehumidity_2m,weathercode,windspeed_10m',
            'daily': 'weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,windspeed_10m_max',
            'timezone': 'auto'
        }

        try:
            # Construct URL with parameters
            from urllib.parse import urlencode
            url = f"{self.OPEN_METEO_API}?{urlencode(params)}"
            logger.debug(f"Fetching weather data from URL: {url}")

            data = fetch_json_with_retry(url)
            if data is None:
                logger.warning("Failed to fetch weather data from Open-Meteo")
                logger.debug(f"Failed URL was: {url}")
                self._handle_weather_error("Failed to fetch weather data")
                return

            # Process the weather data
            weather_data = self._process_weather_data(data)

            # Update state
            def updater(state_dict):
                state_dict['weather_data'] = weather_data
                state_dict['weather_last_checked'] = now
                state_dict['weather_error'] = None
                # Clear error flag on successful fetch
                if 'weather_error' in state_dict:
                    del state_dict['weather_error']

            self.state_manager.update_state(updater)
            self.state_manager.save_to_disk()

            # Publish event
            self.event_bus.publish(EVENT_WEATHER_UPDATE, weather_data)
            logger.debug("Weather data updated successfully")

        except Exception as e:
            logger.error(f"Error checking weather: {e}", exc_info=True)
            self._handle_weather_error(str(e))

    def check_severe_weather(self, force: bool = False) -> None:
        """
        Check for severe weather alerts.

        Args:
            force: If True, check severe weather regardless of last check time
        """
        logger.debug(f"Checking severe weather (force={force})")

        # Check if severe weather is enabled
        settings = load_severe_weather_settings()
        if not settings.get('enabled', True):
            logger.debug("Severe weather service disabled")
            return

        # Check connectivity first (only for non-force checks)
        if not force and not is_online():
            logger.debug("Offline: skipping severe weather check")
            return

        location = get_weather_location()
        if not location:
            logger.debug("No weather location configured")
            return

        latitude, longitude = location

        # Info log to confirm we're processing coordinates
        logger.info(f"Processing severe weather location: {latitude}, {longitude}")

        # Debug: log raw coordinates for troubleshooting
        logger.debug(f"Raw severe weather coordinates received: latitude='{latitude}' (type: {type(latitude)}), longitude='{longitude}' (type: {type(longitude)})")
        interval = get_severe_weather_update_interval()

        now = time_module.time()
        state = self.state_manager.get_state()
        last_checked = state.get('severe_weather_last_checked', 0)

        if not force and (now - last_checked) < interval:
            logger.debug(f"Severe weather not due for check yet (last checked {now - last_checked:.0f}s ago)")
            return

        logger.debug(f"Fetching severe weather alerts for coordinates {latitude}, {longitude}")

        # First, get reverse geocoding to determine region/country for alert filtering
        try:
            region_info = self._get_region_info(latitude, longitude)
            if not region_info:
                logger.warning("Failed to get region info for severe weather filtering")
                # Continue anyway - we'll fetch all alerts and filter later if needed

            # Build MeteoAlarm API request using country-level feed
            # Get country from config or reverse geocoding for the URL
            country_name = None
            # Get region from config for filtering (not for URL)
            region_name = None

            try:
                # Get weather settings
                weather_settings = load_weather_settings()
                # Get country for the MeteoAlarm feed URL
                country_name = weather_settings.get('country')
                # Get region for filtering alerts
                region_name = weather_settings.get('region')

                # Normalize region name for matching: remove diacritics, convert to lowercase
                normalized_region_name = None
                if region_name:
                    import unicodedata
                    # Normalize to remove diacritics
                    normalized = unicodedata.normalize('NFD', region_name)
                    # Remove diacritical marks
                    normalized_region_name = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
                    # Convert to lowercase for case-insensitive matching
                    normalized_region_name = normalized_region_name.lower()
            except Exception as e:
                logger.debug(f"Could not load country/region from weather settings: {e}")

            # Fallback to reverse geocoding if config values not available
            if not country_name and region_info:
                # Try to get country from reverse geocoding first
                country_name = region_info.get('country')
                # Get region for filtering if not available from config
                if not region_name:
                    region_name = region_info.get('county') or region_info.get('state')

                # Normalize region name for matching if we got it from reverse geocoding
                if region_name and not normalized_region_name:
                    import unicodedata
                    normalized = unicodedata.normalize('NFD', region_name)
                    # Remove diacritical marks
                    normalized_region_name = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
                    # Convert to lowercase for case-insensitive matching
                    normalized_region_name = normalized_region_name.lower()

            if not country_name:
                logger.warning("Could not determine country for severe weather alerts")
                # Final fallback to coordinates-based request (though this likely won't work)
                params = {
                    'latitude': latitude,
                    'longitude': longitude,
                    'lang': 'en'
                }
                from urllib.parse import urlencode
                url = f"{self.METEOALARM_API}?{urlencode(params)}"
            else:
                # Use the country name for the URL (MeteoAlarm uses country-level feeds)
                # Normalize country name to create a slug: lowercase and replace non-alphanumeric with hyphens
                import re
                country_slug = re.sub(r'[^a-z0-9]+', '-', country_name.lower()).strip('-')
                url = self.METEOALARM_API.format(country=country_slug)
                # Note: The feed URL doesn't require additional query parameters
                logger.debug(f"Using country '{country_name}' (slug: '{country_slug}') for MeteoAlarm URL")
                if normalized_region_name:
                    logger.debug(f"Filtering alerts for region: '{normalized_region_name}' (from '{region_name}')")

            logger.debug(f"Fetching severe weather data from URL: {url}")
            # Fetch the XML feed content
            xml_content = fetch_url_with_retry(url)
            if xml_content is None:
                logger.warning("Failed to fetch severe weather data from MeteoAlarm")
                logger.debug(f"Failed URL was: {url}")
                self._handle_severe_weather_error("Failed to fetch severe weather data")
                return

            # Parse the XML feed
            try:
                parsed_data = feedparser.parse(xml_content)
                if getattr(parsed_data, 'bozo', False):
                    logger.warning(f"Failed to parse MeteoAlarm feed: {getattr(parsed_data, 'bozo_exception', 'Unknown error')}")
                    self._handle_severe_weather_error("Failed to parse severe weather data")
                    return
            except Exception as e:
                logger.error(f"Error parsing MeteoAlarm feed: {e}", exc_info=True)
                self._handle_severe_weather_error("Failed to parse severe weather data")
                return

            # Process the severe weather data
            severe_weather_data = self._process_severe_weather_data(parsed_data, normalized_region_name)

            # Update state
            def updater(state_dict):
                state_dict['severe_weather_data'] = severe_weather_data
                state_dict['severe_weather_last_checked'] = now
                state_dict['severe_weather_error'] = None
                # Clear error flag on successful fetch
                if 'severe_weather_error' in state_dict:
                    del state_dict['severe_weather_error']

            self.state_manager.update_state(updater)
            self.state_manager.save_to_disk()

            # Publish event
            self.event_bus.publish(EVENT_SEVERE_WEATHER_UPDATE, severe_weather_data)
            logger.debug("Severe weather data updated successfully")

        except Exception as e:
            logger.error(f"Error checking severe weather: {e}", exc_info=True)
            self._handle_severe_weather_error(str(e))

    def _process_weather_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process raw Open-Meteo API data into our internal format.

        Args:
            data: Raw JSON response from Open-Meteo API

        Returns:
            Processed weather data dictionary
        """
        try:
            current = data.get('current_weather', {})
            hourly = data.get('hourly', {})
            daily = data.get('daily', {})

            # Extract current conditions
            processed = {
                'temperature': current.get('temperature'),
                'windspeed': current.get('windspeed'),
                'winddirection': current.get('winddirection'),
                'weathercode': current.get('weathercode'),
                'time': current.get('time'),
                'is_day': current.get('is_day'),
            }

            # Add hourly forecast (next few hours)
            if hourly and 'time' in hourly:
                times = hourly['time'][:6]  # Next 6 hours
                temps = hourly.get('temperature_2m', [])[:6]
                humidities = hourly.get('relativehumidity2m', [])[:6]
                windspeeds = hourly.get('windspeed10m', [])[:6]
                weathercodes = hourly.get('weathercode', [])[:6]

                processed['hourly'] = []
                for i, t in enumerate(times):
                    processed['hourly'].append({
                        'time': t,
                        'temperature': temps[i] if i < len(temps) else None,
                        'humidity': humidities[i] if i < len(humidities) else None,
                        'windspeed': windspeeds[i] if i < len(windspeeds) else None,
                        'weathercode': weathercodes[i] if i < len(weathercodes) else None,
                    })

            # Add daily forecast
            if daily and 'time' in daily:
                times = daily['time'][:7]  # Next 7 days
                temp_max = daily.get('temperature_2m_max', [])[:7]
                temp_min = daily.get('temperature_2m_min', [])[:7]
                precipitation = daily.get('precipitation_sum', [])[:7]
                precipitation_prob = daily.get('precipitation_probability_max', [])[:7]
                windspeed_max = daily.get('windspeed10m_max', [])[:7]
                weathercodes = daily.get('weathercode', [])[:7]

                processed['daily'] = []
                for i, t in enumerate(times):
                    processed['daily'].append({
                        'time': t,
                        'temperature_max': temp_max[i] if i < len(temp_max) else None,
                        'temperature_min': temp_min[i] if i < len(temp_min) else None,
                        'precipitation': precipitation[i] if i < len(precipitation) else None,
                        'precipitation_probability': precipitation_prob[i] if i < len(precipitation_prob) else None,
                        'windspeed_max': windspeed_max[i] if i < len(windspeed_max) else None,
                        'weathercode': weathercodes[i] if i < len(weathercodes) else None,
                    })

                # Add today's forecast data for easy access by WeatherHeader
                if len(processed['daily']) > 0:
                    today = processed['daily'][0]
                    processed['temp_max'] = today.get('temperature_max')
                    processed['temp_min'] = today.get('temperature_min')
                    processed['precip_mm'] = today.get('precipitation')
                    processed['precip_chance'] = today.get('precipitation_probability')
                    processed['windspeed_max'] = today.get('windspeed_max')

            return processed

        except Exception as e:
            logger.error(f"Error processing weather data: {e}", exc_info=True)
            # Return minimal structure to avoid breaking UI
            return {
                'temperature': None,
                'windspeed': None,
                'winddirection': None,
                'weathercode': None,
                'time': None,
                'is_day': None,
                'hourly': [],
                'daily': []
            }

    def _process_severe_weather_data(self, data: Any, region_filter: Optional[str]) -> Dict[str, Any]:
        """
        Process raw MeteoAlarm API data into our internal format.

        Args:
            data: Parsed feed data from feedparser.parse()
            region_filter: Normalized region name to filter alerts by (lowercase, diacritics removed),
                          or None to include all alerts

        Returns:
            Processed severe weather data dictionary
        """
        try:
            entries = getattr(data, 'entries', [])

            # Filter alerts by region if we have a region filter
            filtered_entries = entries
            if region_filter and entries:
                filtered_entries = []
                for entry in entries:
                    area_desc = getattr(entry, 'cap_areadesc', '')
                    if area_desc:
                        # Normalize the area description for comparison
                        import unicodedata
                        normalized_area = unicodedata.normalize('NFD', str(area_desc))
                        normalized_area = ''.join(c for c in normalized_area if unicodedata.category(c) != 'Mn')
                        normalized_area = normalized_area.lower()

                        # Check if the normalized area description matches our filter
                        if normalized_area == region_filter:
                            filtered_entries.append(entry)

            processed_alerts = []
            for entry in filtered_entries:
                processed_alert = {
                    'id': getattr(entry, 'id', None),
                    'event': getattr(entry, 'cap_event', None),
                    'severity': getattr(entry, 'cap_severity', None),
                    'certainty': getattr(entry, 'cap_certainty', None),
                    'urgency': getattr(entry, 'cap_urgency', None),
                    'onset': getattr(entry, 'cap_onset', None),
                    'ends': getattr(entry, 'cap_expires', None),
                    'sender_name': getattr(entry, 'sender_name', None),
                    'headline': getattr(entry, 'cap_headline', None),
                    'description': getattr(entry, 'cap_description', None),
                    'instruction': getattr(entry, 'cap_instruction', None),
                    'web': getattr(entry, 'cap_web', None),
                    'contact': getattr(entry, 'cap_contact', None),
                    'area': {
                        'areaDesc': getattr(entry, 'cap_areadesc', None),
                        'polygon': getattr(entry, 'cap_polygon', None),
                        'geocode': getattr(entry, 'cap_geocode', None),
                    },
                    'parameters': {},  # CAP parameters would go here if needed
                }
                processed_alerts.append(processed_alert)

            return {
                'alerts': processed_alerts,
                'total_count': len(processed_alerts),
                'regions_affected': list(set(a.get('areaDesc') for a in
                                          (alert.get('area', {}) for alert in processed_alerts)
                                          if a.get('areaDesc'))),
                'last_updated': getattr(data, 'updated', None) or getattr(data, 'generated', None)
            }

        except Exception as e:
            logger.error(f"Error processing severe weather data: {e}", exc_info=True)
            return {
                'alerts': [],
                'total_count': 0,
                'regions_affected': [],
                'last_updated': None
            }

    def _get_region_info(self, latitude: float, longitude: float) -> Optional[Dict[str, str]]:
        """
        Get region information via reverse geocoding for alert filtering.

        Args:
            latitude: Latitude coordinate
            longitude: Longitude coordinate

        Returns:
            Dictionary with region information or None if failed
        """
        try:
            params = {
                'lat': latitude,
                'lon': longitude,
                'format': 'jsonv2',
                'addressdetails': 1
            }

            from urllib.parse import urlencode
            url = f"{self.NOMINATIM_API}?{urlencode(params)}"

            # Add a user agent as required by Nominatim's terms of service
            import urllib.request
            request = urllib.request.Request(
                url,
                headers={'User-Agent': 'void-tray/1.0 (https://github.com/void-linux/void-tray)'}
            )

            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))

            address = data.get('address', {})
            return {
                'country': address.get('country', ''),
                'country_code': address.get('country_code', ''),
                'state': address.get('state', ''),
                'county': address.get('county', ''),
                'city': address.get('city', address.get('town', address.get('village', ''))),
                'postcode': address.get('postcode', '')
            }

        except Exception as e:
            logger.debug(f"Reverse geocoding failed: {e}")
            return None

    def _handle_weather_error(self, error_message: str) -> None:
        """Handle weather service errors."""
        logger.error(f"Weather service error: {error_message}")

        def updater(state_dict):
            state_dict['weather_error'] = error_message
            state_dict['weather_last_checked'] = time_module.time()

        self.state_manager.update_state(updater)
        self.state_manager.save_to_disk()

        # Publish error event
        self.event_bus.publish(EVENT_WEATHER_ERROR, {'message': error_message})

    def _handle_severe_weather_error(self, error_message: str) -> None:
        """Handle severe weather service errors."""
        logger.error(f"Severe weather service error: {error_message}")

        def updater(state_dict):
            state_dict['severe_weather_error'] = error_message
            state_dict['severe_weather_last_checked'] = time_module.time()

        self.state_manager.update_state(updater)
        self.state_manager.save_to_disk()

        # Publish error event
        self.event_bus.publish(EVENT_SEVERE_WEATHER_ERROR, {'message': error_message})

    def shutdown(self) -> None:
        """Cleanup resources."""
        logger.debug("WeatherService shutdown")


# Global service instance
_weather_service: Optional[WeatherService] = None


def get_weather_service() -> WeatherService:
    """Get the global weather service instance."""
    global _weather_service
    if _weather_service is None:
        _weather_service = WeatherService()
    return _weather_service