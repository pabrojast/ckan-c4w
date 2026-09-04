# encoding: utf-8
"""Look at the head of an uploaded table and describe it.

Only the first few hundred kilobytes are read: enough to settle the
encoding, the delimiter, whether there is a header and what each column
holds, cheap enough to run inside the upload request. The result is a
JSON-serialisable dict that the column-mapping form renders and that
``mapping.validate_mapping`` checks a submitted spec against.

Nothing here is a guess the user cannot override: every heuristic feeds a
*proposal*, and the mapping form is where the person who knows the data
confirms or corrects it.
"""
import collections
import csv
import io
import re

from ckanext.c4w.data import dates, mapping
from ckanext.c4w.data.errors import DelimiterError, EncodingError

DELIMITERS = (u',', u';', u'\t', u'|')

# Where the numeric parser stops being generous: a comma is a decimal
# separator only when there is exactly one and no dot, as in ``12,5``.
_DECIMAL_COMMA_RE = re.compile(r'^[-+]?\d+,\d+$')
_NUMBER_RE = re.compile(r'^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$')

# Columns whose header says "date" get the year-only and epoch formats
# considered; anything else would call a column of plain integers a date.
DATE_HEADER_RE = re.compile(
    r'(date|time|fecha|timestamp|year|anio|año|month|period|sampled|observed)',
    re.IGNORECASE)

CATEGORICAL_MAX_DISTINCT = 50
CATEGORICAL_MAX_RATIO = 0.2
TOP_VALUES = 50
TOP_VALUES_MAX_DISTINCT = 200
SAMPLE_ROWS = 20


def open_text(path, encoding):
    """Text handle for ``csv.reader``: ``newline=''`` is what csv wants."""
    return io.open(path, 'r', encoding=encoding, newline='', errors='replace')


def to_float(raw):
    """float or None. Accepts a decimal comma when it cannot be a thousands
    separator; never raises."""
    s = (raw or u'').strip()
    if not s:
        return None
    if _NUMBER_RE.match(s):
        try:
            return float(s)
        except ValueError:
            return None
    if _DECIMAL_COMMA_RE.match(s):
        try:
            return float(s.replace(u',', u'.'))
        except ValueError:
            return None
    return None


def decode_head(head):
    """(text, encoding). UTF-8 first, then the Windows codepage, then
    Latin-1 which cannot fail. A NUL byte means a binary file."""
    if b'\x00' in head:
        raise EncodingError(u'The file looks binary, not a text table.')
    if head.startswith(b'\xef\xbb\xbf'):
        return head.decode('utf-8-sig', 'replace'), 'utf-8-sig'
    for encoding in ('utf-8', 'cp1252'):
        try:
            return head.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return head.decode('latin-1'), 'latin-1'


def _complete_lines(text, truncated):
    """Drop a trailing partial line when the head was cut mid-file."""
    if truncated and u'\n' in text:
        text = text[:text.rfind(u'\n')]
    return text


def _count_cells(text, delimiter, quotechar, limit=60):
    """Cells per line for the first ``limit`` non-empty lines."""
    counts = []
    reader = csv.reader(io.StringIO(text), delimiter=delimiter,
                        quotechar=quotechar or u'"')
    try:
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            counts.append(len(row))
            if len(counts) >= limit:
                break
    except csv.Error:
        return []
    return counts


def detect_dialect(text):
    """(delimiter, quotechar). Consistency wins over ``csv.Sniffer``: the
    sniffer is easily fooled by a free-text column with commas in it, while
    "the same number of cells on every line" rarely lies."""
    quotechar = u'"'
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=u''.join(DELIMITERS))
        quotechar = dialect.quotechar or u'"'
    except csv.Error:
        pass

    best = None
    for delimiter in DELIMITERS:
        counts = _count_cells(text, delimiter, quotechar)
        if not counts:
            continue
        modal, freq = collections.Counter(counts).most_common(1)[0]
        if modal < 2:
            continue
        score = (freq / float(len(counts)), modal)
        if best is None or score > best[0]:
            best = (score, delimiter)
    if best is None:
        raise DelimiterError(
            u'Could not find a column separator (comma, semicolon, tab or pipe).')
    return best[1], quotechar


def _looks_like_header(first, second):
    """True/False when the first two rows settle it, else None."""
    if not first:
        return None
    if any(to_float(cell) is not None for cell in first):
        return False
    if second is None:
        return None
    if all(cell.strip() for cell in first) and any(
            to_float(cell) is not None for cell in second):
        return True
    return None


def _read_rows(text, delimiter, quotechar, max_rows):
    reader = csv.reader(io.StringIO(text), delimiter=delimiter,
                        quotechar=quotechar)
    rows = []
    for row in reader:
        if not row or not any(cell.strip() for cell in row):
            continue
        rows.append(row)
        if len(rows) > max_rows:
            break
    return rows


