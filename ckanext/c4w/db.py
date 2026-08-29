# encoding: utf-8
"""Database model for ckanext-c4w (Citizens4Water).

Classic SQLAlchemy ``Table`` + ``mapper`` style bound to CKAN's shared
metadata, mirroring ckanext-csunesco and ckanext-pages, so the tables live in
the same database as core CKAN.

Two design decisions carry most of the weight here.

**Native columns are earned, not default.** A field gets a column when the
public site filters, orders or indexes by it -- everything else lives in a
per-row ``extras`` Text column holding JSON, flattened back to the top level
when the row is dictized. That keeps the schema small enough to reason about
while leaving room to add fields without a migration.

**Two generic link tables replace ~25 many-to-many tables.** The Django
original has a join table per vocabulary; ckanext-csunesco went the other way
and put its facets in the ``extras`` JSON, which forced it to filter in Python
over a capped scan of 1000 rows and made per-facet counts impractical
(see its logic/action/projects.py, "Promote to columns if that ever changes").
Citizens4Water has eleven faceted vocabularies WITH counts plus ~100
countries, so neither extreme works. ``c4w_term_link`` and ``c4w_relation``
give indexed filtering and a single GROUP BY for the counts, at the cost of
two tables instead of twenty-five.
"""
import datetime
import decimal
import logging
import uuid

import sqlalchemy as sa
from sqlalchemy import (
    Table,
    Column,
    types,
    Index,
    UniqueConstraint,
)

from ckan.model.meta import metadata, mapper, Session  # noqa: F401
from ckan.model.domain_object import DomainObject

from ckanext.c4w.text import (  # noqa: F401  (re-exported for callers)
    slugify, normalise_term, load_extras, dump_extras,
)

log = logging.getLogger(__name__)


def make_uuid():
    return str(uuid.uuid4())


def _utcnow():
    return datetime.datetime.utcnow()


# --------------------------------------------------------------------------- #
# Link tables
# --------------------------------------------------------------------------- #

# Entity <-> vocabulary term. One row per (entity, vocabulary, term).
#
# ``term`` is the normalised slug and is what every filter compares against;
# ``label`` is a display cache filled at import time so a value that predates
# a vocabulary edit still renders as the words the author typed.
c4w_term_link_table = Table(
    'c4w_term_link', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('entity_type', types.UnicodeText, nullable=False),
    Column('entity_id', types.UnicodeText, nullable=False),
    Column('vocabulary', types.UnicodeText, nullable=False),
    Column('term', types.UnicodeText, nullable=False),
    Column('label', types.UnicodeText),
    Column('sort_order', types.Integer, default=0),
    # The facet filter: WHERE vocabulary=? AND term=? AND entity_type=?
    Index('ix_c4w_term_link_facet', 'vocabulary', 'term', 'entity_type'),
    # Dictizing one row, or a page of rows via entity_id IN (...).
    Index('ix_c4w_term_link_entity', 'entity_type', 'entity_id'),
    # The facet counts: GROUP BY term over the filtered id set.
    Index('ix_c4w_term_link_count', 'entity_type', 'vocabulary', 'entity_id'),
    # Re-running the importer must not duplicate links. This constraint is
    # what makes "delete the entity's links, insert them again" safe, and it
    # is cheaper than checking in Python.
    UniqueConstraint('entity_type', 'entity_id', 'vocabulary', 'term',
                     name='uq_c4w_term_link'),
)

