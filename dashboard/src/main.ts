import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";
import type { Grain, Meta, ParamSeries, Sites } from "./data/types";
import { LoadError, createLoader } from "./data/load";
import {
  comparisonMode,
  filterRecords,
  recordsInBin,
  recordsInPeriod,
  usesView,
  valuesBySite,
} from "./data/filter";
import { createStore, type Store } from "./state";
import { readConfig, type DashboardConfig } from "./config";
import { createI18n } from "./i18n";
import { createMap, type MapHandle } from "./map/map";
import { createTimeline } from "./charts/timeline";
import { createHistogram } from "./charts/histogram";
import { createComparison } from "./charts/comparison";
import { bindFilters } from "./ui/filters";
import { bindLegend } from "./ui/legend";
import { bindStats } from "./ui/stats";
import { bindCsv } from "./ui/csv";
import { role, roleOrNull } from "./ui/dom";
import { formatCount } from "./util/format";

export type DashboardHandle = {
  destroy: () => void;
  /** Para QA y depuración en el navegador (el root guarda el handle en
   *  `__c4wDashboard`); no es API estable. */
  debug?: { map: MapHandle; store: Store; meta: Meta; sites: Sites };
};

const TREND_LABEL: Record<Grain, "chart.trendYear" | "chart.trendMonth" | "chart.trendDay"> = {
  year: "chart.trendYear",
  month: "chart.trendMonth",
  day: "chart.trendDay",
};

/** Monta un dashboard sobre `root`. Todo lo que necesita —bundle, idioma,
 *  mapa base— viene en los data-* del propio root, así que un template puede
 *  montar varios en una página sin que compartan estado. */
