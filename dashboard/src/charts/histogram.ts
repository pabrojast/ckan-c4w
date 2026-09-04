import type { ActiveElement, ChartConfiguration, ChartEvent } from "chart.js";
import type { ParameterMeta, ParamSeries } from "../data/types";
import { histogramCounts } from "../data/filter";
import { RAMP, binLabels } from "../config/scale";
import type { I18n } from "../i18n";
import { formatCount } from "../util/format";
import { Chart, GRID, MUTED, pointerOnHover } from "./register";

export type HistogramHandle = {
  render: (series: ParamSeries, records: number[], param: ParameterMeta) => void;
  destroy: () => void;
};

export function createHistogram(
  canvas: HTMLCanvasElement,
  i18n: I18n,
  onPick: (bin: number) => void,
): HistogramHandle {
  let chart: Chart<"bar"> | null = null;
  const { locale, t } = i18n;

  return {
    render(series, records, param) {
      const counts = histogramCounts(series, records, param.breaks);
      const labels = binLabels(param.breaks, locale);
      // Sin cortes (escala no fiable) sólo hay un tramo: se pinta neutro.
      const colors = param.breaks.length ? [...RAMP] : ["#8A96A3"];

      const options: ChartConfiguration<"bar">["options"] = {
        layout: { padding: { bottom: 2, top: 6 } },
        plugins: {
          tooltip: {
            callbacks: {
              title: (items) => `${String(items[0]?.label ?? "")} ${param.unit}`.trim(),
              label: (item) => t("chart.tooltipCount", { n: formatCount(item.parsed.y ?? 0, locale) }),
              footer: () => t("chart.clickMap"),
            },
          },
        },
        onClick: (_event: ChartEvent, elements: ActiveElement[]) => {
          if (elements.length) onPick(elements[0].index);
        },
        onHover: pointerOnHover,
        scales: {
          x: {
            grid: { display: false },
            ticks: { maxRotation: 30, minRotation: 0, autoSkip: false, font: { size: 9 }, padding: 2 },
          },
          y: {
            beginAtZero: true,
            border: { display: false },
            ticks: { precision: 0, maxTicksLimit: 4 },
            grid: { color: GRID },
            title: { display: true, text: t("chart.records"), color: MUTED },
          },
        },
      };

      if (!chart) {
        chart = new Chart(canvas, {
          type: "bar",
          data: {
            labels,
            datasets: [
              {
                data: counts,
                backgroundColor: colors,
                borderWidth: 0,
                borderRadius: 4,
                borderSkipped: "start",
                maxBarThickness: 24,
                categoryPercentage: 0.9,
                barPercentage: 0.9,
              },
            ],
          },
          options,
        });
        return;
      }

      chart.data.labels = labels;
      const dataset = chart.data.datasets[0];
      if (dataset) {
        dataset.data = counts;
        dataset.backgroundColor = colors;
      }
      chart.options = options;
      chart.update("none");
    },
    destroy() {
      chart?.destroy();
      chart = null;
    },
  };
}
