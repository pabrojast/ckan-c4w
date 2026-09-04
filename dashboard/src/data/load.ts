import type { Meta, ParamIndex, ParamSeries, Sites } from "./types";

export type Bootstrap = {
  meta: Meta;
  sites: Sites;
};

export class LoadError extends Error {}

async function getJson<T>(url: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { credentials: "same-origin" });
  } catch (cause) {
    throw new LoadError(`request failed: ${url}`, { cause });
  }
  if (!res.ok) throw new LoadError(`${url} answered ${res.status}`);
  // Un servidor con fallback a HTML devolvería la página de error con 200
  // para un bundle inexistente; sin este chequeo el fallo aparecería mucho
  // después como un error de parseo incomprensible.
  const type = res.headers.get("content-type") ?? "";
  if (!type.includes("json")) {
    throw new LoadError(`${url} returned "${type || "no content-type"}" instead of JSON`);
  }
  return (await res.json()) as T;
}

/** Rellena lo opcional para que el resto del código no tenga que preguntarse
 *  si un bundle antiguo trae `dimensions` o `name`. */
function normaliseMeta(meta: Meta): Meta {
  return {
    ...meta,
    dimensions: meta.dimensions ?? [],
    countries: meta.countries ?? [],
    parameters: (meta.parameters ?? []).map((p) => ({
      ...p,
      family: p.family || null,
      breaks: p.breaks ?? [],
      reliableScale: p.reliableScale !== false,
    })),
  };
}

function normaliseSites(sites: Sites): Sites {
  const n = sites.id.length;
  return {
    ...sites,
    name: sites.name ?? new Array(n).fill(null),
    country: sites.country ?? new Array(n).fill(null),
    dims: sites.dims ?? {},
  };
}

export type Loader = {
  bootstrap: () => Promise<Bootstrap>;
  param: (index: ParamIndex) => Promise<ParamSeries>;
};

/** Un cargador por instancia: la caché de series pertenece a un bundle. */
export function createLoader(baseUrl: string): Loader {
  const base = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  const cache = new Map<ParamIndex, ParamSeries>();
  const inflight = new Map<ParamIndex, Promise<ParamSeries>>();

  return {
    async bootstrap() {
      const [meta, sites] = await Promise.all([
        getJson<Meta>(`${base}meta.json`),
        getJson<Sites>(`${base}sites.json`),
      ]);
      if (!meta.parameters?.length) throw new LoadError("meta.json has no parameters");
      if (!sites.id?.length) throw new LoadError("sites.json is empty");
      return { meta: normaliseMeta(meta), sites: normaliseSites(sites) };
    },

    /** Carga (y memoiza) la serie de un parámetro. Volver a uno ya visitado es
     *  instantáneo, que es el patrón real de uso: comparar entre parámetros. */
    param(index) {
      const hit = cache.get(index);
      if (hit) return Promise.resolve(hit);
      // Sin esto, mover rápido el selector dispara varias peticiones del mismo
      // archivo antes de que la primera termine.
      const pending = inflight.get(index);
      if (pending) return pending;

      const request = getJson<ParamSeries>(`${base}p/${index}.json`)
        .then((series) => {
          if (!series.value?.length) throw new LoadError(`parameter ${index} is empty`);
          cache.set(index, series);
          return series;
        })
        .finally(() => {
          inflight.delete(index);
        });
      inflight.set(index, request);
      return request;
    },
  };
}
