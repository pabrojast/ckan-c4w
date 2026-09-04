# encoding: utf-8
"""Dataset actions: the public reads and the wizard's writes.

Reads follow the other entities (``_common`` factories, visibility on the
data, NotFound over NotAuthorized). Writes are the first on this portal, so
their conventions are spelled out here:

* Every write authenticates through a trivial "is signed in" auth function
  and decides OWNERSHIP in the action, with ``_common.can_edit``. A row the
  requester may not edit raises NotFound -- a 403 would confirm the slug
  exists, which the public listing deliberately hides for a draft.
* Validation is navl over the schemas in ``logic/schema.py``; the action
  raises ``ValidationError`` and the view re-renders the step with the
  errors. Files and the column mapping are validated by ``logic/uploads``
  and ``data/mapping`` respectively, not by navl.
* ``search_text`` and the vocabulary links are rewritten on every save, the
  way the importer does it, so a dataset is searchable the moment it is
  approved.
* A change to the FILES or the MAPPING of an approved dataset sends it back
  to the reviewers (``approved=False``); a metadata edit does not.
"""
import datetime
import json
import logging

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants, db
from ckanext.c4w.logic import query as q
from ckanext.c4w.logic.action import _common
from ckanext.c4w.text import html_to_text, dump_extras, load_extras

log = logging.getLogger(__name__)

MAX_DATA_FILES = 40
MAX_ATTACHMENTS = 5

ENTITY = 'dataset'


# --------------------------------------------------------------------------- #
# Listing spec and reads
# --------------------------------------------------------------------------- #

def _spec():
    db.ensure_mappers()
    return q.ListingSpec(
        entity_type=ENTITY,
        model_cls=db.C4wDataset,
        search_columns=('title', 'description', 'provenance'),
        native_filters={
            'grain': 'grain',
            'frequency': 'frequency',
        },
        bool_filters={'featured': 'featured'},
        term_facets=(
            'country',
            'topic',
            'water_type',
            'water_data_type',
            'technology_used',
        ),
        orderings={
            'modified': lambda m: [m.modified.desc().nullslast(),
                                   m.title.asc()],
            'created': lambda m: [m.created.desc().nullslast(),
                                  m.title.asc()],
            'accesses': lambda m: [m.total_accesses.desc(), m.title.asc()],
            'title': lambda m: [m.title.asc()],
            'featured': lambda m: [m.featured.desc(),
                                   m.modified.desc().nullslast()],
        },
        default_order='modified',
        page_size=constants.PAGE_SIZE,
        defer_columns=('mapping_json',),
    )


def _get(reference):
    db.ensure_mappers()
    return _common.get_by_reference(db.C4wDataset, reference)


def _files_of(dataset_id, include_private=False):
    """The dataset's files, data first, as plain dicts."""
    from ckan.model.meta import Session

    rows = (Session.query(db.C4wDatasetFile)
            .filter(db.C4wDatasetFile.dataset_id == dataset_id)
            .order_by(db.C4wDatasetFile.kind.asc(),
                      db.C4wDatasetFile.sort_order.asc(),
                      db.C4wDatasetFile.created.asc())
            .all())
    out = []
    for row in rows:
        item = {
            'id': row.id,
            'kind': row.kind,
            'original_name': row.original_name,
            'url': row.url,
            'content_type': row.content_type,
            'size_bytes': row.size_bytes,
            'sha256': row.sha256,
            'format': row.format,
            'row_estimate': row.row_estimate,
            'created': row.created.isoformat() if row.created else None,
        }
        if include_private:
            item.update({
                'stored_name': row.stored_name,
                'encoding': row.encoding,
                'delimiter': row.delimiter,
                'quotechar': row.quotechar,
                'has_header': row.has_header,
                'sniff': load_extras(row.sniff_json),
            })
        out.append(item)
    return out


