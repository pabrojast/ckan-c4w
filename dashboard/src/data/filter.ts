import type {
  AppState,
  DimensionMeta,
  Grain,
  MapBounds,
  ParamSeries,
  SiteIndex,
  SiteValue,
  Sites,
} from "./types";
import { coarsen, coarserGrain } from "./periods";

/** A partir de este zoom, los gráficos siguen al encuadre del mapa. */
export const VIEW_ZOOM = 4;

/** Mínimo de sitios para que la mediana de un grupo (país, tipo de cuerpo de
 *  agua…) sea una señal y no la anécdota de un solo punto. */
export const MIN_GROUP_SITES = 2;

/** Por encima de esto la línea de tendencia se resume al grano superior. */
export const MAX_TREND_POINTS = 400;

export function padBounds(bounds: MapBounds, factor = 0.25): MapBounds {
  const latSpan = Math.max(bounds.north - bounds.south, 0.01);
  let lonSpan = bounds.east - bounds.west;
  if (lonSpan < 0) lonSpan += 360;
  lonSpan = Math.max(lonSpan, 0.01);
  return {
    west: bounds.west - lonSpan * factor,
    south: Math.max(-90, bounds.south - latSpan * factor),
    east: bounds.east + lonSpan * factor,
    north: Math.min(90, bounds.north + latSpan * factor),
    zoom: bounds.zoom,
  };
}

export function inBounds(lon: number, lat: number, bounds: MapBounds): boolean {
  if (lat < bounds.south || lat > bounds.north) return false;
  // El encuadre cruza el antimeridiano cuando west > east.
  if (bounds.west <= bounds.east) return lon >= bounds.west && lon <= bounds.east;
  return lon >= bounds.west || lon <= bounds.east;
}

export function usesView(state: AppState): boolean {
  return state.selectedSite == null && state.bounds != null && state.bounds.zoom >= VIEW_ZOOM;
}

/** Posiciones de `series` que pasan el filtro actual. */
export function filterRecords(
  series: ParamSeries,
  sites: Sites,
  state: AppState,
  view: boolean,
): number[] {
  const out: number[] = [];
  const box = view && usesView(state) && state.bounds ? padBounds(state.bounds) : null;
  const { country, periodFrom, periodTo, selectedSite } = state;
  const dimFilters = Object.entries(state.dims).filter(([, v]) => v != null) as [string, number][];

  for (let i = 0; i < series.value.length; i++) {
    const period = series.period[i];
    if (period < periodFrom || period > periodTo) continue;
    const s = series.site[i];
    if (selectedSite != null && s !== selectedSite) continue;
    if (country != null && sites.country[s] !== country) continue;
    if (dimFilters.length) {
      let ok = true;
      for (const [key, value] of dimFilters) {
        if (sites.dims[key]?.[s] !== value) {
          ok = false;
          break;
        }
      }
      if (!ok) continue;
    }
    if (box && !inBounds(sites.lon[s], sites.lat[s], box)) continue;
    out.push(i);
  }
  return out;
}

