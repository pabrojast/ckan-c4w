# encoding: utf-8
"""``ckan c4w …`` command group.

Nothing here is required for normal operation -- ``IConfigurable.configure``
already creates the tables on startup. ``init-db`` exists so an operator can
run the bootstrap explicitly and see it succeed or fail loudly instead of
reading the log for the generic error the startup path emits.

The import commands are one-way and touch the legacy database read-only.
"""
import json

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
        (u'datasets', db.C4wDataset),
        (u'dataset files', db.C4wDatasetFile),
        (u'bundle blobs', db.C4wDashboardBundle),
        (u'user profiles', db.C4wUserProfile),
    ]
    for label, model_cls in rows:
        click.echo(u'  %-16s %6d' % (label, Session.query(model_cls).count()))


@c4w.command(name='import')
@click.option('--dsn', required=True,
              help='libpq DSN of the legacy Django database (read-only).')
@click.option('--media-root', default=None,
              help='Directory holding the legacy MEDIA_ROOT.')
@click.option('--only', default=None,
              help='Comma-separated entities to import, in any order.')
@click.option('--since', default=None,
              help='Only rows whose dateUpdated is at or after this ISO '
                   'timestamp. Used for the delta run at cutover.')
@click.option('--fallback-user', default=None,
              help='CKAN username to own rows whose Django author cannot be '
                   'matched. The importer NEVER creates a CKAN user.')
@click.option('--dry-run', is_flag=True,
              help='Map and report, then roll back. Uploads nothing.')
@click.option('--json-report', 'json_report', default=None,
              help='Write the full report to this path as JSON.')
def import_command(dsn, media_root, only, since, fallback_user, dry_run,
                   json_report):
    """Import the legacy Citizens4Water data.

    Idempotent: rows are keyed by their Django id, and ids and slugs are
    assigned once, so a re-run updates in place and no URL moves.
    """
    from ckanext.c4w.migrate import runner

    entities = [e.strip() for e in (only or '').split(',') if e.strip()]
    engine = runner.Runner(
        dsn=dsn, media_root=media_root, only=entities or None,
        dry_run=dry_run, since=since, fallback_user=fallback_user)

    try:
        result = runner.summarise(engine.run())
    except runner.PreflightError as exc:
        # Not a stack trace: this is an operator-addressable condition and the
        # message says exactly what to install.
        raise click.ClickException(u'%s' % exc)

    _print_report(result, dry_run)
    if json_report:
        with open(json_report, 'w') as handle:
            json.dump(result, handle, indent=1, default=str)
        click.echo(u'  full report written to %s' % json_report)


def _print_report(result, dry_run):
    if dry_run:
        click.secho(u'DRY RUN -- nothing was written', fg=u'yellow')
    click.secho(u'\nImported', bold=True)
    for entity, counts in sorted(result.get('entities', {}).items()):
        click.echo(u'  %-14s %s' % (entity, counts))

    media = result.get('media') or {}
    if media:
        click.secho(u'\nMedia', bold=True)
        click.echo(u'  uploaded %s, reused %s, thumbnails mapped %s'
                   % (media.get('uploaded'), media.get('reused'),
                      media.get('thumbnails_mapped')))
        if media.get('missing'):
            click.secho(u'  %d referenced files were not found'
                        % len(media['missing']), fg=u'yellow')
        if media.get('failed'):
            click.secho(u'  %d files could not be uploaded'
                        % len(media['failed']), fg=u'yellow')

    outside = result.get('terms_outside_vocabulary') or {}
    if outside:
        # The single most important thing in this report: a term production
        # holds that constants.py does not declare is INVISIBLE on the site,
        # because a facet hides any option absent from its counts.
        click.secho(u'\nTerms outside the declared vocabulary', fg=u'red',
                    bold=True)
        for vocabulary, terms in sorted(outside.items()):
            click.echo(u'  %s: %s' % (vocabulary, ', '.join(terms)))
        click.echo(u'  -> add these to constants.py, or make the vocabulary '
                   u'free text. Do not discard the data.')

    unresolved = result.get('unresolved_users') or []
    if unresolved:
        click.secho(u'\nDjango accounts with no CKAN user', fg=u'yellow',
                    bold=True)
        for row in unresolved:
            click.echo(u'  %-6s %-24s %-34s %s'
                       % (row.get('django_id'), row.get('name'),
                          row.get('email'), row.get('reason')))
        click.echo(u'  -> those rows have no creator. The importer never '
                   u'creates CKAN accounts.')

    for key, colour in (('skipped', u'yellow'), ('notes', None)):
        items = result.get(key) or []
        if items:
            click.secho(u'\n%s' % key.title(), bold=True, fg=colour)
            for item in items:
                click.echo(u'  %s' % item)


