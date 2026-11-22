# UNESCO citizen-science project list and project claims — DEV, 2026-11-22

On 2026-11-21 Koen Verbist sent a list of 300 water citizen-science projects
(`Global CS water resource management_69.0.xlsx`) to add to the portal. Most
rows are only partly filled in. He also asked that project managers be able to
request access to these entries, so they can edit them and add data sets.

## Import

```
# On a workstation: convert the sheet and look up the project websites.
uvx --with openpyxl --with Pillow --with babel \
    python scripts/enrich-cs-sheet.py SHEET.xlsx OUT/

# In the ckan pod: try it first, then run it.
ckan -c /srv/app/production.ini c4w import-sheet OUT/rows.json \
    --enriched OUT/enriched.json --media-root OUT/media --dry-run --report r.csv
ckan -c /srv/app/production.ini c4w import-sheet OUT/rows.json \
    --enriched OUT/enriched.json --media-root OUT/media --report r.csv
```

How the importer behaves:

- **Running it again is safe.** Each project carries `extras.import_key`, so a
  second run updates the same row. The slug is set on the first import and
  never changes after that.
- **People's changes are kept.** A project that someone has claimed, or edited
  after the import, is skipped.
- **Duplicates are not created.** A row whose name or website matches a project
  already on the portal is skipped. Rows repeated inside the sheet (for example
  "Water Action Volunteers (WAV)" and "Water Action Volunteers") are merged.
  Rows that differ only by place are kept apart, for example
  "Water Quality Monitoring (The Gambia)" and "(Zimbabwe)".
- **Papers are not treated as websites.** Links to publishers (ScienceDirect,
  MDPI, …) and to PDFs are stored as `reference_url` and shown on the page as
  "Publication about this project". They are never used as the project website.
- **Unknown status is flagged.** An empty or "Unknown" status is stored as
  active, with `status_unverified` set so it can be reviewed.
- **Website lookups only fill empty fields.** They never replace what is in the
  sheet. Images are fitted into 600×400. Logos are placed on white rather than
  cropped.
- **Seven images were dropped by hand** after review (stock images, favicons, a
  merchandise photo). They are listed in `OUT/reviewed-image-drops.txt`.

## Claiming a project

- Imported projects show an **Unclaimed** badge and a **Claim this project**
  button. The button opens `/citizens4water/project/<slug>/claim`, which asks
  for a role, organisation, contact e-mail, optional evidence, and a reason of
  at least 20 characters.
- Requests are stored in `c4w_project_claim`. The table is created when the
  plugin starts, so no migration is needed. The actions are
  `c4w_project_claim_create`, `_list`, `_pending_count`, `_mine` and
  `_decide`.
- **Who can decide:** CKAN sysadmins and C4W content administrators. They
  review requests in the ckanext-colab inbox, on the **Citizens4Water project
  claims** tab (`/colab/admin?tab=claims`). Colab only displays the requests;
  the permission checks and the grant happen in C4W.
- **What approval does:**
  - adds an `editor` relation, the same one the project form's "Editors" field
    writes;
  - sets `claimable=false` and records `claimed_by`;
  - if the project's lead organisation is linked to a CKAN organisation, also
    makes the person admin of that organisation.
- **Rejecting** requires a reason of at least 10 characters. The reason is sent
  to the person by e-mail.
- **E-mail:** reviewers are notified of each new request, and the person who
  asked is told the decision.

## DEV deployment, 2026-11-22

- Backup before any change: `output/backups/c4w-dev-20261122-033202-pre-cs-import`
  (18 C4W tables, 209 media objects).
- Image `pabrojast/ckan-base210@sha256:9da598d59762c5f6d4a0e05cd842cd1198ef499f4f2908a4340eca7c52cba2ab`
  (`c4w-cs-import-dev-20261122-r2`). Built on the dev digest
  `sha256:97758f8b…e777e` (`toolbox-paridad-dev-20261120-121ff70`). It replaces
  only ckanext-c4w (b52dcf4) and the `ckanext/` tree of ckanext-colab
  (cee4585, branch `feat/c4w-project-claims-20261122`). Before the build, the
  deployed C4W and Colab sources matched `origin/master` and
  `origin/production` 76885c7 file for file. Deployed with
  `deploy-stories-dev.py --web-only`.
- Import: 281 projects created and 8 skipped as already on the portal
  (HydroCrowd, Florida LAKEWATCH, WAV ×2, FLOW, CrowdWater, Titicaca Aqua
  Watch, Plastic Pirates). The portal now lists 315 projects. 82 of them have
  an image.
- End-to-end check through the full WSGI stack, with throwaway accounts and
  mail captured in-process:
  - the form validates;
  - the claim appears in the Colab inbox for a C4W content administrator;
  - approving gives the person editing rights and removes the badge;
  - rejecting requires a reason, which reaches the person;
  - a user without the role gets 403 on both the inbox and the API.

  The accounts, claims and role were removed afterwards, and the test project
  is back to `claimable=true`.
- **Not yet pinned.** `ckan-unesco-docker` (`miserver-2.10`) still builds
  ckanext-colab at 76885c7 and ckan-c4w from `master`. The next dev image
  built from that repository will drop the claim screens and the importer.
  The imported data and the `c4w_project_claim` table stay.
