import * as maplibregl from "maplibre-gl";
import { Chart, registerables } from "chart.js";
import type { DashboardConfig } from "./config";
import "./archive.css";

Chart.register(...registerables);
type Parameter = { code: string; label: string; family: string; units: string[]; rows: number; eligible: number; min_year: number | null; max_year: number | null; metadata_incomplete: boolean };
type Manifest = { version: string; generation: string; doi: string; observations: number; observed_parameter_codes: number; parameters: Parameter[]; dimensions: { station_ids: number; countries: string[] }; statistics_policy: string };
type Point = { latitude: number; longitude: number; value: number; samples: number; stations: number; station_id: string | null; station_name: string | null };
type Result = { summary: { observations: number; eligible: number; censored: number; stations: number; median: number | null; maximum: number | null }; series: {year: number; value: number; samples: number}[]; stations: Point[]; histogram: {bin: number; count: number}[]; histogram_min: number; histogram_width: number; observations: Record<string, unknown>[]; has_more: boolean; integrations: string[]; map_truncated?: boolean };
const copy = {
  en: { title: "Global Freshwater Quality Archive", all: "All countries", parameter: "Parameter", unit: "Unit", country: "Country", from: "From year", to: "To year", flag: "Reported flags", allFlags: "All observations", plain: "Unflagged", integration: "Sample integration", discrete: "Discrete samples", clear: "Clear station", busy: "Calculating from original observations…", trend: "Annual median of measurements", histogram: "Distribution of measurements", raw: "Original observations", rows: "observations", sites: "stations", eligible: "used in statistics", median: "median", censored: "censored values", policy: "Statistics use uncensored, finite values with Good, Fair, Unknown or unspecified quality. Limits (<, >), estimated and suspect values remain in the table. Units and sample integration types are kept separate. Colours describe the data, not regulatory quality.", download: "Download this page (CSV)", prev: "Previous", next: "Next", page: "Page", empty: "No observations match these filters.", share: "Copy filtered link", copied: "Link copied", limited: "Map limited to 2,000 cells. Zoom in to explore all locations.", source: "Source and attribution", reset: "Reset map filter", allParams: "Search parameters", version: "Version", noNumeric: "No uncensored values available; original observations remain below." },
  es: { title: "Archivo mundial de calidad del agua dulce", all: "Todos los países", parameter: "Parámetro", unit: "Unidad", country: "País", from: "Desde el año", to: "Hasta el año", flag: "Indicadores originales", allFlags: "Todas las observaciones", plain: "Sin indicador", integration: "Integración de muestra", discrete: "Muestras puntuales", clear: "Quitar estación", busy: "Calculando sobre las observaciones originales…", trend: "Mediana anual de mediciones", histogram: "Distribución de mediciones", raw: "Observaciones originales", rows: "observaciones", sites: "estaciones", eligible: "usadas en estadísticas", median: "mediana", censored: "valores censurados", policy: "Las estadísticas usan valores finitos sin censura con calidad Good, Fair, Unknown o no especificada. Los límites (<, >), valores estimados y sospechosos permanecen en la tabla. Las unidades y tipos de integración se mantienen separados. Los colores describen los datos, no su calidad normativa.", download: "Descargar esta página (CSV)", prev: "Anterior", next: "Siguiente", page: "Página", empty: "Ninguna observación coincide con estos filtros.", share: "Copiar enlace filtrado", copied: "Enlace copiado", limited: "Mapa limitado a 2.000 celdas. Acerca la vista para explorar todas las ubicaciones.", source: "Fuente y atribución", reset: "Restablecer filtro del mapa", allParams: "Buscar parámetros", version: "Versión", noNumeric: "Sin valores numéricos no censurados; las observaciones originales permanecen abajo." },
  fr: { title: "Archives mondiales de la qualité de l’eau douce", all: "Tous les pays", parameter: "Paramètre", unit: "Unité", country: "Pays", from: "Depuis l’année", to: "Jusqu’à l’année", flag: "Indicateurs originaux", allFlags: "Toutes les observations", plain: "Sans indicateur", integration: "Intégration", discrete: "Échantillons ponctuels", clear: "Effacer la station", busy: "Calcul à partir des observations originales…", trend: "Médiane annuelle des mesures", histogram: "Distribution des mesures", raw: "Observations originales", rows: "observations", sites: "stations", eligible: "utilisées dans les statistiques", median: "médiane", censored: "valeurs censurées", policy: "Les statistiques utilisent les valeurs finies non censurées de qualité Good, Fair, Unknown ou non précisée. Les limites (<, >), estimations et valeurs suspectes restent dans le tableau. Unités et intégrations sont séparées. Les couleurs décrivent les données, pas une classification réglementaire.", download: "Télécharger cette page (CSV)", prev: "Précédent", next: "Suivant", page: "Page", empty: "Aucune observation ne correspond aux filtres.", share: "Copier le lien filtré", copied: "Lien copié", limited: "Carte limitée à 2 000 cellules. Zoomez pour explorer tous les sites.", source: "Source et attribution", reset: "Réinitialiser le filtre de la carte", allParams: "Rechercher des paramètres", version: "Version", noNumeric: "Aucune valeur non censurée ; les observations originales restent ci-dessous." },
};