@c4w.command()
@click.option('--threshold', default=0.85, show_default=True,
              help='Minimum score for a suggestion.')
@click.option('--apply', 'apply_', is_flag=True,
              help='Write the proposals. Review the table first.')
@click.option('--unlink', default=None,
              help='Clear the CKAN link on one c4w organisation, by slug.')
def link_organisations(threshold, apply_, unlink):
    """Suggest CKAN organizations for the C4W directory entries.

    Filling ckan_org_id wrongly is worse than leaving it empty: the
    organisation page offers a "Datasets on IHP-WINS" link, so a bad match
    sends readers to another institution's catalogue under this one's name.
    So the table is printed for a human FIRST, and --apply is a separate step.
    """
    import ckan.plugins.toolkit as tk
    from ckan.model.meta import Session
    from ckanext.c4w import db
    from ckanext.c4w.migrate import orgmatch

    db.ensure_mappers()

    if unlink:
        row = (Session.query(db.C4wOrganisation)
               .filter(db.C4wOrganisation.slug == unlink).first())
        if row is None:
            raise click.ClickException(u'no c4w organisation %r' % unlink)
        row.ckan_org_id = None
        Session.add(row)
        Session.commit()
        click.secho(u'unlinked %s' % unlink, fg=u'green')
        return

    c4w_orgs = [{'slug': o.slug, 'name': o.name, 'url': o.url,
                 'ckan_org_id': o.ckan_org_id, 'id': o.id}
                for o in Session.query(db.C4wOrganisation)]
    ckan_orgs = tk.get_action('organization_list')(
        {'ignore_auth': True}, {'all_fields': True, 'limit': 1000})

    proposals = orgmatch.propose(c4w_orgs, ckan_orgs, threshold=threshold)
    matched = [p for p in proposals if p.get('ckan_name')]

    click.secho(u'%-38s %-34s %-6s %s'
                % (u'C4W ORGANISATION', u'CKAN ORGANIZATION', u'SCORE',
                   u'WHY'), bold=True)
    for proposal in proposals:
        colour = u'green' if proposal.get('ckan_name') else None
        click.secho(u'%-38s %-34s %-6s %s'
                    % ((proposal['c4w_slug'] or u'')[:38],
                       (proposal.get('ckan_name') or u'-')[:34],
                       proposal.get('score'), proposal.get('reason')[:60]),
                    fg=colour)

    if not apply_:
        click.echo(u'\n%d of %d would be linked. Re-run with --apply once the '
                   u'table above has been reviewed.'
                   % (len(matched), len(proposals)))
        return

    by_slug = {o.slug: o for o in Session.query(db.C4wOrganisation)}
    for proposal in matched:
        row = by_slug.get(proposal['c4w_slug'])
        if row is not None:
            row.ckan_org_id = proposal.get('ckan_id')
            Session.add(row)
    Session.commit()
    click.secho(u'\nlinked %d organisations' % len(matched), fg=u'green')


# --------------------------------------------------------------------------- #
# Datasets
# --------------------------------------------------------------------------- #

def _dataset(reference):
    from ckanext.c4w import db
    from ckanext.c4w.logic.action import _common
    db.ensure_mappers()
    row = _common.get_by_reference(db.C4wDataset, reference)
    if row is None:
        raise click.ClickException(u'no dataset matches %r' % reference)
    return row


