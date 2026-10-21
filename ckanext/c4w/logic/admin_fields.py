"""Explicit editorial contracts. Operational and identity fields are never editable."""
from ckanext.c4w.logic import project_fields as projects

f = projects.field


def multi(name, label, vocabulary=None):
    return f(name, label, 'multi', vocabulary=vocabulary or name)


FIELDS = {
    'organisation': [
        f('name', 'Name', required=True), f('description', 'Description', 'rich'),
        f('url', 'Website', 'url'), f('org_type', 'Organisation type', 'select', vocabulary='org_type'),
        f('contact_point', 'Contact person'), f('contact_point_email', 'Contact email', 'email'),
        f('country', 'Country', 'country'), f('latitude', 'Latitude', 'decimal'),
        f('longitude', 'Longitude', 'decimal'), f('ecsa_member', 'ECSA member', 'bool'),
        f('logo', 'Logo', 'image'), f('logo_credit', 'Logo credit')],
    'platform': [
        f('name', 'Name', required=True), f('description', 'Description', 'rich'),
        f('url', 'Website', 'url'), f('geographic_extent', 'Geographic extent', 'select', vocabulary='geographic_extent'),
        f('locality', 'Locality'), f('country', 'Countries', 'countries'),
        f('contact_point', 'Contact person'), f('contact_point_email', 'Contact email', 'email'),
        f('logo', 'Logo', 'image'), f('logo_credit', 'Logo credit'),
        f('profile_image', 'Profile image', 'image'), f('profile_image_credit', 'Profile image credit')],
    'resource': [
        f('name', 'Name', required=True), f('url', 'Website', 'url'),
        f('abstract', 'Description', 'rich'), f('cs_aspects', 'Citizen science aspects', 'rich'),
        f('category_id', 'Category', 'category'), f('publisher', 'Publisher'),
        f('date_published', 'Publication year', 'year'), f('doi', 'DOI'),
        f('license', 'Licence'), f('in_language', 'Language', 'language'),
        f('time_required', 'Time required', 'decimal'), f('conditions_of_access', 'Conditions of access', 'rich'),
        multi('theme', 'Themes'), multi('audience', 'Audiences'),
        f('authors', 'Authors (separated by commas)', 'terms', vocabulary='author'),
        f('keywords', 'Keywords (separated by commas)', 'terms', vocabulary='resource_keyword'),
        f('education_level', 'Education levels (separated by commas)', 'terms', vocabulary='education_level'),
        f('learning_resource_type', 'Learning resource types (separated by commas)', 'terms', vocabulary='learning_resource_type'),
        f('image1', 'Thumbnail', 'image'), f('image1_credit', 'Thumbnail credit'),
        f('image2', 'Image', 'image'), f('image2_credit', 'Image credit')],
    'event': [
        f('title', 'Title', required=True), f('description', 'Description', 'rich', True),
        f('start_date', 'Start date', 'date', True), f('end_date', 'End date', 'date'),
        f('hour', 'Local start time', 'time'), f('timezone', 'Time zone', 'timezone'),
        f('place', 'Place'), f('country', 'Country', 'country'),
        f('latitude', 'Latitude', 'decimal'), f('longitude', 'Longitude', 'decimal'),
        f('language', 'Language', 'language'), f('event_type', 'Event type', 'select', vocabulary='event_type'),
        f('url', 'Website', 'url'), f('project_id', 'Project', 'project'),
        f('main_organisation_id', 'Organiser', 'organisation')],
    'post': [f('title', 'Title', required=True), f('excerpt', 'Short summary', 'textarea'),
             f('content', 'News', 'news', True), f('image', 'Image', 'image')],
    'dataset': [
        f('title', 'Title', required=True), f('description', 'Description', 'rich', True),
        f('keywords', 'Keywords (separated by commas)', 'terms', vocabulary='keyword'),
        multi('topic', 'Topics'), multi('water_type', 'Water types'), multi('water_data_type', 'Water data types'),
        f('language', 'Language', 'language'), f('project_id', 'Project', 'project'),
        f('organisation_id', 'Organisation', 'organisation'), f('license_id', 'Licence'),
        f('frequency', 'Update frequency', 'select', vocabulary='frequency'),
        f('temporal_start', 'Coverage starts', 'date'), f('temporal_end', 'Coverage ends', 'date'),
        f('country', 'Countries', 'countries'),
        f('geographic_extent', 'Geographic extent', 'select', vocabulary='geographic_extent'),
        f('source_url', 'Source URL', 'url'), f('doi', 'DOI'), f('citation', 'Citation', 'textarea'),
        f('provenance', 'Provenance', 'rich'), f('methodology', 'Methodology', 'rich'),
        multi('technology_used', 'Technologies'), f('data_quality_note', 'Data quality', 'rich'),
        f('related_urls', 'Related URLs (separated by commas)', 'urls'),
        f('contact_name', 'Contact name'), f('contact_email', 'Contact email', 'email'),
        f('contact_url', 'Contact URL', 'url'), f('author', 'Author'), f('author_email', 'Author email', 'email'),
        f('publisher', 'Publisher'), f('attribution_text', 'Attribution', 'textarea')],
}


def fields(entity):
    return list(projects.FIELDS.values()) if entity == 'project' else FIELDS[entity]


def groups(entity):
    return projects.STEPS if entity == 'project' else [dict(step=1, title='Content', fields=fields(entity))]


def term_fields(entity):
    if entity == 'project':
        return projects.TERM_FIELDS
    return {field['name']: field['vocabulary'] or field['name'] for field in fields(entity)
            if field['kind'] in ('multi', 'terms', 'countries')}