# Entity <-> entity, and entity <-> CKAN user.
#
# ``predicate`` names the relationship: 'organisation' (a project's partner
# organisations), 'project' (a resource's projects), 'editor' (a CKAN user
# allowed to edit), 'author'. With ``object_type='user'`` the ``object_id`` is
# a CKAN ``user.id``.
#
# Single-valued foreign keys (a project's main organisation, an event's
# project, a resource's category) are NOT here -- they are indexed native
# columns, because a join table for a scalar buys nothing.
c4w_relation_table = Table(
    'c4w_relation', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('subject_type', types.UnicodeText, nullable=False),
    Column('subject_id', types.UnicodeText, nullable=False),
    Column('predicate', types.UnicodeText, nullable=False),
    Column('object_type', types.UnicodeText, nullable=False),
    Column('object_id', types.UnicodeText, nullable=False),
    Column('sort_order', types.Integer, default=0),
    Index('ix_c4w_relation_subject', 'subject_type', 'subject_id', 'predicate'),
    # The reverse read: "which projects belong to this organisation?", and
    # "which projects may this user edit?".
    Index('ix_c4w_relation_object', 'object_type', 'object_id', 'predicate'),
    UniqueConstraint('subject_type', 'subject_id', 'predicate',
                     'object_type', 'object_id', name='uq_c4w_relation'),
)


# --------------------------------------------------------------------------- #
# Domain tables
# --------------------------------------------------------------------------- #

c4w_project_table = Table(
    'c4w_project', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    # The Django primary key. Carries the 301 from /project/<int> and gives
    # the importer its idempotency key.
    Column('legacy_id', types.Integer),
    Column('slug', types.UnicodeText, nullable=False),
    Column('name', types.UnicodeText, nullable=False),
    Column('url', types.UnicodeText),
    Column('description', types.UnicodeText),
    Column('aim', types.UnicodeText),
    Column('cs_aspects', types.UnicodeText),
    Column('status', types.UnicodeText),
    Column('difficulty_level', types.UnicodeText),
    Column('data_url', types.UnicodeText),
    Column('locality', types.UnicodeText),
    Column('start_date', types.Date),
    Column('end_date', types.Date),
    Column('author', types.UnicodeText),
    Column('author_email', types.UnicodeText),
    Column('main_organisation_id', types.UnicodeText),
    Column('funding_programme', types.UnicodeText),
    Column('image1_url', types.UnicodeText),
    Column('image1_credit', types.UnicodeText),
    Column('image2_url', types.UnicodeText),
    Column('image2_credit', types.UnicodeText),
    Column('image3_url', types.UnicodeText),
    Column('image3_credit', types.UnicodeText),
    Column('approved', types.Boolean, default=False),
    Column('moderated', types.Boolean, default=False),
    Column('hidden', types.Boolean, default=False),
    Column('featured', types.Boolean, default=False),
    Column('total_accesses', types.Integer, default=0),
    # Imported for fidelity and ordering parity, but frozen: the like button
    # is out of scope, so nothing increments these after the migration.
    Column('total_likes', types.Integer, default=0),
    Column('total_followers', types.Integer, default=0),
    Column('created_by', types.UnicodeText),
    # A MultiPolygon as GeoJSON. Native so it survives a round trip, but
    # deferred in every listing query -- it is served by /project/<slug>/geojson
    # and never embedded in a list page.
    Column('geom_geojson', types.UnicodeText),
    Column('extras', types.UnicodeText, default=u'{}'),
    Column('created', types.DateTime, default=_utcnow),
    Column('modified', types.DateTime, default=_utcnow),
    Index('ix_c4w_project_legacy', 'legacy_id', unique=True),
    Index('ix_c4w_project_slug', 'slug', unique=True),
    Index('ix_c4w_project_status', 'status'),
    Index('ix_c4w_project_difficulty', 'difficulty_level'),
    Index('ix_c4w_project_main_org', 'main_organisation_id'),
    Index('ix_c4w_project_created_by', 'created_by'),
    Index('ix_c4w_project_featured', 'featured'),
    Index('ix_c4w_project_accesses', 'total_accesses'),
    Index('ix_c4w_project_created', 'created'),
    # The hot path: the public listing, newest first.
    Index('ix_c4w_project_public_modified', 'approved', 'hidden', 'modified'),
)