export function median(sorted: number[]): number {
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/** Colapsa los registros a un punto por sitio.
 *
 * Mediana y no promedio a propósito: las concentraciones tienen colas muy
 * largas (un vertido puntual mueve el promedio de un sitio un orden de
 * magnitud) y el mapa quedaría dominado por valores atípicos. */
export function valuesBySite(series: ParamSeries, records: number[]): SiteValue[] {
  const groups = new Map<SiteIndex, { values: number[]; samples: number }>();
  for (const i of records) {
    const s = series.site[i];
    let g = groups.get(s);
    if (!g) {
      g = { values: [], samples: 0 };
      groups.set(s, g);
    }
    g.values.push(series.value[i]);
    g.samples += series.samples[i];
  }
  const out: SiteValue[] = [];
  for (const [site, g] of groups) {
    g.values.sort((a, b) => a - b);
    out.push({ site, value: median(g.values), periods: g.values.length, samples: g.samples });
  }
  return out;
}

export function bboxOfSites(
  sites: Sites,
  items: SiteValue[],
): [number, number, number, number] | null {
  if (items.length === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const { site } of items) {
    const x = sites.lon[site];
    const y = sites.lat[site];
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  return Number.isFinite(minX) ? [minX, minY, maxX, maxY] : null;
}

export type SeriesPoint = { period: number; value: number; n: number };

/** Mediana por periodo para la línea de tendencia. Si hay más puntos de los
 *  que una línea puede mostrar, se resume al grano superior (día→mes→año) y se
 *  devuelve ese grano para etiquetar el eje. */
export function trendPoints(
  series: ParamSeries,
  records: number[],
  grain: Grain,
): { points: SeriesPoint[]; grain: Grain } {
  let current = grain;
  for (;;) {
    const buckets = new Map<number, number[]>();
    for (const i of records) {
      const period = coarsen(series.period[i], grain, current);
      let bucket = buckets.get(period);
      if (!bucket) buckets.set(period, (bucket = []));
      bucket.push(series.value[i]);
    }
    const next = coarserGrain(current);
    if (buckets.size > MAX_TREND_POINTS && next) {
      current = next;
      continue;
    }
    const points = [...buckets.entries()]
      .map(([period, values]) => {
        values.sort((a, b) => a - b);
        return { period, value: median(values), n: values.length };
      })
      .sort((a, b) => a.period - b.period);
    return { points, grain: current };
  }
}

/** Conteos por tramo de la escala. `breaks` son 6 cortes, o sea 7 tramos. */
export function histogramCounts(series: ParamSeries, records: number[], breaks: number[]): number[] {
  const counts = new Array(breaks.length + 1).fill(0);
  for (const i of records) {
    const v = series.value[i];
    let bin = breaks.length;
    for (let b = 0; b < breaks.length; b++) {
      if (v < breaks[b]) {
        bin = b;
        break;
      }
    }
    counts[bin] += 1;
  }
  return counts;
}

export function recordsInBin(
  series: ParamSeries,
  records: number[],
  breaks: number[],
  bin: number,
): number[] {
  const lo = bin === 0 ? -Infinity : breaks[bin - 1];
  const hi = bin >= breaks.length ? Infinity : breaks[bin];
  return records.filter((i) => series.value[i] >= lo && series.value[i] < hi);
}

export function recordsInPeriod(
  series: ParamSeries,
  records: number[],
  period: number,
  grain: Grain,
  pointGrain: Grain,
): number[] {
  return records.filter((i) => coarsen(series.period[i], grain, pointGrain) === period);
}

export type GroupMedian = { id: string; median: number; n: number; sites: number };

/** Mediana por grupo (país o dimensión) para el gráfico de comparación. */
export function medianByGroup(
  series: ParamSeries,
  records: number[],
  keyOf: (site: SiteIndex) => string | null,
  limit = 10,
): GroupMedian[] {
  const buckets = new Map<string, { values: number[]; sites: Set<number> }>();
  for (const i of records) {
    const s = series.site[i];
    const key = keyOf(s);
    if (key == null) continue;
    let bucket = buckets.get(key);
    if (!bucket) buckets.set(key, (bucket = { values: [], sites: new Set() }));
    bucket.values.push(series.value[i]);
    bucket.sites.add(s);
  }
  return [...buckets.entries()]
    .map(([id, bucket]) => {
      bucket.values.sort((a, b) => a - b);
      return { id, median: median(bucket.values), n: bucket.values.length, sites: bucket.sites.size };
    })
    .filter((row) => row.sites >= MIN_GROUP_SITES)
    .sort((a, b) => b.median - a.median)
    .slice(0, limit);
}

/** Qué agrupa el gráfico de comparación: país si el dataset lo trae, si no la
 *  primera dimensión categórica, si no nada. */
export type ComparisonMode =
  | { kind: "country" }
  | { kind: "dimension"; dimension: DimensionMeta }
  | { kind: "none" };

export function comparisonMode(countries: number, dimensions: DimensionMeta[]): ComparisonMode {
  if (countries > 1) return { kind: "country" };
  const dimension = dimensions.find((d) => d.values.length > 1);
  if (dimension) return { kind: "dimension", dimension };
  return { kind: "none" };
}

export function summarize(
  series: ParamSeries,
  records: number[],
): { median: number | null; max: number | null; samples: number } {
  if (records.length === 0) return { median: null, max: null, samples: 0 };
  const values: number[] = [];
  let samples = 0;
  let max = -Infinity;
  for (const i of records) {
    const v = series.value[i];
    values.push(v);
    samples += series.samples[i];
    if (v > max) max = v;
  }
  values.sort((a, b) => a - b);
  return { median: median(values), max, samples };
}
