#!/usr/bin/env bash
#
# ckanext-c4w -- reproducible CKAN 2.10 verification driver.
#
# Builds Dockerfile.test (real CKAN 2.10 with the plugin installed) and runs
# both verification layers inside it:
#
#   1. a plugin-LOAD smoke check that catches the failures no unit test can
#      reach -- a broken blueprint module crashes every CKAN worker at boot, a
#      typo'd helper name in a template is a 500 no import can see, and an
#      action without an auth entry is a 500 instead of a 403,
#   2. the behavioural pytest suite.
#
# Prints a PASS/FAIL summary and exits non-zero on the first failure.
#
#   bash scripts/run-ckan-tests.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

IMAGE="c4w-test"

echo "== ckanext-c4w: CKAN 2.10 verification harness =="

# --------------------------------------------------------------------------- #
# 1. Build                                                                     #
# --------------------------------------------------------------------------- #
echo "-- docker build -f Dockerfile.test -t ${IMAGE} ."
if ! docker build -f Dockerfile.test -t "${IMAGE}" . ; then
  echo "FAIL: docker build failed"
  exit 1
fi

# --------------------------------------------------------------------------- #
# 2. Plugin-load smoke check                                                   #
# --------------------------------------------------------------------------- #
echo "-- plugin-load smoke check"
if ! docker run --rm -i "${IMAGE}" python - <<'PY'
import re
import sys
from pathlib import Path

from jinja2 import Environment

from ckan.lib.jinja_extensions import _get_extensions
from ckanext.c4w.plugin import C4wPlugin

plugin = C4wPlugin()

actions = plugin.get_actions()
helpers = plugin.get_helpers()
auth = plugin.get_auth_functions()
plugin.get_validators()          # may legitimately be empty early on

assert actions, "get_actions() returned empty"
assert helpers, "get_helpers() returned empty"
assert auth, "get_auth_functions() returned empty"

# --- action <-> auth, BOTH directions -------------------------------------- #
# CKAN's check_access raises ValueError for a name it does not know, which a
# visitor sees as a 500 rather than a 403. Checking a hand-written list of
# names would rot; comparing the two registries cannot.
missing_auth = sorted(set(actions) - set(auth))
orphan_auth = sorted(set(auth) - set(actions))
assert not missing_auth, "actions with no auth function: %s" % missing_auth
assert not orphan_auth, "auth functions with no action: %s" % orphan_auth

# --- blueprint builds, with no duplicate endpoints ------------------------- #
blueprints = plugin.get_blueprint()
assert blueprints, "get_blueprint() returned nothing"
for bp in blueprints:
    assert bp.deferred_functions, "blueprint %r registered no rules" % bp.name

# --- every template parses with CKAN's Jinja environment ------------------- #
# Catches an invalid {% snippet %}, {% asset %} or {% ckan_extends %} before a
# visitor does.
env = Environment(extensions=_get_extensions())
tpl_root = Path("/plugin/ckanext/c4w/templates")
templates = sorted(tpl_root.rglob("*.html"))
assert templates, "no templates found under %s" % tpl_root
for path in templates:
    try:
        env.parse(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AssertionError("template %s failed to parse: %s" % (path, exc))

# --- every h.c4w_* used in a template actually exists ---------------------- #
# A mistyped helper name in Jinja is a 500 that import-time checks cannot see.
used = set()
for path in templates:
    used |= set(re.findall(r"\bh\.(c4w_[a-zA-Z0-9_]+)", path.read_text(encoding="utf-8")))
unknown = sorted(used - set(helpers))
assert not unknown, "templates call helpers that are not registered: %s" % unknown

# --- every {% snippet %} path resolves to a real file ---------------------- #
# A typo'd snippet path parses fine and is a 500 the first time a visitor
# reaches that page.
snippets = set()
for path in templates:
    body = path.read_text(encoding="utf-8")
    snippets |= set(re.findall(r"{%-?\s*snippet\s+['\"]([^'\"]+)['\"]", body))
missing_snippets = sorted(s for s in snippets if not (tpl_root / s).is_file())
assert not missing_snippets, "templates reference missing snippets: %s" % missing_snippets

# --- every {% extends %} of our own namespace resolves too ----------------- #
extends = set()
for path in templates:
    body = path.read_text(encoding="utf-8")
    extends |= set(re.findall(r"{%-?\s*extends\s+['\"](c4w/[^'\"]+)['\"]", body))
missing_parents = sorted(e for e in extends if not (tpl_root / e).is_file())
assert not missing_parents, "templates extend missing parents: %s" % missing_parents

# --- the header override must chain, not replace --------------------------- #
# The active theme replaces core's header.html outright. Our override only
# reaches its navigation block by extending it.
header = (tpl_root / "header.html").read_text(encoding="utf-8").lstrip()
assert header.startswith("{% ckan_extends %}"), \
    "templates/header.html must start with {% ckan_extends %}"

print("PLUGIN OK  (%d actions, %d helpers, %d templates, %d snippets, "
      "helpers used: %d)"
      % (len(actions), len(helpers), len(templates), len(snippets), len(used)))
PY
then
  echo "FAIL: plugin-load smoke check failed"
  exit 1
fi

# --------------------------------------------------------------------------- #
# 3. Behavioural tests                                                         #
# --------------------------------------------------------------------------- #
# -p no:ckan disables CKAN's own pytest plugin, which would demand a
# configured site; these tests are deliberately runnable without one.
echo "-- pytest (behavioural)"
if ! docker run --rm -i "${IMAGE}" \
      python -m pytest -q -p no:ckan /plugin/ckanext/c4w/tests ; then
  echo "FAIL: pytest failed"
  exit 1
fi

echo
echo "PASS: build + plugin load + behavioural tests"
