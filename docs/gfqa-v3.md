# GFQA v3 archive provider

The GEMS explorer can use the complete GFQA v3 archive while ordinary citizen
datasets keep their existing bundle pipeline. Existing `/data/gemstat`,
`/dashboard`, `/embed`, and `/bundle/*` URLs remain valid. Legacy bundle indices
are never rewritten; enabling the archive switches the dashboard and embed only.

## Import, validate and activate

Requires Python 3.8+ and DuckDB 1.3.2, with no pandas or PyArrow dependency.

```bash
python -m ckanext.c4w.archive.importer --root /var/lib/ckan/gfqa --version v3
# Alternatively, inside a configured CKAN process:
ckan -c /srv/app/production.ini c4w archive-import --root /var/lib/ckan/gfqa
```

`--zip /path/GFQA_v3.zip` supplies an already-downloaded file. The importer checks
the official MD5 and computes SHA-256. `--no-activate` builds without changing
`active.json`. A repeated invocation resumes verified per-file checkpoints or
reuses the complete generation. Run only one importer against a root at a time.

Layout: `GFQA_v3.zip`, `active.json`, and
`generations/v3-<sha256-prefix>/{original,parquet,manifest.json,stations.parquet,parameters.json,methods.json}`.
`work/` and `query-work/` are temporary; they are not part of backups.
Original member bytes are retained. Each observation has source file and row
number, original text columns and derived typed fields. Identical station
metadata duplicates are collapsed in the join dimension only. Conflicting
station metadata stops activation. Measurement duplicates remain untouched.

The source description advertises 622 parameters and 22,982 stations. Direct
inspection of its three metadata files instead found 621 parameter dictionary
rows, 22,981 station rows / 22,946 distinct IDs, and 629 codes in method metadata.
The 35 repeated station rows are identical. The manifest distinguishes these
counts from actual observed parameters and measurements. Do not manufacture or
drop rows to match the advertised figures. All 80 observation CSVs are imported.

## DEV runtime

```ini
ckanext.c4w.archive_enabled = true
ckanext.c4w.archive_root = /var/lib/ckan/gfqa
```

Mount a dedicated persistent archive volume in the web container read-only and
in a query worker read-write. Initial recommended capacity: 30 GiB; actual
archive expansion is 3,140,189,215 bytes plus Parquet, generations and temporary
space. The web process reads only manifests. Results and job claims use CKAN
Redis; every request checks the existing C4W dataset visibility action.

```bash
ckan -c /srv/app/production.ini c4w archive-worker
```

The worker consumes only `c4w-archive` with concurrency one. Each DuckDB query
uses two threads, at most 1 GiB managed memory and disk spill, with a 45-second
interrupt and a 70-second job timeout. Permit at least 2 GiB container memory.
Queue admission stops at 20 pending jobs. Cached results expire after 10 minutes.
Imports belong in an operator Job, never in the web process or generic dashboard
queue. Retain the prior complete generation; restore its name in `active.json`
to roll back. Production is not enabled by this change.

## Public read interfaces

`GET /citizens4water/data/gemstat/archive/meta` returns the versioned manifest.

`POST /citizens4water/data/gemstat/archive/query` accepts JSON up to 8 KiB:

```json
{"operation":"dashboard","parameter":"pH","unit":"pH","country":"France","from_year":2020,"to_year":2024,"flag":"all","integration":"","page":0}
```

Operations: `dashboard`, `summary`, `stations`, `series`, `observations`, `export`.
Optional fields: `station`, `bbox: [west,south,east,north]`, `zoom`. Responses are
`{status:"ready",result:...}` or HTTP 202 `{status:"pending",status_url:...}`.
Poll the supplied status URL. Only predefined operations, validated fields and
bound values are accepted; neither user SQL nor arbitrary source URLs are allowed.
Tables contain 100 original rows per page; `export` contains 10,000 per page and
`has_more` must be checked. The UI download explicitly exports its current page.
Maps aggregate original eligible observations into at most 2,000 geographic
cells, returning `map_truncated` where further zoom is needed. Annual medians and
histograms are calculated from original eligible observations, not medians of
medians. No full observation archive is downloaded by the browser.

Numerical statistics use finite, uncensored values with quality Good, Fair,
Unknown or unspecified. Censored, estimated and suspect values remain in raw
tables and all originals. A flag filter can expose `<`, `>` and `~` rows without
treating a detection limit as an exact measurement. Each query uses one compatible
unit and integration class; an empty integration means discrete samples. Negative
temperatures remain valid. Original local sampling times are never labeled UTC.

Deep links use `gfqa_parameter`, `gfqa_unit`, `gfqa_country`, `gfqa_from`,
`gfqa_to`, `gfqa_flag`, `gfqa_station` on the dashboard or embed URL. Example:

`/citizens4water/data/gemstat/dashboard?gfqa_parameter=pH&gfqa_unit=pH&gfqa_country=France&gfqa_from=2020&gfqa_to=2024`

The archive is global. Its 42-country station dictionary does not include
Madagascar or Ukraine; those pilots use their separate source datasets.

## Dashboard Builder derivatives

```python
from ckanext.c4w.archive.query import export_derivative
export_derivative('/var/lib/ckan/gfqa', 'pH', '/path/gfqa-v3-ph.csv')
```

The exported station/year/parameter/unit/integration table has explicit
`station_year_median`, `measurement_count`, and `quality_policy` columns.
Register a CSV as a CKAN resource before creating its Dashboard Builder view.
One resource remains one table; do not describe a median over these rows as the
median of the original observations. Useful examples are pH, TEMP and PFOS.

Validation: `pytest ckanext/c4w/tests/test_archive.py`, `npm run build` in
`dashboard/`, and `bash scripts/run-ckan-tests.sh` for CKAN integration.
