"""Lossless normalization of the vocabulary used by imported content."""
from functools import lru_cache

from ckanext.c4w.logic import checks


@lru_cache(maxsize=1)
def language_names():
    from babel import Locale
    return {str(label).casefold(): code for code, label in Locale.parse('en').languages.items()
            if code.isalpha() and len(code) in (2, 3)}


def language(value):
    value = str(value or '').strip()
    # Do not collapse dialects such as Argentinian Spanish into another label.
    # Unknown imported values stay selectable until an editor replaces them.
    key = value.casefold().replace('-', ' ')
    return language_names().get(key, value.lower())


def languages(values):
    return list(dict.fromkeys(language(v) for v in checks.as_list(values)))


def same(value, baseline):
    if value in (None, '', []) and baseline in (None, '', []):
        return True
    if isinstance(value, list) or isinstance(baseline, list):
        return checks.as_list(value) == checks.as_list(baseline)
    return str(value).strip() == str(baseline).strip()
