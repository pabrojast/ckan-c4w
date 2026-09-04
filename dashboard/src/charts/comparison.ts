import type { ActiveElement, ChartConfiguration, ChartEvent } from "chart.js";
import type { ComparisonMode } from "../data/filter";
import { medianByGroup } from "../data/filter";
import type { ParameterMeta, ParamSeries, Sites } from "../data/types";
import { colorForValue, formatNumber } from "../config/scale";
import type { I18n } from "../i18n";
import { formatCount } from "../util/format";
import { Chart, GRID, MUTED, pointerOnHover } from "./register";

export type ComparisonHandle = {
  render: (
    series: ParamSeries,
    records: number[],
    param: ParameterMeta,
    mode: ComparisonMode,
  ) => { empty: boolean };
  destroy: () => void;
};

export function createComparison(
  canvas: HTMLCanvasElement,
  sites: Sites,
  i18n: I18n,
  onPick: (mode: ComparisonMode, id: string) => void,
): ComparisonHandle {
  let chart: Chart<"bar"> | null = null;
  const { locale, t } = i18n;

  return {
    render(series, records, param, mode) {
      if (mode.kind === "none") return { empty: true };

      const keyOf =
        mode.kind === "country"
          ? (s: number) => sites.country[s]
          : (s: number) => {
              const v = sites.dims[mode.dimension.key]?.[s];
              return v == null ? null : String(v);
            };
      const labelOf =
        mode.kind === "country"
          ? (id: string) => i18n.countryName(id)
          : (id: string) => mode.dimension.values.find((v) => String(v.id) === id)?.label ?? id;

      const rows = medianByGroup(series, records, keyOf);
      if (rows.length < 2) {
        // Con un solo grupo el gráfico no compara nada; el panel muestra el
        // mensaje vacío en su lugar.
        chart?.destroy();
        chart = null;
        return { empty: true };
      }

      const ids = rows.map((r) => r.id);
      const labels = ids.map(labelOf);
      const values = rows.map((r) => r.median);
      const colors = values.map((v) => colorForValue(v, param.breaks));

      const options: ChartConfiguration<"bar">["options"] = {
        indexAxis: "y",
        layout: { padding: { right: 10, top: 2 } },
        plugins: {
          tooltip: {
            callbacks: {
              label: (item) =>
                t("chart.tooltipGroup", {
                  value: `${formatNumber(item.parsed.x ?? 0, locale)} ${param.unit}`.trim(),
                  sites: formatCount(rows[item.dataIndex]?.sites ?? 0, locale),
                }),
              footer: () => t("chart.clickMap"),
            },
          },
        },
        onClick: (_event: ChartEvent, elements: ActiveElement[]) => {
          if (elements.length) onPick(mode, ids[elements[0].index]);
        },
        onHover: pointerOnHover,
        scales: {
          x: {
            beginAtZero: true,
            grid: { color: GRID },
            border: { display: false },
            ticks: { maxTicksLimit: 5, callback: (v) => formatNumber(Number(v), locale) },
            title: { display: Boolean(param.unit), text: param.unit, color: MUTED },
          },
          y: {
            grid: { display: false },
            // Sin esto Chart.js salta etiquetas y quedan barras sin nombre.
            ticks: { font: { size: 10 }, autoSkip: false },
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
                data: values,
                backgroundColor: colors,
                borderWidth: 0,
                borderRadius: 4,
                borderSkipped: "start",
                maxBarThickness: 18,
                categoryPercentage: 0.85,
                barPercentage: 0.9,
              },
            ],
          },
          options,
        });
        return { empty: false };
      }

      chart.data.labels = labels;
      const dataset = chart.data.datasets[0];
      if (dataset) {
        dataset.data = values;
        dataset.backgroundColor = colors;
      }
      chart.options = options;
      chart.update("none");
      return { empty: false };
    },
    destroy() {
      chart?.destroy();
      chart = null;
    },
  };
}
