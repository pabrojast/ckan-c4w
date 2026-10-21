"""The legacy ProjectForm contract, grouped into six resumable steps."""
import json
import math

from ckanext.c4w import constants
from ckanext.c4w.logic import checks


def field(name, label, kind='text', required=False, vocabulary=None, when=None):
    return dict(name=name, label=label, kind=kind, required=required,
                vocabulary=vocabulary, when=when)


STEPS = (
    dict(step=1, title='Main information', fields=[
        field('name', 'Project name', required=True),
        field('url', 'Project website', 'url'),
        field('status', 'Activity status', 'select', True, 'status'),
        field('description', 'Description', 'rich', True),
        field('aim', 'Objectives', 'rich', True),
        field('cs_aspects', 'Citizen science aspects', 'rich'),
        field('keywords', 'Keywords (separated by commas)', 'terms', True),
        field('data_url', 'Dataset repository (optional)', 'url'),
    ]),
    dict(step=2, title='Classification', fields=[
        field('topic', 'Topics', 'multi', vocabulary='topic'),
        field('has_tag', 'Tags', 'multi', vocabulary='has_tag'),
        field('start_date', 'Approximate start date', 'date'),
        field('end_date', 'Approximate end date', 'date'),
    ]),
    dict(step=3, title='Participation and water', fields=[
        field('participation_task', 'Participation tasks', 'multi', vocabulary='participation_task'),
        field('difficulty_level', 'Difficulty level', 'select', vocabulary='difficulty_level'),
        field('how_to_participate', 'How to participate', 'rich'),
        field('equipment', 'Equipment', 'rich'),
        field('engagement_level', 'Level of engagement', 'multi', vocabulary='engagement_level'),
        field('training_level', 'Training level', 'select', vocabulary='training_level'),
        field('open_participation', 'Open to public participation', 'bool'),
        field('target_group', 'Target group', 'rich'),
        field('number_of_participants', 'Number of participants', 'participants'),
        field('duration_of_involvement', 'Duration of involvement'),
        field('language', 'Languages', 'languages'),
        field('technology_used', 'Technologies used', 'multi', vocabulary='technology_used'),
        field('data_quality_initiatives', 'Data quality initiatives', 'rich'),
        field('water_type', 'Water types', 'multi', vocabulary='water_type'),
        field('water_data_type', 'Water data types', 'multi', vocabulary='water_data_type'),
        field('water_parameters', 'Specific water parameters', 'rich'),
    ]),
    dict(step=4, title='Location', fields=[
        field('geographic_extent', 'Geographic extent', 'multi', vocabulary='geographic_extent'),
        field('locality', 'Project locality'),
        field('country', 'Countries involved', 'countries'),
        field('geom_geojson', 'Project area', 'geometry'),
    ]),
    dict(step=5, title='Impact and responsibilities', fields=[
        field('achievements', 'Achievements', 'rich'),
        field('challenges', 'Challenges and how they were addressed', 'rich'),
        field('interesting_highlights', 'Interesting highlights', 'rich'),
        field('stakeholder_type', 'Stakeholders benefiting from the project', 'multi', vocabulary='stakeholder_type'),
        field('community_impact_type', 'Community impact types', 'multi', vocabulary='community_impact_type'),
        field('community_impact_description', 'Community impact', 'rich'),
        field('outreach_methods', 'Outreach methods', 'rich'),
        field('uses_ai', 'Does the project use AI?', 'bool'),
        field('ai_description', 'How is AI used?', 'rich', when='uses_ai'),
        field('indigenous_knowledge', 'Does the project integrate indigenous knowledge?', 'bool'),
        field('indigenous_description', 'How is indigenous knowledge integrated?', 'rich', when='indigenous_knowledge'),
        field('author', 'Contact person'),
        field('author_email', 'Contact email', 'email'),
        field('main_organisation_id', 'Lead organisation / coordinator', 'organisation'),
        field('organisation', 'Other organisations', 'organisations'),
        field('editors', 'Editors (CKAN usernames, separated by commas)', 'editors'),
        field('lead_partner_type', 'Lead partner type', 'select', vocabulary='lead_partner_type'),
        field('funding_body', 'Funding bodies (separated by commas)', 'terms'),
        field('funding_programme', 'Funding programme'),
    ]),
    dict(step=6, title='Images and review', fields=[
        field('image1', 'Thumbnail', 'image'),
        field('image1_credit', 'Thumbnail credit'),
        field('image2', 'Project logo', 'image'),
        field('image2_credit', 'Logo credit'),
        field('image3', 'Heading image', 'image'),
        field('image3_credit', 'Heading image credit'),
    ]),
)
FIELDS = {f['name']: f for step in STEPS for f in step['fields']}
MULTI = tuple(f['name'] for f in FIELDS.values()
              if f['kind'] in ('multi', 'countries', 'languages', 'organisations'))