def _dictize(row, context, detail=True):
    """A dataset with its files and neighbours.

    Owner-only fields (the mapping, the processing error, the sniffed
    samples) travel only when the requester may edit the row.
    """
    private = _common.can_edit(ENTITY, row, context)
    out = db.entity_dictize(ENTITY, row,
                            include_contact=_common.is_authenticated(context),
                            include_private=private)
    out['can_edit'] = private
    out['mapping'] = load_extras(row.mapping_json) if private else None
    out['bundle_ready'] = bool(row.processing_status == 'ready'
                               and (row.bundle_generation or 0) > 0)
    out['processing_summary'] = (load_extras(row.extras)
                                 .get('processing_summary') or {})
    if not detail:
        return out
    out['files'] = _files_of(row.id, include_private=private)
    out['data_files'] = [f for f in out['files'] if f['kind'] == 'data']
    out['attachments'] = [f for f in out['files']
                          if f['kind'] == 'attachment']
    out['organisation'] = _neighbour('organisation', db.C4wOrganisation,
                                     row.organisation_id)
    out['project'] = _neighbour('project', db.C4wProject, row.project_id)
    return out


def _neighbour(entity_type, model_cls, reference):
    if not reference:
        return None
    from ckan.model.meta import Session
    target = Session.query(model_cls).filter(model_cls.id == reference).first()
    if target is None:
        return None
    if not _common.public_only(entity_type, [target]):
        return None
    return db.entity_dictize(entity_type, target)


@tk.side_effect_free
def c4w_dataset_show(context, data_dict):
    tk.check_access('c4w_dataset_show', context, data_dict)
    row = _get(data_dict.get('id') or data_dict.get('slug'))
    if row is None or not _common.is_visible(ENTITY, row, context):
        raise tk.ObjectNotFound(tk._('Dataset not found'))
    return _dictize(row, context)


c4w_dataset_list = _common.make_list(_spec, 'c4w_dataset_list')
c4w_dataset_facets = _common.make_facets(
    _spec, 'c4w_dataset_facets', orderings=constants.DATASET_ORDERINGS)


def c4w_dataset_record_view(context, data_dict):
    """Increment the view counter; the same visibility rule as show."""
    tk.check_access('c4w_dataset_record_view', context, data_dict)
    from ckan.model.meta import Session

    row = _get(data_dict.get('id'))
    if row is None or not _common.is_visible(ENTITY, row, context):
        raise tk.ObjectNotFound(tk._('Dataset not found'))
    row.total_accesses = (row.total_accesses or 0) + 1
    Session.add(row)
    Session.commit()
    return {'total_accesses': row.total_accesses}


@tk.side_effect_free
def c4w_dataset_bundle_show(context, data_dict):
    """One file of the dashboard bundle, gzip bytes and headers.

    Not for the JSON API: the body is bytes. The web route decompresses for
    a client that does not accept gzip.
    """
    tk.check_access('c4w_dataset_bundle_show', context, data_dict)
    if context.get('api_version'):
        raise tk.ValidationError(
            {'name': [tk._('The bundle is served by the portal, not the API')]})
    row = _get(data_dict.get('id') or data_dict.get('slug'))
    if row is None or not _common.is_visible(ENTITY, row, context):
        raise tk.ObjectNotFound(tk._('Dataset not found'))
    name = u'%s' % (data_dict.get('name') or u'')
    if not row.bundle_generation:
        raise tk.ObjectNotFound(tk._('Not found'))
    from ckan.model.meta import Session
    blob = (Session.query(db.C4wDashboardBundle)
            .filter(db.C4wDashboardBundle.dataset_id == row.id,
                    db.C4wDashboardBundle.generation == row.bundle_generation,
                    db.C4wDashboardBundle.name == name)
            .first())
    if blob is None:
        raise tk.ObjectNotFound(tk._('Not found'))
    return {
        'name': blob.name,
        'etag': blob.etag,
        'content_type': blob.content_type or 'application/json',
        'raw_size': blob.raw_size,
        'gz_size': blob.gz_size,
        'body': blob.body,
        'public': bool(row.approved and not row.hidden),
    }


# --------------------------------------------------------------------------- #
# Write helpers
# --------------------------------------------------------------------------- #

def _require_editable(context, reference):
    """The row, or NotFound when it does not exist or is not ours."""
    row = _get(reference)
    if row is None or not _common.can_edit(ENTITY, row, context):
        raise tk.ObjectNotFound(tk._('Dataset not found'))
    return row


