# encoding: utf-8
"""Read-only access to the legacy Django database.

Every query here is a SELECT inside a READ ONLY transaction, and the session
is pinned to UTC before the first read. That last part is not decoration:
psycopg2 renders a ``timestamptz`` in the CONNECTION's time zone, so the same
row read through a session in Europe/Brussels and one in UTC gives two
different naive datetimes -- enough to move an event to the previous calendar
day and change where it sorts.

The schema is declared as data rather than spread through the runner, so the
tables this reads can be checked against the real database in one place.
``psycopg2`` is imported INSIDE the connect function: a missing driver must not
break CKAN's startup, since this module is only ever reached from the CLI.
"""
import contextlib
import logging

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Schema declarations
# --------------------------------------------------------------------------- #

# The lookup tables behind the closed vocabularies: vocabulary -> (table,
# label column). The importer resolves ids through these rather than assuming
# any id alignment, because there is none.
LOOKUPS = {
    'status': ('projects_status', 'status'),
    'difficulty_level': ('projects_difficultylevel', '"difficultyLevel"'),
    'training_level': ('projects_traininglevel', 'name'),
    'org_type': ('organisations_organisationtype', 'type'),
    'topic': ('projects_topic', 'topic'),
    'has_tag': ('projects_hastag', '"hasTag"'),
    'participation_task': ('projects_participationtask',
                           '"participationTask"'),
    'geographic_extent': ('projects_geographicextend', 'geographicextend'),
    'water_type': ('projects_watertype', 'name'),
    'water_data_type': ('projects_waterdatatype', 'name'),
    'engagement_level': ('projects_engagementlevel', 'name'),
    'technology_used': ('projects_technologyused', 'name'),
    'stakeholder_type': ('projects_stakeholdertype', 'name'),
    'community_impact_type': ('projects_communityimpacttype', 'name'),
    'language': ('projects_language', 'name'),
    'keyword': ('projects_keyword', 'keyword'),
    'funding_body': ('projects_fundingbody', 'body'),
    'theme': ('resources_theme', 'theme'),
    'audience': ('resources_audience', 'audience'),
    'author': ('authors_author', 'author'),
    'resource_keyword': ('resources_keyword', 'keyword'),
    'education_level': ('resources_educationlevel', '"educationLevel"'),
    'learning_resource_type': ('resources_learningresourcetype',
                               '"learningResourceType"'),
}

# Entity -> list of (join table, entity fk, target fk, vocabulary).
#
# Ordered by the join-table id, which is monotonic within an entity and is
# therefore the closest recoverable record of the order the author entered the
# values in. Where rows were later deleted the recovered order is the order of
# what remains, which is the best any reconstruction can do.
TERM_JOINS = {
    'project': [
        ('projects_project_topic', 'project_id', 'topic_id', 'topic'),
        ('"projects_project_hasTag"', 'project_id', 'hastag_id', 'has_tag'),
        ('"projects_project_participationTask"', 'project_id',
         'participationtask_id', 'participation_task'),
        ('projects_project_geographicextend', 'project_id',
         'geographicextend_id', 'geographic_extent'),
        ('projects_project_water_type', 'project_id', 'watertype_id',
         'water_type'),
        ('projects_project_water_data_type', 'project_id', 'waterdatatype_id',
         'water_data_type'),
        ('projects_project_engagement_level', 'project_id',
         'engagementlevel_id', 'engagement_level'),
        ('projects_project_technology_used', 'project_id', 'technologyused_id',
         'technology_used'),
        ('projects_project_stakeholder_types', 'project_id',
         'stakeholdertype_id', 'stakeholder_type'),
        ('projects_project_community_impact_types', 'project_id',
         'communityimpacttype_id', 'community_impact_type'),
        ('projects_project_language', 'project_id', 'language_id', 'language'),
        ('projects_project_keywords', 'project_id', 'keyword_id', 'keyword'),
        ('"projects_project_fundingBody"', 'project_id', 'fundingbody_id',
         'funding_body'),
    ],
    'resource': [
        ('resources_resource_theme', 'resource_id', 'theme_id', 'theme'),
        ('resources_resource_audience', 'resource_id', 'audience_id',
         'audience'),
        ('resources_resource_authors', 'resource_id', 'author_id', 'author'),
        ('resources_resource_keywords', 'resource_id', 'keyword_id',
         'resource_keyword'),
        ('"resources_resource_educationLevel"', 'resource_id',
         'educationlevel_id', 'education_level'),
        ('"resources_resource_learningResourceType"', 'resource_id',
         'learningresourcetype_id', 'learning_resource_type'),
    ],
}

# Vocabularies whose stored term is the LABEL slugified (a lookup table), as
# opposed to the free-text ones where the label is what the author typed.
# Both are slugified the same way; the distinction is only that a value
# outside a closed vocabulary is worth reporting.
FREE_VOCABULARIES = ('keyword', 'funding_body', 'author', 'resource_keyword',
                     'education_level', 'learning_resource_type', 'language')

# Entity -> list of (join table, entity fk, target fk, predicate,
# object entity type). 'user' targets are Django user ids and are resolved
# against CKAN separately.
RELATION_JOINS = {
    'project': [
        ('projects_project_organisation', 'project_id', 'organisation_id',
         'organisation', 'organisation'),
        ('projects_project_editors', 'project_id', 'user_id', 'editor', 'user'),
    ],
    'organisation': [
        ('organisations_organisation_editors', 'organisation_id', 'user_id',
         'editor', 'user'),
    ],
    'platform': [
        ('platforms_platform_organisation', 'platform_id', 'organisation_id',
         'organisation', 'organisation'),
    ],
    'event': [
        ('events_event_organisations', 'event_id', 'organisation_id',
         'organisation', 'organisation'),
    ],
    'resource': [
        ('resources_resource_organisation', 'resource_id', 'organisation_id',
         'organisation', 'organisation'),
        ('resources_resource_project', 'resource_id', 'project_id',
         'project', 'project'),
    ],
}

