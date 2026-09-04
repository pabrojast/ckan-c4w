# encoding: utf-8
"""Date parsing and period keys.

Two things justify a module of its own instead of ``datetime.strptime``:

* Speed. The GEMS archive is ~6 million rows and ``strptime`` costs about
  10 us a call; the slicing fast paths below are an order of magnitude
  cheaper, which is the difference between minutes and tens of seconds.
* Ambiguity. ``05/03/2020`` is May 3rd in the GEMStat export and March 5th
  in most of Europe. The format is detected once from a sample -- a day
  above 12 settles it -- and then applied uniformly, so a file is never
  parsed half one way and half the other.

A period key is an integer (``2019``, ``201903``, ``20190305``) so the
spools stay fixed-width and sort numerically.
"""
import calendar
import datetime
import re

# Order matters twice: it is the tie-break in ``detect_format`` and the
# probe order in the ``auto`` parser. ISO first (unambiguous), then the
# US shape GEMStat uses, then day-first.
CANDIDATE_FORMATS = (
    '%Y-%m-%d',
    '%Y-%m-%dT%H:%M:%S',
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M',
    '%Y-%m-%dT%H:%M',
    '%Y/%m/%d',
    '%m/%d/%Y %H:%M',
    '%m/%d/%Y',
    '%d/%m/%Y %H:%M',
    '%d/%m/%Y',
    '%d-%m-%Y',
    '%d.%m.%Y',
    '%Y-%m',
    '%Y',
    'epoch_s',
    'epoch_ms',
)

# Formats that only make sense when the header says the column is a date:
# a bare 4-digit integer or a 10-digit epoch is otherwise just a number.
HEADER_ONLY_FORMATS = frozenset(('%Y', 'epoch_s', 'epoch_ms'))

GRAINS = ('year', 'month', 'day')

_MIN_YEAR = 1800
_MAX_YEAR = datetime.date.today().year + 1

_EPOCH_RE = re.compile(r'^-?\d{9,13}(\.\d+)?$')


def _valid(y, m, d):
    if not (_MIN_YEAR <= y <= _MAX_YEAR):
        return None
    if not (1 <= m <= 12):
        return None
    if not (1 <= d <= calendar.monthrange(y, m)[1]):
        return None
    return (y, m, d)


def _iso(s):
    # 'YYYY-MM-DD' followed by anything (time, zone). Fast path: no regex.
    if len(s) < 10 or s[4] != '-' or s[7] != '-':
        return None
    y, m, d = s[0:4], s[5:7], s[8:10]
    if not (y.isdigit() and m.isdigit() and d.isdigit()):
        return None
    return _valid(int(y), int(m), int(d))


def _iso_month(s):
    if len(s) != 7 or s[4] != '-':
        return None
    y, m = s[0:4], s[5:7]
    if not (y.isdigit() and m.isdigit()):
        return None
    return _valid(int(y), int(m), 1)


def _year(s):
    if len(s) != 4 or not s.isdigit():
        return None
    return _valid(int(s), 1, 1)


def _slashed(s, sep, day_first):
    # 'A/B/YYYY' optionally followed by ' HH:MM'.
    head = s.split(' ', 1)[0]
    parts = head.split(sep)
    if len(parts) != 3:
        return None
    a, b, y = parts
    if not (a.isdigit() and b.isdigit() and y.isdigit() and len(y) == 4):
        return None
    if day_first:
        return _valid(int(y), int(b), int(a))
    return _valid(int(y), int(a), int(b))


def _ymd_slash(s):
    head = s.split(' ', 1)[0]
    parts = head.split('/')
    if len(parts) != 3:
        return None
    y, m, d = parts
    if not (y.isdigit() and m.isdigit() and d.isdigit() and len(y) == 4):
        return None
    return _valid(int(y), int(m), int(d))


def _epoch(s, millis):
    if not _EPOCH_RE.match(s):
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    if millis:
        value /= 1000.0
    try:
        dt = datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return _valid(dt.year, dt.month, dt.day)


# One parser per candidate. Every one takes the stripped string and returns
# (y, m, d) or None; none of them raises.
_PARSERS = {
    '%Y-%m-%d': _iso,
    '%Y-%m-%dT%H:%M:%S': _iso,
    '%Y-%m-%d %H:%M:%S': _iso,
    '%Y-%m-%d %H:%M': _iso,
    '%Y-%m-%dT%H:%M': _iso,
    '%Y/%m/%d': _ymd_slash,
    '%m/%d/%Y %H:%M': lambda s: _slashed(s, '/', False),
    '%m/%d/%Y': lambda s: _slashed(s, '/', False),
    '%d/%m/%Y %H:%M': lambda s: _slashed(s, '/', True),
    '%d/%m/%Y': lambda s: _slashed(s, '/', True),
    '%d-%m-%Y': lambda s: _slashed(s, '-', True),
    '%d.%m.%Y': lambda s: _slashed(s, '.', True),
    '%Y-%m': _iso_month,
    '%Y': _year,
    'epoch_s': lambda s: _epoch(s, False),
    'epoch_ms': lambda s: _epoch(s, True),
}


