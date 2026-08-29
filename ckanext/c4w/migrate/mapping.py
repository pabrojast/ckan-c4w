# encoding: utf-8
"""Pure Django-row -> c4w-row mapping.

CKAN-free and database-free on purpose: every function here takes a plain dict
(one row as psycopg2 returns it) plus a lookup context, and returns a plain
dict. That is what lets tests/test_import_mapping.py exercise the whole
mapping against a fixture with nothing but the standard library, and it covers
most of the migration's risk for the price of a JSON file.

Each mapper returns:

    {'columns': {...},          # native c4w columns
     'extras': {...},           # JSON overflow, flattened on dictize
     'terms': {vocab: [(term, label), ...]},
     'relations': {predicate: [(object_type, object_id), ...]},
     'notes': [str, ...]}       # for the operator's import report

PRIVACY NOTE, and it is a real one: ``extras`` is NOT private storage.
db.entity_dictize merges it into the TOP LEVEL of the dictized row, which is
what every template and the API read. Anything put there is published. So the
legacy author's name and email go to the import REPORT and never to extras.
"""
import datetime
import re

from ckanext.c4w.text import (
    ensure_scheme, html_to_text, is_blank_html, normalise_term, normalise_text,
    slugify, split_free_terms,
)


def _sanitize(value, rich=False):
    """Sanitise stored HTML, importing the sanitiser lazily.

    The importer refuses to run without bleach (see runner.preflight) because
    the fail-closed path strips every tag, and here that would be written to
    the database rather than merely rendered once.
    """
    from ckanext.c4w.logic import sanitize as s
    if value is None:
        return None
    cleaned = s.sanitize_rich_html(value) if rich else s.sanitize_html(value)
    return cleaned or None


def _html_field(value, rich=False):
    """Sanitise an HTML field, collapsing editor-empty values to None.

    A WYSIWYG stores '<p><br></p>' for a field the author opened and left
    alone; that is empty to a reader and truthy to ``if value``, which is how
    an empty section heading ends up on a page.
    """
    if value is None or is_blank_html(value):
        return None
    return _sanitize(normalise_text(value), rich=rich)


def _bool(value):
    """Django's tri-state booleans collapse to two.

    projects_project.hidden is NULL in 42 of 43 rows, and the c4w column
    declares default=False. Normalising here means no downstream predicate has
    to be NULL-aware.
    """
    return bool(value)


def _parse_stamp(value):
    """Coerce whatever the source handed us into a datetime, or None.

    psycopg2 gives a datetime; a JSON export gives an ISO string. Accepting
    both keeps the mappers usable from the fixture as well as from the live
    database, which is what makes them testable at all.
    """
    if value is None or isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    text = (u'%s' % value).strip()
    if not text:
        return None
    text = text.replace(u'Z', u'+00:00')
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        for fmt in ('%Y-%m-%d %H:%M:%S%z', '%Y-%m-%d %H:%M:%S',
                    '%Y-%m-%d'):
            try:
                return datetime.datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def _date(value):
    """A timestamptz read as a date in UTC.

    psycopg2 returns a tz-aware datetime rendered in the CONNECTION's TimeZone,
    so a bare .date() silently rebases every value if the session is not UTC --
    moving some rows to the previous calendar day. Converting explicitly makes
    the result independent of the connection.
    """
    if isinstance(value, datetime.date) and not isinstance(
            value, datetime.datetime):
        return value
    stamp = _parse_stamp(value)
    if stamp is None:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(datetime.timezone.utc)
    return stamp.date()


def _naive_utc(value):
    """A timestamptz as a naive UTC datetime, for the same reason as _date."""
    stamp = _parse_stamp(value)
    if stamp is None:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(datetime.timezone.utc)
    return stamp.replace(tzinfo=None)


