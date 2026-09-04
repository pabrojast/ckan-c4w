import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  Filler,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from "chart.js";

Chart.register(
  LineController,
  BarController,
  LineElement,
  BarElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Filler,
);

/** Tokens del portal C4W: la tipografía y los grises de los ejes son los del
 *  resto del sitio, no los de Chart.js. */
export const INK = "#0A1628";
export const MUTED = "#5B6B7A";
export const RULE = "rgba(10, 22, 40, 0.10)";
export const GRID = "rgba(10, 22, 40, 0.07)";
export const ACCENT = "#005FAA";
export const ACCENT_WASH = "rgba(0, 119, 212, 0.10)";
export const SURFACE = "#FFFFFF";

Chart.defaults.font.family = '"IBM Plex Sans", "Noto Sans", "Segoe UI", sans-serif';
Chart.defaults.font.size = 11;
Chart.defaults.color = MUTED;
Chart.defaults.borderColor = RULE;
Chart.defaults.maintainAspectRatio = false;
Chart.defaults.animation = false;
Chart.defaults.plugins.tooltip.backgroundColor = INK;
Chart.defaults.plugins.tooltip.titleColor = SURFACE;
Chart.defaults.plugins.tooltip.bodyColor = SURFACE;
Chart.defaults.plugins.tooltip.footerColor = "rgba(255,255,255,0.7)";
Chart.defaults.plugins.tooltip.cornerRadius = 2;
Chart.defaults.plugins.tooltip.padding = 8;
Chart.defaults.plugins.tooltip.displayColors = false;

export { Chart };

/** Cursor de mano sobre una marca clicable, sin tocar el canvas al azar. */
export function pointerOnHover(event: { native: Event | null }, elements: unknown[]): void {
  const el = event.native?.target;
  if (el instanceof HTMLElement) el.style.cursor = elements.length ? "pointer" : "default";
}

export type ChartHandle = {
  destroy: () => void;
};
