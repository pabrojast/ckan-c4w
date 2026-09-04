import type { Meta, ParameterMeta, ParamSeries, Sites } from "../data/types";
import { periodToInput } from "../data/periods";
import type { I18n } from "../i18n";
import { escapeCsv, slug } from "../util/format";
import { role } from "./dom";

/** Exporta exactamente lo que está filtrado, no el dataset entero: el CSV
 *  debe poder reproducir lo que la persona está viendo. */
export function bindCsv(
  root: HTMLElement,
  meta: Meta,
  sites: Sites,
  i18n: I18n,
  getContext: () => { series: ParamSeries; records: number[]; param: ParameterMeta } | null,
): void {
  const button = role<HTMLButtonElement>(root, "csv");
  const { t } = i18n;
  const grain = meta.dataset.grain;
  const dims = meta.dimensions.filter((d) => sites.dims[d.key]);

  button.addEventListener("click", () => {
    const context = getContext();
    if (!context) return;
    const { series, records, param } = context;
    const header = [
      t("csv.site"),
      t("csv.name"),
      t("csv.country"),
      t("csv.period"),
      t("csv.lat"),
      t("csv.lon"),
      t("csv.value"),
      t("csv.unit"),
      t("csv.samples"),
      ...dims.map((d) => d.key),
    ].join(",");

    const labelOf = new Map<string, Map<number, string>>();
    for (const d of dims) labelOf.set(d.key, new Map(d.values.map((v) => [v.id, v.label])));

    const lines = [header];
    for (const i of records) {
      const s = series.site[i];
      const row = [
        escapeCsv(sites.id[s]),
        escapeCsv(sites.name[s] ?? ""),
        escapeCsv(sites.country[s] ?? ""),
        periodToInput(series.period[i], grain),
        sites.lat[s],
        sites.lon[s],
        series.value[i],
        escapeCsv(param.unit),
        series.samples[i],
      ];
      for (const d of dims) {
        const v = sites.dims[d.key]?.[s];
        row.push(escapeCsv(v == null ? "" : (labelOf.get(d.key)?.get(v) ?? String(v))));
      }
      lines.push(row.join(","));
    }

    const blob = new Blob([`﻿${lines.join("\n")}`], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slug(meta.dataset.slug || meta.dataset.title)}-${slug(param.key)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  });
}
