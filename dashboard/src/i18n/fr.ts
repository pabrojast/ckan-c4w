import type { MessageKey } from "./en";

export const fr: Record<MessageKey, string> = {
  "boot.loading": "Chargement du jeu de données…",
  "boot.loadingDetail": "Lecture des sites et des paramètres.",
  "boot.missingTitle": "Ce tableau de bord n'est pas prêt",
  "boot.missingDetail": "Le paquet de données n'a pas pu être lu. Si le jeu de données vient d'être envoyé, il est peut-être encore en traitement.",
  "boot.errorTitle": "Impossible de charger le paramètre",

  "mast.count": "{sites} sites · {records} enregistrements",
  "mast.source": "Source",

  "filter.aria": "Filtres",
  "filter.parameter": "Paramètre",
  "filter.country": "Pays",
  "filter.period": "Période",
  "filter.from": "Du",
  "filter.to": "Au",
  "filter.all": "Tous",
  "filter.site": "Site",
  "filter.clearSite": "Retirer le site",
  "filter.loading": "Chargement du paramètre…",

  "chart.trend": "Tendance",
  "chart.trendYear": "Médiane par année",
  "chart.trendMonth": "Médiane par mois",
  "chart.trendDay": "Médiane par jour",
  "chart.distribution": "Distribution",
  "chart.countries": "Pays",
  "chart.empty": "Aucune valeur ne correspond à ce filtre.",
  "chart.emptyGroups": "Pas assez de groupes avec des sites pour comparer.",
  "chart.records": "Enregistrements",
  "chart.tooltipPeriod": "{value} · {n} enregistrements",
  "chart.tooltipCount": "{n} enregistrements",
  "chart.tooltipGroup": "{value} · {sites} sites",
  "chart.clickMap": "Cliquer pour le voir sur la carte",

  "view.chip": "{n} dans cette vue",
  "empty.banner": "Aucune mesure ne correspond à ces filtres. Élargissez la période ou retirez un filtre.",
  "empty.view": "Aucune mesure dans cette vue. Dézoomez ou changez les filtres.",

  "stat.sites": "sites",
  "stat.records": "périodes-site",
  "stat.samples": "mesures",
  "stat.median": "médiane",
  "stat.max": "maximum",
  "stat.csv": "Télécharger le CSV",

  "scale.unreliable":
    "Presque toutes les valeurs de ce paramètre sont identiques (souvent la limite de détection) : l'échelle de couleur les distingue à peine.",
  "scale.aria": "Échelle de couleur",

  "popup.only": "Afficher uniquement ce site",
  "popup.periods": "{n} périodes avec des données",

  "map.aria": "Carte des sites",
  "map.attribution": "OpenFreeMap · contributeurs OpenStreetMap",

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
