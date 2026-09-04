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

## 2. Enable it — ORDER MATTERS

```ini
ckan.plugins = ... cstoolbox chartjs chartscha terriassistant csunesco c4w security
```

**`c4w` must come after `theme_ejemplo`.** This is not a preference.

CKAN resolves a template by walking plugin template directories in load order,
and `{% ckan_extends %}` extends the *next* definition along that path.
`ckanext-theme-ejemplo/templates/header.html` does **not** use
`{% ckan_extends %}` — it replaces core's header outright and defines
`header_site_navigation_tabs` itself. Our `templates/header.html` only reaches
that block by extending the theme's copy, which requires `c4w` to be loaded
after it.

Put `c4w` before `theme_ejemplo` and the Citizens4Water tab disappears from
the navigation **silently** — no error, no log line.
`tests/test_scaffold.py::test_header_override_chains_instead_of_replacing`
guards the `{% ckan_extends %}` itself, but nothing can guard the ordering
from inside the extension.

On `/citizens4water/*` the extension replaces the IHP-WINS header and footer
with its own chrome (login still goes to CKAN, register to `/colab`). The
Citizens4Water tab in the IHP-WINS masthead is only for the rest of the
portal.

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

## 6. Verify

```bash
bash scripts/run-ckan-tests.sh
```

Builds `Dockerfile.test` (real CKAN 2.10) and runs three layers: the image
build, a plugin-load smoke check, and the behavioural pytest suite. The smoke
check is the one that catches what unit tests cannot — a blueprint that fails
to build (which crashes every CKAN worker at boot), a template that does not
parse, a helper called from Jinja that is not registered, and any action
without a matching auth function (which CKAN turns into a 500, not a 403).
