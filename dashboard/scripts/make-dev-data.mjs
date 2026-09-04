// Genera un bundle sintético en public/dev-data/ para `npm run dev`.
// Mismo contrato que produce ckanext/c4w/data/bundle.py (ver README).
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, "../public/dev-data");
mkdirSync(resolve(out, "p"), { recursive: true });

// PRNG determinista para que el harness sea reproducible.
let seed = 20240904;
const rand = () => ((seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296);
const lognormal = (mu, sigma) => {
  const u = rand() || 1e-9;
  const v = rand();
  return Math.exp(mu + sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v));
};

const countries = [
  { id: "CL", lat: -33.45, lon: -70.66, spread: 4 },
  { id: "PE", lat: -12.05, lon: -77.04, spread: 3 },
  { id: "ES", lat: 40.42, lon: -3.7, spread: 3 },
];
const bodies = ["River", "Lake", "Stream"];
const landUse = ["Urban", "Agriculture", "Forest"];
const siteCount = 40;

const sites = { id: [], name: [], lat: [], lon: [], country: [], dims: { body: [], land_use: [] } };
for (let i = 0; i < siteCount; i++) {
  const c = countries[i % countries.length];
  sites.id.push(`${c.id}-${String(i + 1).padStart(3, "0")}`);
  sites.name.push(i % 4 === 0 ? null : `${bodies[i % 3]} site ${i + 1}`);
  sites.lat.push(+(c.lat + (rand() - 0.5) * c.spread).toFixed(4));
  sites.lon.push(+(c.lon + (rand() - 0.5) * c.spread).toFixed(4));
  sites.country.push(c.id);
  sites.dims.body.push(i % 3);
  sites.dims.land_use.push(Math.floor(rand() * 3));
}

const params = [
  { key: "nitrate", label: "Nitrate", unit: "mg/L", family: "Nutrients", mu: 0.3, sigma: 0.9 },
  { key: "phosphate", label: "Phosphate", unit: "mg/L", family: "Nutrients", mu: -2.2, sigma: 0.8 },
  { key: "turbidity", label: "Turbidity", unit: "NTU", family: "Physical", mu: 2.8, sigma: 0.7 },
  // Casi todo en el límite de detección: demuestra `reliableScale: false`.
  { key: "lead", label: "Lead, dissolved", unit: "µg/L", family: "Metals", mu: 0, sigma: 0, floor: 0.5 },
];

const months = [];
for (let y = 2020; y <= 2024; y++) for (let m = 1; m <= 12; m++) months.push(y * 100 + m);

function percentile(sorted, p) {
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))));
  return sorted[idx];
}

const meta = {
  schema: 1,
  dataset: {
    slug: "dev-sample",
    title: "Dev sample · river monitoring",
    credit: "Synthetic data · Citizens4Water dev harness",
    source: "https://example.org/dev-sample",
    license: "CC-BY-4.0",
    grain: "month",
    generatedAt: new Date().toISOString(),
  },
  records: 0,
  siteCount,
  minPeriod: months[0],
  maxPeriod: months[months.length - 1],
  parameters: [],
  dimensions: [
    { key: "body", label: "Water body", values: bodies.map((label, id) => ({ id, label, count: 0 })) },
    { key: "land_use", label: "Land use", values: landUse.map((label, id) => ({ id, label, count: 0 })) },
  ],
  countries: countries.map((c) => ({ id: c.id, count: 0 })),
};
for (const c of sites.country) meta.countries.find((x) => x.id === c).count++;
for (const v of sites.dims.body) meta.dimensions[0].values[v].count++;
for (const v of sites.dims.land_use) meta.dimensions[1].values[v].count++;

params.forEach((p, index) => {
  const series = { site: [], period: [], value: [], samples: [] };
  const seen = new Set();
  for (let s = 0; s < siteCount; s++) {
    const coverage = 0.35 + rand() * 0.5;
    for (const m of months) {
      if (rand() > coverage) continue;
      const trend = 1 + ((m % 100) - 6) * 0.03 * (s % 2 ? 1 : -1);
      let value = p.floor != null ? p.floor + (rand() < 0.05 ? rand() * 3 : 0) : lognormal(p.mu, p.sigma) * trend;
      value = +value.toFixed(4);
      series.site.push(s);
      series.period.push(m);
      series.value.push(value);
      series.samples.push(1 + Math.floor(rand() * 4));
      seen.add(s);
    }
  }
  const sorted = [...series.value].sort((a, b) => a - b);
  let breaks = [0.1, 0.25, 0.5, 0.75, 0.9, 0.97].map((q) => percentile(sorted, q));
  let reliable = breaks[0] < breaks[breaks.length - 1];
  if (!reliable) breaks = [];
  meta.parameters.push({
    key: p.key,
    label: p.label,
    unit: p.unit,
    family: p.family,
    records: series.value.length,
    sites: seen.size,
    minPeriod: Math.min(...series.period),
    maxPeriod: Math.max(...series.period),
    breaks,
    reliableScale: reliable,
  });
  meta.records += series.value.length;
  writeFileSync(resolve(out, `p/${index}.json`), JSON.stringify(series));
});

writeFileSync(resolve(out, "meta.json"), JSON.stringify(meta, null, 1));
writeFileSync(resolve(out, "sites.json"), JSON.stringify(sites));
writeFileSync(
  resolve(out, "stats.json"),
  JSON.stringify({ rejected: { bad_date: 3 }, dropped: [], perParameter: {}, warnings: [] }),
);
console.log(`dev bundle written to ${out}: ${meta.records} records, ${siteCount} sites, ${params.length} parameters`);
