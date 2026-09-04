import type { ActiveElement, ChartConfiguration, ChartEvent } from "chart.js";
import type { Grain, ParameterMeta, ParamSeries } from "../data/types";
import { trendPoints } from "../data/filter";
import { formatPeriod } from "../data/periods";
import { formatNumber } from "../config/scale";
import type { I18n } from "../i18n";
import { formatCount } from "../util/format";
import { ACCENT, ACCENT_WASH, Chart, GRID, MUTED, SURFACE, pointerOnHover } from "./register";

export type TimelineHandle = {
  render: (series: ParamSeries, records: number[], param: ParameterMeta) => Grain;
  destroy: () => void;
};

export function createTimeline(
  canvas: HTMLCanvasElement,
  grain: Grain,
  i18n: I18n,
  onPick: (period: number, pointGrain: Grain) => void,
): TimelineHandle {
  let chart: Chart<"line"> | null = null;
  const { locale, t } = i18n;

  return {
    render(series, records, param) {
      const { points, grain: pointGrain } = trendPoints(series, records, grain);
      // Escala de categorías sobre el periodo y no temporal: los datos ya
      // vienen agregados, así que una escala de tiempo sólo añadiría huecos.
      const periods = points.map((p) => p.period);
      const labels = periods.map((p) => formatPeriod(p, pointGrain, locale));
      const values = points.map((p) => p.value);
      const counts = points.map((p) => p.n);
      const sparse = points.length <= 36;

      const options: ChartConfiguration<"line">["options"] = {
        layout: { padding: { top: 6, bottom: 2, right: 6 } },
        interaction: { mode: "index", intersect: false },
        plugins: {
          tooltip: {
            callbacks: {
              title: (items) => String(items[0]?.label ?? ""),
              label: (item) =>
                t("chart.tooltipPeriod", {
                  value: `${formatNumber(item.parsed.y ?? 0, locale)} ${param.unit}`.trim(),
                  n: formatCount(counts[item.dataIndex] ?? 0, locale),
                }),
              footer: () => t("chart.clickMap"),
            },
          },
        },
        onClick: (_event: ChartEvent, elements: ActiveElement[]) => {
          if (elements.length) onPick(periods[elements[0].index], pointGrain);
        },
        onHover: pointerOnHover,
        scales: {
          x: {
            grid: { display: false },
            ticks: { maxTicksLimit: 7, maxRotation: 0, font: { size: 10 } },
          },
          y: {
            beginAtZero: false,
            grid: { color: GRID },
            border: { display: false },
            ticks: { maxTicksLimit: 5, callback: (v) => formatNumber(Number(v), locale) },
            title: { display: Boolean(param.unit), text: param.unit, color: MUTED },
          },
        },
      };

      if (!chart) {
        chart = new Chart(canvas, {
          type: "line",
          data: {
            labels,
            datasets: [
              {
                data: values,
                borderColor: ACCENT,
                backgroundColor: ACCENT_WASH,
                borderWidth: 2,
                borderJoinStyle: "round",
                borderCapStyle: "round",
                pointRadius: sparse ? 3 : 0,
                pointHoverRadius: 5,
                pointBackgroundColor: ACCENT,
                pointBorderColor: SURFACE,
                pointBorderWidth: 2,
                fill: true,
                tension: 0.2,
              },
            ],
          },
          options,
        });
        return pointGrain;
      }

      chart.data.labels = labels;
      const dataset = chart.data.datasets[0];
      if (dataset) {
        dataset.data = values;
        dataset.pointRadius = sparse ? 3 : 0;
      }
      chart.options = options;
      chart.update("none");
      return pointGrain;
    },
    destroy() {
      chart?.destroy();
      chart = null;
    },
  };
}
