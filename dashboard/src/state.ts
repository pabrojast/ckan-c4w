import type { AppState, MapBounds, Meta, ParamIndex } from "./data/types";

type Listener = () => void;

export type Store = {
  get: () => AppState;
  set: (patch: Partial<AppState>) => void;
  subscribe: (listener: Listener) => () => void;
  setParam: (param: ParamIndex) => void;
  setBounds: (bounds: MapBounds) => void;
  clearSite: () => void;
};

/** Elige el parámetro inicial: el que más registros tiene, para que la primera
 *  pantalla muestre algo denso en vez de un mapa casi vacío. */
function defaultParam(meta: Meta): ParamIndex {
  let best = 0;
  for (let i = 1; i < meta.parameters.length; i++) {
    if (meta.parameters[i].records > meta.parameters[best].records) best = i;
  }
  return best;
}

/** Un store por dashboard: dos instancias en la misma página no comparten nada. */
export function createStore(meta: Meta): Store {
  const listeners = new Set<Listener>();
  const param = defaultParam(meta);
  const p = meta.parameters[param];
  const dims: Record<string, number | null> = {};
  for (const d of meta.dimensions) dims[d.key] = null;

  let state: AppState = {
    param,
    country: null,
    dims,
    periodFrom: p.minPeriod,
    periodTo: p.maxPeriod,
    selectedSite: null,
    bounds: null,
  };

  function set(patch: Partial<AppState>): void {
    state = { ...state, ...patch };
    for (const listener of listeners) listener();
  }

  return {
    get: () => state,
    set,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    /** Cambiar de parámetro reencuadra el periodo al del nuevo: cada uno cubre
     *  un rango distinto y conservar el anterior dejaría el mapa vacío sin
     *  explicar por qué. */
    setParam(next) {
      const q = meta.parameters[next];
      set({ param: next, periodFrom: q.minPeriod, periodTo: q.maxPeriod, selectedSite: null });
    },
    setBounds(bounds) {
      set({ bounds });
    },
    clearSite() {
      set({ selectedSite: null });
    },
  };
}
