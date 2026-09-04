import type { ParameterMeta } from "../data/types";
import { formatNumber, legendGradient, MISSING } from "../config/scale";
import type { I18n } from "../i18n";
import { role } from "./dom";

export function bindLegend(root: HTMLElement, i18n: I18n): (param: ParameterMeta) => void {
  const { locale, t } = i18n;
  const fill = role(root, "legend-fill");
  const name = role(root, "legend-name");
  const unit = role(root, "legend-unit");
  const high = role(root, "legend-high");
  const low = role(root, "legend-low");
  const warning = role(root, "scale-warning");

  return function renderLegend(param) {
    const usable = param.reliableScale && param.breaks.length > 0;
    fill.style.background = usable ? legendGradient() : MISSING;
    name.textContent = param.label;
    unit.textContent = param.unit;
    high.textContent = usable ? `> ${formatNumber(param.breaks[param.breaks.length - 1], locale)}` : "";
    low.textContent = usable ? `< ${formatNumber(param.breaks[0], locale)}` : "";
    // Aviso explícito en vez de un gradiente que aparenta separar valores que
    // en realidad son casi todos el mismo límite de detección.
    warning.hidden = usable;
    warning.textContent = usable ? "" : t("scale.unreliable");
  };
}