export async function mountArchive(root: HTMLElement, cfg: DashboardConfig) {
  const language = cfg.lang.startsWith("es") ? "es" : cfg.lang.startsWith("fr") ? "fr" : "en";
  const t = copy[language];
  const number = new Intl.NumberFormat(language, { maximumFractionDigits: 3 });
  const base = root.dataset.archiveBase!;
  const response = await fetch(base + "meta", { credentials: "same-origin" });
  if (!response.ok) throw new Error("GFQA archive is unavailable");
  const meta = await response.json() as Manifest;
  root.classList.add("c4w-archive");
  root.innerHTML = `<header class="c4w-archive__head"><div><span class="c4w-archive__eyebrow">UNEP GEMS/Water · ${t.version} ${meta.version}</span><h2>${t.title}</h2><p data-a="coverage"></p></div><a data-a="source" target="_blank" rel="noopener">${t.source} ↗</a></header>
    <form class="c4w-archive__filters"><label class="c4w-archive__parameter">${t.allParams}<input data-a="search" type="search" placeholder="pH, PFAS, temperature…"><select data-a="parameter" aria-label="${t.parameter}"></select></label>
    <label>${t.unit}<select data-a="unit"></select></label><label>${t.country}<select data-a="country"></select></label>
    <label>${t.from}<input data-a="from" type="number" min="1900" max="2024" value="1906"></label><label>${t.to}<input data-a="to" type="number" min="1900" max="2024" value="2024"></label>
    <label>${t.flag}<select data-a="flag"><option value="all">${t.allFlags}</option><option value="unflagged">${t.plain}</option><option value="&lt;">&lt;</option><option value="&gt;">&gt;</option><option value="~">~</option></select></label>
    <label>${t.integration}<select data-a="integration"><option value="">${t.discrete}</option></select></label></form>
    <div class="c4w-archive__toolbar"><button data-a="reset" type="button">${t.reset}</button><button data-a="station" type="button" hidden>${t.clear}</button><button data-a="share" type="button">${t.share}</button><span data-a="status" role="status" aria-live="polite"></span></div>
    <div class="c4w-archive__kpis" data-a="kpis"></div>
    <div class="c4w-archive__visuals"><div class="c4w-archive__map" data-a="map" aria-label="${t.sites}"></div><div class="c4w-archive__charts"><section><h3>${t.trend}</h3><div><canvas data-a="trend"></canvas></div></section><section><h3>${t.histogram}</h3><div><canvas data-a="histogram"></canvas></div></section></div></div>
    <p class="c4w-archive__policy">${t.policy}</p><section class="c4w-archive__raw"><div class="c4w-archive__toolbar"><h3>${t.raw}</h3><button data-a="download" type="button">${t.download}</button></div><div class="c4w-archive__table" tabindex="0"><table data-a="table"></table></div><div class="c4w-archive__toolbar"><button data-a="prev" type="button">${t.prev}</button><span data-a="page"></span><button data-a="next" type="button">${t.next}</button></div></section>`;
  const el = <T extends HTMLElement = HTMLElement>(name: string) => root.querySelector<T>(`[data-a="${name}"]`)!;
  const sel = (name: string) => el<HTMLSelectElement>(name);
  const option = (select: HTMLSelectElement, value: string, label: string) => select.add(new Option(label, value));
  el("coverage").textContent = `${number.format(meta.observations)} ${t.rows} · ${number.format(meta.dimensions.station_ids)} ${t.sites} · ${meta.observed_parameter_codes} ${t.parameter.toLowerCase()}`;
  el<HTMLAnchorElement>("source").href = `https://doi.org/${meta.doi}`;
  option(sel("country"), "", t.all);
  meta.dimensions.countries.forEach(c => option(sel("country"), c, c));
  let activeCode = new URL(location.href).searchParams.get("gfqa_parameter") || "pH";
  if (!meta.parameters.some(p => p.code === activeCode)) activeCode = "pH";
  const fillParameters = () => {
    const needle = el<HTMLInputElement>("search").value.toLowerCase();
    sel("parameter").replaceChildren();
    for (const p of meta.parameters.filter(p => p.code === activeCode || `${p.code} ${p.label} ${p.family}`.toLowerCase().includes(needle))) {
      option(sel("parameter"), p.code, `${p.label} · ${p.code}${p.rows ? "" : " (0)"}`);
    }
    sel("parameter").value = activeCode;
  };
  fillParameters();
  const units = () => {
    sel("unit").replaceChildren();
    (meta.parameters.find(p => p.code === activeCode)?.units || []).forEach(u => option(sel("unit"), u, u || "—"));
  };
  units();
  const url = new URL(location.href);
  for (const field of ["country", "unit", "flag", "from", "to"] as const) {
    const value = url.searchParams.get("gfqa_" + field);
    if (value) (el(field) as HTMLInputElement).value = value;
  }
  let station = url.searchParams.get("gfqa_station") || "";
  let page = 0;
  const initialBounds = url.searchParams.get("gfqa_bbox")?.split(",").map(Number);
  let bounds: number[] | undefined = initialBounds?.length === 4 && initialBounds.every(Number.isFinite) ? initialBounds : undefined;
  const initialIntegration = url.searchParams.get("gfqa_integration") || "";
  if (initialIntegration) { option(sel("integration"), initialIntegration, initialIntegration); sel("integration").value = initialIntegration; }
  let current: Result | null = null;
  let controller: AbortController | null = null;
  let requestNumber = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const map = new maplibregl.Map({ container: el("map"), style: cfg.basemap, center: [12, 25], zoom: 1.5, cooperativeGestures: true, attributionControl: { compact: true } });
  map.addControl(new maplibregl.NavigationControl(), "top-right");
  const trend = new Chart<"line", number[], string>(el<HTMLCanvasElement>("trend"), { type: "line", data: { labels: [], datasets: [{ label: t.median, data: [], borderColor: "#087c9c", backgroundColor: "#087c9c20", borderWidth: 2, pointRadius: 2, fill: true }] }, options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { maxTicksLimit: 8 } } } } });
  const histogram = new Chart<"bar", number[], string>(el<HTMLCanvasElement>("histogram"), { type: "bar", data: { labels: [], datasets: [{ label: t.rows, data: [], backgroundColor: "#219ebc" }] }, options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { maxTicksLimit: 6 } }, y: { beginAtZero: true } } } });
  let mapLoaded = false;
  const redrawMap = () => {
    if (!mapLoaded || !current) return;
    const values = current.stations.map(p => p.value).filter(Number.isFinite).sort((a, b) => a - b);
    const lo = values[0] || 0, hi = values[values.length - 1] || lo + 1;
    const source = map.getSource("gfqa") as maplibregl.GeoJSONSource;
    source.setData({ type: "FeatureCollection", features: current.stations.map(p => ({ type: "Feature", geometry: { type: "Point", coordinates: [p.longitude, p.latitude] }, properties: { ...p, shade: (p.value - lo) / (hi - lo || 1) } })) });
  };
  map.on("load", () => {
    mapLoaded = true;
    map.addSource("gfqa", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "gfqa-points", type: "circle", source: "gfqa", paint: { "circle-radius": ["interpolate", ["linear"], ["get", "stations"], 1, 5, 100, 13], "circle-color": ["interpolate", ["linear"], ["get", "shade"], 0, "#b9e4eb", 0.5, "#219ebc", 1, "#034f74"], "circle-stroke-width": 1, "circle-stroke-color": "#ffffff", "circle-opacity": 0.85 } });
    map.on("click", "gfqa-points", event => {
      const feature = event.features?.[0];
      if (!feature) return;
      const props = feature.properties as Record<string, unknown>;
      if (props.station_id) {
        station = String(props.station_id); page = 0; void refresh();
      } else if (feature.geometry.type === "Point") {
        map.flyTo({ center: feature.geometry.coordinates as [number, number], zoom: Math.min(16, map.getZoom() + 2) });
      }
    });
    map.on("mouseenter", "gfqa-points", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "gfqa-points", () => { map.getCanvas().style.cursor = ""; });
    redrawMap();
  });
  const tableColumns = ["station_id", "country", "date", "local_time", "reported_value", "reported_unit", "value_flags", "data_quality", "depth", "integrated_value", "method_code", "source_file", "source_row"];
  function render(result: Result) {
    current = result;
    el("kpis").replaceChildren();
    const kpis: [string, number | null][] = [[t.rows, result.summary.observations], [t.eligible, result.summary.eligible], [t.sites, result.summary.stations], [t.median + ` (${sel("unit").value})`, result.summary.median], [t.censored, result.summary.censored]];
    for (const [label, value] of kpis) {
      const box = document.createElement("div"), strong = document.createElement("strong"), span = document.createElement("span");
      strong.textContent = value == null ? "—" : number.format(value); span.textContent = label; box.append(strong, span); el("kpis").append(box);
    }
    trend.data.labels = result.series.map(r => String(r.year)); trend.data.datasets[0]!.data = result.series.map(r => r.value); trend.update();
    histogram.data.labels = result.histogram.map(r => number.format(result.histogram_min + r.bin * result.histogram_width)); histogram.data.datasets[0]!.data = result.histogram.map(r => r.count); histogram.update();
    const integration = sel("integration").value;
    sel("integration").replaceChildren(); option(sel("integration"), "", t.discrete);
    result.integrations.filter(Boolean).forEach(v => option(sel("integration"), v, v)); sel("integration").value = integration;
    el("station").hidden = !station; el("station").textContent = `${t.clear}: ${station}`;
    el("status").textContent = result.map_truncated ? t.limited : result.summary.observations === 0 ? t.empty : result.summary.eligible === 0 ? t.noNumeric : "";
    const table = el<HTMLTableElement>("table"); table.replaceChildren();
    const head = table.createTHead().insertRow();
    for (const column of tableColumns) { const th = document.createElement("th"); th.textContent = column.replaceAll("_", " "); th.scope = "col"; head.append(th); }
    const body = table.createTBody();
    for (const row of result.observations) {
      const tr = body.insertRow();
      for (const column of tableColumns) {
        const cell = tr.insertCell();
        if (column === "station_id") {
          const button = document.createElement("button"); button.type = "button"; button.textContent = String(row[column] ?? "");
          button.addEventListener("click", () => { station = String(row[column]); page = 0; void refresh(); }); cell.append(button);
        } else cell.textContent = String(row[column] ?? "");
      }
    }
    el<HTMLButtonElement>("prev").disabled = page === 0; el<HTMLButtonElement>("next").disabled = !result.has_more;
    el("page").textContent = `${t.page} ${page + 1} · 100 ${t.rows} / ${t.page.toLowerCase()}`;
    redrawMap();
  }
  async function refresh() {
    controller?.abort(); controller = new AbortController();
    const signal = controller.signal, id = ++requestNumber;
    el("status").textContent = t.busy;
    const payload = { operation: "dashboard", parameter: activeCode, unit: sel("unit").value, country: sel("country").value, from_year: Number(el<HTMLInputElement>("from").value), to_year: Number(el<HTMLInputElement>("to").value), flag: sel("flag").value, integration: sel("integration").value, station, page, bbox: bounds, zoom: map.getZoom() };
    try {
      let res = await fetch(base + "query", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal });
      let data = await res.json();
      const statusUrl = data.status_url;
      for (let attempt = 0; data.status === "pending" && attempt < 120; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        if (signal.aborted) return;
        res = await fetch(statusUrl, { credentials: "same-origin", signal }); data = await res.json();
      }
      if (id !== requestNumber) return;
      if (data.status !== "ready") throw new Error(data.message || "Archive query timed out");
      render(data.result as Result);
    } catch (error) {
      if (!signal.aborted) el("status").textContent = error instanceof Error ? error.message : String(error);
    }
  }
  el<HTMLFormElement>("search").addEventListener("input", fillParameters);
  root.querySelector("form")!.addEventListener("submit", event => event.preventDefault());
  sel("parameter").addEventListener("change", () => { activeCode = sel("parameter").value; units(); station = ""; page = 0; sel("integration").value = ""; void refresh(); });
  for (const field of ["unit", "country", "from", "to", "flag", "integration"]) el(field).addEventListener("change", () => { page = 0; if (field === "country") { station = ""; bounds = undefined; } void refresh(); });
  map.on("moveend", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const b = map.getBounds();
      bounds = [Math.max(-180, b.getWest()), Math.max(-90, b.getSouth()), Math.min(180, b.getEast()), Math.min(90, b.getNorth())];
      if (bounds[0]! > bounds[2]!) bounds = undefined;
      page = 0; void refresh();
    }, 500);
  });
  el("reset").addEventListener("click", () => { bounds = undefined; station = ""; page = 0; void refresh(); });
  el("station").addEventListener("click", () => { station = ""; page = 0; void refresh(); });
  el("prev").addEventListener("click", () => { page = Math.max(0, page - 1); void refresh(); });
  el("next").addEventListener("click", () => { page++; void refresh(); });
  el("share").addEventListener("click", () => {
    const link = new URL(location.href);
    const fields: Record<string, string> = { parameter: activeCode, unit: sel("unit").value, country: sel("country").value, flag: sel("flag").value, from: el<HTMLInputElement>("from").value, to: el<HTMLInputElement>("to").value, integration: sel("integration").value, bbox: bounds?.join(",") || "", station };
    for (const [key, value] of Object.entries(fields)) value ? link.searchParams.set("gfqa_" + key, value) : link.searchParams.delete("gfqa_" + key);
    history.replaceState(null, "", link);
    void navigator.clipboard?.writeText(link.href).then(() => { el("status").textContent = t.copied; });
  });
  el("download").addEventListener("click", () => {
    if (!current) return;
    const escape = (v: unknown) => {
      const raw = String(v ?? "");
      const safe = /^\s*[=+@-]/.test(raw) && !Number.isFinite(Number(raw)) ? "'" + raw : raw;
      return '"' + safe.replaceAll('"', '""') + '"';
    };
    const text = [tableColumns.join(","), ...current.observations.map(row => tableColumns.map(c => escape(row[c])).join(","))].join("\r\n");
    const blob = URL.createObjectURL(new Blob(["\ufeff", text], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a"); a.href = blob; a.download = `GFQA-v3-${activeCode}-page-${page + 1}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(blob), 1000);
  });
  void refresh();
  return { destroy() { controller?.abort(); clearTimeout(timer); map.remove(); trend.destroy(); histogram.destroy(); } };
}
