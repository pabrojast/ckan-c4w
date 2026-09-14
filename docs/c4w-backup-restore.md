# Restore a C4W development backup

This private bundle contains account records, password hashes and deployment
credentials. Keep directories at 700 and files at 600. Never publish it, add it
to Git, or copy its environment/secrets directly into production.

## Contents and consistency

`database/schema.sql` is the CKAN database schema without owners/ACLs.
`database/data.sql` contains only all `c4w_*` tables and the referenced CKAN
users, organisations, user memberships and organisation extras. Data and counts
come from the same read-only repeatable-read PostgreSQL snapshot.
`database/dependencies.json` is the private identity reconciliation inventory;
`database/unresolved-references.json` records references already absent in the source.
`media.json` maps source Azure URLs to local copies with size, SHA-256 and ETag.
It includes draft, published and legacy mapped assets. Dashboard bundles are
in the database. Azure downloads use conditional ETags and before/after checks;
this is not an atomic cross-service snapshot. New writes after the snapshot
require a fresh backup before the actual migration.

`source.tar.gz` includes the local source at capture time;
`deployed-source.tar.gz` contains the deployed extension. `private/` holds the
configuration, environment, Kubernetes resources and referenced secrets.
`local-storage.tar.gz` captures the CKAN local upload directory.
Images and their actual digests are in `private/pods.json`; do not rely on a
mutable tag to reproduce this deployment. `ckan-image.tar.gz`, when present,
contains the actual CKAN image: load it with `docker load -i ckan-image.tar.gz`.
The matching local image ID and registry reference are recorded in `image.json`.

## Isolated restore

1. Verify every checksum: run `sha256sum -c SHA256SUMS` inside this directory.
2. Start an isolated PostgreSQL server compatible with the captured server
   (source: PostgreSQL 11; isolated verification: PostgreSQL 17), with the
   extensions listed in `database/schema.sql`. Use PostgreSQL/psql 17 for
   the included pg_dump 17 schema (including its transaction_timeout setting).
   Use an empty database; never execute these commands on an existing portal.
3. As the isolated database administrator, run `psql -v ON_ERROR_STOP=1`
   first with `database/schema.sql`, then with `database/data.sql`. The data
   script temporarily disables triggers in that session to load dependencies.
4. Compare exact row counts against `database/counts.json`; compare every
   restored table with a fresh export of the COPY data. Check referenced users
   and organisations against the recorded unresolved-reference inventory.
5. Restore media into a separate Azure/Azurite container, keeping each
   `container/blob` key from `media.json`, or serve the local copies for QA.
   Rewrite Azure URLs only in the isolated database/configuration. Hash all
   restored files and compare with the manifest before opening CKAN.
6. Use the captured CKAN image and extension source, configured only for the
   isolated database, storage, Redis and Solr. Disable outbound mail and workers
   that call external services. Rebuild search indexes from restored content.
7. Verify public catalogue, authors, images, downloaded measurements and both
   dashboards; check private drafts and moderation as the corresponding user.

## Future integration into IHP-WINS

This bundle is not a replacement for the production CKAN database. Before
cutover, take a fresh production backup and restore this bundle to staging.
Prepare an explicit identity map: match an existing user by ID plus username,
otherwise by unique username with matching identity evidence. Map organisations
by ID/name. Stop on conflicting or ambiguous matches. Never overwrite existing
passwords, sysadmin flags, tokens or organisation roles from development.
Review new accounts/role assignments separately. Remap all C4W owner/editor,
upload, review, profile and organisation references together in a transaction.
Preserve C4W IDs and slugs; detect conflicts instead of overwriting records.
Copy missing media to the target storage and rewrite only the mapped URLs.

Use `/c4w-preview` for production QA while legacy Django owns
`/citizens4water`; the old ingress also intercepts paths beginning with
`/citizens4water`. The actual production import, identity decisions and ingress
cutover are outside this development delivery.

## Development rollback

Use the bundle labelled `before` for rollback. Its `image.json` and
`private/pods.json` identify the previous CKAN image and immutable digest;
the final bundle records the new release instead. Restore only the previous
CKAN image in deployment `ckan`, context `default`, namespace `ckan`, and wait
for readiness. Keep additive tables/columns. Restore database/media only into
an isolated environment first; replaying a pre-release dump would discard new
contributions. Do not alter the dashboard sidecars or the production context.
