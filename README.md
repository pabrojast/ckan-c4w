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

Requires CKAN 2.10. See [INSTALL.md](INSTALL.md) — **the position of `c4w` in
`ckan.plugins` matters.**

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
| `migrate/*` | One-time import from the legacy Django database. |

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

## Status

Built: the package and schema, the public read surfaces (projects,
organisations, resources, training resources, platforms, events, news) with
their facets, the C4W portal chrome (own header/footer, UNESCO colours),
CKAN login/register in that chrome, a sysadmin moderation queue, “my
submissions”, and the one-way importer.

Still to build: the submission forms (the `/submit` chooser is in place),
and the cutover redirects for the remaining Django static pages.

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