c4w_organisation_table = Table(
    'c4w_organisation', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('legacy_id', types.Integer),
    Column('slug', types.UnicodeText, nullable=False),
    Column('name', types.UnicodeText, nullable=False),
    Column('url', types.UnicodeText),
    Column('description', types.UnicodeText),
    Column('org_type', types.UnicodeText),
    Column('logo_url', types.UnicodeText),
    Column('logo_credit', types.UnicodeText),
    Column('contact_point', types.UnicodeText),
    Column('contact_point_email', types.UnicodeText),
    Column('latitude', types.Numeric(9, 6)),
    Column('longitude', types.Numeric(9, 6)),
    Column('country', types.UnicodeText),
    # Organisations were never moderated in Django; keep that default.
    Column('approved', types.Boolean, default=True),
    Column('ecsa_member', types.Boolean, default=False),
    # Optional link to a CKAN organization. Deliberately NOT a real foreign
    # key: a hard FK would make deleting a CKAN organization fail or cascade
    # into this row. The action re-checks it with organization_show and
    # degrades to None when the target is gone.
    Column('ckan_org_id', types.UnicodeText),
    Column('created_by', types.UnicodeText),
    Column('extras', types.UnicodeText, default=u'{}'),
    Column('created', types.DateTime, default=_utcnow),
    Column('modified', types.DateTime, default=_utcnow),
    Index('ix_c4w_organisation_legacy', 'legacy_id', unique=True),
    Index('ix_c4w_organisation_slug', 'slug', unique=True),
    Index('ix_c4w_organisation_type', 'org_type'),
    Index('ix_c4w_organisation_country', 'country'),
    Index('ix_c4w_organisation_ckan_org', 'ckan_org_id'),
    Index('ix_c4w_organisation_public', 'approved', 'modified'),
)

# The DCMI category tree used by the resource library.
#
# A table rather than a term_link vocabulary because the filter is a
# two-level dependent select, and because ``path`` ("Text : Report") turns
# "everything under Text" into one indexed LIKE instead of a recursive walk.
c4w_category_table = Table(
    'c4w_category', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('legacy_id', types.Integer),
    Column('text', types.UnicodeText, nullable=False),
    Column('parent_id', types.UnicodeText),
    Column('path', types.UnicodeText),
    Column('depth', types.Integer, default=0),
    Column('sort_order', types.Integer, default=0),
    Index('ix_c4w_category_legacy', 'legacy_id', unique=True),
    Index('ix_c4w_category_parent', 'parent_id'),
    Index('ix_c4w_category_path', 'path'),
)

c4w_resource_table = Table(
    'c4w_resource', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('legacy_id', types.Integer),
    Column('slug', types.UnicodeText, nullable=False),
    Column('name', types.UnicodeText, nullable=False),
    Column('url', types.UnicodeText),
    Column('abstract', types.UnicodeText),
    Column('cs_aspects', types.UnicodeText),
    Column('category_id', types.UnicodeText),
    Column('publisher', types.UnicodeText),
    # Django stores the publication year as an integer, not a date.
    Column('date_published', types.Integer),
    Column('doi', types.UnicodeText),
    Column('license', types.UnicodeText),
    Column('in_language', types.UnicodeText),
    # Training resources share this table but get their own public surface at
    # /training_resources, so this flag is a route selector, not just a facet.
    Column('is_training_resource', types.Boolean, default=False),
    Column('time_required', types.Float),
    Column('conditions_of_access', types.UnicodeText),
    Column('image1_url', types.UnicodeText),
    Column('image1_credit', types.UnicodeText),
    Column('image2_url', types.UnicodeText),
    Column('image2_credit', types.UnicodeText),
    Column('approved', types.Boolean, default=False),
    Column('moderated', types.Boolean, default=False),
    Column('hidden', types.Boolean, default=False),
    Column('featured', types.Boolean, default=False),
    Column('created_by', types.UnicodeText),
    Column('extras', types.UnicodeText, default=u'{}'),
    Column('created', types.DateTime, default=_utcnow),
    Column('modified', types.DateTime, default=_utcnow),
    Index('ix_c4w_resource_legacy', 'legacy_id', unique=True),
    Index('ix_c4w_resource_slug', 'slug', unique=True),
    Index('ix_c4w_resource_category', 'category_id'),
    Index('ix_c4w_resource_language', 'in_language'),
    Index('ix_c4w_resource_training', 'is_training_resource'),
    Index('ix_c4w_resource_featured', 'featured'),
    Index('ix_c4w_resource_created', 'created'),
    # The hot path splits by surface, so the flag leads the index.
    Index('ix_c4w_resource_public_modified',
          'is_training_resource', 'approved', 'hidden', 'modified'),
)