export async function mount(root: HTMLElement, override?: Partial<DashboardConfig>): Promise<DashboardHandle> {
  const cfg = { ...readConfig(root), ...override };
  const i18n = createI18n(cfg.lang);
  const { t, locale } = i18n;
  i18n.applyDom(root);
  root.classList.add("is-enhanced");

  const boot = role(root, "boot");
  const bootTitle = role(root, "boot-title");
  const bootDetail = role(root, "boot-detail");

  function showBoot(title: string, detail: string, failed = false): void {
    boot.hidden = false;
    boot.classList.toggle("is-failed", failed);
    bootTitle.textContent = title;
    bootDetail.textContent = detail;
  }

  showBoot(t("boot.loading"), t("boot.loadingDetail"));

  const loader = createLoader(cfg.bundleBase);
  let meta: Meta;
  let sites: Sites;
  try {
    ({ meta, sites } = await loader.bootstrap());
  } catch (error) {
    showBoot(t("boot.missingTitle"), error instanceof LoadError ? t("boot.missingDetail") : String(error), true);
    return { destroy() {} };
  }

  const grain = meta.dataset.grain;
  const store = createStore(meta);
  const mode = comparisonMode(meta.countries.length, meta.dimensions);

  // La serie del parámetro activo. `null` mientras se está pidiendo el archivo,
  // que es el único momento en que el dashboard no tiene datos que dibujar.
  let series: ParamSeries | null = null;
  let loadedParam = -1;
  let records: number[] = [];

  const syncFilters = bindFilters(root, meta, sites, store, i18n);
  const renderLegend = bindLegend(root, i18n);
  const renderStats = bindStats(root, i18n);
  bindCsv(root, meta, sites, i18n, () =>
    series ? { series, records, param: meta.parameters[store.get().param] } : null,
  );

  // ---- cabecera --------------------------------------------------------
  const count = roleOrNull(root, "count");
  if (count) {
    count.textContent = t("mast.count", {
      sites: formatCount(meta.siteCount, locale),
      records: formatCount(meta.records, locale),
    });
  }
  const credit = roleOrNull(root, "credit");
  if (credit) credit.textContent = meta.dataset.credit || "";
  for (const link of root.querySelectorAll<HTMLAnchorElement>('[data-role="source"]')) {
    const href = meta.dataset.source || cfg.sourceUrl;
    if (href) link.href = href;
    else link.hidden = true;
  }

  // ---- paneles -----------------------------------------------------------
  const panelTrend = role(root, "panel-trend");
  const panelDistribution = role(root, "panel-distribution");
  const panelComparison = role(root, "panel-comparison");
  const trendSubtitle = roleOrNull(root, "trend-subtitle");
  const comparisonTitle = roleOrNull(root, "comparison-title");
  if (comparisonTitle && mode.kind === "dimension") comparisonTitle.textContent = mode.dimension.label;
  if (mode.kind === "none") panelComparison.hidden = true;

  const emptyGlobal = role(root, "empty");
  const viewChip = role(root, "view-chip");
  const viewChipText = role(root, "view-chip-text");
  const loadingChip = role(root, "loading");

  // Antes de construir el mapa: MapLibre mide el contenedor al crearse y, con
  // el boot encima, el canvas quedaría dimensionado a cero si estuviera oculto.
  boot.hidden = true;

  const map = createMap(role(root, "map"), {
    sites,
    styleUrl: cfg.basemap,
    // El crédito del dataset ya está en la cabecera; aquí sólo el mapa base.
    attribution: t("map.attribution"),
    i18n,
    onSelectSite(site) {
      store.set({ selectedSite: site });
    },
    onOnlySite(site) {
      store.set({ selectedSite: site, bounds: null });
    },
    onBoundsChange(bounds) {
      store.set({ bounds });
    },
  });

  const timeline = createTimeline(role<HTMLCanvasElement>(root, "chart-trend"), grain, i18n, (period, pointGrain) => {
    if (!series) return;
    map.fitTo(valuesBySite(series, recordsInPeriod(series, records, period, grain, pointGrain)));
  });
  const histogram = createHistogram(role<HTMLCanvasElement>(root, "chart-distribution"), i18n, (bin) => {
    if (!series) return;
    const hits = recordsInBin(series, records, meta.parameters[store.get().param].breaks, bin);
    map.fitTo(valuesBySite(series, hits));
  });
  const comparison = createComparison(role<HTMLCanvasElement>(root, "chart-comparison"), sites, i18n, (m, id) => {
    if (m.kind === "country") store.set({ country: id, selectedSite: null, bounds: null });
    else if (m.kind === "dimension") {
      store.set({ dims: { ...store.get().dims, [m.dimension.key]: Number(id) }, selectedSite: null, bounds: null });
    }
  });

  let prevParam = -1;
  let prevCountry: string | null = null;
  let prevDims = "";
  let renderToken = 0;

  async function render(): Promise<void> {
    const token = ++renderToken;
    const state = store.get();
    const param = meta.parameters[state.param];

    if (loadedParam !== state.param) {
      loadingChip.hidden = false;
      try {
        const loaded = await loader.param(state.param);
        // Otro render arrancó mientras se descargaba: ese es el que manda.
        if (token !== renderToken) return;
        series = loaded;
        loadedParam = state.param;
      } catch (error) {
        if (token !== renderToken) return;
        loadingChip.hidden = true;
        showBoot(t("boot.errorTitle"), error instanceof LoadError ? error.message : String(error), true);
        return;
      }
      loadingChip.hidden = true;
    }
    if (!series) return;

    // Reencuadrar sólo cuando cambia lo que define la extensión geográfica.
    // `prevParam` arranca en -1, así que el primer render siempre reencuadra.
    const dimsKey = JSON.stringify(state.dims);
    const refit = state.param !== prevParam || state.country !== prevCountry || dimsKey !== prevDims;
    prevParam = state.param;
    prevCountry = state.country;
    prevDims = dimsKey;

    const mapRecords = filterRecords(series, sites, state, false);
    const items = valuesBySite(series, mapRecords);
    // Los gráficos siguen al encuadre; el mapa no, o se filtraría a sí mismo.
    records = filterRecords(series, sites, refit ? { ...state, bounds: null } : state, true);

    syncFilters();
    renderLegend(param);
    renderStats(series, records, items, param);
    map.update(items, param, refit);
    map.select(state.selectedSite);

    const viewing = usesView(state);
    viewChip.hidden = !viewing;
    if (viewing) viewChipText.textContent = t("view.chip", { n: formatCount(records.length, locale) });
    emptyGlobal.hidden = records.length > 0;
    emptyGlobal.textContent = viewing ? t("empty.view") : t("empty.banner");
    panelTrend.classList.toggle("is-empty", records.length === 0);
    panelDistribution.classList.toggle("is-empty", records.length === 0);

    if (records.length > 0) {
      const pointGrain = timeline.render(series, records, param);
      if (trendSubtitle) trendSubtitle.textContent = t(TREND_LABEL[pointGrain]);
      histogram.render(series, records, param);
    }
    const result = comparison.render(series, records, param, mode);
    panelComparison.classList.toggle("is-empty", result.empty || records.length === 0);
  }

  const unsubscribe = store.subscribe(() => {
    void render();
  });
  void render();

  function onKey(event: KeyboardEvent): void {
    if (event.key !== "Escape") return;
    map.closePopup();
    if (store.get().selectedSite != null) store.clearSite();
  }
  root.addEventListener("keydown", onKey);

  // El mapa no se entera solo de que el contenedor cambió de tamaño (un panel
  // que se pliega, la ventana que se estrecha): se le avisa.
  const observer = typeof ResizeObserver === "function" ? new ResizeObserver(() => map.resize()) : null;
  observer?.observe(role(root, "map"));

  const handle: DashboardHandle = {
    debug: { map, store, meta, sites },
    destroy() {
      unsubscribe();
      observer?.disconnect();
      root.removeEventListener("keydown", onKey);
      timeline.destroy();
      histogram.destroy();
      comparison.destroy();
      map.destroy();
    },
  };
  (root as HTMLElement & { __c4wDashboard?: DashboardHandle }).__c4wDashboard = handle;
  return handle;
}

function autoMount(): void {
  for (const root of document.querySelectorAll<HTMLElement>("[data-c4w-dashboard]")) {
    if (root.dataset.c4wMounted) continue;
    root.dataset.c4wMounted = "1";
    void mount(root).catch((error: unknown) => {
      console.error("c4w-dashboard: mount failed", error);
    });
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", autoMount, { once: true });
} else {
  autoMount();
}