def _validate(data, schema, context):
    from ckanext.c4w.logic import schema as schemas
    validated, errors = schemas.validate(data, schema, context)
    if errors:
        raise tk.ValidationError(errors)
    return validated


def _write_terms(row, wanted):
    """Rewrite the row's term links per vocabulary; returns the labels."""
    from ckan.model.meta import Session

    labels = []
    for vocabulary, pairs in wanted.items():
        (Session.query(db.C4wTermLink)
         .filter(db.C4wTermLink.entity_type == ENTITY,
                 db.C4wTermLink.entity_id == row.id,
                 db.C4wTermLink.vocabulary == vocabulary)
         .delete(synchronize_session=False))
        seen = set()
        for order, (term, label) in enumerate(pairs):
            if not term or term in seen:
                continue
            seen.add(term)
            Session.add(db.C4wTermLink(
                entity_type=ENTITY, entity_id=row.id, vocabulary=vocabulary,
                term=term, label=label, sort_order=order))
            labels.append(label)
    return labels


def _closed_pairs(vocabulary, terms):
    return [(t, constants.label_for(vocabulary, t)) for t in (terms or [])]


def _write_search_text(row, extra_labels=()):
    parts = []
    for value in (row.title, row.description, row.provenance, row.author,
                  row.publisher, row.citation, row.doi):
        if value:
            parts.append(u'%s' % value)
    extras = load_extras(row.extras)
    for key in ('methodology', 'data_quality_note', 'attribution_text'):
        if extras.get(key):
            parts.append(u'%s' % extras[key])
    mapping = load_extras(row.mapping_json)
    for param in (mapping.get('parameters') or []):
        if isinstance(param, dict):
            for key in ('label', 'source', 'unit', 'family'):
                if param.get(key):
                    parts.append(u'%s' % param[key])
    parts.extend(l for l in extra_labels if l)
    haystack = u' '.join(html_to_text(p) or u'' for p in parts)
    row.search_text = u' '.join(haystack.split())[:200000] or None


def _current_labels(row):
    """Labels of every term the row already carries (for the haystack)."""
    labels = db.term_labels_for(ENTITY, row.id).get(row.id, {})
    out = []
    for vocabulary, items in labels.items():
        for item in items:
            out.append(item.get('label') or item.get('term'))
    return out


def _touch(row):
    row.modified = db._utcnow()


def _reset_approval(row):
    """A changed file or mapping goes back to the reviewers."""
    if row.approved or row.moderated:
        row.approved = False
        row.moderated = False


def _set_extras(row, updates):
    extras = load_extras(row.extras)
    for key, value in updates.items():
        if value in (None, u'', [], {}):
            extras.pop(key, None)
        else:
            extras[key] = value
    row.extras = dump_extras(extras)


def _user_id(context):
    user = context.get('auth_user_obj')
    return getattr(user, 'id', None)


def _primary_data_file(dataset_id):
    from ckan.model.meta import Session
    return (Session.query(db.C4wDatasetFile)
            .filter(db.C4wDatasetFile.dataset_id == dataset_id,
                    db.C4wDatasetFile.kind == u'data')
            .order_by(db.C4wDatasetFile.sort_order.asc(),
                      db.C4wDatasetFile.created.asc())
            .first())


# --------------------------------------------------------------------------- #
# Create / update
# --------------------------------------------------------------------------- #

def _apply_step1(row, data):
    row.title = data['title']
    row.description = data['description']
    row.language = data.get('language') or u'en'
    row.project_id = data.get('project_id') or None
    row.organisation_id = data.get('organisation_id') or None
    return {
        'keyword': list(data.get('keywords') or []),
        'topic': _closed_pairs('topic', data.get('topic')),
        'water_type': _closed_pairs('water_type', data.get('water_type')),
        'water_data_type': _closed_pairs('water_data_type',
                                         data.get('water_data_type')),
    }


def _apply_step4(row, data):
    row.license_id = data['license_id']
    row.frequency = data.get('frequency') or None
    row.temporal_start = data.get('temporal_start') or None
    row.temporal_end = data.get('temporal_end') or None
    row.source_url = data.get('source_url') or None
    row.doi = data.get('doi') or None
    row.citation = data.get('citation') or None
    row.provenance = data['provenance']
    _set_extras(row, {
        'geographic_extent': data.get('geographic_extent'),
        'methodology': data.get('methodology'),
        'data_quality_note': data.get('data_quality_note'),
        'related_urls': data.get('related_urls'),
    })
    return {
        'country': [(c, c) for c in (data.get('country') or [])],
        'technology_used': _closed_pairs('technology_used',
                                         data.get('technology_used')),
    }


