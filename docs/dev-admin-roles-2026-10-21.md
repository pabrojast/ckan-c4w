# C4W administrator assignments in DEV — 2026-10-21

System administrators can now manage C4W administrator assignments from
**Content administration → Administrators** at
`/citizens4water/admin/administrators`. Search finds existing active accounts by
username, name or email. Add administrator grants C4W content and moderation
access; Remove administrator revokes that assignment immediately. The page shows
whether each assigned account is eligible for review notifications.

The screen, account search and API actions require an active IHP-WINS system
administrator, matching the existing command-line policy. Delegated C4W
administrators cannot assign roles. Assignments record the actor and date, and
never change account email, password or global sysadmin privileges. The existing
CLI now uses the same action. No schema migration is required.

## Verification and deployment

- 573 application tests pass, including 10 new role-management cases. Plugin,
  template and route smoke checks pass.
- Browser QA verified account search, grant/revoke, immediate content/moderation
  access changes, restricted page/API access, anonymous login redirection, CSRF
  rejection, missing-email warnings and system-admin notification opt-in/out.
- Desktop and 390-pixel mobile screenshots were reviewed; no horizontal overflow.
- DEV target: context `default`, namespace `ckan`, host `data.dev-wins.com`.
- Image: `pabrojast/ckan-base210@sha256:81bbac65adf5550e432be697de462372f3c71b2988935df48f96762a09d7dc25`.
- Built on the current DEV digest
  `sha256:57d5c8146c84da57bd7340791f4e1c4446c87ded85328d53ffec73a92ba4895c`,
  preserving the other extensions and deployment settings.
- All 195 local Python/template/JS/CSS/image files matched the deployed source;
  nine additional files inherited from the base image were preserved.
- Four containers Ready, zero restarts. All 1,897 existing rows across the 18 C4W
  tables were unchanged after verification.

The three temporary QA accounts and their role assignments were removed. No real
administrator assignment was changed, and no notification email was sent during
QA. DEV still has no assigned C4W notification recipients until an operator adds
them through the new screen. Production was not modified.

Private evidence is in `output/verification/c4w-admin-roles-20261021/`; screenshots
are in `output/playwright/c4w-admin-roles-20261021/`. Both paths are ignored by Git.
