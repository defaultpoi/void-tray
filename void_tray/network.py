"""Connectivity probes and diagnosis."""

import socket
import threading
import time as time_module

from .log import _log


NETWORK_RETRY_SECONDS = 10  # how often to recheck connectivity if offline at startup


OFFLINE_AFTER_FAILED_PROBES = 2  # consecutive failed probes before showing 'offline' (ignores blips)


ONLINE_PROBE_TARGETS = [
    ("1.1.1.1", 443),  # Cloudflare
    ("8.8.8.8", 443),  # Google
    ("9.9.9.9", 443),  # Quad9
]


PROBE_HOSTNAMES = ('cloudflare.com', 'google.com')  # resolved to catch DNS-only outages


DNS_TIMEOUT_SECONDS = 3


GATEWAY_PORTS = (443, 80, 53)


def _tcp_reachable():
    """True if ANY public endpoint accepts a TCP connection on the HTTPS port
    -- raw IPs, so no DNS involved, and nothing tied to this app's own
    dependencies (weather, Twitch, feed hosts)."""
    for host, port in ONLINE_PROBE_TARGETS:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            continue
    return False


_dns_lock = threading.Lock()


_dns_lookup = None  # (thread, result) of the lookup in flight, guarded by _dns_lock


def _dns_ok(timeout=DNS_TIMEOUT_SECONDS):
    """True if one of PROBE_HOSTNAMES resolves within `timeout` seconds.
    getaddrinfo can't be given a timeout, so it runs on a helper thread that
    is abandoned if the resolver hangs. Only one such thread exists at a time:
    a probe that finds the previous lookup still stuck waits for that one
    instead of starting another, so a dead resolver costs one thread rather
    than one per probe (and probes come from several places every few
    seconds)."""
    global _dns_lookup
    with _dns_lock:
        if _dns_lookup is None or not _dns_lookup[0].is_alive():
            result = []

            def lookup(found):
                for hostname in PROBE_HOSTNAMES:
                    try:
                        socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
                        found.append(True)
                        return
                    except OSError:
                        continue
            worker = threading.Thread(target=lookup, args=(result,), daemon=True)
            _dns_lookup = (worker, result)
            worker.start()
        worker, result = _dns_lookup
    worker.join(timeout)
    return bool(result)


def _parse_default_gateway(route_table):
    """The default route's gateway from the text of /proc/net/route, as a
    dotted quad, or None if there is no default route."""
    for line in route_table.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 3 and fields[1] == '00000000' and int(fields[3], 16) & 2:
            raw = bytes.fromhex(fields[2])[::-1]  # stored little-endian
            return '.'.join(str(b) for b in raw)
    return None


def _default_gateway():
    try:
        with open('/proc/net/route') as f:
            return _parse_default_gateway(f.read())
    except (OSError, ValueError):
        return None


def _gateway_answers(gateway):
    """True if the router is alive: it accepts a connection or actively
    refuses one (either proves something answered); silence means it doesn't
    answer, though a firewalled router can also look silent."""
    for port in GATEWAY_PORTS:
        try:
            with socket.create_connection((gateway, port), timeout=1):
                return True
        except ConnectionRefusedError:
            return True
        except OSError:
            continue
    return False


def diagnose_connectivity():
    """{'online': bool, 'cause': str, 'detail': str}. Online means the
    internet is reachable AND hostnames resolve (an outage of either breaks
    every feed). When it isn't, `cause` says which stage failed:
    'dns', 'no-route', 'gateway' or 'internet'."""
    if _tcp_reachable():
        if _dns_ok():
            return {'online': True, 'cause': 'ok', 'detail': ''}
        return {'online': False, 'cause': 'dns',
                'detail': 'DNS lookups fail (the internet is reachable by IP address)'}
    gateway = _default_gateway()
    if gateway is None:
        return {'online': False, 'cause': 'no-route', 'detail': 'no default route (the link is down)'}
    if _gateway_answers(gateway):
        return {'online': False, 'cause': 'internet',
                'detail': f'the router ({gateway}) answers, but the internet is unreachable '
                          '(provider or upstream)'}
    return {'online': False, 'cause': 'gateway', 'detail': f'the router ({gateway}) does not answer'}


def is_online():
    return diagnose_connectivity()['online']


_online_lock = threading.Lock()


_online_waiting = []  # callbacks waiting for the network, guarded by _online_lock


_online_poller_running = False  # a thread is polling for them, guarded by _online_lock


def _wait_for_network():
    """The one poller behind run_when_online: waits until the network is up,
    then starts every callback that has been added meanwhile on a thread of its
    own (one slow callback must not hold up the others)."""
    global _online_poller_running
    while True:
        try:
            if is_online():
                break
        except Exception as e:  # one odd probe must not end the wait for everyone
            _log(f'connectivity probe failed: {type(e).__name__}: {e}')
        time_module.sleep(NETWORK_RETRY_SECONDS)
    with _online_lock:
        callbacks = list(_online_waiting)
        del _online_waiting[:]
        _online_poller_running = False
    for fn in callbacks:
        threading.Thread(target=fn, daemon=True).start()


def run_when_online(fn):
    """Calls fn() on a background thread as soon as the network is up (polling
    every NETWORK_RETRY_SECONDS). is_online() blocks for up to a second per
    probe target, so it must never run on the GTK main loop: while offline
    that froze the tray for seconds at every retry. Every caller shares one
    poller: with a thread per caller, five of them probed the network (and
    could each leave a lookup stuck in a hung resolver) every few seconds."""
    global _online_poller_running
    with _online_lock:
        _online_waiting.append(fn)
        if _online_poller_running:
            return
        _online_poller_running = True
    threading.Thread(target=_wait_for_network, daemon=True).start()


def next_connectivity(online, failures, probe_ok):
    """(online, failures) after one probe: a success means online at once, but
    it takes OFFLINE_AFTER_FAILED_PROBES failures in a row to go offline, so
    one dropped probe doesn't flash the indicator."""
    if probe_ok:
        return True, 0
    failures += 1
    return (online and failures < OFFLINE_AFTER_FAILED_PROBES), failures
