import type { Grain } from "./types";

/** Periodos compactos: 2019 · 201903 · 20190314. Enteros para que ordenar y
 *  comparar sea aritmética y el JSON de una serie larga siga siendo pequeño. */

export function periodParts(period: number, grain: Grain): { y: number; m: number; d: number } {
  if (grain === "year") return { y: period, m: 1, d: 1 };
  if (grain === "month") return { y: Math.floor(period / 100), m: period % 100, d: 1 };
  return { y: Math.floor(period / 10000), m: Math.floor(period / 100) % 100, d: period % 100 };
}

export function periodToDate(period: number, grain: Grain): Date {
  const { y, m, d } = periodParts(period, grain);
  return new Date(Date.UTC(y, m - 1, d));
}

const pad = (n: number): string => String(n).padStart(2, "0");

/** Valor para un <input type=number|month|date> según el grano. */
export function periodToInput(period: number, grain: Grain): string {
  const { y, m, d } = periodParts(period, grain);
  if (grain === "year") return String(y);
  if (grain === "month") return `${y}-${pad(m)}`;
  return `${y}-${pad(m)}-${pad(d)}`;
}

export function inputToPeriod(value: string, grain: Grain): number | null {
  const text = value.trim();
  if (!text) return null;
  if (grain === "year") {
    const y = Number(text);
    return Number.isInteger(y) && y > 0 ? y : null;
  }
  const match = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(text);
  if (!match) return null;
  const y = Number(match[1]);
  const m = Number(match[2]);
  const d = match[3] ? Number(match[3]) : 1;
  if (m < 1 || m > 12 || d < 1 || d > 31) return null;
  if (grain === "month") return y * 100 + m;
  return y * 10000 + m * 100 + d;
}

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(locale: string, grain: Grain): Intl.DateTimeFormat {
  const key = `${locale}|${grain}`;
  let f = formatters.get(key);
  if (!f) {
    const options: Intl.DateTimeFormatOptions =
      grain === "month"
        ? { month: "short", year: "numeric", timeZone: "UTC" }
        : { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" };
    f = new Intl.DateTimeFormat(locale, options);
    formatters.set(key, f);
  }
  return f;
}

export function formatPeriod(period: number, grain: Grain, locale: string): string {
  if (grain === "year") return String(period);
  try {
    return formatter(locale, grain).format(periodToDate(period, grain));
  } catch {
    return periodToInput(period, grain);
  }
}

/** Agrupa periodos finos en gruesos: day→month, month→year (para la línea de
 *  tendencia cuando hay demasiados puntos como para leerlos). */
export function coarsen(period: number, from: Grain, to: Grain): number {
  if (from === to) return period;
  if (from === "day" && to === "month") return Math.floor(period / 100);
  if (from === "day" && to === "year") return Math.floor(period / 10000);
  if (from === "month" && to === "year") return Math.floor(period / 100);
  return period;
}

export function coarserGrain(grain: Grain): Grain | null {
  if (grain === "day") return "month";
  if (grain === "month") return "year";
  return null;
}
