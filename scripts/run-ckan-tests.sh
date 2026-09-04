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

# --- the live legacy URLs still resolve ------------------------------------ #
# Every path below is served RIGHT NOW by the Django site at
# https://ihp-wins.unesco.org/citizens4water/ (verified against its urls.py).
# A visitor, a bookmark or a search result hitting one of these after the
# cutover must not get a 404. Trailing slashes are part of the contract:
# Django published /events/ and /platforms/ WITH one, and Flask's default
# strict_slashes would 404 exactly those.
from flask import Flask

probe = Flask(__name__)
for bp in blueprints:
    probe.register_blueprint(bp)

# (path, expected endpoint). Asserting the ENDPOINT and not merely that
# something matched is what catches a numeric path falling through to the slug
# rule and quietly serving a second, uncanonicalised address for the same row.
MUST_RESOLVE = [
    ("/citizens4water/", "c4w.index"),
    ("/citizens4water/projects", "c4w.project_list"),
    ("/citizens4water/project/32", "c4w.project_legacy"),
    ("/citizens4water/project/be-resilient", "c4w.project_detail"),
    ("/citizens4water/organisations", "c4w.organisation_list"),
    ("/citizens4water/organisation/6", "c4w.organisation_legacy"),
    ("/citizens4water/resources", "c4w.resource_list"),
    ("/citizens4water/resource/7", "c4w.resource_legacy"),
    ("/citizens4water/training_resources", "c4w.training_resource_list"),
    ("/citizens4water/platforms/", "c4w.platform_list"),
    ("/citizens4water/platforms", "c4w.platform_list"),
    ("/citizens4water/platform/8", "c4w.platform_legacy"),
    ("/citizens4water/events/", "c4w.event_list"),
    ("/citizens4water/events", "c4w.event_list"),
    ("/citizens4water/blog", "c4w.post_list"),
    # A numeric blog path must 301, not serve the post at a second URL.
    ("/citizens4water/blog/7", "c4w.post_legacy"),
    ("/citizens4water/blog/some-post", "c4w.post_detail"),
    ("/citizens4water/blog/2026/1/1/some-post", "c4w.post_legacy_dated"),
    ("/citizens4water/about", "c4w.about"),
    ("/citizens4water/account", "c4w.account"),
    ("/citizens4water/submit", "c4w.submit"),
    ("/citizens4water/admin", "c4w.admin_index"),
]
adapter = probe.url_map.bind("ihp-wins.unesco.org")
route_problems = []
for path, expected in MUST_RESOLVE:
    try:
        endpoint, _args = adapter.match(path)
    except Exception as exc:
        route_problems.append("%s -> %s" % (path, type(exc).__name__))
        continue
    if endpoint != expected:
        route_problems.append("%s -> %s (expected %s)" % (path, endpoint, expected))
assert not route_problems, "route map problems: %s" % route_problems

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

# --- no IHP-WINS masthead override ---------------------------------------- #
# A root-level header.html would inject a Citizens4Water tab into the
# UNESCO nav. C4W is a distinct portal; that tab is not wanted.
assert not (tpl_root / "header.html").exists(), \
    "templates/header.html must not exist (it would override the IHP-WINS nav)"

print("PLUGIN OK  (%d actions, %d helpers, %d templates, %d snippets, "
      "helpers used: %d, %d legacy URLs resolve)"
      % (len(actions), len(helpers), len(templates), len(snippets), len(used),
         len(MUST_RESOLVE)))
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
