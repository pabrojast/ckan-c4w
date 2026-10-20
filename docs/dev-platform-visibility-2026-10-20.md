# Platform visibility — DEV verification, 2026-10-20

The platform inventory at `/citizens4water/admin/platforms` now includes
published, pending and hidden platforms. Sysadmins can hide and unhide each
record independently, identify duplicate names by slug and URL, and retain the
search and page after moderation. Hiding does not change approval.

The public platform catalogue, counts and facets exclude hidden and unapproved
records for every role. Authorized private previews explain their visibility.

## Release

- Source: `828d62a8cde6cac50b6f14717471b54ec5cd5953`, pushed to `origin/master`.
- DEV: `kubectl --context default -n ckan`, `https://data.dev-wins.com`.
- Base image: `pabrojast/ckan-base210@sha256:d714660b36ce66c167bae645e47eb60e364fccf6a7f03cfa366145058d29b385`.
- Released image: `pabrojast/ckan-base210@sha256:d163f7188de9136bdfb83029df0faaee912b61d1e31dfc66a44a3d6a469b2d51`.
- Deployed with the Docker repository's guarded `scripts/deploy-stories-dev.py`
  using `--apply --web-only` and the expected base digest.
- Only the CKAN container image changed. Other containers, init containers,
  deployment settings, PVC specifications and Secret contents were preserved.
- Production was not modified; `origin/production` remains at
  `67f97790e762f8af5f7055452ec5809fe9535dec`.

The idempotent schema upgrade adds `c4w_platform.hidden BOOLEAN NOT NULL
DEFAULT FALSE`. It was tested twice against an isolated PostgreSQL 17 restore,
then applied to DEV before replacing the web container.

## Evidence

- Full suite: **480 passed**; integrated plugin smoke passed.
- All 13 changed runtime file hashes match the deployed source. The image
  comparison found no other source changes.
- The pre-upgrade database dump was restored and verified. It includes all C4W
  data; activity, activity_detail and tracking_raw data were excluded.
  Dump SHA-256: `cda8fa819aad08d595d9be8e9a7cf5413a4941681ff41c5d5fd4cb53c93a4406`.
- Browser checks used real admin/member logins and anonymous access. They
  covered Hide/Unhide, private detail access, public counts, denied member
  writes, missing-CSRF rejection, duplicate names, search, 25-row pagination
  and returning to page 2 after moderation.
- Desktop (1280 × 900) and mobile (390 × 844) checks passed, with no horizontal
  overflow. Unhiding an unapproved platform did not publish it.
- Final inventory: **6 platforms in administration, 5 public** for all roles.
  Both original `test` and `test-2` records retain their previous approval and
  visibility. The project inventory still has 45 records, and
  `Test project Karen` remains hidden with its Unhide control.
- All 26 temporary platforms and both temporary users were removed. Original
  row counts and content hashes match for all 16 C4W tables; comparison excludes
  access counters and separately verifies the new platform visibility values.
- Private evidence is under
  `output/verification/c4w-platforms-dev-20261020/`; screenshots are under
  `output/playwright/c4w-platforms-dev-20261020/`. These are excluded from Git.

## Operational observations

The old pod restarted after liveness timeouts during the rollout. Deployment
availability was false from 13:40:20 to 13:46:49 UTC; the new replica completed
the rollout at 13:48:24 UTC with all four containers ready and zero restarts.
Slow catalogue requests and worker timeouts were observed during startup.
No probe, resource or gateway settings were changed.

One browser Unhide request received an empty HTTP 403 from the DEV gateway.
Repeating the action succeeded, and six subsequent visibility POSTs all
returned HTTP 302 with the expected persisted states. The exact gateway rule
was not identified because raw WAF transaction logging is disabled. This is
recorded as an intermittent gateway observation, not a resolved WAF defect.
