# Citizens4Water dataset dashboard

Map + charts explorer for one C4W dataset. It is the GemsWater / FreshWater
explorer generalised to any dataset that ckanext-c4w has processed into a
*bundle* (see "Bundle contract"). Vite + TypeScript, no framework:
Chart.js 4.5.1, MapLibre GL 6.4.1, date-fns 4.4.0.

The built output is **committed** under `ckanext/c4w/public/c4w/dashboard/`
so the CKAN image needs no Node. Rebuild and commit whenever `src/` changes.

## Build

```bash
cd dashboard
npm ci            # pinned versions in package-lock.json (Node 22)
npm run build     # tsc && vite build  ->  ../ckanext/c4w/public/c4w/dashboard/
```

Output:

| File | What |
|---|---|
| `c4w-dashboard.js` | ES module, auto-mounts every `[data-c4w-dashboard]` |
| `c4w-dashboard.css` | dashboard styles (scoped under `.c4w-dash`) + MapLibre CSS |
| `c4w-dashboard-worker.js` | MapLibre web worker, emitted as a real file (no `blob:` URLs, CSP-friendly) |
| `BUILD.json` | `{version, builtAt}` — read by the `c4w_dashboard_asset` helper for cache-busting |

Development harness (does not need CKAN):

```bash
npm run dev-data  # writes a synthetic bundle to public/dev-data/ (gitignored)
npm run dev       # http://localhost:5173/c4w/dashboard/   (?lang=es|fr, ?full=1)
```

`index.html` is a hand copy of the Jinja snippet without Jinja; if you change
the snippet, change the harness too. `public/fonts` is a symlink to the
portal's self-hosted IBM Plex files.

## How a page includes it

The template renders the DOM skeleton from
`ckanext/c4w/templates/c4w/snippets/dashboard_shell.html` and includes the
built assets (the helper appends `?v=<builtAt>` from `BUILD.json`):

```jinja
{% block styles %}{{ super() }}
  <link rel="stylesheet" href="{{ h.c4w_dashboard_asset('c4w-dashboard.css') }}">
{% endblock %}
{% block c4w_content %}
  {% snippet 'c4w/snippets/dashboard_shell.html',
     dataset=dataset,
     bundle_base=h.c4w_url('dataset_bundle', slug=dataset.slug, name=''),
     basemap_style=basemap_style, lang=h.lang(), full=False %}
{% endblock %}
{% block scripts %}{{ super() }}
  <script type="module" src="{{ h.c4w_dashboard_asset('c4w-dashboard.js') }}"></script>
{% endblock %}
```

Root data attributes (all read by `src/config.ts`):

| Attribute | Meaning |
|---|---|
| `data-bundle-base` | URL prefix serving `meta.json`, `sites.json`, `p/<i>.json`; trailing slash added if missing |
| `data-lang` | `en`, `es` or `fr`; anything else falls back to `en` |
| `data-basemap` | MapLibre style URL; empty → `https://tiles.openfreemap.org/styles/positron` (the only external request the dashboard makes) |
| `data-title` | dataset title |
| `data-source-url` | fallback for the source link when `meta.dataset.source` is empty |

`full=True` (embed page) adds `c4w-dash--full`: the dashboard fills the
viewport and drops its frame. The portal page keeps a fixed workspace height
(`clamp(560px, 100vh - 14rem, 880px)`); below 900px the notebook stacks under
the map with fixed chart heights.

Every element the code touches carries a `data-role`; the code only queries
inside its root, so several dashboards can live on one page. The full role list
is in the comment at the top of the snippet. `mount(root, override?)` is also
exported for manual mounting.

## Bundle contract

Periods are compact integers by grain: `2019` (year), `201903` (month),
`20190314` (day).

