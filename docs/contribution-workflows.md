# C4W contributions

Accounts belong to CKAN. The C4W registration actions call `user_create`
with a deferred commit, then save the supplementary profile and pending
verification token in the same transaction. First and last names become
CKAN's `fullname`; the old `fullname` action input remains compatible.
Passwords are neither stored in C4W tables nor echoed in form errors.

Email confirmation activates both citizen and manager accounts. A manager's
organisation request is reviewed independently; approval grants the existing
CKAN organisation editor/admin role. Rejection does not suspend the account.
Active CKAN users without a C4W profile can contribute without registering again.

The optional contributor preference can be changed on `/citizens4water/account`.
It controls the display of the contributor's name and affiliation in C4W project
pages, not the visibility of the global CKAN user page or explicitly supplied
project contact information. It is off by default.

## Project workflow

`/citizens4water/submit` offers projects, water datasets, news and events.
`/submit/project` creates a draft; `/submit/project/<id>/<step>` saves each of
six steps. `logic/project_fields.py` declares the fields, based on the legacy
`ProjectForm`; required content is checked on submission. The first save needs
a project name. Date ranges, controlled vocabularies, safe markup, URLs and
WGS84 polygon geometry are checked server-side even on partial saves.

The `c4w_project_revision` row holds the complete editable payload, current
step, optimistic version, review state and feedback. There is one working
revision per project. A new proposal reserves a stable project ID and slug.
For published projects, edits remain exclusively in the revision until approval.
Approval replaces editable columns, terms, relations, images and geometry in
one transaction, retaining URLs, existing extras, view counters and hidden flags.

States: `draft` → `pending` → `approved` or `changes_requested`. Saving after
submission returns the revision to `draft`. The author can correct and resubmit.
The public version remains available throughout subsequent reviews.
Owners, approved project editors and sysadmins can access the revision;
selecting an organisation never grants organisation permissions.

The admin queue links to `/submit/project/<id>/review`, with a complete preview
and approve/request-corrections actions. Corrections require a reason. Both
saves and decisions require the latest `version`; stale requests are rejected.
Legacy projects enter the revision workflow lazily when first edited. The
generic moderation action cannot bypass review of an existing revision.

Action additions: `c4w_project_create`, `c4w_project_draft_show`,
`c4w_project_update`, `c4w_project_completeness`, `c4w_project_image_upload`,
`c4w_project_submit`, `c4w_project_review`, `c4w_lookup`, and
`c4w_profile_preferences_update`. Each has a corresponding CKAN auth function.
Draft actions return `id`, `slug`, `payload`, `status`, `version`,
`wizard_step`, `review_note`, `published` and `modified`.

`c4w_lookup` accepts `kind` (`ckan_organisation`, `organisation`, `project`),
`q`, and optional comma-separated `ids`. It returns at most 20 slim results
(`id`, `title`, `name`) plus `more`. Searches fold accents/case and match all
words. Private project results are restricted to their owner/editors or admins.
The equivalent portal route is `/lookup/<kind>`; all URLs honor the preview prefix.

## Images, location and datasets

Three verified PNG/JPEG/WebP uploads are converted to JPEG with a white
background: thumbnail/logo 600×400, heading 1100×400. Crop positioning in the
form matches the server crop. The size limit follows `ckan.max_image_size`.
Uploads use the configured CKAN uploader; failed persistence cleans up the
new file. Replaced public images remain referenced until approval.

The location editor supports drawing multiple areas, dragging boundary points,
clearing, and importing/editing GeoJSON. Geometry is normalized to WGS84
MultiPolygon, limited to 500 KB and 10,000 vertices. Very detailed geometry
(over 500 vertices) is edited through GeoJSON instead of DOM drag handles.
MapLibre is vendored from the dashboard's pinned dependency; regenerate with
`npm ci --prefix dashboard && python3 scripts/vendor-project-map.py`.

Data remains optional. Projects can link an external repository and have many
C4W datasets. `/submit/data?project_id=<id>` preselects the project; the data
wizard revalidates the reference on save. A user may also submit independent
data. “Save and propose a project” saves the data details, creates a project
draft and returns to the saved data form with that project selected.
Datasets keep the existing CSV/TSV pipeline and separate review/processing
states. Public project pages show only datasets visible to that visitor; a
public dataset never exposes a private project's name or link.

## Upgrade and verification

`c4w init-db` adds the revision table and the three supplementary profile
columns (`given_name`, `surname`, `profile_visible`) idempotently.
To inspect old verified managers whose CKAN account still awaits organisation
review, run `c4w activate-verified-managers`; add `--apply` for the one-time
activation. It only considers CKAN `pending` accounts with a verified C4W
manager profile and no approval; active/deleted accounts are excluded.

