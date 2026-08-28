# encoding: utf-8
"""``ckan c4w …`` command group.

Nothing here is required for normal operation: ``IConfigurable.configure``
already creates the tables on startup. ``init-db`` exists so an operator can
run the bootstrap explicitly and see it either succeed or fail loudly, rather
than reading the log for the generic error the startup path emits.
"""
import click


@click.group(short_help=u'Citizens4Water portal commands')
def c4w():
    """Management commands for ckanext-c4w."""


@c4w.command()
def init_db():
    """Create the c4w_* tables (idempotent)."""
    from ckanext.c4w import db
    db.ensure_tables()
    click.secho(u'ckanext-c4w: tables ready', fg=u'green')


@c4w.command()
def report():
    """Print a row count per table.

    Doubles as the acceptance check after an import: an operator reads this
    before deciding to cut over.
    """
    from ckan.model.meta import Session
    from ckanext.c4w import db

    db.ensure_mappers()
    rows = [
        (u'projects', db.C4wProject),
        (u'organisations', db.C4wOrganisation),
        (u'resources', db.C4wResource),
        (u'categories', db.C4wCategory),
        (u'platforms', db.C4wPlatform),
        (u'events', db.C4wEvent),
        (u'posts', db.C4wPost),
        (u'term links', db.C4wTermLink),
        (u'relations', db.C4wRelation),
        (u'media map', db.C4wMediaMap),
    ]
    for label, model_cls in rows:
        count = Session.query(model_cls).count()
        click.echo(u'  %-16s %6d' % (label, count))
