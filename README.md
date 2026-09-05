# ckanext-c4w

The **Citizens4Water** portal as a CKAN extension.

Replaces the standalone Django platform served at
`https://ihp-wins.unesco.org/citizens4water/` with a **distinct portal** at
the same URL, running as a CKAN extension. It is connected to IHP-WINS for
accounts, language and identity, but it has its own chrome — not the UNESCO
masthead — so the visitor is not looking at two headers that say the same
thing. The catalogue is citizen science initiatives on hydrology and water
management, with the organisations, resources, repositories, events and news
around them.

Requires CKAN 2.10. See [INSTALL.md](INSTALL.md). The portal is at
`/citizens4water/` and does not add a tab to the IHP-WINS masthead.

## Why an extension

The Django platform already delegated identity to CKAN: it authenticated
against `/api/3/action/user_login`, and both sign-up and password reset
redirected to IHP-WINS. What remained was two stacks, two databases and two
deployments for one product — plus five overlapping mechanisms whose only job
was serving Django under the `/citizens4water/` subpath. All of that
disappears inside CKAN, which already lives at that address.

## Shape of the code

| Path | Role |
|---|---|
| `plugin.py` | One plugin class. Every hook imports its module lazily. |
| `blueprint.py` | The URL map. Views are two-line wrappers; nothing here touches the ORM. |
| `db.py` | Classic `Table` + `mapper` on CKAN's metadata, plus `ensure_tables()`. |
| `constants.py` | Every vocabulary and form step. **Imports no CKAN**, so the pure tests need none. |
| `text.py` | Slugs and JSON extras. Also CKAN-free — the migration mapper depends on it. |
| `logic/action/*` | Business logic. `logic/actions.py` aggregates it. |
| `logic/views_*` | Orchestration between a request and the actions. |
| `logic/checks.py`, `validators.py`, `schema.py`, `forms.py` | The form infrastructure: CKAN-free value rules, their navl wrappers, per-form schemas, form parsing. |
| `logic/uploads.py` | Byte-verified CSV/TSV and attachment uploads through CKAN's uploader (Azure Blob on IHP-WINS). |
| `data/*` | The processing pipeline: sniff a table, map its columns, aggregate, build the dashboard bundle. **Imports no CKAN** except `data/jobs.py`. |
| `dashboard/` | The dashboard front end (Vite + TypeScript, Chart.js + MapLibre). Built into `public/c4w/dashboard/`, which is committed. |
| `assets/css/c4w.css`, `templates/c4w/snippets/icons.html`, `macros/ui.html` | The visual system: IHP-WINS tokens (UNESCO blue, 14px cards, soft shadows), a vendored Inter font, an inline SVG icon sprite. No CDN, ever. |
| `migrate/*` | One-time import from the legacy Django database. |
| `scripts/render_preview.py` | Renders the real templates with stub helpers into `build/preview/`, to iterate on the CSS and take screenshots without a CKAN site. |

Two decisions are worth knowing before reading further.

**Native columns are earned.** A field gets a column when the public site
filters, orders or indexes by it. Everything else lives in a JSON `extras`
column and is flattened to the top level when the row is dictized, so a
template reads `project.target_group` without knowing which side it is on.

**Two generic link tables replace ~25 many-to-many tables.** `c4w_term_link`
holds entity-to-vocabulary-term, `c4w_relation` holds entity-to-entity. The
project listing has eleven faceted vocabularies *with counts* plus ~100
countries; filtering that from a JSON column means scanning and filtering in
Python, and produces no counts at all.

## The data flow

Registered people share tables of water measurements; each becomes a
catalogue entry with an interactive dashboard (map, trend, distribution,
comparison) built from a precomputed bundle.

```
/register → /verify/<token> → /login            (portal chrome, not CKAN's)
/submit/data → 1 about → 2 files → 3 columns → 4 coverage & method → 5 contact & review
             → processing (inline ≤ 20 MB, else queued) → /admin (approve)
/data, /data/<slug>, /data/<slug>/dashboard, /data/<slug>/embed
```

- **Registration** has two profiles, as in ckanext-csunesco: a *citizen
  scientist* is active once the e-mailed link is opened; a *project manager*
  is reviewed by a sysadmin after that and, on approval, becomes editor of an
  existing IHP-WINS organisation or admin of a new one. Errors are generic
  on purpose (no account enumeration), tokens are stored hashed, the public
  forms are rate-limited.
- **Files** go to the object store through CKAN's uploader; only CSV/TSV is
  accepted for data (bytes are inspected, not names). Several files with the
  same header may belong to one dataset.
- **The column mapping** is proposed from the first 256 KB of the file and
  confirmed in the wizard: site id, latitude, longitude, date, either a
  parameter/value/unit triple (long layout) or one column per parameter
  (wide layout), up to three categorical filters, a time grain.
- **Processing** streams the file into per-parameter spools, aggregates by
  site and period (median), computes percentile colour breaks and writes
  `meta.json`, `sites.json`, `p/<i>.json` as gzip blobs in the database. The
  dashboard fetches them same-origin from `/data/<slug>/bundle/…`.
- **Metadata** follows the fields of schemingdcat's `unesco/dataset.yaml`
  (title, description, keywords, licence, temporal extent, bounding box,
  contact, provenance, frequency, DOI, citation, source) so a later export
  to the IHP-WINS catalogue is a mapping, not a redesign.

## Status

Built: the package and schema, the public read surfaces (projects,
organisations, resources, training resources, platforms, events, news) with
their facets, the C4W portal chrome (own header/footer, UNESCO colours),
registration / verification / login in that chrome, the data wizard with
its processing pipeline and dashboards, a sysadmin moderation queue with
manager approval, “my submissions”, and the one-way importer.

Still to build: the submission forms for projects, organisations, events,
platforms and resources (the `/submit` chooser announces them), a
background worker on the cluster for large files, and the cutover redirects
for the remaining Django static pages.

- [`docs/migration-runbook.md`](docs/migration-runbook.md) — the operator's
  procedure, and what to expect from the data.
- `docs/migration-field-map.json` — the 440-column field map the importer was
  built from, produced by surveying the real production database and then
  adversarially re-checking every claim.

### A note on the source data

The dev `c4w-postgres` volume has a damaged catalogue: the Django types exist
but the tables do not, left by an interrupted migrate. Restore a production
dump into a fresh database rather than trying to repair it — the runbook has
the commands.
