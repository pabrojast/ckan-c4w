import type { MessageKey } from "./en";

export const es: Record<MessageKey, string> = {
  "boot.loading": "Cargando el dataset…",
  "boot.loadingDetail": "Leyendo sitios y parámetros.",
  "boot.missingTitle": "Este dashboard no está listo",
  "boot.missingDetail": "No se pudo leer el paquete de datos. Si el dataset se acaba de subir, puede que aún se esté procesando.",
  "boot.errorTitle": "No se pudo cargar el parámetro",

  "mast.count": "{sites} sitios · {records} registros",
  "mast.source": "Fuente",

  "filter.aria": "Filtros",
  "filter.parameter": "Parámetro",
  "filter.country": "País",
  "filter.period": "Periodo",
  "filter.from": "Desde",
  "filter.to": "Hasta",
  "filter.all": "Todos",
  "filter.site": "Sitio",
  "filter.clearSite": "Quitar sitio",
  "filter.loading": "Cargando parámetro…",

  "chart.trend": "Tendencia",
  "chart.trendYear": "Mediana por año",
  "chart.trendMonth": "Mediana por mes",
  "chart.trendDay": "Mediana por día",
  "chart.distribution": "Distribución",
  "chart.countries": "Países",
  "chart.empty": "No hay valores con este filtro.",
  "chart.emptyGroups": "No hay grupos con suficientes sitios para comparar.",
  "chart.records": "Registros",
  "chart.tooltipPeriod": "{value} · {n} registros",
  "chart.tooltipCount": "{n} registros",
  "chart.tooltipGroup": "{value} · {sites} sitios",
  "chart.clickMap": "Clic para verlo en el mapa",

  "view.chip": "{n} en esta vista",
  "empty.banner": "Ninguna medición con estos filtros. Amplía el periodo o quita un filtro.",
  "empty.view": "No hay mediciones en esta vista. Aleja el mapa o cambia los filtros.",

  "stat.sites": "sitios",
  "stat.records": "periodos-sitio",
  "stat.samples": "mediciones",
  "stat.median": "mediana",
  "stat.max": "máximo",
  "stat.csv": "Descargar CSV",

  "scale.unreliable":
    "Casi todos los valores de este parámetro son iguales (a menudo el límite de detección): la escala de color apenas los distingue.",
  "scale.aria": "Escala de color",

  "popup.only": "Ver solo este sitio",
  "popup.periods": "{n} periodos con dato",

  "map.aria": "Mapa de sitios",
  "map.attribution": "OpenFreeMap · colaboradores de OpenStreetMap",

  "csv.site": "site_id",
  "csv.name": "site_name",
  "csv.country": "country",
  "csv.period": "period",
  "csv.lat": "lat",
  "csv.lon": "lon",
  "csv.value": "value",
  "csv.unit": "unit",
  "csv.samples": "samples",
};
