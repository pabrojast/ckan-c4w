import * as maplibre from './vendor/maplibre-gl.mjs';

const messages = JSON.parse(document.getElementById('c4w-form-messages')?.textContent || '{}');
const t = key => messages[key] || key;

maplibre.setWorkerUrl(new URL('./vendor/maplibre-gl-worker.mjs', import.meta.url).href);
document.querySelectorAll('[data-project-map]').forEach(root => {
  const field = root.querySelector('textarea');
  const status = root.querySelector('[data-map-status]');
  const empty = () => ({type: 'MultiPolygon', coordinates: []});
  let geometry = empty(), points = [], drawing = false, markers = [];
  const normalize = g => {
    if (g.type === 'Feature') return normalize(g.geometry);
    if (g.type === 'FeatureCollection') return {type: 'MultiPolygon', coordinates: g.features.flatMap(f => normalize(f).coordinates)};
    if (g.type === 'Polygon') return {type: 'MultiPolygon', coordinates: [g.coordinates]};
    if (g.type === 'MultiPolygon') return g;
    throw new Error(t("Use a Polygon or MultiPolygon GeoJSON."));
  };
  try { if (field.value) geometry = normalize(JSON.parse(field.value)); } catch (_) { status.textContent = t("Check the GeoJSON coordinates below."); }
  let map;
  try {
    map = new maplibre.Map({container: root.querySelector('.c4w-project-map'), style: root.dataset.style,
      center: [0, 15], zoom: 1, attributionControl: true});
  } catch (_) { status.textContent = t("Map unavailable. You can still import or edit GeoJSON below."); return; }
  map.addControl(new maplibre.NavigationControl());
  function features() {
    const result = geometry.coordinates.length ? [{type: 'Feature', properties: {}, geometry}] : [];
    if (points.length >= 2) result.push({type: 'Feature', properties: {}, geometry: {type: 'LineString', coordinates: points}});
    return {type: 'FeatureCollection', features: result};
  }
  function refresh(save = true) {
    if (map.getSource('areas')) map.getSource('areas').setData(features());
    if (save) { field.value = geometry.coordinates.length ? JSON.stringify(geometry) : ''; field.dispatchEvent(new Event('input', {bubbles: true})); }
  }
  function fit() {
    const coords = geometry.coordinates.flat(2);
    if (!coords.length) return;
    const bounds = coords.reduce((b, p) => b.extend(p), new maplibre.LngLatBounds(coords[0], coords[0]));
    map.fitBounds(bounds, {padding: 35, maxZoom: 12, duration: 0});
  }
  function clearMarkers() { markers.forEach(m => m.remove()); markers = []; }
  map.on('load', () => {
    map.addSource('areas', {type: 'geojson', data: features()});
    map.addLayer({id: 'area-fill', type: 'fill', source: 'areas', filter: ['==', '$type', 'Polygon'], paint: {'fill-color': '#0077b8', 'fill-opacity': .2}});
    map.addLayer({id: 'area-line', type: 'line', source: 'areas', paint: {'line-color': '#0077b8', 'line-width': 3}});
    fit();
  });
  map.on('error', () => { status.textContent = t("Map tiles unavailable. Your saved area is retained; you can also use GeoJSON."); });
  root.querySelector('[data-draw]').addEventListener('click', () => {
    clearMarkers(); drawing = true; points = []; map.getCanvas().style.cursor = 'crosshair';
    status.textContent = t("Click at least three points, then select Finish area.");
  });
  map.on('click', event => {
    if (!drawing) return;
    points.push([event.lngLat.lng, event.lngLat.lat]);
    refresh(false); status.textContent = t('Points selected:') + ' ' + points.length;
  });
  root.querySelector('[data-finish]').addEventListener('click', () => {
    if (points.length < 3) { status.textContent = t("Choose at least three points."); return; }
    geometry.coordinates.push([[...points, points[0]]]); points = []; drawing = false;
    map.getCanvas().style.cursor = ''; refresh(); status.textContent = t("Area saved in this form. Save the step to keep it.");
  });
  root.querySelector('[data-undo]').addEventListener('click', () => { points.pop(); refresh(false); });
  root.querySelector('[data-clear]').addEventListener('click', () => {
    clearMarkers(); geometry = empty(); points = []; drawing = false; refresh(); status.textContent = t("Areas cleared.");
  });
  root.querySelector('[data-edit-areas]').addEventListener('click', () => {
    clearMarkers(); drawing = false; points = [];
    if (geometry.coordinates.flat(2).length > 500) { status.textContent = t("For detailed areas, edit or replace the GeoJSON below."); return; }
    geometry.coordinates.forEach(polygon => polygon.forEach(ring => ring.slice(0, -1).forEach((point, i) => {
      const marker = new maplibre.Marker({draggable: true}).setLngLat(point).addTo(map);
      marker.on('dragend', () => {
        const p = marker.getLngLat(); ring[i] = [p.lng, p.lat]; if (!i) ring[ring.length - 1] = ring[0]; refresh();
      }); markers.push(marker);
    })));
    status.textContent = t("Drag a point to adjust the boundary, then save the step.");
  });
  root.querySelector('[data-geojson-import]').addEventListener('change', async event => {
    const file = event.target.files[0]; if (!file) return;
    try {
      if (file.size > 500000) throw new Error(t("Use a GeoJSON file smaller than 500 KB."));
      const candidate = normalize(JSON.parse(await file.text()));
      if (!Array.isArray(candidate.coordinates)) throw new Error(t("Invalid coordinates."));
      geometry = candidate; clearMarkers(); points = []; refresh(); fit(); status.textContent = t("Area imported. Save this step to keep it.");
    } catch (error) { status.textContent = error.message; }
  });
  field.addEventListener('change', () => {
    try { geometry = field.value ? normalize(JSON.parse(field.value)) : empty(); clearMarkers(); refresh(false); fit(); }
    catch (_) { status.textContent = t("Check the GeoJSON format."); }
  });
});