TERM_FIELDS = {f['name']: f['vocabulary'] or f['name']
               for f in FIELDS.values()
               if f['kind'] in ('multi', 'countries', 'languages')}
TERM_FIELDS.update(keywords='keyword', funding_body='funding_body')


def geometry(value):
    """Bounded WGS84 Polygon/MultiPolygon; no arbitrary feature properties."""
    if not value:
        return ''
    if isinstance(value, str) and len(value) > 500000:
        raise ValueError('Project area is too large (maximum 500 KB).')
    try:
        obj = json.loads(value) if isinstance(value, str) else value
        if obj.get('type') == 'Feature':
            obj = obj['geometry']
        if obj.get('type') == 'FeatureCollection':
            polygons = []
            for f in obj['features']:
                g = json.loads(geometry(f['geometry']))
                polygons.extend(g['coordinates'])
            obj = {'type': 'MultiPolygon', 'coordinates': polygons}
        polygons = [obj['coordinates']] if obj['type'] == 'Polygon' else obj['coordinates']
        if obj['type'] not in ('Polygon', 'MultiPolygon') or not polygons:
            raise ValueError()
        count = 0
        for polygon in polygons:
            if not polygon:
                raise ValueError()
            for ring in polygon:
                if len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError()
                for point in ring:
                    count += 1
                    if len(point) != 2 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in point):
                        raise ValueError()
                    if not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
                        raise ValueError()
        if count > 10000:
            raise ValueError()
        return json.dumps({'type': 'MultiPolygon', 'coordinates': polygons})
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ValueError('Use a valid Polygon or MultiPolygon in WGS84, with at most 10,000 vertices.')


