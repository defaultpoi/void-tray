"""Twitch and YouTube live checks and their display formatting."""

import json
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .constants import YOUTUBE_CHECK_TIMEOUT_SECONDS
from .log import _log


TWITCH_GQL_URL = "https://gql.twitch.tv/gql"


TWITCH_GQL_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # Twitch's own public web-client ID —


TWITCH_BATCH_SIZE = 20  # operations per GQL request


def _check_twitch_batch(channels):
    """One batched GQL POST for `channels`; {channel: {'title', 'category'}}
    for the live ones, or None if the request failed."""
    payload = json.dumps([
        {
            "operationName": "StreamMetadata",
            "query": "query StreamMetadata($channelLogin: String!) { "
                     "user(login: $channelLogin) { stream { type title viewersCount createdAt game { name } } } }",
            "variables": {"channelLogin": channel},
        }
        for channel in channels
    ]).encode('utf-8')
    req = urllib.request.Request(
        TWITCH_GQL_URL,
        data=payload,
        headers={
            'Client-Id': TWITCH_GQL_CLIENT_ID,
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            results = json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None  # None = request failed (distinct from {} = nobody live)
    if not isinstance(results, list):
        return None
    live = {}
    for channel, result in zip(channels, results):
        user = (result.get('data') or {}).get('user')
        if not user:
            continue
        stream = user.get('stream')
        if stream and stream.get('type') == 'live':
            viewers = stream.get('viewersCount')
            live[channel] = {
                'title': stream.get('title') or '',
                'category': (stream.get('game') or {}).get('name') or '',
                'viewers': viewers if isinstance(viewers, int) else None,
                'started': parse_iso_timestamp(stream.get('createdAt')),
            }
    return live


def check_twitch_live_channels(channels):
    """Uses Twitch's internal (unofficial) GraphQL API — the same one twitch.tv
    itself uses for logged-out visitors — so no app registration/secret is
    needed. Undocumented; could break if Twitch changes their internal schema.
    Channels are batched into JSON-array POSTs of TWITCH_BATCH_SIZE (one
    request per channel would be wasteful, one giant request risks rejection).
    Returns {channel: {'title', 'category'}} for whichever channels are
    currently live; a failed/offline channel is simply absent from the
    result, not marked False. Returns None if any request failed, so the
    caller keeps its last known state rather than acting on a partial view."""
    live = {}
    for start in range(0, len(channels), TWITCH_BATCH_SIZE):
        batch = _check_twitch_batch(channels[start:start + TWITCH_BATCH_SIZE])
        if batch is None:
            return None
        live.update(batch)
    return live


def _parse_streamlink_json(output):
    """json.loads() on `streamlink --json` output, tolerating log lines before
    the object (some streamlink versions print their log to stdout)."""
    start = output.find('{')
    if start < 0:
        raise ValueError('no JSON object in output')
    return json.loads(output[start:])


YOUTUBE_PAGE_TIMEOUT_SECONDS = 15


YOUTUBE_PAGE_MAX_BYTES = 4 * 1024 * 1024


def _json_after(html, name):
    """The JSON object assigned to `name` in the page's scripts, or None."""
    match = re.search(re.escape(name) + r'\s*=\s*', html)
    if not match:
        return None
    try:
        return json.JSONDecoder().raw_decode(html, match.end())[0]
    except ValueError:
        return None


def _find_key(obj, key):
    """First value stored under `key` anywhere inside nested dicts/lists."""
    stack = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if key in current:
                return current[key]
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return None


def parse_youtube_live_details(html):
    """{'viewers', 'started'} (each None if not found) from a YouTube watch
    page: the start time is in the player response's liveBroadcastDetails, the
    concurrent viewers in the page data's videoViewCountRenderer ("283 watching
    now") -- where yt-dlp reads them from too."""
    started = viewers = None
    player = _json_after(html, 'ytInitialPlayerResponse')
    if player is not None:
        details = _find_key(player, 'liveBroadcastDetails')
        if isinstance(details, dict):
            started = parse_iso_timestamp(details.get('startTimestamp'))
    data = _json_after(html, 'ytInitialData')
    if data is not None:
        renderer = _find_key(data, 'videoViewCountRenderer')
        if isinstance(renderer, dict) and renderer.get('isLive', True):
            view_count = renderer.get('viewCount') or {}
            text = view_count.get('simpleText') or ''.join(
                run.get('text', '') for run in view_count.get('runs', []) if isinstance(run, dict))
            viewers = parse_viewer_count(text)
    return {'viewers': viewers, 'started': started}


def fetch_youtube_live_details(channel):
    """Best-effort extra request for a channel that is live: viewers and start
    time. Anything that goes wrong just means those parts stay unknown."""
    req = urllib.request.Request(
        f'https://www.youtube.com/{channel}/live',
        headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0',
                 'Accept-Language': 'en-US,en;q=0.9',
                 'Cookie': 'SOCS=CAI'})  # accepts the consent page that EU visitors get
    try:
        with urllib.request.urlopen(req, timeout=YOUTUBE_PAGE_TIMEOUT_SECONDS) as resp:
            html = resp.read(YOUTUBE_PAGE_MAX_BYTES).decode('utf-8', 'replace')
        return parse_youtube_live_details(html)
    except Exception:
        return {'viewers': None, 'started': None}


def check_youtube_live_channels(channels):
    """Uses `streamlink --json <channel>/live` per channel -- the same
    extraction path used for actual playback, so there's no risk of this
    check disagreeing with what clicking the row would actually do. No
    keyless YouTube API exists for batch-checking many channels in one
    request, so this is sequential, one streamlink invocation per channel.
    A channel is live if streamlink found playable streams for it (title and
    category are optional and may be empty).
    Returns {channel: {'title', 'category'}} for whichever channels are
    currently live (a not-live channel is simply absent, same convention as
    Twitch's check);
    returns None only if every single channel's check failed (e.g.
    streamlink isn't installed) -- an individual channel's request failing
    is treated the same as that channel not being live, and is simply
    retried next poll. Failures are logged to stderr for diagnosis."""
    if not channels:
        return {}
    live = {}
    any_success = False
    for channel in channels:
        url = f'https://www.youtube.com/{channel}/live'
        try:
            result = subprocess.run(
                ['streamlink', '--json', url],
                capture_output=True, text=True, timeout=YOUTUBE_CHECK_TIMEOUT_SECONDS
            )
            parsed = _parse_streamlink_json(result.stdout)
        except Exception as e:
            _log(f'YouTube check for {channel} failed: {type(e).__name__}: {e}')
            continue  # this channel's check failed; left absent, retried next poll
        any_success = True
        metadata = parsed.get('metadata') or {}
        if parsed.get('error'):
            _log(f"YouTube {channel}: not live ({str(parsed['error'])[:160]})")
        elif parsed.get('streams') or metadata.get('title'):
            live[channel] = {
                'title': metadata.get('title') or '',
                'category': metadata.get('category') or '',
                **fetch_youtube_live_details(channel),
            }
    if not any_success:
        return None  # every channel's check failed (e.g. streamlink missing)
    return live


def parse_iso_timestamp(text):
    """'2026-10-11T10:05:12Z' / '...+00:00' -> epoch seconds, or None."""
    if not text or not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def parse_viewer_count(text):
    """'283 watching now' -> 283, '1,234 watching' -> 1234, '1.2K watching' ->
    1200; None if the text doesn't start with a number."""
    if not text:
        return None
    text = str(text).replace('\u00a0', ' ').strip()
    suffixed = re.match(r'(\d+(?:[.,]\d+)?)\s*([kKmM])\b', text)
    if suffixed:
        number = float(suffixed.group(1).replace(',', '.'))
        return int(round(number * (1000 if suffixed.group(2) in 'kK' else 1000000)))
    plain = re.match(r'\d[\d.,\s]*', text)
    if not plain:
        return None
    digits = re.sub(r'\D', '', plain.group(0))
    return int(digits) if digits else None


def format_elapsed(seconds):
    """'3h 21min', '45min', '<1min', '2d 5h'."""
    minutes = int(max(0, seconds) // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f'{days}d {hours}h'
    if hours:
        return f'{hours}h {minutes}min'
    return f'{minutes}min' if minutes else '<1min'


def format_viewers(count):
    """283 -> '283', 1234 -> '1.2k', 15400 -> '15k'."""
    if count < 1000:
        return str(count)
    if count < 10000:
        return f'{count / 1000:.1f}k'
    return f'{count // 1000}k'


def live_stats_text(entry, now_ts):
    """'283 - 3h 21min' (whichever parts are known) for a live row; '' if none."""
    parts = []
    viewers = entry.get('viewers')
    if isinstance(viewers, int) and viewers >= 0:
        parts.append(format_viewers(viewers))
    started = entry.get('started')
    if isinstance(started, (int, float)) and started > 0:
        parts.append(format_elapsed(now_ts - started))
    return ' - '.join(parts)
