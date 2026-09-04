export const en = {
  "boot.loading": "Loading the dataset…",
  "boot.loadingDetail": "Reading sites and parameters.",
  "boot.missingTitle": "This dashboard is not ready",
  "boot.missingDetail": "The data bundle could not be read. If the dataset was just uploaded, it may still be processing.",
  "boot.errorTitle": "Could not load the parameter",

  "mast.count": "{sites} sites · {records} records",
  "mast.source": "Source",

  "filter.aria": "Filters",
  "filter.parameter": "Parameter",
  "filter.country": "Country",
  "filter.period": "Period",
  "filter.from": "From",
  "filter.to": "To",
  "filter.all": "All",
  "filter.site": "Site",
  "filter.clearSite": "Clear site",
  "filter.loading": "Loading parameter…",

  "chart.trend": "Trend",
  "chart.trendYear": "Median per year",
  "chart.trendMonth": "Median per month",
  "chart.trendDay": "Median per day",
  "chart.distribution": "Distribution",
  "chart.countries": "Countries",
  "chart.empty": "No values match this filter.",
  "chart.emptyGroups": "Not enough groups with sites to compare.",
  "chart.records": "Records",
  "chart.tooltipPeriod": "{value} · {n} records",
  "chart.tooltipCount": "{n} records",
  "chart.tooltipGroup": "{value} · {sites} sites",
  "chart.clickMap": "Click to see it on the map",

  "view.chip": "{n} in this view",
  "empty.banner": "No measurements match these filters. Widen the period or clear a filter.",
  "empty.view": "No measurements in this view. Zoom out or change the filters.",

  "stat.sites": "sites",
  "stat.records": "site-periods",
  "stat.samples": "measurements",
  "stat.median": "median",
  "stat.max": "maximum",
  "stat.csv": "Download CSV",

  "scale.unreliable":
    "Almost every value for this parameter is the same (often the detection limit), so the colour scale barely separates them.",
  "scale.aria": "Colour scale",

  "popup.only": "Show only this site",
  "popup.periods": "{n} periods with data",

  "map.aria": "Site map",
  "map.attribution": "OpenFreeMap · OpenStreetMap contributors",

  "csv.site": "site_id",
  "csv.name": "site_name",
  "csv.country": "country",
  "csv.period": "period",
  "csv.lat": "lat",
  "csv.lon": "lon",
  "csv.value": "value",
  "csv.unit": "unit",
  "csv.samples": "samples",
} as const;

export type MessageKey = keyof typeof en;