# Entity -> the SELECT that reads it. The geometry is converted in SQL rather
# than in Python so no GeoDjango or shapely dependency is needed.
ENTITY_QUERIES = {
    'project': (
        'SELECT p.*, ST_AsGeoJSON(ST_Transform('
        'p."projectGeographicLocation", 4326), 7) AS geom_geojson '
        'FROM projects_project p ORDER BY p.id'),
    'organisation': (
        'SELECT o.* FROM organisations_organisation o ORDER BY o.id'),
    'platform': 'SELECT p.* FROM platforms_platform p ORDER BY p.id',
    'event': 'SELECT e.* FROM events_event e ORDER BY e.id',
    'post': 'SELECT b.* FROM blog_post b ORDER BY b.id',
    'resource': 'SELECT r.* FROM resources_resource r ORDER BY r.id',
}

# The order entities must be imported in, so a foreign key always points at
# something that already exists.
IMPORT_ORDER = ('organisation', 'project', 'platform', 'resource', 'event',
                'post')

# Media-bearing columns, per entity: column -> the c4w column it fills.
MEDIA_COLUMNS = {
    'project': {'image1': 'image1_url', 'image2': 'image2_url',
                'image3': 'image3_url'},
    'organisation': {'logo': 'logo_url'},
    'platform': {'logo': 'logo_url', 'profileImage': 'profile_image_url'},
    'resource': {'image1': 'image1_url', 'image2': 'image2_url'},
    'post': {'image': 'image_url'},
}


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #

@contextlib.contextmanager
def connect(dsn):
    """A read-only, UTC-pinned connection to the legacy database.

    READ ONLY is belt and braces on top of only issuing SELECTs: it makes an
    accidental write fail at the server rather than succeed against what may
    be production.
    """
    import psycopg2
    import psycopg2.extras

    connection = psycopg2.connect(dsn)
    try:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute("SET TIME ZONE 'UTC'")
        yield _Reader(connection, psycopg2.extras.RealDictCursor)
    finally:
        connection.rollback()
        connection.close()


class _Reader(object):
    def __init__(self, connection, cursor_factory):
        self._connection = connection
        self._cursor_factory = cursor_factory

    def rows(self, sql, params=None):
        """Run a SELECT and yield dict rows."""
        with self._connection.cursor(
                cursor_factory=self._cursor_factory) as cursor:
            cursor.execute(sql, params or ())
            for row in cursor:
                yield dict(row)

    def one(self, sql, params=None):
        for row in self.rows(sql, params):
            return row
        return None

    def count(self, table):
        row = self.one('SELECT count(*) AS n FROM %s' % table)
        return int(row['n']) if row else 0

    # --- schema-driven readers ------------------------------------------- #

    def lookup(self, vocabulary):
        """``{legacy_id: label}`` for one lookup table."""
        table, column = LOOKUPS[vocabulary]
        return {row['id']: row['label'] for row in self.rows(
            'SELECT id, %s AS label FROM %s' % (column, table))}

    def lookups(self):
        return {vocabulary: self.lookup(vocabulary) for vocabulary in LOOKUPS}

    def entity_rows(self, entity):
        return self.rows(ENTITY_QUERIES[entity])

    def term_links(self, entity):
        """``{legacy_entity_id: {vocabulary: [legacy_term_id, ...]}}``."""
        out = {}
        for table, entity_fk, target_fk, vocabulary in TERM_JOINS.get(
                entity, ()):
            sql = ('SELECT %s AS subject, %s AS target FROM %s ORDER BY id'
                   % (entity_fk, target_fk, table))
            for row in self.rows(sql):
                (out.setdefault(row['subject'], {})
                    .setdefault(vocabulary, []).append(row['target']))
        return out

    def relation_links(self, entity):
        """``{legacy_entity_id: [(predicate, object_type, legacy_id), ...]}``."""
        out = {}
        for table, entity_fk, target_fk, predicate, object_type in (
                RELATION_JOINS.get(entity, ())):
            sql = ('SELECT %s AS subject, %s AS target FROM %s ORDER BY id'
                   % (entity_fk, target_fk, table))
            for row in self.rows(sql):
                out.setdefault(row['subject'], []).append(
                    (predicate, object_type, row['target']))
        return out

    def project_countries(self):
        """``{legacy_project_id: [{'code','lat','lon'}, ...]}``.

        Carries the coordinates as well as the code, because the map needs a
        point per country and the term link only records the code. Consumers
        join the two on ``code``, never on position.
        """
        catalogue = {row['id']: row for row in self.rows(
            'SELECT id, country, country_name, latitude, longitude '
            'FROM projects_projectcountry')}
        out = {}
        sql = ('SELECT project_id AS subject, projectcountry_id AS target '
               'FROM "projects_project_projectCountry" ORDER BY id')
        for row in self.rows(sql):
            entry = catalogue.get(row['target'])
            if not entry or not entry.get('country'):
                continue
            out.setdefault(row['subject'], []).append({
                'code': (entry['country'] or u'').strip().upper(),
                'lat': _float(entry.get('latitude')),
                'lon': _float(entry.get('longitude')),
            })
        return out

    def categories(self):
        return list(self.rows(
            'SELECT id, text, parent_id FROM resources_category ORDER BY id'))

    def users(self):
        """The Django accounts, for reconciliation against CKAN."""
        return list(self.rows(
            'SELECT id, name, email, is_active, is_staff '
            'FROM authtools_user ORDER BY id'))


def _float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