def _column_stats(name, index, rows):
    values = [(row[index] if index < len(row) else u'') for row in rows]
    nonnull = [v.strip() for v in values if v and v.strip()]
    total = len(rows) or 1
    stats = {
        'name': name,
        'index': index,
        'type': 'empty',
        'nonnull': len(nonnull),
        'null_ratio': round(1.0 - len(nonnull) / float(total), 4),
        'numeric_ratio': 0.0,
        'date_ratio': 0.0,
        'date_format': None,
        'distinct': 0,
        'min': None,
        'max': None,
        'samples': [],
        'top_values': None,
    }
    if not nonnull:
        return stats

    seen = []
    for v in nonnull:
        if v not in seen:
            seen.append(v)
            if len(seen) >= 5:
                break
    stats['samples'] = seen

    distinct = set(nonnull)
    stats['distinct'] = len(distinct)

    numbers = [f for f in (to_float(v) for v in nonnull) if f is not None]
    stats['numeric_ratio'] = round(len(numbers) / float(len(nonnull)), 4)

    header_hint = bool(DATE_HEADER_RE.search(name or u''))
    if header_hint or stats['numeric_ratio'] < 0.9:
        fmt, ratio = dates.detect_format(nonnull[:500], header_hint=header_hint)
        stats['date_format'] = fmt
        stats['date_ratio'] = round(ratio, 4)

    is_date = stats['date_ratio'] >= 0.9 and (
        header_hint or stats['numeric_ratio'] < 0.9)
    if is_date:
        stats['type'] = 'date'
        parser = dates.make_parser(stats['date_format'])
        parsed = [p for p in (parser(v) for v in nonnull[:500]) if p]
        if parsed:
            lo, hi = min(parsed), max(parsed)
            stats['min'] = u'%04d-%02d-%02d' % lo
            stats['max'] = u'%04d-%02d-%02d' % hi
    elif stats['numeric_ratio'] >= 0.9:
        stats['type'] = 'numeric'
        stats['min'] = min(numbers)
        stats['max'] = max(numbers)
    else:
        ratio = len(distinct) / float(len(nonnull))
        if (len(distinct) <= CATEGORICAL_MAX_DISTINCT
                and ratio <= CATEGORICAL_MAX_RATIO and len(nonnull) >= 5):
            stats['type'] = 'categorical'
        else:
            stats['type'] = 'text'

    if stats['type'] in ('categorical', 'text') and (
            len(distinct) <= TOP_VALUES_MAX_DISTINCT):
        counter = collections.Counter(nonnull)
        stats['top_values'] = [[v, n] for v, n in counter.most_common(TOP_VALUES)]
    return stats


def sniff_bytes(head, size_bytes=None, max_rows=2000):
    """Describe a table from the first bytes of the file.

    ``size_bytes`` is the whole file's size, used to drop a torn last line
    and to estimate the row count. Raises ``EncodingError`` or
    ``DelimiterError`` when the bytes are not a table at all.
    """
    text, encoding = decode_head(head)
    truncated = bool(size_bytes and size_bytes > len(head))
    text = _complete_lines(text, truncated)
    if not text.strip():
        raise DelimiterError(u'The file is empty.')

    delimiter, quotechar = detect_dialect(text)
    rows = _read_rows(text, delimiter, quotechar, max_rows)
    if not rows:
        raise DelimiterError(u'The file is empty.')

    first = rows[0]
    second = rows[1] if len(rows) > 1 else None
    has_header = _looks_like_header(first, second)
    if has_header is None:
        try:
            has_header = csv.Sniffer().has_header(text[:20000])
        except csv.Error:
            has_header = True

    width = max(len(r) for r in rows[:200])
    if has_header:
        names = [(c.strip() or u'column_%d' % (i + 1)) for i, c in enumerate(first)]
        names += [u'column_%d' % (i + 1) for i in range(len(names), width)]
        data_rows = rows[1:max_rows + 1]
    else:
        names = [u'column_%d' % (i + 1) for i in range(width)]
        data_rows = rows[:max_rows]

    # Duplicate headers would make the mapping ambiguous; suffix them.
    seen = collections.Counter()
    unique = []
    for name in names:
        seen[name] += 1
        unique.append(name if seen[name] == 1 else u'%s_%d' % (name, seen[name]))
    names = unique

    columns = [_column_stats(name, i, data_rows) for i, name in enumerate(names)]

    sample_lines = text.count(u'\n') or 1
    if size_bytes:
        bytes_per_line = len(text.encode('utf-8')) / float(sample_lines)
        estimate = int(size_bytes / max(bytes_per_line, 1.0))
        row_estimate = max(estimate - (1 if has_header else 0), len(data_rows))
    else:
        row_estimate = len(data_rows)

    sniffed = {
        'encoding': encoding,
        'delimiter': delimiter,
        'quotechar': quotechar,
        'has_header': bool(has_header),
        'row_estimate': row_estimate,
        'columns': columns,
        'sample_rows': [list(r) for r in data_rows[:SAMPLE_ROWS]],
    }
    sniffed['proposal'] = mapping.propose(sniffed)
    return sniffed
