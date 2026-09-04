import type { ParameterMeta, ParamSeries, SiteValue } from "../data/types";
import { summarize } from "../data/filter";
import { formatValue } from "../config/scale";
import type { I18n } from "../i18n";
import { formatCount } from "../util/format";
import { role } from "./dom";

export function bindStats(
  root: HTMLElement,
  i18n: I18n,
): (series: ParamSeries, records: number[], items: SiteValue[], param: ParameterMeta) => void {
  const { locale } = i18n;
  const sitesEl = role(root, "stat-sites");
  const recordsEl = role(root, "stat-records");
  const samplesEl = role(root, "stat-samples");
  const medianEl = role(root, "stat-median");
  const maxEl = role(root, "stat-max");

  return function renderStats(series, records, items, param) {
    const { median, max, samples } = summarize(series, records);
    sitesEl.textContent = formatCount(items.length, locale);
    recordsEl.textContent = formatCount(records.length, locale);
    samplesEl.textContent = formatCount(samples, locale);
    medianEl.textContent = formatValue(median, param.unit, locale);
    maxEl.textContent = formatValue(max, param.unit, locale);
  };
}
