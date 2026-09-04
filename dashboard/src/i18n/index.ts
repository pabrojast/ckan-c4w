import { en, type MessageKey } from "./en";
import { es } from "./es";
import { fr } from "./fr";

export type Locale = "en" | "es" | "fr";
export type { MessageKey };

const tables: Record<Locale, Record<MessageKey, string>> = { en, es, fr };

export type I18n = {
  locale: Locale;
  t: (key: MessageKey, vars?: Record<string, string | number>) => string;
  /** Nombre localizado de un país a partir de su ISO 3166-1 alpha-2; cualquier
   *  otro valor (el servidor no pudo resolverlo) se muestra tal cual. */
  countryName: (code: string | null) => string;
  /** Aplica la tabla a los elementos [data-i18n] / [data-i18n-aria] del root:
   *  el snippet Jinja los deja en inglés (o en lo que traduzca CKAN) y aquí se
   *  pisan con la tabla del idioma pedido, para que estáticos y dinámicos
   *  hablen el mismo idioma. */
  applyDom: (root: ParentNode) => void;
};

export function resolveLocale(lang: string | null | undefined): Locale {
  const short = (lang ?? "").toLowerCase().slice(0, 2);
  if (short === "es" || short === "fr") return short;
  return "en";
}

export function createI18n(lang: string | null | undefined): I18n {
  const locale = resolveLocale(lang);
  const table = tables[locale];

  let displayNames: Intl.DisplayNames | null = null;
  try {
    displayNames = new Intl.DisplayNames([locale], { type: "region" });
  } catch {
    displayNames = null;
  }

  function t(key: MessageKey, vars?: Record<string, string | number>): string {
    let text = table[key] ?? en[key];
    if (vars) {
      for (const [name, value] of Object.entries(vars)) {
        text = text.replaceAll(`{${name}}`, String(value));
      }
    }
    return text;
  }

  function countryName(code: string | null): string {
    if (!code) return "";
    if (displayNames && /^[A-Za-z]{2}$/.test(code)) {
      try {
        return displayNames.of(code.toUpperCase()) ?? code;
      } catch {
        return code;
      }
    }
    return code;
  }

  function applyDom(root: ParentNode): void {
    for (const el of root.querySelectorAll<HTMLElement>("[data-i18n]")) {
      const key = el.dataset.i18n as MessageKey | undefined;
      if (key && key in en) el.textContent = t(key);
    }
    for (const el of root.querySelectorAll<HTMLElement>("[data-i18n-aria]")) {
      const key = el.dataset.i18nAria as MessageKey | undefined;
      if (key && key in en) el.setAttribute("aria-label", t(key));
    }
  }

  return { locale, t, countryName, applyDom };
}
