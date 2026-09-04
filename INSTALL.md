# Installing ckanext-c4w

Target: CKAN 2.10 (the IHP-WINS portal runs 2.10.9).

## 1. Install

```bash
pip install -e /path/to/ckan-c4w
```

In the portal image, add it next to the other extensions:

```dockerfile
RUN pip install -e git+https://github.com/pabrojast/ckanext-c4w#egg=ckanext-c4w
```

## 2. Enable it

```ini
ckan.plugins = ... cstoolbox chartjs chartscha terriassistant csunesco c4w security
```

`c4w` after `theme_ejemplo` is the usual order. The extension does **not**
override the IHP-WINS masthead: there is no Citizens4Water tab in the UNESCO
nav. The portal lives at `/citizens4water/` with its own chrome, including
its own `/login` and `/register` pages (CKAN's `user.login` would render in
the UNESCO chrome). Logout and password reset stay with CKAN.

## 3. Tables

Nothing to run: `IConfigurable.configure` calls `db.ensure_tables()` on
startup, which is idempotent. There is no Alembic migration directory —
schema changes are handled by `create_all(checkfirst=True)` plus the
`_AUTO_HEAL_COLUMNS` whitelist in `db.py`.

To run it explicitly and see it succeed or fail loudly:

```bash
ckan -c /srv/app/production.ini c4w init-db
ckan -c /srv/app/production.ini c4w report      # row count per table
```

## 4. Configuration

All options are optional; the extension works with none of them set.

| Option | Default | Effect |
|---|---|---|
| `ckanext.c4w.preview_prefix` | *unset* | Mounts the portal at a **second** URL prefix as well as `/citizens4water`. Used only to QA the migrated portal in production while the legacy Django app still owns `/citizens4water`. See the warning below. |
| `ckanext.c4w.import_fallback_user` | site user | CKAN username to own imported rows whose Django author cannot be matched. The importer never creates CKAN users. |
| `ckan.upload.c4w.types` | `image` | Upload families accepted for portal images. |
| `ckan.upload.c4w.mimetypes` | `image/jpeg image/png image/webp` | Narrows what the file picker offers; the uploader also verifies the real file signature with Pillow. |
| `ckanext.c4w.data_max_upload_mb` | `256` | Largest data file (CSV/TSV). Must stay below the ingress body limit and what uwsgi's `harakiri` allows to receive. |
| `ckanext.c4w.attachment_max_upload_mb` | `25` | Largest protocol / field-sheet attachment. |
| `ckanext.c4w.data_inline_max_mb` | `20` | Datasets up to this size (sum of data files) are processed inside the request; larger ones are **queued**. |
| `ckanext.c4w.async_processing` | `false` | Enqueue queued datasets as CKAN background jobs. Needs `ckan jobs worker`; the IHP-WINS cluster runs none today, so leave it off and use the CLI. |
| `ckanext.c4w.job_timeout` | `3600` | Seconds a background job may run (`ckan.jobs.timeout` is 180 on IHP-WINS, too short). |
| `ckanext.c4w.data_max_rows` / `data_max_sites` / `data_max_parameters` | `10000000` / `200000` / `300` | Hard caps of the pipeline. |
| `ckanext.c4w.bundle_min_records` / `bundle_min_distinct` | `20` / `5` | A parameter below either is left out of the dashboard. |
| `ckanext.c4w.bundle_max_blob_mb` | `25` | Largest bundle file the database will hold. |
| `ckanext.c4w.data_fetch_hosts` | *unset* | Extra hosts the pipeline may download stored files from. The site host and the object store are always allowed. |
| `ckanext.c4w.basemap_style` | OpenFreeMap positron | MapLibre style URL. The only external request a dashboard makes. |
| `ckanext.c4w.verification_ttl_hours` | `48` | Life of an e-mail verification link. |
| `ckanext.c4w.rate_limit_enabled` / `rate_limit_max` / `rate_limit_window` | `true` / `10` / `300` | Per-client limit on the registration, resend and login forms. |
| `ckanext.c4w.moderation_notify_email` | *unset* | Address told about new submissions and manager requests. |

Registration relies on CKAN's own `ckan.auth.create_user_via_web = true`,
on `smtp.*` for the verification e-mail, and -- when both
`ckan.recaptcha.publickey` and `ckan.recaptcha.privatekey` are set -- on
reCAPTCHA v3 (score > 0.5). Without the keys the form simply has no CAPTCHA.

### The preview prefix

Do **not** set it to `/citizens4water-preview`. The production ingress is an
Azure Application Gateway rule with `pathType: Prefix` and path
`/citizens4water`, and that controller matches the prefix **literally** rather
than by path element — so `/citizens4water-preview` would still be routed to
the legacy Django app. Use something that cannot collide, e.g. `/c4w-preview`,
and verify it reaches CKAN before trusting any QA done through it.

## 5. Uploads

The portal has no local upload disk: `ckan.storage_path` is unset and
`ckanext-asset-storage` is configured with `backend_type = azure_blobs`.
Images therefore land in Azure Blob Storage through the normal CKAN uploader,
which `ckanext-asset-storage` intercepts. Nothing extra to configure here, but
uploads will fail if that extension is removed.

## 6. Data processing

A submitted dataset is processed inside the request when its files total
at most `data_inline_max_mb`; otherwise it is marked **queued** and an
operator runs it:

```bash
ckan -c /srv/app/production.ini c4w process-pending           # every queued dataset
ckan -c /srv/app/production.ini c4w process-dataset <slug>    # one, inline, now
ckan -c /srv/app/production.ini c4w dataset-report <slug> --dump-bundle /tmp/b
```

A cron entry calling `process-pending` is enough on a deployment without a
jobs worker. A reviewer can also press **Process** in the queue, which uses
the same inline/queued rule.

Large existing datasets (GEMStat: 39 CSVs, ~500 MB) are loaded the same
way: create the dataset in the wizard, upload the files (same header), map
the columns with **Include every parameter code found in the file** on, and
run `process-dataset` from the CLI.

## 7. The dashboard build

The dashboard front end lives in `dashboard/` and its build output is
**committed** under `ckanext/c4w/public/c4w/dashboard/`, so the CKAN image
needs no Node. After changing anything in `dashboard/src`:

```bash
cd dashboard && npm ci && npm run build      # Node 22
```

and commit the output (`c4w-dashboard.js`, `.css`, the worker, `BUILD.json`,
whose stamp busts the browser cache).

## 8. Verify

```bash
bash scripts/run-ckan-tests.sh
```

Builds `Dockerfile.test` (real CKAN 2.10) and runs three layers: the image
build, a plugin-load smoke check, and the behavioural pytest suite. The smoke
check is the one that catches what unit tests cannot — a blueprint that fails
to build (which crashes every CKAN worker at boot), a template that does not
parse, a helper called from Jinja that is not registered, and any action
without a matching auth function (which CKAN turns into a 500, not a 403).