def _apply_step5(row, data):
    row.contact_name = data['contact_name']
    row.contact_email = data['contact_email']
    row.author = data.get('author') or None
    row.author_email = data.get('author_email') or None
    row.publisher = data.get('publisher') or None
    _set_extras(row, {
        'contact_url': data.get('contact_url'),
        'attribution_text': data.get('attribution_text'),
        'terms_accepted_at': db._utcnow().isoformat(),
    })
    return {}


_STEP_APPLY = {1: _apply_step1, 4: _apply_step4, 5: _apply_step5}


def c4w_dataset_create(context, data_dict):
    """Create a draft from step 1 of the wizard."""
    tk.check_access('c4w_dataset_create', context, data_dict)
    from ckan.model.meta import Session
    from ckanext.c4w.logic import schema as schemas

    data = _validate(data_dict, schemas.dataset_step_schema(1), context)
    db.ensure_mappers()
    row = db.C4wDataset(
        slug=db.unique_slug(db.C4wDataset, data['title']),
        title=data['title'],
        processing_status=u'draft',
        wizard_step=2,
        created_by=_user_id(context),
    )
    wanted = _apply_step1(row, data)
    Session.add(row)
    Session.flush()
    labels = _write_terms(row, wanted)
    _write_search_text(row, labels)
    Session.commit()
    return _dictize(row, context)


def c4w_dataset_update(context, data_dict):
    """Save one navl step (1, 4 or 5) of an existing draft or dataset."""
    tk.check_access('c4w_dataset_update', context, data_dict)
    from ckan.model.meta import Session
    from ckanext.c4w.logic import schema as schemas

    row = _require_editable(context, data_dict.get('id'))
    try:
        step = int(data_dict.get('step') or 0)
    except (TypeError, ValueError):
        step = 0
    if step not in _STEP_APPLY:
        raise tk.ValidationError({'step': [tk._('Unknown step')]})
    data = _validate(data_dict, schemas.dataset_step_schema(step), context)
    wanted = _STEP_APPLY[step](row, data)
    labels = _write_terms(row, wanted)
    # Labels of the vocabularies this step did not touch still belong in
    # the haystack.
    keep = [l for l in _current_labels(row)]
    _write_search_text(row, labels + keep)
    row.wizard_step = max(row.wizard_step or 1, step + 1)
    _touch(row)
    Session.add(row)
    Session.commit()
    return _dictize(row, context)


def c4w_dataset_delete(context, data_dict):
    """Delete a dataset with its files, links and bundle.

    The owner may delete their own unapproved dataset; a sysadmin any.
    """
    tk.check_access('c4w_dataset_delete', context, data_dict)
    from ckan.model.meta import Session
    from ckanext.c4w.logic import uploads

    row = _require_editable(context, data_dict.get('id'))
    if row.approved and not _common.is_sysadmin(context):
        raise tk.NotAuthorized(
            tk._('A published dataset can only be removed by a reviewer'))
    files = (Session.query(db.C4wDatasetFile)
             .filter(db.C4wDatasetFile.dataset_id == row.id).all())
    stored = [f.stored_name for f in files if f.stored_name]
    for query in (
            Session.query(db.C4wDatasetFile)
            .filter(db.C4wDatasetFile.dataset_id == row.id),
            Session.query(db.C4wDashboardBundle)
            .filter(db.C4wDashboardBundle.dataset_id == row.id),
            Session.query(db.C4wTermLink)
            .filter(db.C4wTermLink.entity_type == ENTITY,
                    db.C4wTermLink.entity_id == row.id),
            Session.query(db.C4wRelation)
            .filter(db.C4wRelation.subject_type == ENTITY,
                    db.C4wRelation.subject_id == row.id)):
        query.delete(synchronize_session=False)
    Session.delete(row)
    Session.commit()
    for name in stored:
        uploads.delete_stored(name)
    return {'id': row.id, 'deleted': True}


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #

def c4w_dataset_file_upload(context, data_dict):
    """Store one uploaded file (``kind`` data|attachment) for a dataset."""
    tk.check_access('c4w_dataset_file_upload', context, data_dict)
    from ckan.model.meta import Session
    from ckanext.c4w.logic import uploads

    row = _require_editable(context, data_dict.get('id'))
    upload = data_dict.get('upload')
    if upload is None or not getattr(upload, 'filename', None):
        raise tk.ValidationError({'upload': [tk._('Choose a file')]})
    kind = data_dict.get('kind') or u'data'
    if kind not in (u'data', u'attachment'):
        raise tk.ValidationError({'kind': [tk._('Unknown file kind')]})

    existing = _files_of(row.id, include_private=True)
    same_kind = [f for f in existing if f['kind'] == kind]
    limit = MAX_DATA_FILES if kind == u'data' else MAX_ATTACHMENTS
    if len(same_kind) >= limit:
        raise tk.ValidationError({'upload': [
            tk._('At most %d files of this kind') % limit]})

    try:
        if kind == u'data':
            stored = uploads.store_data_file(upload, row.id)
        else:
            stored = uploads.store_attachment(upload, row.id)
    except uploads.UploadError as exc:
        raise tk.ValidationError({'upload': [u'%s' % exc]})

    if kind == u'data' and same_kind:
        # Every data file of a dataset shares one mapping, so it must share
        # the header of the first one.
        first = (same_kind[0].get('sniff') or {}).get('columns') or []
        mine = (stored.get('sniff') or {}).get('columns') or []
        if [c.get('name') for c in first] != [c.get('name') for c in mine]:
            stored['cleanup']()
            raise tk.ValidationError({'upload': [tk._(
                'The columns of this file differ from the first file. '
                'Every file of one dataset must have the same header.')]})
    if any(f['sha256'] == stored['sha256'] for f in same_kind):
        stored['cleanup']()
        raise tk.ValidationError({'upload': [
            tk._('This file was already uploaded')]})

    cleanup = stored.pop('cleanup')
    sniffed = stored.pop('sniff', None)
    try:
        file_row = db.C4wDatasetFile(
            dataset_id=row.id,
            uploaded_by=_user_id(context),
            sort_order=len(same_kind),
            sniff_json=dump_extras(sniffed) if sniffed else None,
            **stored)
        Session.add(file_row)
        if kind == u'data':
            row.processing_status = u'uploaded'
            row.wizard_step = max(row.wizard_step or 1, 3)
            _reset_approval(row)
            if not same_kind:
                # The first file decides the proposal; a stale mapping from
                # a deleted file would name columns that no longer exist.
                row.mapping_json = None
        _touch(row)
        Session.add(row)
        Session.commit()
    except Exception:
        Session.rollback()
        cleanup()
        raise
    return {'file': _file_dict(file_row, include_private=True),
            'dataset': _dictize(row, context)}


def _file_dict(file_row, include_private=False):
    for item in _files_of(file_row.dataset_id, include_private):
        if item['id'] == file_row.id:
            return item
    return None


def c4w_dataset_file_delete(context, data_dict):
    tk.check_access('c4w_dataset_file_delete', context, data_dict)
    from ckan.model.meta import Session
    from ckanext.c4w.logic import uploads

    row = _require_editable(context, data_dict.get('id'))
    file_row = (Session.query(db.C4wDatasetFile)
                .filter(db.C4wDatasetFile.id == data_dict.get('file_id'),
                        db.C4wDatasetFile.dataset_id == row.id).first())
    if file_row is None:
        raise tk.ObjectNotFound(tk._('File not found'))
    stored_name = file_row.stored_name
    kind = file_row.kind
    Session.delete(file_row)
    if kind == u'data':
        remaining = [f for f in _files_of(row.id) if f['kind'] == u'data'
                     and f['id'] != file_row.id]
        _reset_approval(row)
        if not remaining:
            row.processing_status = u'draft'
            row.mapping_json = None
            row.wizard_step = min(row.wizard_step or 2, 2)
        elif row.processing_status == u'ready':
            row.processing_status = u'mapped'
    _touch(row)
    Session.add(row)
    Session.commit()
    uploads.delete_stored(stored_name)
    return {'deleted': True, 'dataset': _dictize(row, context)}