`meta.json`
```json
{"schema": 1,
 "dataset": {"slug": "...", "title": "...", "credit": "...", "source": "https://...",
             "license": "CC-BY-4.0", "grain": "month", "generatedAt": "2026-09-04T…"},
 "records": 5697, "siteCount": 40, "minPeriod": 202001, "maxPeriod": 202412,
 "parameters": [{"key": "nitrate", "label": "Nitrate", "unit": "mg/L", "family": "Nutrients",
                 "records": 1400, "sites": 40, "minPeriod": 202001, "maxPeriod": 202412,
                 "breaks": [0.4, 0.7, 1.2, 2.1, 3.6, 5.9], "reliableScale": true}],
 "dimensions": [{"key": "body", "label": "Water body",
                 "values": [{"id": 0, "label": "River", "count": 14}]}],
 "countries": [{"id": "CL", "count": 14}]}
```

- `parameters[i]` is served as `p/<i>.json`; `family` may be `null` (no
  optgroups); `breaks` are six ascending cuts → seven bins; an empty `breaks`
  or `reliableScale: false` paints everything neutral and shows a warning.
- `countries[].id` should be ISO 3166-1 alpha-2 (the UI localises those with
  `Intl.DisplayNames`); any other string is shown verbatim.
- `dimensions` and `countries` may be empty; the country select and the
  comparison panel hide themselves.

`sites.json` (columnar, index-aligned):
```json
{"id": ["CL-001"], "name": ["Río Maipo"], "lat": [-33.6], "lon": [-70.4],
 "country": ["CL"], "dims": {"body": [0], "land_use": [2]}}
```
`name`/`country` entries may be `null`; `dims.<key>[site]` is the value id or
`null`.

`p/<i>.json`: `{"site": [..], "period": [..], "value": [..], "samples": [..]}` —
`site` indexes `sites.json`, `value` is the median for that site × period,
`samples` how many raw rows it summarises.

`stats.json` is not read by the UI.

## What the UI does

- Parameter select (grouped by family), country select, one select per
  dimension, period range (`number` / `month` / `date` inputs by grain,
  clamped to the parameter's range), site chip.
- Map: clustered points coloured by `step` over the breaks; cluster colour is
  the mean of medians; selection ring; popup with median / periods / samples
  and "Show only this site"; legend tube; view chip; empty banner.
- Charts (Chart.js, `animation: false`, single series so no legend box):
  trend (median per period, automatically coarsened day→month→year above
  400 points), distribution (histogram over the bins, RAMP colours),
  comparison (by country, else by the first dimension, else hidden). All
  three click through to the map.
- From zoom ≥ 4 the charts follow the map viewport.
- KPI strip: sites · site-periods · measurements · median · maximum.
- Download CSV of the filtered slice:
  `site_id,site_name,country,period,lat,lon,value,unit,samples,<dim keys…>`
  (UTF-8 with BOM, periods as `YYYY`, `YYYY-MM` or `YYYY-MM-DD`).
- i18n: en / es / fr tables in `src/i18n/`; the bundle re-applies its table to
  the snippet's `[data-i18n]` labels so server-rendered and dynamic strings
  match. Keys are listed in `src/i18n/en.ts`.

## Porting notes (vs GemsWater)

- Everything is per-instance: `createStore`, `createLoader`, `createI18n`,
  `createMap`, `createTimeline/Histogram/Comparison` return handles; no
  module-level singletons, no `document.getElementById`.
- Year → generic period (`src/data/periods.ts`); trend coarsening replaces
  `medianByPeriod(yearsPerBin)`.
- `medianByCountry` → `medianByGroup(keyOf)`; minimum 2 sites per group
  (GemsWater used 3, citizen datasets are small).
- Countries are optional and ISO2 (server resolves ISO3/names); no ISO3 table.
- Dimensions (≤ 3 categorical columns) are new: filters, comparison fallback,
  CSV columns.
- Dropped: Tailwind, Google Fonts (portal ships IBM Plex), the language switch
  (CKAN's locale drives `data-lang`), the boot screen (replaced by an overlay
  inside the root), the AI assistant.
- Styling uses the portal tokens (`--c4w-*`) with local fallbacks; the value
  RAMP is unchanged because it is data semantics (percentile bins); `MISSING`
  is the portal's blue-grey.
- Chart marks follow the dataviz specs: 2px lines, 4px rounded data-ends,
  ≤ 24px bars, ink tooltips.
