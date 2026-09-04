import {
  AttributionControl,
  Map as MapLibreMap,
  NavigationControl,
  Popup,
  setWorkerUrl,
  type GeoJSONSource,
} from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import type { MapBounds, ParameterMeta, SiteIndex, SiteValue, Sites } from "../data/types";
import { bboxOfSites } from "../data/filter";
import { MISSING, clusterColorExpression, formatValue, pointColorExpression } from "../config/scale";
import type { I18n } from "../i18n";
import { escapeHtml, formatCount } from "../util/format";

// Worker como archivo real (c4w-dashboard-worker.js) y no como blob: una CSP
// sin `worker-src blob:` rompería el mapa en silencio.
setWorkerUrl(workerUrl);

const INK = "#0A1628";
const SURFACE = "#FFFFFF";
const SELECT = "#005FAA";

type FeatureCollection = {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    geometry: { type: "Point"; coordinates: [number, number] };
    properties: { s: number; v: number; n: number };
  }[];
};

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

export type MapHandle = {
  /** La instancia MapLibre, sólo para depuración. */
  raw: MapLibreMap;
  update: (items: SiteValue[], param: ParameterMeta, refit: boolean) => void;
  fitTo: (items: SiteValue[]) => void;
  select: (site: SiteIndex | null) => void;
  closePopup: () => void;
  resize: () => void;
  destroy: () => void;
};

type MapOptions = {
  sites: Sites;
  styleUrl: string;
  attribution: string;
  i18n: I18n;
  onSelectSite: (site: SiteIndex) => void;
  onOnlySite: (site: SiteIndex) => void;
  onBoundsChange: (bounds: MapBounds) => void;
};

