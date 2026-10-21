# C4W editorial changes verified in DEV — 2026-10-21

The eight content inventories now provide direct administrative editing, with a
separate moderation queue for submitted contributions. Published content keeps
its current public version until a proposal is approved. Pending proposals and
another author's open draft block direct editing. My submissions includes edits
and multiple content types; rejection/correction feedback and notification status
are visible to the contributor.

Descriptive fields support sanitized hyperlinks. Image controls provide a preview
and a Delete image button; replacement clears the deletion flag. Validation accepts
participant ranges, normalizes recognized imported language names, and preserves
unchanged legacy values using a server-side baseline. It still validates changed
values and never bypasses URL, email, HTML or reference checks. The footer uses the
existing official UNESCO-IHP asset from the IHP-WINS theme.

## Deployment and verification

- Target: `default` / namespace `ckan`, public host `data.dev-wins.com`.
- Image: `pabrojast/ckan-base210@sha256:ffef066904c4aa07753720cf6966b707257c5774ef4b5da8b46c284b17c2f54b`.
- Built on the previous deployed digest; only the C4W extension was replaced.
- Added three nullable audit/notification columns to each existing revision table.
- 563 application tests pass, including imported metadata, concurrent updates,
  protected drafts, atomic image writes, safe links, recipient selection and
  contact-only preservation. Plugin, template and route smoke checks pass.
- An isolated PostgreSQL restore passed all 1,906 backup rows and 209 media checks.
  Schema initialization ran twice; candidate validation accepted all 124 existing
  content records across the eight inventories.
- Browser QA used disposable C4W administrator and ordinary contributor accounts
  with copies of real content. All eight editors persisted changes and fitted a
  390-pixel viewport. Verified submission, queue visibility, blocked direct edits,
  required rejection reason, feedback, resubmission and approval; also verified
  image replacement/deletion, link persistence, unsafe-link rejection, anonymous
  and member access restrictions, and the desktop/mobile footer.
- The live source hashes matched all 186 Python/template/JS/CSS/image files checked.
  The deployment completed with all four containers ready and zero restarts.

Browser QA exposed DEV gateway false positives on RiverWatch prose and existing
news video embeds. The companion change in
`ckan-unesco-docker/deploy/envoy-dev/coraza.conf` limits exclusions to specific
signatures, named narrative fields and C4W editorial POST routes. Twelve gateway
unit tests and 19 checks using an isolated Envoy/Coraza proxy passed. Script tags,
event handlers, SQL injection, other command signatures and requests outside the
intended route/field scope remain blocked. The live policy patch changes only its
Wasm directives; gateway routing, logging, quotas and production are unchanged.

## IslandWatch and preservation

The old IslandWatch contact was still present in the published row and had no
pending project revision in DEV. The normal versioned administrative action,
using the existing `ckan_admin` maintenance identity, removed `author` and
`author_email`. The public page and edit form both show the cleared contact.

The before/after comparison covers all 18 C4W tables (1,896 existing C4W rows;
the backup also includes 10 CKAN dependency rows). Apart from IslandWatch's two
contact fields, modification timestamp, search text, observed access counter and
one approved revision, all records are unchanged. Its terms, relations, other
metadata, ownership and visibility are unchanged. All 209 original Azure objects
retain their ETags and sizes. The eight QA copies, two users and uploaded test
image were removed.

## Operational notes

Notification recipients are active users explicitly assigned the C4W administrator
role. DEV has no such recipients after QA cleanup. The moderation queue works and
shows a warning; email delivery to a real recipient was not exercised. A global
CKAN sysadmin can review content but is not automatically added to the email list.
The backup script now includes submitter/editor identities in its CKAN dependency
export. See [content administration](content-administration.md) for the workflow.

DEV had worker timeouts and 15 restarts on the previous pod before this rollout.
The replacement pod was independently verified healthy; no unrelated resource or
probe settings were changed. Production has not been deployed or modified.

Private backup and verification evidence (including temporary credentials) stays
in ignored `output/backups/c4w-dev-20261021-103254-before-editorial-20261021/` and
`output/verification/c4w-editorial-20261021/`. Screenshots are in
`output/playwright/c4w-editorial-20261021/`.
