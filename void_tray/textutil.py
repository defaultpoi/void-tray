"""Text folding shared by the config and rule code."""

import unicodedata


def _fold_text(text):
    """Lowercase, accents and extra spaces removed, so 'Café' matches 'cafe'."""
    decomposed = unicodedata.normalize('NFKD', str(text))
    stripped = ''.join(ch for ch in decomposed if not unicodedata.combining(ch))
    return ' '.join(stripped.casefold().split())