c4w_platform_table = Table(
    'c4w_platform', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('legacy_id', types.Integer),
    Column('slug', types.UnicodeText, nullable=False),
    Column('name', types.UnicodeText, nullable=False),
    Column('url', types.UnicodeText),
    Column('description', types.UnicodeText),
    Column('geographic_extent', types.UnicodeText),
    Column('locality', types.UnicodeText),
    Column('contact_point', types.UnicodeText),
    Column('contact_point_email', types.UnicodeText),
    Column('logo_url', types.UnicodeText),
    Column('logo_credit', types.UnicodeText),
    Column('profile_image_url', types.UnicodeText),
    Column('profile_image_credit', types.UnicodeText),
    Column('approved', types.Boolean, default=True),
    Column('created_by', types.UnicodeText),
    Column('extras', types.UnicodeText, default=u'{}'),
    Column('created', types.DateTime, default=_utcnow),
    Column('modified', types.DateTime, default=_utcnow),
    Index('ix_c4w_platform_legacy', 'legacy_id', unique=True),
    Index('ix_c4w_platform_slug', 'slug', unique=True),
    Index('ix_c4w_platform_extent', 'geographic_extent'),
    Index('ix_c4w_platform_public', 'approved', 'modified'),
)

c4w_event_table = Table(
    'c4w_event', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('legacy_id', types.Integer),
    Column('slug', types.UnicodeText, nullable=False),
    Column('title', types.UnicodeText, nullable=False),
    Column('description', types.UnicodeText),
    Column('place', types.UnicodeText),
    # Free text in Django, not an ISO code. Preserved as typed; the ISO code
    # for faceting, when derivable, goes to c4w_term_link.
    Column('country', types.UnicodeText),
    Column('start_date', types.DateTime),
    Column('end_date', types.DateTime),
    Column('hour', types.Time),
    Column('timezone', types.UnicodeText),
    Column('language', types.UnicodeText),
    Column('url', types.UnicodeText),
    Column('featured', types.Boolean, default=False),
    Column('event_type', types.UnicodeText),
    Column('latitude', types.Numeric(9, 6)),
    Column('longitude', types.Numeric(9, 6)),
    Column('project_id', types.UnicodeText),
    Column('main_organisation_id', types.UnicodeText),
    Column('approved', types.Boolean, default=False),
    Column('created_by', types.UnicodeText),
    Column('extras', types.UnicodeText, default=u'{}'),
    Column('created', types.DateTime, default=_utcnow),
    Column('modified', types.DateTime, default=_utcnow),
    Index('ix_c4w_event_legacy', 'legacy_id', unique=True),
    Index('ix_c4w_event_slug', 'slug', unique=True),
    Index('ix_c4w_event_project', 'project_id'),
    Index('ix_c4w_event_main_org', 'main_organisation_id'),
    Index('ix_c4w_event_type', 'event_type'),
    # Events list chronologically, upcoming first, so date leads.
    Index('ix_c4w_event_public_start', 'approved', 'start_date'),
)

