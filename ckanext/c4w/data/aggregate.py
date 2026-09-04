# encoding: utf-8
"""Second pass: one parameter's spool -> median per (site, period).

Aggregation is per parameter and streamed so memory is bounded by the
chunk size, never by the dataset. A spool that fits in one chunk is sorted
in memory; a bigger one is sorted chunk by chunk to sidecar files and
merged with ``heapq.merge``, which keeps one record per chunk in RAM.

The median rather than the mean: water-quality series are skewed and
peppered with detection-limit values, and the GEMS explorer this ports
already settled on the median for the map and the trend.
"""
import heapq
import os
import struct

from ckanext.c4w.data.ingest import RECORD, RECORD_SIZE

_READ_BYTES = RECORD_SIZE * 65536


def _iter_records(path):
    """(period, site, value) tuples -- period first so tuples sort by the
    grouping key with no key function."""
    with open(path, 'rb') as fh:
        while True:
            chunk = fh.read(_READ_BYTES)
            if not chunk:
                return
            usable = len(chunk) - (len(chunk) % RECORD_SIZE)
            for site, period, value in RECORD.iter_unpack(chunk[:usable]):
                yield (period, site, value)


def _write_sorted(records, path):
    with open(path, 'wb') as fh:
        fh.write(b''.join(RECORD.pack(site, period, value)
                          for period, site, value in records))


def _median(values):
    values.sort()
    n = len(values)
    mid = n // 2
    if n % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


def _grouped(ordered):
    """Consecutive (period, site) runs -> (site, period, median, samples)."""
    current = None
    values = []
    for period, site, value in ordered:
        key = (period, site)
        if key != current:
            if current is not None:
                yield (current[1], current[0], _median(values), len(values))
            current = key
            values = [value]
        else:
            values.append(value)
    if current is not None:
        yield (current[1], current[0], _median(values), len(values))


def aggregate_spool(path, chunk_records=1000000):
    """Yield ``(site, period, median, samples)`` ordered by period then site."""
    total = os.path.getsize(path) // RECORD_SIZE
    if total == 0:
        return
    if total <= chunk_records:
        for item in _grouped(sorted(_iter_records(path))):
            yield item
        return

    sidecars = []
    try:
        batch = []
        for record in _iter_records(path):
            batch.append(record)
            if len(batch) >= chunk_records:
                batch.sort()
                sidecar = u'%s.sort%d' % (path, len(sidecars))
                _write_sorted(batch, sidecar)
                sidecars.append(sidecar)
                batch = []
        if batch:
            batch.sort()
            sidecar = u'%s.sort%d' % (path, len(sidecars))
            _write_sorted(batch, sidecar)
            sidecars.append(sidecar)
        merged = heapq.merge(*[_iter_records(s) for s in sidecars])
        for item in _grouped(merged):
            yield item
    finally:
        for sidecar in sidecars:
            try:
                os.remove(sidecar)
            except OSError:
                pass
