# Dev migration, 8 September 2026

Target: `https://data.dev-wins.com/citizens4water/`, Kubernetes context
`default`, namespace `ckan`. Production was not changed.

The original Citizens4Water backup supplied 43 projects, 31 organisations,
6 resources, 4 platforms, 13 events and 16 news posts. Existing dev records
were preserved; the importer updates by legacy ID and keeps established slugs.
The media pass resolved 165 originals, with no missing or failed images, and
mapped 304 legacy thumbnail paths. A repeated import uploaded zero images.
27 source users have no unambiguous existing CKAN account; their authorship
links remain unresolved in the private import report. No accounts were invented.

| Dataset | Original input | Dashboard |
|---|---|---|
| [GEMStat](https://data.dev-wins.com/citizens4water/data/gemstat/dashboard) | 38 measurement CSVs, 7,494,549 rows; station inventory attached separately | 1,344,502 station/year/parameter medians, 13,417 stations, 118 parameters, 1906–2023 |
| [FreshWater Watch](https://data.dev-wins.com/citizens4water/data/freshwater-watch/dashboard) | 73,991 observations in the source CSV | 195,975 site/day/parameter medians, 53,374 site identities, 7 parameters, 2012–2026 |

FreshWater sites use name and coordinates rounded to four decimals. Nitrate
and phosphate are the source's interval midpoints; original categories remain
in the downloadable CSV. Categorical filters use the most frequent category
per site. Both source licences remain unspecified. Dashboard colour scales
are descriptive and do not assert regulatory compliance.

The dev image includes a supervised `c4w-worker` for the `c4w` queue. This uses
CKAN's connection disposal and job cleanup around each fork, with one plugin
initialisation. The standard worker on this installation aborted during its
second plugin initialisation before starting a C4W job. The dedicated worker
processed the 23 MB validation CSV successfully. Existing CKAN queues retain
their existing worker.

Validation includes the CKAN plugin-load harness, 419 behavioural/structural
tests, dashboard TypeScript/Vite build, source and hosted file SHA-256 checks,
and browser checks of uploads, mapping, permissions, moderation, responsive
charts and map rendering. Temporary validation accounts and datasets are
removed after the final checks.

Private backups, exact deployment manifests, full import reports, screenshots
and rollback instructions are held outside the plugin repository in:
`ckan-unesco-docker/backups/c4w-dev-migration-20260908-195001/`.
Source database dumps and connection files must not be committed.
