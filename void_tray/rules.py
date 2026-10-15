"""Feed items: ids, ages, mute rules, highlight words and tooltip details."""

import calendar
import fnmatch
import hashlib
import re
import time as time_module
import urllib.parse
import urllib.request

from .config import _edit_config_section, _read_config_sections
from .textutil import _fold_text


def parse_mute_rules(text):
    """'category=sports,golf; author=Some Name' -> [('category', 'sports'),
    ('category', 'golf'), ('author', 'some name')] (field lowercased, patterns
    folded). A comma lists alternatives for one field (write \\, for a literal
    comma); pieces without '=' or with an empty side are ignored."""
    rules = []
    for piece in (text or '').split(';'):
        field, sep, values = piece.partition('=')
        field = field.strip().lower()
        if not (sep and field):
            continue
        for value in re.split(r'(?<!\\),', values):
            pattern = _fold_text(value.replace('\\,', ','))
            if pattern:
                rules.append((field, pattern))
    return rules


def load_feed_mute_rules():
    """{feed url: [(field, pattern), ...]} from the fourth field of [feeds] lines."""
    rules = {}
    for line in _read_config_sections()['feeds']:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) > 3 and parts[3]:
            parsed = parse_mute_rules(parts[3])
            if parsed:
                rules.setdefault(parts[0], []).extend(parsed)
    return rules


# A rule's field name -> the feedparser entry keys it reads ('category' is how
# RSS calls what feedparser exposes as `tags`).
MUTE_FIELD_KEYS = {
    'category': ('tags',), 'categories': ('tags',), 'tag': ('tags',), 'tags': ('tags',),
    'author': ('author', 'authors'), 'summary': ('summary',), 'description': ('summary',),
}


_VALUE_KEYS = ('term', 'label', 'name', 'value', 'href')


def _flatten_field_values(value):
    if value is None:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, (int, float)):
        yield str(value)
    elif isinstance(value, dict):
        for key in _VALUE_KEYS:
            if value.get(key):
                yield str(value[key])
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten_field_values(item)


def entry_field_values(entry, field):
    """Every string value the entry has for `field` (a feed with several
    <category> elements gives several)."""
    values = []
    for key in MUTE_FIELD_KEYS.get(field, (field,)):
        values.extend(_flatten_field_values(entry.get(key)))
    return values


def entry_matches_mute_rules(entry, rules):
    """True if ANY rule matches: any one of the field's values equals the
    pattern (glob wildcards * and ?, case and accents ignored)."""
    for field, pattern in rules:
        for value in entry_field_values(entry, field):
            if fnmatch.fnmatchcase(_fold_text(value), pattern):
                return True
    return False


def title_is_highlighted(title, words):
    """True if the title contains one of `words` as a whole word/phrase."""
    folded = _fold_text(title)
    return any(re.search(r'(?<!\w)' + re.escape(word) + r'(?!\w)', folded) for word in words)


def item_is_highlighted(item, words):
    """True when the item's title, or any of its categories, contains one of
    `words` as a whole word/phrase (see title_is_highlighted)."""
    if title_is_highlighted(item.get('title', ''), words):
        return True
    return any(title_is_highlighted(term, words) for term in entry_tag_terms(item))


def feed_source_url(feed_url, feed_links):
    """The website a feed belongs to: the link the feed itself declares, else
    the feed address's own origin (https://host/)."""
    link = (feed_links or {}).get(feed_url)
    if isinstance(link, str) and link.startswith(('http://', 'https://')):
        return link
    parts = urllib.parse.urlsplit(feed_url)
    return f'{parts.scheme}://{parts.netloc}/' if parts.scheme and parts.netloc else feed_url


FEED_TERMS_LIMIT = 40  # categories / authors listed in the right-click menu


def entry_tag_terms(entry):
    """The item's category names, in feed order, without repeats."""
    terms, seen = [], set()
    for tag in entry.get('tags') or []:
        term = (tag.get('term') if isinstance(tag, dict) else tag)
        if isinstance(term, str) and term.strip() and term.strip().casefold() not in seen:
            seen.add(term.strip().casefold())
            terms.append(term.strip())
    return terms


def entry_author_name(entry):
    author = entry.get('author')
    return author.strip() if isinstance(author, str) else ''


