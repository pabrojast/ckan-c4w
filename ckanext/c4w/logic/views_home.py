# encoding: utf-8
"""The Citizens4Water landing page."""
import ckan.plugins.toolkit as tk


def index():
    """Render the portal home.

    Nothing here may raise on missing data: this is the front door, and it has
    to render on a freshly installed instance with no rows at all.
    """
    return tk.render('c4w/home.html', extra_vars={})