Run `bash scripts/run-ckan-tests.sh` for plugin registration, route/template
checks, schema upgrade and behavioral tests. The proposal tests cover draft
isolation, all six steps, permissions, approval/corrections, stale writes,
unsafe markup/images, geometry, dataset visibility and CKAN registration.
`python3 scripts/render_preview.py` renders all form steps for layout checks.

Dev deployment uses `deploy/Dockerfile.dev` on the **current exact dev image**,
so other extensions retain their deployed versions. Verify that base again
before changing only the `ckan` container image in deployment `ckan`:
`kubectl --context default -n ckan`. Keep the previous image reference for
rollback. Do not roll back by dropping the additive tables or profile columns.
Production context `ckan` is outside this delivery.

### Verification on 2026-09-11

- Final automated run: **441 passed**, plus plugin, routes and template checks.
- Dev registration and verification created real CKAN citizen/manager accounts;
  CAPTCHA and mail delivery were intercepted only in the test process.
- Authenticated checks covered all six project steps, stored image upload,
  corrections/resubmission, approval, editor access and public revision isolation.
- Browser checks covered 390/768/1440 px, keyboard lookups, required-selection
  feedback, map drawing and save/reload. The final image passed all six project
  steps at 390 px without horizontal overflow.
- A CSV uploaded through the browser passed column detection, metadata/review
  persistence and real pipeline processing (`ready`, generation 1), remaining
  unapproved. Independent data could be saved, taken through a new project
  proposal, and linked on return without losing the draft.
- Existing CKAN account privacy preferences and non-admin review denial passed.
- Dev rollout completed with one ready/available replica. Final image:
  `pabrojast/ckan-base210:c4w-forms-dev-20260911-r5@sha256:6779ee642b161d6d7ee1443bbe73a5c2b1247d5a1aab3a1b1ef60d170da5bb0b`.
  Its base preserves the concurrent `cs-app-editors-20260911-r1` deployment.
- Cleanup removed four test projects, one dataset and its files/bundle, three
  project images, and all four temporary CKAN users. No test accounts remain.

Browser captures are under ignored `output/playwright/`. Real SMTP delivery
was not exercised; deployed CAPTCHA and mail configuration were unchanged.


## News, events and entry from the home page

The home page always offers project, data, and news/event contributions.
Anonymous visitors enter registration with a local `came_from`; existing users
can sign in with that same destination. `registration_next` on the private
C4W profile keeps the destination through email confirmation, including when
confirmation happens in another browser and when the email is resent.
Only local portal submission paths are retained. Account and organisation
approval remain separate: any verified contributor may propose content.

`/submit/news-events` selects news or events. `/submit/news` and `/submit/event`
create drafts, and `/submit/content/<entity>/<id>` edits them. The `/review`
suffix provides an author/admin preview and admin decisions. Actions
`c4w_content_create`, `c4w_content_draft_show`, `c4w_content_update`,
`c4w_content_image_upload`, `c4w_content_submit`, and `c4w_content_review` all
have auth functions and accept only `entity_type=post|event`.

`c4w_content_revision` stores the working payload, optimistic version, status,
author and review feedback. Draft/pending edits never replace the published
row. Only a submitted revision can be approved; requesting corrections requires
a note. News retains its native published/draft status; events retain approved.
The generic event approve action cannot bypass the revision workflow.

All portal POST forms explicitly invoke CKAN's CSRF protector, even when the
site exempts extension blueprints globally. Images use the existing `c4w`
uploader category (Pillow validation and PNG/JPEG/WebP conversion), while
measurements and attachments keep `c4w_data`.

Schema changes are additive and idempotent. Rolling back the image must keep
the revision table and registration destination column. See
`c4w-backup-restore.md` for the selective development backup and isolated
restoration procedure. Live production import/cutover is a separate delivery.

## Project visibility and administration

The public project catalogue, its counts and its home-page selections show
only approved projects that are not hidden, including for sysadmins.
`/citizens4water/admin/projects` is the sysadmin inventory: it includes every
project, supports search and pagination, and offers Hide/Unhide while keeping
the current search and page. Its read-only action, `c4w_admin_project_list`,
requires a sysadmin and accepts `q`, `page` and `page_size` (25 by default,
at most 100); it returns the standard listing envelope.

The existing `/citizens4water/admin` review queue remains separate. Hiding
does not delete a project or change approval; unhiding an unapproved project
does not publish it. Authorised owners, editors and sysadmins can still
preview non-public detail pages, which display a visibility notice.