def count_feed_terms(entries):
    """{'category': [[term, n], ...], 'author': [[name, n], ...]} over the
    given items (feed entries or stored unread items), most common first
    (capped), for the feed menu."""
    categories, authors = {}, {}
    for entry in entries:
        for term in entry_tag_terms(entry):
            categories[term] = categories.get(term, 0) + 1
        author = entry_author_name(entry)
        if author:
            authors[author] = authors.get(author, 0) + 1

    def top(counter):
        return [[k, v] for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:FEED_TERMS_LIMIT]]
    return {'category': top(categories), 'author': top(authors)}


def item_detail_text(item):
    """The category / author lines shown in a news item's tooltip ('' if the
    item has none stored)."""
    lines = []
    tags = [t for t in item.get('tags') or [] if isinstance(t, str)]
    if tags:
        lines.append('Categories: ' + ', '.join(tags))
    if item.get('author'):
        lines.append('Author: ' + str(item['author']))
    return '\n'.join(lines)


def escape_mute_value(value):
    """`value` as a mute-rule pattern that matches exactly that text: glob
    characters are bracketed, commas escaped, and the two separators of the
    config syntax (; and |) become the single-character wildcard."""
    out = []
    for ch in str(value).strip():
        if ch in '*?[':
            out.append(f'[{ch}]')
        elif ch == ',':
            out.append('\\,')
        elif ch in ';|':
            out.append('?')
        else:
            out.append(ch)
    return ''.join(out)


def add_feed_mute_rule(feed_url, field, value):
    """Adds 'field=value' to the mute rules (fourth field) of feed_url's line
    in [feeds]. If the line already has a rule for that field the value joins
    it ('category=a, b'); otherwise a new 'field=value' rule is appended. True
    if something was added, False if the feed line wasn't found or the value
    was already muted."""
    escaped = escape_mute_value(value)
    wanted = _fold_text(escaped)
    result = {'added': False}

    def merge(rules_text):
        pieces = rules_text.split(';')
        for index, piece in enumerate(pieces):
            name, sep, _values = piece.partition('=')
            if sep and name.strip().lower() == field:
                if wanted in [pattern for _f, pattern in parse_mute_rules(piece)]:
                    return None  # already muted
                pieces[index] = f'{piece.rstrip()}, {escaped}'
                return ';'.join(pieces)
        return f'{rules_text.strip()}; {field}={escaped}' if rules_text.strip() else f'{field}={escaped}'

    def transform(section):
        out = []
        for line in section:
            stripped = line.strip()
            parts = stripped.split('|') if stripped and not stripped.startswith('#') else None
            if parts is None or parts[0].strip() != feed_url or result['added']:
                out.append(line)
                continue
            parts += [''] * (4 - len(parts))
            merged = merge(parts[3])
            if merged is None:
                out.append(line)
                continue
            parts[3] = merged
            out.append('|'.join(parts) + '\n')
            result['added'] = True
        return out
    _edit_config_section('feeds', transform)
    return result['added']


def unread_feed_terms(unread, feed_url):
    """Category / author counts over the feed's items that are still unread."""
    return count_feed_terms([item for item in unread if item.get('feed_url') == feed_url])


def is_muted(title, mute_phrases):
    """True when the title contains any phrase, ignoring case and accents (the
    same folding the per-feed mute rules use). A phrase that folds to nothing
    would match every title, so it never mutes."""
    folded_title = _fold_text(title)
    return any(folded and folded in folded_title for folded in map(_fold_text, mute_phrases))


def entry_id(entry, feed_url):
    """A stable id for entries with a real id/link. Entries lacking both
    (the fallback: title+published) are hashed together with feed_url, so the
    same title+date on two different feeds doesn't collide."""
    raw = entry.get('id') or entry.get('link')
    if raw:
        return hashlib.sha1(raw.encode('utf-8', 'ignore')).hexdigest()
    fallback = feed_url + '\x00' + entry.get('title', '') + entry.get('published', '')
    return hashlib.sha1(fallback.encode('utf-8', 'ignore')).hexdigest()


def entry_age_seconds(entry):
    """Returns seconds since publish/update time, or None if no date info available."""
    parsed = entry.get('published_parsed') or entry.get('updated_parsed')
    if not parsed:
        return None
    try:
        entry_time = calendar.timegm(parsed)
        return time_module.time() - entry_time
    except Exception:
        return None