# --------------------------------------------------------------------------- #
# Column mapping
# --------------------------------------------------------------------------- #

@tk.side_effect_free
def c4w_dataset_mapping_propose(context, data_dict):
    """The sniff of the first data file and a proposed (or stored) mapping."""
    tk.check_access('c4w_dataset_mapping_propose', context, data_dict)
    from ckanext.c4w.data import mapping as mapper

    row = _require_editable(context, data_dict.get('id'))
    primary = _primary_data_file(row.id)
    if primary is None:
        raise tk.ValidationError({'upload': [tk._('Upload a data file first')]})
    sniffed = load_extras(primary.sniff_json)
    stored = load_extras(row.mapping_json)
    if stored:
        spec = mapper.normalise(stored)
    else:
        spec = sniffed.get('proposal') or mapper.propose(sniffed)
    return {
        'file': _file_dict(primary, include_private=True),
        'sniff': sniffed,
        'mapping': spec,
        'stored': bool(stored),
    }


def c4w_dataset_mapping_update(context, data_dict):
    """Validate and store the column mapping (wizard step 3)."""
    tk.check_access('c4w_dataset_mapping_update', context, data_dict)
    from ckan.model.meta import Session
    from ckanext.c4w.data import mapping as mapper
    from ckanext.c4w.logic import schema as schemas

    row = _require_editable(context, data_dict.get('id'))
    primary = _primary_data_file(row.id)
    if primary is None:
        raise tk.ValidationError({'upload': [tk._('Upload a data file first')]})
    spec = data_dict.get('mapping')
    if not isinstance(spec, dict):
        raise tk.ValidationError({'mapping': [tk._('No mapping given')]})
    sniffed = load_extras(primary.sniff_json)
    spec = mapper.normalise(spec)
    errors = mapper.validate_mapping(spec, sniffed)
    fields = _validate({
        'layout': spec.get('layout'),
        'grain': (spec.get('date') or {}).get('grain'),
        'unit_note': data_dict.get('unit_note') or u'',
    }, schemas.dataset_step_schema(3), context)
    if errors:
        raise tk.ValidationError(errors)

    new_json = json.dumps(spec, sort_keys=True)
    changed = new_json != (row.mapping_json or u'')
    row.mapping_json = new_json
    row.layout = fields['layout']
    row.grain = fields['grain']
    _set_extras(row, {'unit_note': fields.get('unit_note')})
    if changed:
        row.processing_status = u'mapped'
        _reset_approval(row)
    row.wizard_step = max(row.wizard_step or 1, 4)
    _write_search_text(row, _current_labels(row))
    _touch(row)
    Session.add(row)
    Session.commit()
    return _dictize(row, context)


# --------------------------------------------------------------------------- #
# Submit and process
# --------------------------------------------------------------------------- #

def _complete_errors(row, context):
    """Everything still missing before a dataset may be submitted."""
    from ckanext.c4w.logic import schema as schemas

    current = db.entity_dictize(ENTITY, row, include_contact=True,
                                include_private=True)
    terms = current.get('terms') or {}
    data = dict(current)
    data['keywords'] = [t for t in terms.get('keyword', [])]
    for vocabulary in ('topic', 'water_type', 'water_data_type',
                       'technology_used', 'country'):
        data[vocabulary] = list(terms.get(vocabulary, []))
    data['terms_accepted'] = bool(current.get('terms_accepted_at'))
    data['licence_confirm'] = bool(current.get('terms_accepted_at'))
    data['layout'] = row.layout
    data['grain'] = row.grain
    data['related_urls'] = current.get('related_urls') or []
    schema = schemas.dataset_full_schema()
    # project/organisation ids are already validated and may point at rows
    # not visible to the requester (a reviewer editing); do not re-check.
    schema.pop('project_id', None)
    schema.pop('organisation_id', None)
    _, errors = schemas.validate(data, schema, context)
    errors = dict(errors or {})
    if _primary_data_file(row.id) is None:
        errors['data_file'] = [tk._('Upload a data file')]
    if not row.mapping_json:
        errors['mapping_json'] = [tk._('Map the columns')]
    return errors