def validate(values, step=None, complete=False, context=None, baseline=None, field_specs=None):
    """Allow incomplete drafts; validate all supplied values and final required fields."""
    import ckan.plugins.toolkit as tk
    from ckanext.c4w.logic.sanitize import sanitize_html
    from ckanext.c4w.text import is_blank_html, html_to_text
    from ckanext.c4w.logic import legacy
    from ckanext.c4w.logic.validators import existing
    fields = field_specs if field_specs is not None else (STEPS[step - 1]['fields'] if step else FIELDS.values())
    out, errors = {}, {}
    for f in fields:
        name, kind = f['name'], f['kind']
        if kind == 'image':
            continue
        value = values.get(name)
        try:
            if kind == 'bool':
                value = checks.is_truthy(value)
            elif kind == 'geometry':
                value = geometry(value)
            elif kind == 'multi':
                value = checks.check_vocabulary_terms(f['vocabulary'], value)
            elif kind == 'countries':
                value = checks.check_country_codes(value, limit=250)
            elif kind == 'languages':
                value = legacy.languages(value)
                old = legacy.languages((baseline or {}).get(name))
                for v in value:
                    if baseline is None or v not in old:
                        checks.check_language_code(v)
            elif kind == 'organisations':
                value = list(dict.fromkeys(existing('organisation')(v, context or {}) for v in checks.as_list(value)))
            elif kind == 'terms':
                value = [label for term, label in checks.check_free_terms(value, limit=30)]
            elif kind == 'editors':
                import ckan.model as model
                value = checks.as_list(value)
                if len(value) > 20:
                    raise ValueError('At most 20 editors.')
                for username in value:
                    user = model.User.get(username)
                    if not user or user.state != 'active':
                        raise ValueError('Choose active CKAN usernames.')
                value = list(dict.fromkeys(model.User.get(v).id for v in value))
            elif value is None or value == '':
                value = ''
            elif kind == 'select':
                value = checks.check_vocabulary_term(f['vocabulary'], value)
            elif kind == 'organisation':
                value = existing('organisation')(value, context or {})
            elif kind == 'project':
                value = existing('project')(value, context or {})
            elif kind == 'country':
                from ckanext.c4w.logic.action.content_write import country_code
                value = checks.check_country_code(country_code(value))
            elif kind == 'language':
                value = checks.check_language_code(legacy.language(value))
            elif kind == 'time':
                import datetime
                time = datetime.time.fromisoformat(str(value))
                if time.tzinfo or time.microsecond:
                    raise ValueError('Enter a local time as HH:MM or HH:MM:SS.')
                value = time.isoformat()
            elif kind == 'timezone':
                import pytz
                if value not in pytz.all_timezones_set:
                    raise ValueError('Choose a valid time zone, such as Europe/Paris.')
            elif kind == 'decimal':
                value = float(value)
                if not math.isfinite(value) or (name == 'latitude' and not -90 <= value <= 90) or (name == 'longitude' and not -180 <= value <= 180) or (name == 'time_required' and value < 0):
                    raise ValueError('Enter a valid number within the allowed range.')
            elif kind == 'year':
                value = int(value)
                if not 1 <= value <= 9999:
                    raise ValueError('Enter a publication year.')
            elif kind == 'category':
                from ckanext.c4w import db
                if not db.Session.query(db.C4wCategory).filter_by(id=value).first():
                    raise ValueError('Choose an existing category.')
            elif kind == 'urls':
                value = [checks.check_url(v) for v in checks.as_list(value)]
                if len(value) > 5:
                    raise ValueError('At most five related links.')
            elif kind == 'participants':
                value = checks.clean_text(value, max_length=1000)
                if value.isdigit():
                    value = int(value)
            elif kind == 'url':
                value = checks.check_url(value)
            elif kind == 'email':
                value = checks.check_email(value)
            elif kind == 'date':
                date = checks.check_date(value)
                value = date.isoformat() if date else ''
            elif kind == 'number':
                value = int(value)
                if value < 0 or value > 1000000000:
                    raise ValueError('Enter a non-negative number of participants.')
            elif kind in ('rich', 'news'):
                from ckanext.c4w.logic.sanitize import sanitize_rich_html
                value = (sanitize_rich_html if kind == 'news' else sanitize_html)(str(value))
                limit = 100000 if kind == 'news' else 15000
                if len(html_to_text(value)) > limit and not (baseline is not None and legacy.same(value, sanitize_html(str(baseline.get(name) or '')))):
                    raise ValueError('Text is too long (maximum %s characters).' % format(limit, ','))
                if is_blank_html(value):
                    value = ''
            elif kind == 'textarea':
                value = str(value).strip()
                if len(value) > 5000:
                    raise ValueError('Text is too long (maximum 5,000 characters).')
            else:
                limit = 5000 if kind == 'textarea' else (200 if name == 'name' else 1000)
                value = checks.clean_text(value, max_length=limit)
            if complete and f['required'] and not value and not (baseline is not None and legacy.same(values.get(name), baseline.get(name))):
                raise ValueError('This field is required.')
            out[name] = value
        except (ValueError, TypeError, tk.Invalid) as exc:
            # Compatibility applies only to persisted, unchanged metadata.
            # Never use it to bypass HTML, URL, email or reference validation.
            preserve = kind in ('text', 'textarea', 'select', 'multi', 'countries', 'country', 'language', 'participants', 'terms', 'year', 'date', 'timezone', 'time', 'decimal')
            if baseline is not None and preserve and legacy.same(values.get(name), baseline.get(name)):
                out[name] = baseline.get(name) or ''
            else:
                errors[name] = [tk._(str(exc))]
    merged = dict(values, **out)
    if merged.get('start_date') and merged.get('end_date') and merged['end_date'] < merged['start_date'] and not (baseline is not None and all(legacy.same(merged.get(k), baseline.get(k)) for k in ('start_date', 'end_date'))):
        errors['end_date'] = [tk._('End date must be after the start date.')]
    return out, errors
