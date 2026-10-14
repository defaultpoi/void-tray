"""
Network utilities for void-tray.

Handles HTTP requests with retry logic, timeout management,
and detailed connectivity diagnostics (TCP, DNS, gateway checks).
"""

import socket
import time as time_module
import urllib.request
import urllib.error
import urllib.parse
from typing import Optional, Tuple, Dict, Any
import logging

logger = logging.getLogger(__name__)

# Network constants
NETWORK_TIMEOUT_SECONDS = 15
NETWORK_RETRY_SECONDS = 5
OFFLINE_AFTER_FAILED_PROBES = 2
CONFIRM_PROBE_SECONDS = 30
NETWORK_RESET_COOLDOWN_SECONDS = 60
FEED_RETRY_SECONDS = 5 * 60  # 5 minutes
BREAKING_PULSE_MS = 500
ALERT_PULSE_INTERVAL_MS = 1000
SCHEDULER_TICK_SECONDS = 30
OFFLINE_PROBE_SECONDS = 5
STREAMLINK_READY_TIMEOUT_SECONDS = 20
PLAYER_KILL_TIMEOUT_SECONDS = 5
TEMP_RING_RADIUS = 7
TEMP_RING_LINE_WIDTH = 2
PORT_MIN = 15000
PORT_MAX = 25000
MAX_UNREAD_ITEMS = 500
MAX_ITEM_AGE_SECONDS = 60 * 60 * 24  # 24 hours
SEEN_RETENTION_SECONDS = 60 * 60 * 24 * 30  # 30 days
OFFLINE_AFTER_FAILED_PROBES = 2
GATEWAY_PORTS = [80, 443, 53, 22, 21]