export function createMap(container: HTMLElement, options: MapOptions): MapHandle {
  const { sites, styleUrl, attribution, i18n, onSelectSite, onOnlySite, onBoundsChange } = options;
  const { locale, t } = i18n;

  const map = new MapLibreMap({
    container,
    style: styleUrl,
    center: [12, 20],
    zoom: 1.3,
    attributionControl: false,
  });
  map.addControl(new NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new AttributionControl({ compact: true, customAttribution: attribution }), "bottom-right");

  const popup = new Popup({
    closeButton: false,
    maxWidth: "280px",
    offset: 12,
    className: "c4w-dash-popup",
  });

  let ready = false;
  let boundsTimer = 0;
  let current: SiteValue[] = [];
  let currentParam: ParameterMeta | null = null;
  // Si `update` llega antes de que el estilo cargue, se guarda y se aplica en style.load.
  let pending: { items: SiteValue[]; param: ParameterMeta; refit: boolean } | null = null;

  function toCollection(items: SiteValue[]): FeatureCollection {
    return {
      type: "FeatureCollection",
      features: items.map((item) => ({
        type: "Feature" as const,
        geometry: {
          type: "Point" as const,
          coordinates: [sites.lon[item.site], sites.lat[item.site]] as [number, number],
        },
        properties: { s: item.site, v: item.value, n: item.samples },
      })),
    };
  }

  function emitBounds(): void {
    if (!ready) return;
    const box = map.getBounds();
    onBoundsChange({
      west: box.getWest(),
      south: box.getSouth(),
      east: box.getEast(),
      north: box.getNorth(),
      zoom: map.getZoom(),
    });
  }

  function scheduleBounds(): void {
    window.clearTimeout(boundsTimer);
    boundsTimer = window.setTimeout(emitBounds, 140);
  }

  function fit(items: SiteValue[], maxZoom = 9): void {
    const box = bboxOfSites(sites, items);
    if (!box) return;
    const [minX, minY, maxX, maxY] = box;
    if (minX === maxX && minY === maxY) {
      map.easeTo({ center: [minX, minY], zoom: Math.min(11, maxZoom + 2) });
      return;
    }
    map.fitBounds(
      [
        [minX, minY],
        [maxX, maxY],
      ],
      { padding: 48, maxZoom, duration: 600 },
    );
  }

  function openPopup(site: SiteIndex, value: number, samples: number): void {
    const item = current.find((i) => i.site === site);
    const periods = item?.periods ?? 0;
    const param = currentParam;
    const name = sites.name[site];
    const country = i18n.countryName(sites.country[site]);
    const kicker = [country, name ? sites.id[site] : null].filter(Boolean).join(" · ");
    popup
      .setLngLat([sites.lon[site], sites.lat[site]])
      .setHTML(
        `<article class="c4w-dash-popup__card">
          <header>
            ${kicker ? `<p class="c4w-dash-popup__kicker">${escapeHtml(kicker)}</p>` : ""}
            <h3>${escapeHtml(name || sites.id[site])}</h3>
          </header>
          <dl>
            <div><dt>${escapeHtml(t("stat.median"))}</dt>
                 <dd>${escapeHtml(param ? formatValue(value, param.unit, locale) : String(value))}</dd></div>
            <div><dt>${escapeHtml(t("stat.records"))}</dt><dd>${formatCount(periods, locale)}</dd></div>
            <div><dt>${escapeHtml(t("stat.samples"))}</dt><dd>${formatCount(samples, locale)}</dd></div>
          </dl>
          <button type="button" class="c4w-dash-popup__only" data-site="${site}">${escapeHtml(
            t("popup.only"),
          )}</button>
        </article>`,
      )
      .addTo(map);

    popup
      .getElement()
      ?.querySelector<HTMLButtonElement>(".c4w-dash-popup__only")
      ?.addEventListener("click", () => {
        onOnlySite(site);
        popup.remove();
      });
  }

  // `style.load` y NO `load`: `load` espera a que TODOS los sources del estilo
  // terminen, y positron trae un source ráster (`ne2_shaded`, el relieve de
  // Natural Earth) que en maplibre-gl 6.4 se queda con `_loaded === false`
  // para siempre. Con `load`, este callback no corría nunca: no se añadían
  // estas capas y, como el estilo nunca se daba por cargado, tampoco se pedían
  // los tiles del mapa base — de ahí el mapa en blanco hasta que el usuario
  // movía la cámara y forzaba un update.
  map.on("style.load", () => {
    map.addSource("sites", {
      type: "geojson",
      data: EMPTY,
      // Agrupado desde el arranque: a escala mundial pueden ser miles de
      // sitios y sin clusters la vista inicial es una mancha.
      cluster: true,
      clusterRadius: 46,
      clusterMaxZoom: 11,
      // supercluster sólo acumula sumas, así que se acumula el total de los
      // valores y el color del grupo se calcula dividiendo por `point_count`.
      clusterProperties: {
        vSum: ["+", ["coalesce", ["get", "v"], 0]],
      },
    });
    map.addSource("selected", { type: "geojson", data: EMPTY });

    map.addLayer({
      id: "clusters",
      type: "circle",
      source: "sites",
      filter: ["has", "point_count"],
      paint: {
        "circle-color": MISSING,
        "circle-radius": ["step", ["get", "point_count"], 14, 25, 18, 100, 23, 750, 30, 4000, 38],
        "circle-opacity": 0.92,
        "circle-stroke-width": 2,
        "circle-stroke-color": SURFACE,
      },
    });

    map.addLayer({
      id: "cluster-count",
      type: "symbol",
      source: "sites",
      filter: ["has", "point_count"],
      layout: {
        "text-field": ["to-string", ["get", "point_count"]],
        "text-size": 11,
        // Fontstack que positron ya carga; pedir otra dispararía una petición
        // de glifos que el estilo no tiene.
        "text-font": ["Noto Sans Regular"],
      },
      paint: { "text-color": INK },
    });

    map.addLayer({
      id: "points",
      type: "circle",
      source: "sites",
      filter: ["!", ["has", "point_count"]],
      paint: {
        // El radio crece con el zoom para que a escala mundial los puntos no
        // se fundan en una mancha y de cerca sigan siendo clicables.
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 3, 4, 4.5, 9, 7, 14, 11],
        "circle-color": MISSING,
        "circle-opacity": 0.9,
        // Un trazo de tinta: el tramo más claro de la rampa es casi blanco y
        // sin él desaparecería sobre el mapa base.
        "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 3, 0.6, 8, 1.2],
        "circle-stroke-color": INK,
        "circle-stroke-opacity": 0.55,
      },
    });

    map.addLayer({
      id: "selected-ring",
      type: "circle",
      source: "selected",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 7, 9, 13, 14, 18],
        "circle-color": "rgba(0,0,0,0)",
        "circle-stroke-width": 2.5,
        "circle-stroke-color": SELECT,
      },
    });

    map.on("click", "points", (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      const props = feature.properties as { s: number; v: number; n: number };
      onSelectSite(Number(props.s));
      openPopup(Number(props.s), Number(props.v), Number(props.n));
    });

    // Clic en un grupo: acercar hasta el zoom en que se abre.
    map.on("click", "clusters", (event) => {
      const feature = map.queryRenderedFeatures(event.point, { layers: ["clusters"] })[0];
      if (!feature || feature.geometry.type !== "Point") return;
      const clusterId = feature.properties?.cluster_id as number | undefined;
      if (clusterId == null) return;
      const coords = feature.geometry.coordinates as [number, number];
      const source = map.getSource("sites") as GeoJSONSource;
      void source.getClusterExpansionZoom(clusterId).then((zoom) => {
        map.easeTo({ center: coords, zoom });
      });
    });

    for (const layer of ["points", "clusters"] as const) {
      map.on("mouseenter", layer, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", layer, () => {
        map.getCanvas().style.cursor = "";
      });
    }

    map.on("moveend", scheduleBounds);
    map.on("zoomend", scheduleBounds);

    ready = true;
    if (pending) {
      const { items, param, refit } = pending;
      pending = null;
      handle.update(items, param, refit);
    }
    emitBounds();
  });

  const handle: MapHandle = {
    raw: map,
    update(items, param, refit) {
      if (!ready) {
        pending = { items, param, refit };
        return;
      }
      current = items;
      currentParam = param;
      (map.getSource("sites") as GeoJSONSource).setData(toCollection(items));
      map.setPaintProperty("points", "circle-color", pointColorExpression(param.breaks));
      map.setPaintProperty("clusters", "circle-color", clusterColorExpression(param.breaks));
      if (refit && items.length) fit(items);
    },
    fitTo(items) {
      if (ready) fit(items);
    },
    select(site) {
      if (!ready) return;
      const source = map.getSource("selected") as GeoJSONSource | undefined;
      if (!source) return;
      if (site == null) {
        source.setData(EMPTY);
        return;
      }
      source.setData({
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            geometry: { type: "Point", coordinates: [sites.lon[site], sites.lat[site]] },
            properties: { s: site, v: 0, n: 0 },
          },
        ],
      });
    },
    closePopup() {
      popup.remove();
    },
    resize() {
      map.resize();
    },
    destroy() {
      window.clearTimeout(boundsTimer);
      popup.remove();
      map.remove();
    },
  };

  return handle;
}
