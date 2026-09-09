"""Operator-only persistence for the pure reference dataset profiles."""
from ckanext.c4w.data.references import profile, VERSION


def load(source, directory, owner, dry_run=False, process=False, publish=False):
    """Idempotent operator import; never creates users or silently adopts a row."""
    prepared = profile(source, directory)
    report = {k: v for k, v in prepared.items() if k not in ('paths', 'data_paths')}
    report.update(dry_run=dry_run, uploaded=0, reused=0)
    if dry_run:
        return report

    from ckan import model
    from ckan.model.meta import Session
    from ckanext.c4w import db
    from ckanext.c4w.data import jobs
    from ckanext.c4w.logic import uploads
    from ckanext.c4w.text import load_extras, dump_extras
    from werkzeug.datastructures import FileStorage

    db.ensure_mappers()
    user = Session.query(model.User).filter_by(name=owner, state='active').first()
    if user is None or not user.sysadmin:
        raise ValueError('The reference owner must be an existing active CKAN sysadmin')
    slug = prepared['metadata']['slug']
    row = Session.query(db.C4wDataset).filter_by(slug=slug).first()
    if row is not None and load_extras(row.extras).get('reference_source') != source:
        raise ValueError('The reference slug belongs to another dataset: ' + slug)
    if row is not None and row.processing_status in ('queued', 'processing'):
        raise ValueError('The reference dataset is already queued or processing')
    if row is not None and load_extras(row.extras).get('reference_fingerprint') == prepared['fingerprint'] and row.processing_status == 'ready':
        if publish:
            row.approved = row.moderated = row.featured = True
            Session.commit()
        report.update(reused=len(prepared['files']), dataset_id=row.id,
                      slug=row.slug, status=row.processing_status,
                      unchanged=True, approved=row.approved)
        return report
    if row is None:
        row = db.C4wDataset(slug=slug, title=prepared['metadata']['title'],
                            created_by=user.id)
        Session.add(row)
        Session.flush()
    extras = load_extras(row.extras)
    for key, value in prepared['metadata'].items():
        if key in ('methodology', 'data_quality_note'):
            extras[key] = value
        else:
            setattr(row, key, value)
    extras.update(reference_source=source, reference_version=VERSION,
                  reference_generated_at=prepared['source_generated_at'])
    row.extras = dump_extras(extras)
    row.approved = row.moderated = False
    row.processing_status = 'uploaded'
    row.modified = db._utcnow()
    Session.commit()
    wanted = set()
    for order, (path, item) in enumerate(zip(prepared['paths'], prepared['files'])):
        file_row = Session.query(db.C4wDatasetFile).filter_by(
            dataset_id=row.id, kind=item['kind'], sha256=item['sha256']).first()
        if file_row is not None:
            file_row.sort_order = order
            report['reused'] += 1
        else:
            store_file = (uploads.store_data_file if item['kind'] == 'data'
                          else uploads.store_attachment)
            with open(path, 'rb') as stream:
                stored = store_file(FileStorage(
                    stream=stream, filename=item['name']), row.id)
            cleanup = stored.pop('cleanup')
            sniffed = stored.pop('sniff', {})
            try:
                file_row = db.C4wDatasetFile(dataset_id=row.id, uploaded_by=user.id,
                    sort_order=order, sniff_json=dump_extras(sniffed), **stored)
                Session.add(file_row)
                Session.commit()
            except Exception:
                Session.rollback()
                cleanup()
                raise
            report['uploaded'] += 1
        wanted.add(file_row.id)
    # Detach superseded source files; preserve blobs for recovery and auditing.
    Session.query(db.C4wDatasetFile).filter(
        db.C4wDatasetFile.dataset_id == row.id,
        ~db.C4wDatasetFile.id.in_(wanted)
    ).delete(synchronize_session=False)
    row.mapping_json = dump_extras(prepared['mapping'])
    row.layout = prepared['mapping']['layout']
    row.grain = prepared['mapping']['date']['grain']
    row.processing_status = 'mapped'
    row.wizard_step = 5
    row.submitted_at = db._utcnow()
    row.search_text = ' '.join(str(prepared['metadata'][k]) for k in (
        'title', 'description', 'publisher'))
    extras = load_extras(row.extras)
    extras.update(reference_fingerprint=prepared['fingerprint'],
                  reference_files=prepared['files'],
                  reference_counts=prepared['reference_counts'])
    row.extras = dump_extras(extras)
    Session.commit()
    if process:
        status = jobs.process_dataset(row.id)
        if status != 'ready':
            raise ValueError('Reference processing failed; see the dataset processing report')
        if publish:
            row.approved = row.moderated = True
            row.featured = True
            Session.commit()
    report.update(dataset_id=row.id, slug=row.slug, status=row.processing_status,
                  approved=row.approved)
    return report
