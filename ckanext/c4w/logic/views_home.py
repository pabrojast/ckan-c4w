# encoding: utf-8
"""The Citizens4Water landing page."""
import logging

import ckan.plugins.toolkit as tk

log = logging.getLogger(__name__)


def _safe(action, data_dict):
    """Fail-soft: the front door must render with no rows and no 500."""
    try:
        return tk.get_action(action)({}, data_dict)
    except Exception:
        log.debug("ckanext-c4w: home %s unavailable", action, exc_info=True)
        return {}


def index():
    """Render the portal home.

    Nothing here may raise on missing data: this is the front door, and it has
    to render on a freshly installed instance with no rows at all.
    """
    stats = _safe('c4w_stats', {})
    featured = _safe('c4w_project_list', {
        'featured': u'true', 'page_size': 1, 'order': 'featured'})
    latest = _safe('c4w_project_list', {
        'page_size': 6, 'order': 'modified'})
    posts = _safe('c4w_post_list', {'page_size': 3, 'order': 'created'})
    events = _safe('c4w_event_list', {'page_size': 8, 'order': 'start'})

    featured_project = None
    for project in (featured.get('results') or []):
        featured_project = project
        break
    latest_projects = list(latest.get('results') or [])
    if featured_project is None and latest_projects:
        featured_project = latest_projects[0]
    if featured_project:
        featured_id = featured_project.get('id')
        latest_projects = [p for p in latest_projects
                           if p.get('id') != featured_id]

    return tk.render('c4w/home.html', extra_vars={
        'stats': stats,
        'featured_project': featured_project,
        'latest_projects': latest_projects,
        'latest_posts': posts.get('results') or [],
        'upcoming_events': (events.get('upcoming') or [])[:3],
    })


def about():
    """Short attribution page. The funding text is contractual."""
    return tk.render('c4w/about.html', extra_vars={})
