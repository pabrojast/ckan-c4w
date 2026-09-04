import type { ExpressionSpecification } from "maplibre-gl";

/** Rampa secuencial única para todos los parámetros.
 *
 * Un solo gradiente y no una paleta por variable: los cortes vienen de los
 * percentiles de cada parámetro (o de los bins que fijó quien subió los
 * datos), así que el color siempre significa lo mismo —"qué tan alto respecto
 * al resto de las mediciones de ESTE parámetro"— sin importar si la unidad es
 * mg/L, µS/cm o m³/s. Es monótona en luminosidad, así que el orden se lee
 * también en escala de grises. Heredada de GemsWater tal cual. */
export const RAMP = [
  "#FFF7D6",
  "#FEE39A",
  "#FDC663",
  "#F99F3E",
  "#EE7126",
  "#D2451E",
  "#9E1B1B",
] as const;

/** Sitio sin dato bajo el filtro actual, o escala no fiable. Gris azulado del
 *  portal, no el gris pardo de GemsWater. */
export const MISSING = "#8A96A3";

/** Tramo (0…6) al que cae un valor según los cortes del parámetro. */
export function binOf(value: number, breaks: number[]): number {
  for (let i = 0; i < breaks.length; i++) {
    if (value < breaks[i]) return i;
  }
  return breaks.length;
}

export function colorForValue(value: number | null, breaks: number[]): string {
  if (value == null || !Number.isFinite(value)) return MISSING;
  return RAMP[Math.min(binOf(value, breaks), RAMP.length - 1)];
}

/** Expresión MapLibre que colorea por tramos.
 *
 * `step` y no `interpolate` a propósito: los cortes son percentiles, no una
 * escala lineal, así que interpolar entre ellos sugeriría una continuidad que
 * los datos no tienen. */
export function pointColorExpression(breaks: number[]): ExpressionSpecification {
  if (!breaks.length) return MISSING as unknown as ExpressionSpecification;
  const args: unknown[] = ["step", ["get", "v"], RAMP[0]];
  for (let i = 0; i < breaks.length; i++) {
    args.push(breaks[i], RAMP[Math.min(i + 1, RAMP.length - 1)]);
  }
  return args as ExpressionSpecification;
}

/** Color de un cluster, a partir del promedio de las medianas que agrupa.
 *
 * `vSum` lo acumula supercluster vía `clusterProperties`; el divisor es
 * `point_count`. Es un promedio de medianas —no la mediana del conjunto—
 * porque supercluster sólo sabe acumular sumas; para colorear un grupo alcanza. */
export function clusterColorExpression(breaks: number[]): ExpressionSpecification {
  if (!breaks.length) return MISSING as unknown as ExpressionSpecification;
  const average: ExpressionSpecification = ["/", ["get", "vSum"], ["max", ["get", "point_count"], 1]];
  const args: unknown[] = ["step", average, RAMP[0]];
  for (let i = 0; i < breaks.length; i++) {
    args.push(breaks[i], RAMP[Math.min(i + 1, RAMP.length - 1)]);
  }
  return args as ExpressionSpecification;
}

export function legendGradient(): string {
  return `linear-gradient(to top, ${RAMP.join(", ")})`;
}

/** Cifras significativas según la magnitud: 0,0034 mg/L y 1.240 µS/cm
 *  necesitan precisiones muy distintas, y fijar decimales dejaría una de las
 *  dos inútil. */
function digitsFor(value: number): number {
  const abs = Math.abs(value);
  if (abs === 0) return 0;
  if (abs >= 100) return 0;
  if (abs >= 10) return 1;
  if (abs >= 1) return 2;
  if (abs >= 0.01) return 3;
  return 4;
}

export function formatNumber(value: number, locale: string): string {
  return value.toLocaleString(locale, { maximumFractionDigits: digitsFor(value) });
}

export function formatValue(value: number | null, unit: string, locale: string): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return unit ? `${formatNumber(value, locale)} ${unit}` : formatNumber(value, locale);
}

/** Etiquetas de los 7 tramos, para el histograma y la leyenda. */
export function binLabels(breaks: number[], locale: string): string[] {
  if (!breaks.length) return [];
  const labels: string[] = [`< ${formatNumber(breaks[0], locale)}`];
  for (let i = 0; i < breaks.length - 1; i++) {
    labels.push(`${formatNumber(breaks[i], locale)} – ${formatNumber(breaks[i + 1], locale)}`);
  }
  labels.push(`> ${formatNumber(breaks[breaks.length - 1], locale)}`);
  return labels;
}