c4w_post_table = Table(
    'c4w_post', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('legacy_id', types.Integer),
    Column('slug', types.UnicodeText, nullable=False),
    Column('title', types.UnicodeText, nullable=False),
    Column('content', types.UnicodeText),
    Column('excerpt', types.UnicodeText),
    Column('image_url', types.UnicodeText),
    Column('author_id', types.UnicodeText),
    Column('created_on', types.DateTime),
    Column('updated_on', types.DateTime),
    Column('sticky', types.Boolean, default=False),
    # Django stores 0/1; a readable string keeps ad-hoc SQL honest.
    Column('status', types.UnicodeText, default=u'draft'),
    Column('extras', types.UnicodeText, default=u'{}'),
    Column('created', types.DateTime, default=_utcnow),
    Column('modified', types.DateTime, default=_utcnow),
    Index('ix_c4w_post_legacy', 'legacy_id', unique=True),
    Index('ix_c4w_post_slug', 'slug', unique=True),
    Index('ix_c4w_post_author', 'author_id'),
    Index('ix_c4w_post_public', 'status', 'sticky', 'created_on'),
)

# Legacy media path -> uploaded URL.
#
# Two jobs: it makes the image importer idempotent (a re-run finds the row and
# skips the upload), and it backs the /citizens4water/media/<path> redirect so
# every external link to an old image keeps resolving after the cutover.
c4w_media_map_table = Table(
    'c4w_media_map', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('legacy_path', types.UnicodeText, nullable=False),
    Column('new_url', types.UnicodeText),
    Column('sha256', types.UnicodeText),
    Column('imported_at', types.DateTime, default=_utcnow),
    Index('ix_c4w_media_map_path', 'legacy_path', unique=True),
)


_ALL_TABLES = [
    c4w_term_link_table,
    c4w_relation_table,
    c4w_category_table,
    c4w_organisation_table,
    c4w_project_table,
    c4w_resource_table,
    c4w_platform_table,
    c4w_event_table,
    c4w_post_table,
    c4w_media_map_table,
]


# --------------------------------------------------------------------------- #
# Domain objects
# --------------------------------------------------------------------------- #

class C4wTermLink(DomainObject):
    pass


class C4wRelation(DomainObject):
    pass


class C4wCategory(DomainObject):
    pass


class C4wOrganisation(DomainObject):
    pass


class C4wProject(DomainObject):
    pass


class C4wResource(DomainObject):
    pass


class C4wPlatform(DomainObject):
    pass


class C4wEvent(DomainObject):
    pass


class C4wPost(DomainObject):
    pass


class C4wMediaMap(DomainObject):
    pass


# Entity type name -> mapped class. The single place that resolves the
# ``<entity>`` path segment of the moderation routes to a table, so no other
# module needs its own if-chain.
ENTITY_CLASSES = {
    'project': C4wProject,
    'organisation': C4wOrganisation,
    'resource': C4wResource,
    'platform': C4wPlatform,
    'event': C4wEvent,
    'post': C4wPost,
}


_mapped = False


def _ensure_mappers():
    """Wire the classic mappers exactly once."""
    global _mapped
    if _mapped:
        return
    mapper(C4wTermLink, c4w_term_link_table)
    mapper(C4wRelation, c4w_relation_table)
    mapper(C4wCategory, c4w_category_table)
    mapper(C4wOrganisation, c4w_organisation_table)
    mapper(C4wProject, c4w_project_table)
    mapper(C4wResource, c4w_resource_table)
    mapper(C4wPlatform, c4w_platform_table)
    mapper(C4wEvent, c4w_event_table)
    mapper(C4wPost, c4w_post_table)
    mapper(C4wMediaMap, c4w_media_map_table)
    _mapped = True


def ensure_mappers():
    """Public wrapper for modules that build ORM queries directly."""
    _ensure_mappers()


# --------------------------------------------------------------------------- #
# Schema bootstrap
# --------------------------------------------------------------------------- #
#
# SECURITY: every identifier below is a HARD-CODED constant. ALTER TABLE
# statements are NEVER built from user-supplied names, so there is no
# SQL-injection surface here.
#
# This list is EMPTY on purpose in the first release: every column is created
# by ``create_all``. It is the place to register any column added AFTER a
# table has shipped, so deployments that already have the old table self-heal
# on startup instead of needing a migration.
#
# Tuples are (table_name, column_name, column_sql_type).
_AUTO_HEAL_COLUMNS = []