def _strptime_parser(fmt):
    """Fallback for a format we have no fast path for."""
    def parse(s):
        try:
            dt = datetime.datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            return None
        return _valid(dt.year, dt.month, dt.day)
    return parse


def make_parser(fmt):
    """A callable ``str -> (y, m, d) | None`` for one format.

    ``None`` or ``'auto'`` probes the candidates in order and then sticks
    with whichever worked, re-probing only on a miss -- so a file with a
    single consistent format pays for the probe once.
    """
    if fmt and fmt != 'auto':
        base = _PARSERS.get(fmt) or _strptime_parser(fmt)

        def parse(raw):
            if not raw:
                return None
            return base(raw.strip())
        return parse

    state = {'current': None}
    ordered = [_PARSERS[f] for f in CANDIDATE_FORMATS
               if f not in HEADER_ONLY_FORMATS]

    def parse_auto(raw):
        if not raw:
            return None
        s = raw.strip()
        current = state['current']
        if current is not None:
            out = current(s)
            if out is not None:
                return out
        for candidate in ordered:
            out = candidate(s)
            if out is not None:
                state['current'] = candidate
                return out
        return None
    return parse_auto


def detect_format(values, header_hint=False):
    """Best candidate format for a sample, and its success ratio.

    Returns ``(fmt, ratio)``; ``(None, 0.0)`` when nothing fits. A day above
    12 is what separates ``%m/%d/%Y`` from ``%d/%m/%Y``: with all days at or
    below 12 the two tie and the earlier candidate (US, as GEMStat) wins.
    """
    sample = [v.strip() for v in values if v and v.strip()]
    if not sample:
        return None, 0.0
    scored = []
    for fmt in CANDIDATE_FORMATS:
        if fmt in HEADER_ONLY_FORMATS and not header_hint:
            continue
        parser = _PARSERS[fmt]
        hits = sum(1 for v in sample if parser(v) is not None)
        if hits:
            scored.append((hits, fmt))
    if not scored:
        return None, 0.0
    best_hits = max(hits for hits, _ in scored)
    tied = [fmt for hits, fmt in scored if hits == best_hits]
    # A date-only and a date-time variant share one parser, so they always
    # tie; report the one that matches what the values actually carry.
    has_time = any(':' in v for v in sample)
    for fmt in tied:
        if ('%H' in fmt) == has_time:
            return fmt, best_hits / float(len(sample))
    return tied[0], best_hits / float(len(sample))


def format_resolution(fmt):
    """'day' | 'month' | 'year' -- the finest period a format can express."""
    if fmt == '%Y':
        return 'year'
    if fmt == '%Y-%m':
        return 'month'
    return 'day'


def period_key(y, m, d, grain):
    """Integer period key for a grain: 2019 / 201903 / 20190305."""
    if grain == 'year':
        return y
    if grain == 'month':
        return y * 100 + m
    if grain == 'day':
        return y * 10000 + m * 100 + d
    raise ValueError('unknown grain: %r' % (grain,))


def period_parts(key, grain):
    """Inverse of ``period_key``: (y, m, d) with 1 for missing parts."""
    key = int(key)
    if grain == 'year':
        return key, 1, 1
    if grain == 'month':
        return key // 100, key % 100, 1
    if grain == 'day':
        return key // 10000, (key // 100) % 100, key % 100
    raise ValueError('unknown grain: %r' % (grain,))


def period_to_iso(key, grain):
    """'2019' | '2019-03' | '2019-03-05'."""
    y, m, d = period_parts(key, grain)
    if grain == 'year':
        return '%04d' % y
    if grain == 'month':
        return '%04d-%02d' % (y, m)
    return '%04d-%02d-%02d' % (y, m, d)


def period_bounds(key, grain):
    """(first_day, last_day) of a period as ISO dates."""
    y, m, d = period_parts(key, grain)
    if grain == 'year':
        return '%04d-01-01' % y, '%04d-12-31' % y
    if grain == 'month':
        last = calendar.monthrange(y, m)[1]
        return '%04d-%02d-01' % (y, m), '%04d-%02d-%02d' % (y, m, last)
    iso = '%04d-%02d-%02d' % (y, m, d)
    return iso, iso
