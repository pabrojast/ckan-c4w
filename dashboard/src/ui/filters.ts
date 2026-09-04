import type { Meta, Sites } from "../data/types";
import { inputToPeriod, periodToInput } from "../data/periods";
import type { I18n } from "../i18n";
import type { Store } from "../state";
import { formatCount } from "../util/format";
import { el, role } from "./dom";

const INPUT_TYPE = { year: "number", month: "month", day: "date" } as const;

/** Construye los selectores y devuelve una función que los resincroniza con el
 *  estado. Se llama en cada render para que los controles reflejen cambios que
 *  vienen del mapa o de los gráficos, no sólo del propio control. */
export function bindFilters(root: HTMLElement, meta: Meta, sites: Sites, store: Store, i18n: I18n): () => void {
  const { locale, t } = i18n;
  const grain = meta.dataset.grain;
  const paramSelect = role<HTMLSelectElement>(root, "parameter");
  const countryField = role(root, "country-field");
  const countrySelect = role<HTMLSelectElement>(root, "country");
  const dimsHost = role(root, "dimensions");
  const fromInput = role<HTMLInputElement>(root, "period-from");
  const toInput = role<HTMLInputElement>(root, "period-to");
  const siteChip = role(root, "site-chip");
  const siteLabel = role(root, "site-chip-label");
  const siteClear = role<HTMLButtonElement>(root, "site-clear");

  // ---- parámetro: agrupado por familia cuando el dataset la declara -------
  const hasFamilies = meta.parameters.some((p) => p.family);
  const groups = new Map<string, { index: number; label: string }[]>();
  meta.parameters.forEach((p, index) => {
    const family = hasFamilies ? p.family || "—" : "";
    const list = groups.get(family) ?? [];
    list.push({ index, label: p.label });
    groups.set(family, list);
  });
  paramSelect.textContent = "";
  const sortedGroups = [...groups].sort((a, b) => a[0].localeCompare(b[0], locale));
  for (const [family, items] of sortedGroups) {
    const host = hasFamilies ? el("optgroup", { label: family }) : paramSelect;
    for (const item of items.sort((a, b) => a.label.localeCompare(b.label, locale))) {
      const records = meta.parameters[item.index].records;
      host.append(el("option", { value: String(item.index) }, `${item.label} (${formatCount(records, locale)})`));
    }
    if (host !== paramSelect) paramSelect.append(host);
  }

  // ---- país: sólo si el bundle trae países ---------------------------------
  const hasCountries = meta.countries.length > 0;
  countryField.hidden = !hasCountries;
  if (hasCountries) {
    countrySelect.textContent = "";
    countrySelect.append(el("option", { value: "" }, t("filter.all")));
    const sorted = [...meta.countries].sort((a, b) =>
      i18n.countryName(a.id).localeCompare(i18n.countryName(b.id), locale),
    );
    for (const c of sorted) {
      countrySelect.append(
        el("option", { value: c.id }, `${i18n.countryName(c.id)} (${formatCount(c.count, locale)})`),
      );
    }
  }

  // ---- dimensiones categóricas: un select por cada una ---------------------
  const dimSelects = new Map<string, HTMLSelectElement>();
  dimsHost.textContent = "";
  for (const dim of meta.dimensions) {
    if (!sites.dims[dim.key]) continue;
    const id = `${root.id || "c4w-dash"}-dim-${dim.key}`;
    const label = el("label", { class: "c4w-dash__field" });
    label.append(el("span", {}, dim.label));
    const select = el("select", { id, "data-dimension": dim.key });
    select.append(el("option", { value: "" }, t("filter.all")));
    for (const v of dim.values) {
      select.append(el("option", { value: String(v.id) }, `${v.label} (${formatCount(v.count, locale)})`));
    }
    label.append(select);
    dimsHost.append(label);
    dimSelects.set(dim.key, select);
    select.addEventListener("change", () => {
      const dims = { ...store.get().dims, [dim.key]: select.value === "" ? null : Number(select.value) };
      store.set({ dims, selectedSite: null, bounds: null });
    });
  }

  // ---- periodo: el tipo del input sigue al grano del dataset ---------------
  fromInput.type = INPUT_TYPE[grain];
  toInput.type = INPUT_TYPE[grain];
  if (grain === "year") {
    fromInput.step = "1";
    toInput.step = "1";
    fromInput.inputMode = "numeric";
    toInput.inputMode = "numeric";
  }

  paramSelect.addEventListener("change", () => {
    store.setParam(Number(paramSelect.value));
  });
  countrySelect.addEventListener("change", () => {
    store.set({ country: countrySelect.value || null, selectedSite: null, bounds: null });
  });
  fromInput.addEventListener("change", () => {
    const value = inputToPeriod(fromInput.value, grain);
    if (value == null) return;
    const { periodTo } = store.get();
    store.set({ periodFrom: value, periodTo: value > periodTo ? value : periodTo });
  });
  toInput.addEventListener("change", () => {
    const value = inputToPeriod(toInput.value, grain);
    if (value == null) return;
    const { periodFrom } = store.get();
    store.set({ periodTo: value, periodFrom: value < periodFrom ? value : periodFrom });
  });
  siteClear.addEventListener("click", () => {
    store.clearSite();
  });

  return function sync(): void {
    const state = store.get();
    const param = meta.parameters[state.param];
    paramSelect.value = String(state.param);
    if (hasCountries) countrySelect.value = state.country ?? "";
    for (const [key, select] of dimSelects) {
      const v = state.dims[key];
      select.value = v == null ? "" : String(v);
    }
    // Los límites siguen al parámetro: cada uno cubre un periodo distinto y
    // dejar el rango global permitiría pedir periodos sin ningún dato.
    const lo = periodToInput(param.minPeriod, grain);
    const hi = periodToInput(param.maxPeriod, grain);
    fromInput.min = lo;
    fromInput.max = hi;
    toInput.min = lo;
    toInput.max = hi;
    fromInput.value = periodToInput(state.periodFrom, grain);
    toInput.value = periodToInput(state.periodTo, grain);
    const site = state.selectedSite;
    siteChip.hidden = site == null;
    if (site != null) siteLabel.textContent = sites.name[site] || sites.id[site];
  };
}