def _time(value):
    """A Django TimeField as a datetime.time.

    psycopg2 hands back a time object, but a JSON export gives 'HH:MM:SS' --
    and the column is typed, so a string reaches the driver and fails there
    rather than here. Coercing at the boundary keeps the mapper's output valid
    whatever fed it.
    """
    if value is None:
        return None
    if isinstance(value, datetime.time):
        return value
    if isinstance(value, datetime.datetime):
        return value.time()
    text = (u'%s' % value).strip()
    if not text:
        return None
    for fmt in ('%H:%M:%S.%f', '%H:%M:%S', '%H:%M'):
        try:
            return datetime.datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _number(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coordinate(value):
    """A latitude/longitude, discarding the null-island placeholder.

    Three organisations carry (0, 0), which is a Gulf-of-Guinea point no one
    entered; treating it as data would put a pin in the ocean.
    """
    number = _number(value)
    if number is None or number == 0.0:
        return None
    return number


def _media_path(value):
    """A Django ImageField value, normalised.

    Every nullable media column in this corpus stores the EMPTY STRING rather
    than NULL, and an importer branching on ``IS NOT NULL`` would try to fetch
    a file called ''.
    """
    return normalise_text(value)


def _text_terms(value, vocabulary):
    """Free-text terms from a legacy value, as (term, label) pairs."""
    return split_free_terms(value)


def _lookup_term(ctx_table, key, vocabulary):
    """Resolve a legacy lookup-table id to a (term, label) pair.

    Uses the LABEL from the legacy table, slugified, rather than assuming the
    ids line up with anything -- they do not.

    The key is looked up both as given and as a string, because the lookup
    tables arrive either from psycopg2 (integer keys) or from a JSON fixture
    (string keys, since JSON objects have no integer keys). Matching on only
    one of those silently resolved every organisation type to None -- the
    whole Type facet would have shipped empty.
    """
    table = ctx_table or {}
    label = table.get(key)
    if label is None and key is not None:
        label = table.get(u'%s' % key)
    if label is None:
        try:
            label = table.get(int(key))
        except (TypeError, ValueError):
            label = None
    if not label:
        return None, None
    return normalise_term(label), label


# --------------------------------------------------------------------------- #
# Project
# --------------------------------------------------------------------------- #

# Columns that carry prose and go to extras rather than earning a column.
PROJECT_EXTRA_HTML = ('how_to_participate', 'equipment')

PROJECT_EXTRA_TEXT = (
    ('water_parameters', 'water_parameters'),
    ('target_group', 'target_group'),
    ('data_quality_initiatives', 'data_quality_initiatives'),
    ('ai_description', 'ai_description'),
    ('indigenous_description', 'indigenous_description'),
    ('achievements', 'achievements'),
    ('challenges', 'challenges'),
    ('community_impact_description', 'community_impact_description'),
    ('outreach_methods', 'outreach_methods'),
    ('interesting_highlights', 'interesting_highlights'),
    ('number_of_participants', 'number_of_participants'),
    ('duration_of_involvement', 'duration_of_involvement'),
    ('lead_partner_type', 'lead_partner_type'),
    ('host', 'host'),
)


def map_project(row, ctx=None):
    ctx = ctx or {}
    notes = []

    name = normalise_text(row.get('name'))
    if not name:
        # Django permits a blank name; c4w_project.name is NOT NULL and the
        # slug derives from it. Skipping is safer than inventing a title.
        return {'skip': True,
                'notes': ['project %s has no name' % row.get('id')]}

    status_term, _label = _lookup_term(ctx.get('status'), row.get('status_id'),
                                       'status')
    difficulty_term, _ = _lookup_term(
        ctx.get('difficulty_level'), row.get('difficultyLevel_id'),
        'difficulty_level')
    training_term, _ = _lookup_term(
        ctx.get('training_level'), row.get('training_level_id'),
        'training_level')

    url = ensure_scheme(row.get('url'))
    if url and 'ihp-wins.unesco.org/citizens4water' in url:
        notes.append('project %s: url points at the portal itself (%s)'
                     % (row.get('id'), url))

    columns = {
        'legacy_id': row.get('id'),
        'name': name,
        'url': url,
        # The BASE column, not the _en shadow: modeltranslation keeps the two
        # byte-identical on every row, and only English is active.
        'description': _html_field(row.get('description')),
        'aim': _html_field(row.get('aim')),
        'cs_aspects': _html_field(
            row.get('citizen_science_aspects_description')),
        'status': status_term,
        'difficulty_level': difficulty_term,
        'data_url': ensure_scheme(row.get('data')),
        'locality': normalise_text(row.get('projectlocality')),
        'start_date': _date(row.get('start_date')),
        'end_date': _date(row.get('end_date')),
        'author': normalise_text(row.get('author')),
        # Not lowercased: the local part of an address is case-sensitive.
        'author_email': normalise_text(row.get('author_email')),
        'funding_programme': normalise_text(row.get('fundingProgram')),
        'approved': _bool(row.get('approved')),
        'moderated': _bool(row.get('moderated')),
        'hidden': _bool(row.get('hidden')),
        'featured': _bool(row.get('featured')),
        'total_accesses': int(row.get('totalAccesses') or 0),
        'total_likes': int(row.get('totalLikes') or 0),
        'total_followers': int(row.get('totalFollowers') or 0),
        'geom_geojson': normalise_text(row.get('geom_geojson')),
        'created': _naive_utc(row.get('dateCreated')),
        'modified': _naive_utc(row.get('dateUpdated'))
                    or _naive_utc(row.get('dateCreated')),
    }
    for index in (1, 2, 3):
        columns['image%d_url' % index] = None      # filled by the media pass
        columns['image%d_credit' % index] = normalise_text(
            row.get('imageCredit%d' % index))

    extras = {}
    for key in PROJECT_EXTRA_HTML:
        source = {'how_to_participate': 'howToParticipate',
                  'equipment': 'equipment'}[key]
        value = _html_field(row.get(source))
        if value:
            extras[key] = value
    for key, source in PROJECT_EXTRA_TEXT:
        value = normalise_text(row.get(source))
        if value:
            extras[key] = value
    for key, source in (('open_participation', 'open_participation'),
                        ('uses_ai', 'uses_ai'),
                        ('indigenous_knowledge', 'indigenous_knowledge'),
                        ('doing_at_home', 'doingAtHome')):
        if row.get(source) is not None:
            extras[key] = bool(row.get(source))
    if training_term:
        extras['training_level'] = training_term

    # Plain-text shadows so the ILIKE search matches what a reader sees. The
    # listing searches name, description and aim, and those hold sanitised
    # HTML -- 'E. coli levels' misses because the stored text is
    # 'E. coli</i> levels'.
    for key, source in (('description_text', 'description'),
                        ('aim_text', 'aim')):
        shadow = html_to_text(row.get(source))
        if shadow:
            extras[key] = shadow

    # Legacy provenance that is safe to publish (no personal data).
    for key, source in (('legacy_origin', 'origin'),
                        ('legacy_origin_url', 'originURL'),
                        ('legacy_origin_uid', 'originUID')):
        value = normalise_text(row.get(source))
        if value:
            extras[key] = value

    terms = {}
    keywords = _text_terms(row.get('_keywords'), 'keyword')
    if keywords:
        terms['keyword'] = keywords

    return {
        'columns': columns,
        'extras': extras,
        'terms': terms,
        'relations': {},
        'notes': notes,
        'media': {'image1': _media_path(row.get('image1')),
                  'image2': _media_path(row.get('image2')),
                  'image3': _media_path(row.get('image3'))},
        'legacy_author': row.get('creator_id'),
    }


# --------------------------------------------------------------------------- #
# Organisation
# --------------------------------------------------------------------------- #

def map_organisation(row, ctx=None):
    ctx = ctx or {}
    name = normalise_text(row.get('name'))
    if not name:
        return {'skip': True,
                'notes': ['organisation %s has no name' % row.get('id')]}

    org_type_term, _ = _lookup_term(ctx.get('org_type'), row.get('orgType_id'),
                                    'org_type')
    country = normalise_text(row.get('country'))

    columns = {
        'legacy_id': row.get('id'),
        'name': name,
        'url': ensure_scheme(row.get('url')),
        'description': _html_field(row.get('description')),
        'org_type': org_type_term,
        'logo_url': None,                          # filled by the media pass
        # 'NA' is a placeholder one row uses for "no credit".
        'logo_credit': _credit(row.get('logoCredit')),
        'contact_point': normalise_text(row.get('contactPoint')),
        'contact_point_email': normalise_text(row.get('contactPointEmail')),
        'latitude': _coordinate(row.get('latitude')),
        'longitude': _coordinate(row.get('longitude')),
        # Uppercased: the ISO 3166-1 alpha-2 form, matching what the country
        # facet and Babel's territory table expect.
        'country': country.upper() if country else None,
        'approved': _bool(row.get('approved')),
        'ecsa_member': _bool(row.get('ecsaMember')),
        'created': _naive_utc(row.get('dateCreated')),
        'modified': _naive_utc(row.get('dateUpdated'))
                    or _naive_utc(row.get('dateCreated')),
    }
    extras = {}
    shadow = html_to_text(row.get('description'))
    if shadow:
        extras['description_text'] = shadow

    return {
        'columns': columns,
        'extras': extras,
        'terms': {},
        'relations': {},
        'notes': [],
        'media': {'logo': _media_path(row.get('logo'))},
        'legacy_author': row.get('creator_id'),
    }


def _credit(value):
    """An image credit, discarding the 'NA' placeholder."""
    text = normalise_text(value)
    if not text or text.strip().upper() in (u'NA', u'N/A', u'-'):
        return None
    return text


# --------------------------------------------------------------------------- #
# Platform
# --------------------------------------------------------------------------- #

def map_platform(row, ctx=None):
    name = normalise_text(row.get('name'))
    if not name:
        return {'skip': True,
                'notes': ['platform %s has no name' % row.get('id')]}

    extent = normalise_text(row.get('geographicExtend'))
    columns = {
        'legacy_id': row.get('id'),
        'name': name,
        'url': ensure_scheme(row.get('url')),
        'description': _html_field(row.get('description')),
        # Stored SHOUTED in Django ('MACRO-REGIONAL'); normalised so it lines
        # up with constants.GEOGRAPHIC_EXTENTS. Leaving the raw code in place
        # made the whole facet vanish from the page, because facet_group.html
        # hides any option absent from the counts.
        'geographic_extent': normalise_term(extent) if extent else None,
        'locality': normalise_text(row.get('platformLocality')),
        'contact_point': normalise_text(row.get('contactPoint')),
        'contact_point_email': normalise_text(row.get('contactPointEmail')),
        'logo_url': None,
        'logo_credit': _credit(row.get('logoCredit')),
        'profile_image_url': None,
        'profile_image_credit': _credit(row.get('profileImageCredit')),
        'approved': _bool(row.get('approved')),
        'created': _naive_utc(row.get('dateCreated')),
        'modified': _naive_utc(row.get('dateUpdated'))
                    or _naive_utc(row.get('dateCreated')),
    }
    extras = {}
    shadow = html_to_text(row.get('description'))
    if shadow:
        extras['description_text'] = shadow

    terms = {}
    countries = parse_country_field(row.get('countries'))
    if countries:
        terms['country'] = [(code, code) for code in countries]

    return {
        'columns': columns,
        'extras': extras,
        'terms': terms,
        'relations': {},
        'notes': [],
        'media': {'logo': _media_path(row.get('logo')),
                  'profileImage': _media_path(row.get('profileImage'))},
        'legacy_author': row.get('creator_id'),
    }


_ISO2_RE = re.compile(r'^[A-Z]{2}$')


def parse_country_field(value):
    """Parse a django-countries multiple field into ISO 3166-1 alpha-2 codes.

    Stored as a comma-separated string. Anything that is not two letters is
    dropped rather than guessed at -- the country vocabulary is documented to
    hold ISO codes, and a macro-region name silently entering it would break
    every consumer that maps a term through Babel.
    """
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = (u'%s' % value).split(u',')
    out = []
    for part in parts:
        code = (u'%s' % part).strip().upper()
        if _ISO2_RE.match(code) and code not in out:
            out.append(code)
    return out


# --------------------------------------------------------------------------- #
# Event
# --------------------------------------------------------------------------- #

def map_event(row, ctx=None):
    title = normalise_text(row.get('title'))
    if not title:
        return {'skip': True,
                'notes': ['event %s has no title' % row.get('id')]}

    notes = []
    timezone = normalise_text(row.get('timezone')) or u'Europe/Brussels'
    if not _valid_timezone(timezone):
        notes.append('event %s: unknown time zone %r, defaulted'
                     % (row.get('id'), timezone))
        timezone = u'Europe/Brussels'

    event_type = normalise_text(row.get('event_type'))
    language = normalise_text(row.get('language'))

    columns = {
        'legacy_id': row.get('id'),
        'title': title,
        'description': _html_field(row.get('description')),
        'place': normalise_text(row.get('place')),
        # Free text in Django, not an ISO code: 'Worldwide' and
        # 'England, Wales, Scotland and Northern Ireland' are both in there.
        # Preserved as typed; the ISO code, when derivable, is a term link.
        'country': normalise_text(row.get('country')),
        'start_date': _naive_utc(row.get('start_date')),
        'end_date': _naive_utc(row.get('end_date')),
        'hour': _time(row.get('hour')),
        'timezone': timezone,
        'language': language.lower() if language else None,
        'url': ensure_scheme(row.get('url')),
        'featured': _bool(row.get('featured')),
        'event_type': normalise_term(event_type) if event_type else None,
        'latitude': _coordinate(row.get('latitude')),
        'longitude': _coordinate(row.get('longitude')),
        'approved': _bool(row.get('approved')),
        'created': _naive_utc(row.get('start_date')),
        'modified': _naive_utc(row.get('start_date')),
    }
    extras = {}
    shadow = html_to_text(row.get('description'))
    if shadow:
        extras['description_text'] = shadow

    return {
        'columns': columns,
        'extras': extras,
        'terms': {},
        'relations': {},
        'notes': notes,
        'media': {},
        'legacy_author': row.get('creator_id'),
        'legacy_project': row.get('project_id'),
        'legacy_main_organisation': row.get('mainOrganisation_id'),
    }


def _valid_timezone(name):
    if not name:
        return False
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except Exception:
        try:
            import pytz
            return name in pytz.all_timezones_set
        except Exception:
            # Cannot verify; accept rather than rewrite a value that may well
            # be correct.
            return True


# --------------------------------------------------------------------------- #
# Blog post
# --------------------------------------------------------------------------- #

def map_post(row, ctx=None):
    title = normalise_text(row.get('title'))
    if not title:
        return {'skip': True,
                'notes': ['post %s has no title' % row.get('id')]}

    # blog_post already HAS a slug, and it is the address the site published.
    # Reuse it rather than regenerating, or every inbound link breaks.
    slug = normalise_text(row.get('slug'))

    columns = {
        'legacy_id': row.get('id'),
        'slug': slugify(slug) if slug else None,
        'title': title,
        # The only field on the portal that gets the WIDER allowlist: an
        # editor writing news legitimately needs sub-headings and tables, and
        # this corpus also carries in-body images and two video embeds.
        'content': _html_field(row.get('content'), rich=True),
        'excerpt': html_to_text(row.get('excerpt')),
        'image_url': None,
        'sticky': _bool(row.get('sticky')),
        'status': u'published' if row.get('status') == 1 else u'draft',
        'created_on': _naive_utc(row.get('created_on')),
        'updated_on': _naive_utc(row.get('updated_on')),
        'created': _naive_utc(row.get('created_on')),
        'modified': _naive_utc(row.get('updated_on'))
                    or _naive_utc(row.get('created_on')),
    }
    extras = {}
    # The excerpt is stored as HTML and Django rendered it with |safe; the c4w
    # card strips tags. Keeping the markup means a later template can restore
    # the one hyperlink that would otherwise be lost.
    excerpt_html = _html_field(row.get('excerpt'), rich=True)
    if excerpt_html:
        extras['excerpt_html'] = excerpt_html
    shadow = html_to_text(row.get('content'))
    if shadow:
        extras['content_text'] = shadow

    return {
        'columns': columns,
        'extras': extras,
        'terms': {},
        'relations': {},
        'notes': [],
        'media': {'image': _media_path(row.get('image'))},
        'legacy_author': row.get('author_id'),
    }


# --------------------------------------------------------------------------- #
# Resource
# --------------------------------------------------------------------------- #

def map_resource(row, ctx=None):
    ctx = ctx or {}
    name = normalise_text(row.get('name'))
    if not name:
        return {'skip': True,
                'notes': ['resource %s has no name' % row.get('id')]}

    language = normalise_text(row.get('inLanguage'))
    columns = {
        'legacy_id': row.get('id'),
        'name': name,
        'url': ensure_scheme(row.get('url')),
        'abstract': _html_field(row.get('abstract')),
        'cs_aspects': _html_field(
            row.get('description_citizen_science_aspects')),
        'category_id': None,      # resolved from category_id by the runner
        'publisher': normalise_text(row.get('publisher')),
        'date_published': _int(row.get('datePublished')),
        'doi': normalise_text(row.get('resourceDOI')),
        'license': normalise_text(row.get('license')),
        'in_language': language.lower() if language else None,
        'is_training_resource': _bool(row.get('isTrainingResource')),
        'time_required': _number(row.get('timeRequired')),
        'conditions_of_access': normalise_text(row.get('conditionsOfAccess')),
        'image1_url': None,
        'image1_credit': _credit(row.get('imageCredit1')),
        'image2_url': None,
        'image2_credit': _credit(row.get('imageCredit2')),
        'approved': _bool(row.get('approved')),
        'moderated': _bool(row.get('moderated')),
        'hidden': _bool(row.get('hidden')),
        'featured': _bool(row.get('featured')),
        'created': _naive_utc(row.get('dateCreated')),
        'modified': _naive_utc(row.get('dateUpdated'))
                    or _naive_utc(row.get('dateCreated')),
    }
    extras = {}
    for key, source in (('abstract_text', 'abstract'),
                        ('cs_aspects_text',
                         'description_citizen_science_aspects')):
        shadow = html_to_text(row.get(source))
        if shadow:
            extras[key] = shadow
    if row.get('dateUploaded') is not None:
        extras['date_uploaded'] = _naive_utc(row.get('dateUploaded'))

    return {
        'columns': columns,
        'extras': extras,
        'terms': {},
        'relations': {},
        'notes': [],
        'media': {'image1': _media_path(row.get('image1')),
                  'image2': _media_path(row.get('image2'))},
        'legacy_author': row.get('creator_id'),
        'legacy_category': row.get('category_id'),
    }


def _int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


MAPPERS = {
    'project': map_project,
    'organisation': map_organisation,
    'platform': map_platform,
    'event': map_event,
    'post': map_post,
    'resource': map_resource,
}
