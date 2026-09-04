# encoding: utf-8
"""Pure processing pipeline: uploaded table -> dashboard bundle.

CKAN-free on purpose, and it must stay that way. Everything in this package
is exercised by tests that need nothing but the standard library, which is
what lets the sniffing heuristics, the unit rules and the aggregation be
checked against fixture CSVs in a second rather than inside a CKAN
container. The single module that reaches into CKAN (``jobs.py``) lives
next to these but imports them, never the other way round.

Module map, in the order the data flows through them::

    sniff     head of the file -> encoding, delimiter, column types, proposal
    mapping   the column-mapping spec: propose / normalise / validate
    dates     date formats -> (y, m, d) -> period keys per grain
    units     unit normalisation (mass per volume -> ug/L, sediment rejected)
    ingest    streaming pass over the rows -> per-parameter binary spools
    aggregate spool -> median per (site, period), external sort when large
    bundle    aggregated series -> meta / sites / p/<i> / stats JSON
    pipeline  the three above, end to end, with a scratch directory
    fetch     bring the raw file back from the object store, safely
"""