def _tcp_reachable(host: str = "1.1.1.1", port: int = 53, timeout: float = NETWORK_TIMEOUT_SECONDS) -> bool:
    """Check if we can establish a TCP connection to the given host:port."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return True
    except (socket.error, OSError):
        return False
    finally:
        try:
            sock.close()
        except:
            pass


def _dns_ok(hostname: str = "www.google.com", timeout: float = NETWORK_TIMEOUT_SECONDS) -> bool:
    """Check if DNS resolution works for the given hostname."""
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(hostname, 80)
        socket.setdefaulttimeout(None)  # Reset to default
        return True
    except socket.gaierror:
        return False
    finally:
        socket.setdefaulttimeout(None)


def _default_gateway() -> Optional[str]:
    """Get the default gateway IP address from the system route table."""
    try:
        with open('/proc/net/route', 'r') as f:
            for line in f.readlines()[1:]:  # Skip header
                parts = line.strip().split()
                if len(parts) < 8:
                    continue
                if parts[1] != '00000000' or not int(parts[7], 16) & 2:
                    continue  # Not default route or not gateway
                return socket.inet_ntoa(struct.pack("<L", int(parts[2], 16)))
    except (FileNotFoundError, ValueError, IndexError, OSError):
        # Fallback for non-Linux or permission issues
        pass
    return None


def _gateway_answers(gateway_ip: str, timeout: float = NETWORK_TIMEOUT_SECONDS) -> bool:
    """Check if the gateway answers TCP connections on common ports."""
    if not gateway_ip:
        return False

    for port in GATEWAY_PORTS:
        if _tcp_reachable(gateway_ip, port, timeout=1.0):  # Short timeout for gateway check
            return True
    return False


def diagnose_connectivity() -> Dict[str, Any]:
    """
    Perform detailed connectivity diagnosis.

    Returns:
        Dict with keys:
        - online: bool (overall connectivity status)
        - cause: str (one of: 'ok', 'no-route', 'dns', 'gateway', 'internet')
        - detail: str (human-readable description)
    """
    # Step 1: Check if we have a default route/gateway
    gateway = _default_gateway()
    if not gateway:
        return {
            'online': False,
            'cause': 'no-route',
            'detail': 'No default route found'
        }

    # Step 2: Check DNS resolution
    if not _dns_ok():
        return {
            'online': False,
            'cause': 'dns',
            'detail': 'DNS resolution failed'
        }

    # Step 3: Check if gateway answers
    if not _gateway_answers(gateway):
        return {
            'online': False,
            'cause': 'gateway',
            'detail': f'Gateway {gateway} not responding'
        }

    # Step 4: Check upstream internet connectivity
    if not _tcp_reachable():  # Default to 1.1.1.1:53 (Cloudflare DNS)
        return {
            'online': False,
            'cause': 'internet',
            'detail': 'Upstream internet not reachable'
        }

    # All checks passed
    return {
        'online': True,
        'cause': 'ok',
        'detail': ''
    }


def is_online() -> bool:
    """Quick check for online status (just the boolean result)."""
    return diagnose_connectivity()['online']


def fetch_url_with_retry(
    url: str,
    timeout: float = NETWORK_TIMEOUT_SECONDS,
    max_retries: int = 3,
    backoff_factor: float = 0.5
) -> Optional[bytes]:
    """
    Fetch a URL with retry logic and exponential backoff.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds
        max_retries: Maximum number of retry attempts
        backoff_factor: Backoff multiplier (wait time = backoff_factor * (2 ** retry))

    Returns:
        Response content as bytes, or None if all attempts failed
    """
    import urllib.request
    import urllib.error

    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={'User-Agent': 'void-tray/1.0'}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, OSError) as e:
            last_exception = e
            if attempt < max_retries:
                wait_time = backoff_factor * (2 ** attempt)
                logger.debug(f"Fetch failed (attempt {attempt + 1}), retrying in {wait_time}s: {e}")
                time_module.sleep(wait_time)
            else:
                logger.debug(f"Fetch failed after {max_retries + 1} attempts: {e}")

    return None


def fetch_json_with_retry(
    url: str,
    timeout: float = NETWORK_TIMEOUT_SECONDS,
    max_retries: int = 3
) -> Optional[Dict[str, Any]]:
    """
    Fetch and parse JSON from a URL with retry logic.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds
        max_retries: Maximum number of retry attempts

    Returns:
        Parsed JSON as dict, or None if failed
    """
    import json

    data = fetch_url_with_retry(url, timeout=timeout, max_retries=max_retries)
    if data is None:
        return None

    try:
        return json.loads(data.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"Failed to parse JSON from {url}: {e}")
        return None


def parse_iso_timestamp(timestamp_str: str) -> Optional[float]:
    """
    Parse ISO 8601 timestamp to Unix timestamp.

    Args:
        timestamp_str: ISO 8601 formatted string (e.g., '2026-10-11T10:00:00Z')

    Returns:
        Unix timestamp as float, or None if parsing failed
    """
    try:
        # Handle Zulu time and timezone offsets
        if timestamp_str.endswith('Z'):
            timestamp_str = timestamp_str[:-1] + '+00:00'

        # Parse with strptime (limited but works for common formats)
        # For production, consider using dateutil.parser or pendulum
        if '+' in timestamp_str or '-' in timestamp_str[-6:]:
            # Has timezone offset
            dt_str, tz_offset = timestamp_str.rsplit('+', 1) if '+' in timestamp_str[-6:] else timestamp_str.rsplit('-', 1)
            if '-' in dt_str:  # Put the minus back if it was part of the time
                dt_str = dt_str + '-'
            # Simple handling - ignore offset for now (assume UTC)
            # Proper implementation would parse the offset
            dt_str = timestamp_str.split('+')[0].split('-')[0]  # This is rough

        # Try common formats
        formats = [
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%S.%f',
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d'
        ]

        for fmt in formats:
            try:
                dt = time_module.strptime(timestamp_str, fmt)
                return time_module.mktime(dt)
            except ValueError:
                continue

        logger.warning(f"Unable to parse timestamp: {timestamp_str}")
        return None
    except Exception as e:
        logger.warning(f"Error parsing timestamp {timestamp_str}: {e}")
        return None


def _free_local_port() -> int:
    """Find a free local port for streamlink HTTP server."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('', 0))
        port = sock.getsockname()[1]
        sock.close()
        return port
    finally:
        sock.close()


# Import struct for _default_gateway (moved here to avoid circular imports)
try:
    import struct
except ImportError:
    pass