def c4w_dataset_submit(context, data_dict):
    """Send a complete draft to the reviewers and start processing."""
    tk.check_access('c4w_dataset_submit', context, data_dict)
    from ckan.model.meta import Session
    from ckanext.c4w.data import jobs
    from ckanext.c4w.logic import mail

    row = _require_editable(context, data_dict.get('id'))
    errors = _complete_errors(row, context)
    if errors:
        raise tk.ValidationError(errors)
    first_time = row.submitted_at is None
    row.submitted_at = db._utcnow()
    row.wizard_step = 6
    _touch(row)
    Session.add(row)
    Session.commit()
    status = row.processing_status
    if status in (u'uploaded', u'mapped', u'failed', u'draft'):
        status = jobs.dispatch(row.id)
    if first_time:
        try:
            url = tk.h.url_for('c4w.admin_index', qualified=True)
        except Exception:
            url = u''
        mail.notify_moderators(
            tk._(u'[Citizens4Water] New dataset submitted: %s') % row.title,
            u'%s\n\n%s' % (row.title, url))
    row = _get(row.id)
    return _dictize(row, context)


def c4w_dataset_process(context, data_dict):
    """(Re-)run the pipeline for a dataset the requester may edit."""
    tk.check_access('c4w_dataset_process', context, data_dict)
    from ckanext.c4w.data import jobs

    row = _require_editable(context, data_dict.get('id'))
    if _primary_data_file(row.id) is None or not row.mapping_json:
        raise tk.ValidationError(
            {'mapping_json': [tk._('Upload a file and map its columns first')]})
    status = jobs.dispatch(row.id, force=tk.asbool(data_dict.get('force')))
    row = _get(row.id)
    out = _dictize(row, context)
    out['dispatch'] = status
    return out


@tk.side_effect_free
def c4w_dataset_completeness(context, data_dict):
    """What is still missing before submission, for the review step."""
    tk.check_access('c4w_dataset_completeness', context, data_dict)
    row = _require_editable(context, data_dict.get('id'))
    return {'errors': _complete_errors(row, context),
            'dataset': _dictize(row, context)}


# --------------------------------------------------------------------------- #
# Registries
# --------------------------------------------------------------------------- #

def get_actions():
    return {
        'c4w_dataset_show': c4w_dataset_show,
        'c4w_dataset_list': c4w_dataset_list,
        'c4w_dataset_facets': c4w_dataset_facets,
        'c4w_dataset_record_view': c4w_dataset_record_view,
        'c4w_dataset_bundle_show': c4w_dataset_bundle_show,
        'c4w_dataset_create': c4w_dataset_create,
        'c4w_dataset_update': c4w_dataset_update,
        'c4w_dataset_delete': c4w_dataset_delete,
        'c4w_dataset_file_upload': c4w_dataset_file_upload,
        'c4w_dataset_file_delete': c4w_dataset_file_delete,
        'c4w_dataset_mapping_propose': c4w_dataset_mapping_propose,
        'c4w_dataset_mapping_update': c4w_dataset_mapping_update,
        'c4w_dataset_submit': c4w_dataset_submit,
        'c4w_dataset_process': c4w_dataset_process,
        'c4w_dataset_completeness': c4w_dataset_completeness,
    }


@tk.auth_allow_anonymous_access
def _signed_in(context, data_dict):
    """Any signed-in user may call; the action decides ownership."""
    if _common.is_authenticated(context):
        return {'success': True}
    return {'success': False, 'msg': tk._('Not authorized')}


def get_auth_functions():
    functions = _common.public_read_auth(
        'c4w_dataset_show', 'c4w_dataset_list', 'c4w_dataset_facets',
        'c4w_dataset_record_view', 'c4w_dataset_bundle_show')
    for name in ('c4w_dataset_create', 'c4w_dataset_update',
                 'c4w_dataset_delete', 'c4w_dataset_file_upload',
                 'c4w_dataset_file_delete', 'c4w_dataset_mapping_propose',
                 'c4w_dataset_mapping_update', 'c4w_dataset_submit',
                 'c4w_dataset_process', 'c4w_dataset_completeness'):
        functions[name] = _signed_in
    return functions
