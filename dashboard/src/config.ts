export type DashboardConfig = {
  bundleBase: string;
  lang: string;
  basemap: string;
  title: string;
  sourceUrl: string;
};

export const DEFAULT_BASEMAP = "https://tiles.openfreemap.org/styles/positron";

/** Lee la configuración de los atributos data-* del elemento raíz (los pone el
 *  snippet Jinja `c4w/snippets/dashboard_shell.html`). */
export function readConfig(root: HTMLElement): DashboardConfig {
  const d = root.dataset;
  const bundleBase = (d.bundleBase ?? "").trim();
  if (!bundleBase) throw new Error("c4w-dashboard: data-bundle-base is required");
  return {
    bundleBase,
    lang: d.lang ?? document.documentElement.lang ?? "en",
    basemap: (d.basemap ?? "").trim() || DEFAULT_BASEMAP,
    title: d.title ?? "",
    sourceUrl: d.sourceUrl ?? "",
  };
}
