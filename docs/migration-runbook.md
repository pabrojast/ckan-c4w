# Migrating Citizens4Water into CKAN

The operator's procedure. Every command here is one you actually run; the
reasoning behind the design is in the commit messages and in
`migration-field-map.json`.

## What the data looks like

Production holds a small, well-bounded corpus:

| | rows | notes |
|---|---|---|
| projects | 43 | 34 approved, 0 hidden, 1 gap in the id range (id 10) |
| organisations | 31 | 7 types, including `Intergovernmental` which the Django fixture never seeded |
| resources | 6 | **zero** training resources; both training-only lookup tables are empty |
| platforms | 4 | |
| events | 13 | |
| blog posts | 16 | 15 published |
| Django users | 62 | 26 references belong to one account with no CKAN counterpart |
| media files | 148 distinct paths | plus easy_thumbnails derivatives |

Two facts that change what you should expect afterwards:

- **`/training_resources` will be live and empty.** That is faithful to
  production, not an import failure.
- **No project country carries coordinates.** All 191
  `projects_projectcountry` rows have a NULL latitude — the
  `update_countries_coordinates` management command was never run — so
  `country_points` comes out empty and any map will need centroids from
  elsewhere. The import report says so explicitly.

## Before you start

The importer refuses to run without `bleach`, and that refusal is the point.
`logic/sanitize.py` fails CLOSED: with bleach missing it strips every tag.
That is the right safety net for a web request and catastrophic here, because
sanitisation happens **before** storage — a run without it would write every
description and post body as tagless text, with no error to notice.

```bash
ckan -c /srv/app/production.ini c4w init-db      # idempotent; configure() also does this
```

## Getting a source database

Production is the only complete copy. The dev `c4w-postgres` volume has a
**damaged catalogue** — the Django types exist but the tables do not, from an
interrupted migrate — so restore into a fresh database rather than trying to
repair it:

```bash
# read-only against production
kubectl --context=ckan -n ckan exec c4w-postgres-0 -- \
  pg_dump -U citizens4water -d citizens4water --no-owner --no-privileges > c4w_prod.sql

kubectl --context=default -n ckan scale sts c4w-postgres --replicas=1
kubectl --context=default -n ckan exec c4w-postgres-0 -- \
  psql -U citizens4water -d postgres -c "CREATE DATABASE c4w_import;"
kubectl --context=default -n ckan exec -i c4w-postgres-0 -- \
  psql -U citizens4water -d c4w_import < c4w_prod.sql
```

## The import

```bash
DSN='postgresql://citizens4water:***@c4w-postgres:5432/c4w_import'

# 1. See what it would do, writing nothing.
ckan -c /srv/app/production.ini c4w import --dsn "$DSN" --dry-run \
     --json-report /tmp/c4w-dry.json

# 2. The real run.
ckan -c /srv/app/production.ini c4w import --dsn "$DSN" \
     --media-root /c4w-media --json-report /tmp/c4w-import.json

# 3. Row counts.
ckan -c /srv/app/production.ini c4w report
```

Re-running is safe and expected. Rows are keyed by their Django id, and ids
and slugs are assigned once, so a second run updates in place and no URL
moves.

### Reading the report

Three sections decide whether you cut over.

**Terms outside the declared vocabulary** is the one to take seriously. A term
production holds that `constants.py` does not declare is *invisible* on the
site — `facet_group.html` hides any option absent from its counts, so the
value does not render wrong, it disappears. If this section is non-empty,
either add the terms to `constants.py` or make that vocabulary free text.
Never discard the data.

**Django accounts with no CKAN user** lists rows that imported with no
creator. The importer never creates CKAN accounts; that is a decision for a
human. The largest block belongs to the Django account `Karen`
(26 rows, including 14 of the 16 blog posts), whose username does not exist in
CKAN. A CKAN user `karenverst` does exist but is a *different* Django account
that created nothing, so the two are not safely equatable from the data alone.
Resolve it with one `UPDATE` once somebody confirms the identity.

**Missing media** lists referenced files that were not found. Expected to be
non-empty if you ran without `--media-root`; investigate if it is non-empty
when you did supply one.

## Linking the organisation directory to CKAN

`c4w_organisation.ckan_org_id` is optional. Filling it wrongly is worse than
leaving it empty: the organisation page offers a "Datasets on IHP-WINS" link,
so a bad match sends readers to another institution's catalogue under this
one's name. So the proposals are printed for a human first.

```bash
ckan -c /srv/app/production.ini c4w link-organisations              # review the table
ckan -c /srv/app/production.ini c4w link-organisations --apply      # then write it
ckan -c /srv/app/production.ini c4w link-organisations --unlink <slug>
```

## The cutover

The importer only reads the Django database, so it runs while the Django site
is still serving. That is what makes a zero-downtime switch possible.

1. Deploy the CKAN image with `c4w` in `ckan.plugins` — **after
   `theme_ejemplo`**, or the navigation tab silently disappears (see
   `INSTALL.md`). `/citizens4water` still goes to Django: its ingress takes
   precedence over the catch-all.
2. Set `ckanext.c4w.preview_prefix = /c4w-preview` and QA there. **Not**
   `/citizens4water-preview`: the production ingress is an Azure Application
   Gateway rule with `pathType: Prefix`, and that controller matches the
   prefix *literally*, so anything starting with `/citizens4water` still goes
   to Django. Verify the preview prefix actually reaches CKAN before trusting
   any QA done through it.
3. Import, review the report, link the organisations.
4. **The switch**: run `c4w import --since <timestamp of step 3>` for the
   delta, then delete the `c4w-ingress` in the `ckan` namespace. The catch-all
   takes `/citizens4water` immediately.
5. **Rollback**: `kubectl apply -f k8s/ingress-apg-c4w.yaml` from the Django
   repo. Keep `c4w-web` running until you are sure.

Afterwards: remove `preview_prefix` and delete the preview branch in
`blueprint.py`; scale the Django deployments to zero but do not delete them
until the rollback window closes; keep `c4w-media-data-rw` until every image
is confirmed to resolve from the object store.

## What is NOT covered yet

The public read surfaces and the importer. Still to build: the submission
forms, the moderation panel, and the cutover redirects for the Django static
pages (`/about/`, `/criteria/`, `/privacy/`, `/terms/`, `/imprint/`, `/faq/`)
which need `ckanext-pages` entries before the switch, or the navigation will
show 404s.
