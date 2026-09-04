/** Modelo de datos del dashboard C4W.
 *
 * El servidor (ckanext-c4w, paquete `data/`) precalcula un bundle por dataset:
 *
 *   meta.json      configuración + catálogo de parámetros, dimensiones y países
 *   sites.json     tabla de sitios, columnar (índices alineados)
 *   p/<i>.json     la serie de UN parámetro: (sitio, periodo, mediana, muestras)
 *
 * Igual que en GemsWater, el explorador carga meta + sitios al arrancar y pide
 * cada parámetro cuando se elige, porque cargar todos de golpe no escala a
 * cientos de parámetros.
 *
 * Los periodos son enteros compactos según el grano del dataset:
 *   year  -> 2019        month -> 201903        day -> 20190314
 */

export type Grain = "year" | "month" | "day";

/** Índice dentro de `Meta.parameters`; también el nombre del archivo en p/. */
export type ParamIndex = number;

/** Índice dentro de las columnas de `Sites`. */
export type SiteIndex = number;

export type ParameterMeta = {
  key: string;
  label: string;
  unit: string;
  /** Agrupador del selector (p. ej. "Nutrients"); null si el dataset no los usa. */
  family: string | null;
  records: number;
  sites: number;
  minPeriod: number;
  maxPeriod: number;
  /** Seis cortes (p10…p97, o los bins manuales del mapeo) = siete tramos. */
  breaks: number[];
  /** false cuando los percentiles colapsan (casi todo en el límite de
   *  detección): se avisa en vez de pintar un gradiente vacío de información. */
  reliableScale: boolean;
};

export type DimensionValue = { id: number; label: string; count: number };

export type DimensionMeta = {
  key: string;
  label: string;
  values: DimensionValue[];
};

export type CountryMeta = {
  /** ISO 3166-1 alpha-2 cuando el servidor pudo resolverlo; si no, el valor crudo. */
  id: string;
  count: number;
};

export type DatasetMeta = {
  slug: string;
  title: string;
  credit: string;
  source: string;
  license: string;
  grain: Grain;
  generatedAt: string;
};

export type Meta = {
  schema: number;
  dataset: DatasetMeta;
  records: number;
  siteCount: number;
  minPeriod: number;
  maxPeriod: number;
  parameters: ParameterMeta[];
  dimensions: DimensionMeta[];
  countries: CountryMeta[];
};

export type Sites = {
  id: string[];
  name: (string | null)[];
  lat: number[];
  lon: number[];
  country: (string | null)[];
  /** Valor (id de `DimensionMeta.values`) por sitio y dimensión; la moda. */
  dims: Record<string, (number | null)[]>;
};

/** Contenido de p/<i>.json: la serie de un solo parámetro. */
export type ParamSeries = {
  site: SiteIndex[];
  period: number[];
  value: number[];
  /** Cuántas mediciones crudas resumió esta mediana. */
  samples: number[];
};

export type MapBounds = {
  west: number;
  south: number;
  east: number;
  north: number;
  zoom: number;
};

export type AppState = {
  param: ParamIndex;
  country: string | null;
  dims: Record<string, number | null>;
  periodFrom: number;
  periodTo: number;
  selectedSite: SiteIndex | null;
  bounds: MapBounds | null;
};

/** Un sitio resumido para el mapa: mediana de sus valores bajo el filtro. */
export type SiteValue = {
  site: SiteIndex;
  value: number;
  periods: number;
  samples: number;
};