@c4w.command(name='process-dataset')
@click.argument('reference')
@click.option('--force', is_flag=True,
              help='Run even if the dataset is marked processing.')
def process_dataset(reference, force):
    """Run the pipeline for one dataset (slug or id), inline, now.

    The way to process a file too large for the web request, and the
    fallback on a deployment without a jobs worker.
    """
    from ckanext.c4w.data import jobs

    row = _dataset(reference)
    if row.processing_status == u'processing' and not force:
        raise click.ClickException(
            u'%s is already processing; use --force to override' % row.slug)

    def progress(done, total=None):
        click.echo(u'  %s rows read' % format(done, ','), err=True)

    click.echo(u'processing %s (%s)' % (row.slug, row.id))
    status = jobs.process_dataset(row.id, force=True, progress=progress)
    row = _dataset(row.id)
    colour = u'green' if status == u'ready' else u'red'
    click.secho(u'%s: %s' % (row.slug, status), fg=colour)
    if row.processing_error:
        click.echo(u'  ' + row.processing_error)
    if status != u'ready':
        raise SystemExit(1)


@c4w.command(name='process-pending')
@click.option('--retry-failed', is_flag=True,
              help='Also re-run datasets whose last run failed.')
def process_pending(retry_failed):
    """Process every queued dataset, one after another."""
    from ckan.model.meta import Session
    from ckanext.c4w import db
    from ckanext.c4w.data import jobs

    db.ensure_mappers()
    statuses = [u'queued']
    if retry_failed:
        statuses.append(u'failed')
    rows = (Session.query(db.C4wDataset)
            .filter(db.C4wDataset.processing_status.in_(statuses))
            .order_by(db.C4wDataset.submitted_at.asc().nullslast()).all())
    if not rows:
        click.echo(u'nothing to process')
        return
    failures = 0
    for row in rows:
        click.echo(u'processing %s' % row.slug)
        status = jobs.process_dataset(row.id, force=True)
        click.secho(u'  %s' % status,
                    fg=u'green' if status == u'ready' else u'red')
        if status != u'ready':
            failures += 1
    if failures:
        raise SystemExit(1)


@c4w.command(name='dataset-report')
@click.argument('reference')
@click.option('--dump-bundle', 'dump_dir', default=None,
              help='Write the current bundle files into this directory.')
def dataset_report(reference, dump_dir):
    """Print a dataset's state; optionally dump its bundle to disk."""
    import gzip
    import os
    from ckan.model.meta import Session
    from ckanext.c4w import db

    row = _dataset(reference)
    click.echo(u'%s  [%s]' % (row.title, row.slug))
    for key in ('processing_status', 'processing_error', 'bundle_generation',
                'record_count', 'site_count', 'parameter_count', 'grain',
                'temporal_start', 'temporal_end', 'approved', 'hidden',
                'submitted_at', 'processed_at'):
        click.echo(u'  %-18s %s' % (key, getattr(row, key)))
    files = (Session.query(db.C4wDatasetFile)
             .filter(db.C4wDatasetFile.dataset_id == row.id).all())
    for f in files:
        click.echo(u'  file %-10s %10s bytes  %s' % (
            f.kind, f.size_bytes, f.url))
    blobs = (Session.query(db.C4wDashboardBundle)
             .filter(db.C4wDashboardBundle.dataset_id == row.id,
                     db.C4wDashboardBundle.generation
                     == row.bundle_generation).all())
    for blob in blobs:
        click.echo(u'  bundle %-14s %8s raw  %8s gz' % (
            blob.name, blob.raw_size, blob.gz_size))
        if dump_dir:
            target = os.path.join(dump_dir, blob.name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'wb') as handle:
                handle.write(gzip.decompress(blob.body))
    if dump_dir and blobs:
        click.secho(u'bundle written to %s' % dump_dir, fg=u'green')