def _ensure_columns(engine):
    """Add missing whitelisted columns via ALTER TABLE."""
    if not _AUTO_HEAL_COLUMNS:
        return
    inspector = sa.inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table_name, column_name, column_type in _AUTO_HEAL_COLUMNS:
        if table_name not in existing_tables:
            # create_all will build the whole table; nothing to alter.
            continue
        existing = {c['name'] for c in inspector.get_columns(table_name)}
        if column_name in existing:
            continue
        alter_sql = 'ALTER TABLE {t} ADD COLUMN {c} {ty}'.format(
            t=table_name, c=column_name, ty=column_type)
        try:
            with engine.begin() as conn:
                conn.execute(sa.text(alter_sql))
            log.info("ckanext-c4w: added missing column %s.%s",
                     table_name, column_name)
        except Exception:
            log.error("ckanext-c4w: could not auto-heal a table column")


def _ensure_indexes(engine):
    """Create any missing index on any of our tables.

    ``create_all`` only builds indexes when it first creates the owning table,
    so an index added after a table has shipped would never appear on an
    existing deployment. Walking every table means a new index heals wherever
    it is declared. Runs after ``_ensure_columns`` so the columns it spans
    already exist.
    """
    for table in _ALL_TABLES:
        for index in table.indexes:
            try:
                index.create(bind=engine, checkfirst=True)
            except Exception:
                log.error("ckanext-c4w: could not auto-heal a table index")


def ensure_tables():
    """Create the plugin tables if absent and wire the mappers. Idempotent."""
    from ckan.model import meta

    engine = meta.engine
    _ensure_mappers()
    metadata.create_all(bind=engine, tables=_ALL_TABLES, checkfirst=True)
    _ensure_columns(engine)
    _ensure_indexes(engine)


# --------------------------------------------------------------------------- #
# Slugs
# --------------------------------------------------------------------------- #
#
# slugify/normalise_term live in the CKAN-free text module so the importer's
# mapping layer can apply exactly the same rules without importing CKAN. They
# are re-exported here because callers reach for them alongside the models.

def unique_slug(model_cls, base, exclude_id=None):
    """Return ``base`` or the first free ``base-2``, ``base-3``, ... variant.

    A slug is assigned once and then persisted: renaming an entity does NOT
    move its URL, because a moved URL breaks every inbound link for what is
    usually a cosmetic edit.
    """
    _ensure_mappers()
    base = slugify(base) or u'item'
    candidate = base
    suffix = 1
    while True:
        query = Session.query(model_cls.id).filter(model_cls.slug == candidate)
        if exclude_id:
            query = query.filter(model_cls.id != exclude_id)
        if query.first() is None:
            return candidate
        suffix += 1
        candidate = u'%s-%d' % (base, suffix)


# --------------------------------------------------------------------------- #
# Dictization
# --------------------------------------------------------------------------- #

# Columns never exposed through a dictized row.
_PRIVATE_COLUMNS = frozenset()


def _row_columns(obj):
    """Native column values of a mapped row, as a plain dict."""
    table = sa.inspect(type(obj)).local_table
    out = {}
    for column in table.columns:
        if column.name in _PRIVATE_COLUMNS:
            continue
        value = getattr(obj, column.name, None)
        if isinstance(value, (datetime.datetime, datetime.date,
                              datetime.time)):
            value = value.isoformat()
        elif isinstance(value, decimal.Decimal):
            value = float(value)
        out[column.name] = value
    return out


