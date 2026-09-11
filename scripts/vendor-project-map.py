"""Copy the pinned dashboard map dependency for the server-rendered project form."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / 'dashboard/node_modules/maplibre-gl'
target = root / 'ckanext/c4w/public/c4w/vendor'
version = json.loads((source / 'package.json').read_text())['version']
expected = json.loads((root / 'dashboard/package.json').read_text())['dependencies']['maplibre-gl']
assert version == expected, 'Run npm ci in dashboard before vendoring.'
target.mkdir(parents=True, exist_ok=True)
for filename in ('maplibre-gl.mjs', 'maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs', 'maplibre-gl.css'):
    text = (source / 'dist' / filename).read_text()
    text = '\n'.join(line for line in text.splitlines() if not line.startswith('//# sourceMappingURL='))
    (target / filename).write_text(text)
(target / 'MAPLIBRE-LICENSE.txt').write_text((source / 'LICENSE.txt').read_text())
(target / 'README.txt').write_text('MapLibre GL JS ' + version + '\nRegenerate: npm ci --prefix dashboard && python3 scripts/vendor-project-map.py\n')