def terms_for(entity_type, entity_ids):
    """Vocabulary terms for one or many entities.

    Returns ``{entity_id: {vocabulary: [term, ...]}}`` from a SINGLE query, so
    dictizing a page of 18 rows costs one round trip rather than eighteen.
    Terms keep their stored order (``sort_order``), which is what preserves
    the sequence the author typed their keywords in.
    """
    _ensure_mappers()
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]
    ids = [i for i in (entity_ids or []) if i]
    out = {i: {} for i in ids}
    if not ids:
        return out
    rows = (
        Session.query(C4wTermLink)
        .filter(C4wTermLink.entity_type == entity_type)
        .filter(C4wTermLink.entity_id.in_(ids))
        .order_by(C4wTermLink.vocabulary, C4wTermLink.sort_order,
                  C4wTermLink.term)
        .all()
    )
    for row in rows:
        out.setdefault(row.entity_id, {}).setdefault(
            row.vocabulary, []).append(row.term)
    return out


def term_labels_for(entity_type, entity_ids):
    """As ``terms_for`` but carrying the display label alongside the term."""
    _ensure_mappers()
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]
    ids = [i for i in (entity_ids or []) if i]
    out = {i: {} for i in ids}
    if not ids:
        return out
    rows = (
        Session.query(C4wTermLink)
        .filter(C4wTermLink.entity_type == entity_type)
        .filter(C4wTermLink.entity_id.in_(ids))
        .order_by(C4wTermLink.vocabulary, C4wTermLink.sort_order,
                  C4wTermLink.term)
        .all()
    )
    for row in rows:
        out.setdefault(row.entity_id, {}).setdefault(
            row.vocabulary, []).append(
                {'term': row.term, 'label': row.label or row.term})
    return out


def relations_for(subject_type, subject_ids):
    """Outgoing relations, as ``{subject_id: {predicate: [object_id, ...]}}``."""
    _ensure_mappers()
    if isinstance(subject_ids, str):
        subject_ids = [subject_ids]
    ids = [i for i in (subject_ids or []) if i]
    out = {i: {} for i in ids}
    if not ids:
        return out
    rows = (
        Session.query(C4wRelation)
        .filter(C4wRelation.subject_type == subject_type)
        .filter(C4wRelation.subject_id.in_(ids))
        .order_by(C4wRelation.predicate, C4wRelation.sort_order)
        .all()
    )
    for row in rows:
        out.setdefault(row.subject_id, {}).setdefault(
            row.predicate, []).append(row.object_id)
    return out


def entity_dictize(entity_type, obj, terms=None, relations=None):
    """Dictize one row: native columns + flattened extras + terms + relations.

    ``extras`` is merged at the TOP level, not nested, so a template reads
    ``project.target_group`` without knowing whether that field earned a
    column. Native columns win a name collision -- the column is the schema,
    the extras blob is the overflow.

    Pass ``terms``/``relations`` when dictizing a list, so the caller can
    fetch them once for the whole page instead of per row.
    """
    if obj is None:
        return None
    out = _row_columns(obj)
    extras = load_extras(out.pop('extras', None))
    for key, value in extras.items():
        out.setdefault(key, value)
    if terms is None:
        terms = term_labels_for(entity_type, obj.id).get(obj.id, {})
    if relations is None:
        relations = relations_for(entity_type, obj.id).get(obj.id, {})
    # ``terms`` maps vocabulary -> [term slug]; ``term_labels`` keeps the label
    # alongside. The label column would otherwise be write-only: for a CLOSED
    # vocabulary a template can resolve the slug through constants.py, but for
    # an open one (keyword, funding_body, author) the stored label is the only
    # record of what the author actually typed -- and without it a country
    # renders as 'ca' where the sidebar two columns away says 'Canada'.
    out['term_labels'] = terms
    out['terms'] = {vocabulary: [t['term'] for t in items]
                    for vocabulary, items in terms.items()}
    out['relations'] = relations
    out['entity_type'] = entity_type
    return out


def list_dictize(entity_type, rows):
    """Dictize a page of rows with one terms query and one relations query."""
    rows = list(rows or [])
    ids = [r.id for r in rows]
    terms = term_labels_for(entity_type, ids)
    relations = relations_for(entity_type, ids)
    return [
        entity_dictize(entity_type, row,
                       terms=terms.get(row.id, {}),
                       relations=relations.get(row.id, {}))
        for row in rows
    ]
