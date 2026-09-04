# encoding: utf-8
"""Plain data for the Citizens4Water portal.

This module MUST NOT import CKAN. It is the single source of truth for every
controlled vocabulary, entity name and form step in the extension, and it is
read by four different consumers that would otherwise each grow their own
copy: the navl schemas, the Jinja templates (via ``h.c4w_option_list``), the
``c4w_option_lists`` action, and the tests.

Being CKAN-free is what lets ``tests/test_pure_logic.py`` and
``tests/test_import_mapping.py`` run with nothing but the standard library.

Vocabulary shape
----------------
``VOCABULARIES[name]`` is a tuple of ``(term, label)`` pairs.

``term`` is the stored value: a lowercase slug, stable forever. Renaming a
label is a display change; renaming a term is a data migration. ``label`` is
what a visitor reads and what the importer matches the legacy Django rows
against.

INVARIANT, enforced by tests/test_pure_logic.py: for every vocabulary seeded
from a legacy lookup table, ``term == text.normalise_term(label)``. Django
stores the LABEL, so the importer can only arrive at the term by slugifying
it -- and a hand-abbreviated term therefore lands outside its own vocabulary.
That is not hypothetical: 'Not yet started' had been shortened to
'not-started' here, which would have put five projects in a Status facet whose
option list could never match them, and three of the four training levels had
the same defect. EVENT_TYPES, LEAD_PARTNER_TYPES and POST_STATUSES are exempt
because Django stores a code for those, not a label.

Provenance: the terms below were extracted from the Django fixtures under
``citizens4water_platform-1/src/*/fixtures/*.json`` and cross-checked against
the live facets of https://ihp-wins.unesco.org/citizens4water/. Where the two
disagree, production wins and the divergence is marked -- the fixtures are a
seed, and the admins have edited the tables since.
"""

# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #

# Every entity the portal manages. Used to validate the ``<entity>`` path
# segment of the moderation routes -- an unknown value is a 404 and is never
# interpolated into SQL.
ENTITY_TYPES = (
    'project',
    'organisation',
    'resource',
    'platform',
    'event',
    'post',
    'dataset',
)

# Entities that go through the approve/reject moderation queue. ``post`` is
# absent on purpose: a blog post uses a draft/published status set by its
# author, not a reviewer decision.
MODERATED_ENTITY_TYPES = (
    'project',
    'organisation',
    'resource',
    'platform',
    'event',
    'dataset',
)

# Operations the moderation POST accepts. Unknown values 404 rather than
# being interpolated into anything that touches the database.
MODERATION_OPS = (
    'approve',
    'hide',
    'feature',
    # Re-run the processing pipeline. Only a dataset has one.
    'process',
)

# Not every entity grew every flag. The moderate action consults these
# rather than probing a live row, so a URL cannot invent a column.
ENTITY_HAS_HIDDEN = (
    'project',
    'resource',
    'dataset',
)
ENTITY_HAS_FEATURED = (
    'project',
    'resource',
    'event',
    'dataset',
)
ENTITY_HAS_MODERATED = (
    'project',
    'resource',
    'dataset',
)
# Entities with a processing pipeline a reviewer may re-run.
ENTITY_HAS_PROCESS = (
    'dataset',
)

# Detail endpoint per entity, so account and admin tables can link without
# each template hard-coding a six-way switch.
DETAIL_ENDPOINTS = {
    'project': 'project_detail',
    'organisation': 'organisation_detail',
    'resource': 'resource_detail',
    'platform': 'platform_detail',
    'event': 'event_detail',
    'post': 'post_detail',
    'dataset': 'dataset_detail',
}

# The submit chooser. This list is what the chooser page and its tests read
# so they cannot drift. An entry with a route in SUBMIT_ENDPOINTS renders as
# a link; the others are announced as coming later.
SUBMIT_CHOICES = (
    ('dataset', u'Data',
     u'A table of water measurements (CSV) that becomes an interactive '
     u'dashboard with a map and charts.'),
    ('project', u'Project',
     u'A citizen science initiative on hydrology or water management.'),
    ('resource', u'Resource',
     u'A document, dataset or tool useful to practitioners.'),
    ('organisation', u'Organisation',
     u'An organisation working on citizen science and water.'),
    ('event', u'Event',
     u'An event for the citizen science and water community.'),
    ('platform', u'Repository',
     u'A platform or repository related to citizen science and water.'),
)

# Chooser key -> blueprint endpoint of its first form step.
SUBMIT_ENDPOINTS = {
    'dataset': 'submit_data_start',
}


def moderate_error(entity_type, operation):
    """Stable error key if the pair is illegal, otherwise None.

    Kept CKAN-free so the URL contract is testable without a site: an
    unknown entity, an unknown operation, or a flag the entity does not
    have, must never reach SQL.
    """
    if entity_type not in ENTITY_TYPES:
        return 'unknown_entity'
    if operation not in MODERATION_OPS:
        return 'unknown_op'
    if operation == 'approve' and entity_type not in MODERATED_ENTITY_TYPES:
        return 'not_moderated'
    if operation == 'hide' and entity_type not in ENTITY_HAS_HIDDEN:
        return 'no_hidden'
    if operation == 'feature' and entity_type not in ENTITY_HAS_FEATURED:
        return 'no_featured'
    if operation == 'process' and entity_type not in ENTITY_HAS_PROCESS:
        return 'no_process'
    return None


# --------------------------------------------------------------------------- #
# Controlled vocabularies
# --------------------------------------------------------------------------- #

STATUSES = (
    ('not-yet-started', 'Not yet started'),
    ('active', 'Active'),
    ('periodically-active', 'Periodically active'),
    ('on-hold', 'On hold'),
    ('completed', 'Completed'),
    ('abandoned', 'Abandoned'),
)

TOPICS = (
    ('ocean', 'Ocean'),
    ('water', 'Water'),
    ('marine', 'Marine'),
    ('indigenous-culture', 'Indigenous culture'),
    ('social-sciences', 'Social sciences'),
    ('education', 'Education'),
    ('biodiversity', 'Biodiversity'),
    ('climate', 'Climate'),
    ('culture', 'Culture'),
)

HAS_TAGS = (
    ('fees-applicable', 'Fees applicable'),
    ('suitable-for-children', 'Suitable for children'),
    ('teaching-materials-available', 'Teaching materials available'),
    ('do-it-yourself', 'Do-it-yourself'),
    ('participate-from-home', 'Participate from home'),
)

DIFFICULTY_LEVELS = (
    ('not-indicated', 'Not Indicated'),
    ('easy', 'Easy'),
    ('medium', 'Medium'),
    ('hard', 'Hard'),
)

PARTICIPATION_TASKS = (
    ('annotation', 'Annotation'),
    ('audio-or-video-recording', 'Audio or video recording'),
    ('classification-or-tagging', 'Classification or tagging'),
    ('diy-hacking-making', 'DIY hacking/making'),
    ('data-analysis', 'Data analysis'),
    ('data-entry', 'Data Entry'),
    ('download-software-for-distributed-computing', 'Download software for distributed computing'),
    ('finding-entities', 'Finding entities'),
    ('geolocation', 'Geolocation'),
    ('identification', 'Identification'),
    ('learning', 'Learning'),
    ('measurement', 'Measurement'),
    ('observation', 'Observation'),
    ('photography', 'Photography'),
    ('problem-solving', 'Problem solving'),
    ('sample-analysis', 'Sample analysis'),
    ('site-selection-and-or-description', 'Site selection and/or description'),
    ('specimen-sample-collection', 'Specimen/sample collection'),
    ('transcription', 'Transcription'),
    ('other', 'Other'),
)

GEOGRAPHIC_EXTENTS = (
    ('global', 'Global'),
    ('macro-regional', 'Macro-regional'),
    ('national', 'National'),
    ('sub-national', 'Sub-national'),
    ('regional', 'Regional'),
    ('city', 'City'),
    ('neighbourhood', 'Neighbourhood'),
)

WATER_TYPES = (
    ('surface-water', 'Surface water'),
    ('soil-water', 'Soil water'),
    ('groundwater', 'Groundwater'),
    ('snow-ice', 'Snow & ice'),
    ('other', 'Other'),
)

WATER_DATA_TYPES = (
    ('water-quantity', 'Water quantity'),
    ('physical-water-quality', 'Physical water quality'),
    ('chemical-water-quality', 'Chemical water quality'),
    ('biological-water-quality', 'Biological water quality'),
    ('other', 'Other'),
)

# Haklay's participation typology, as seeded by the Django fixture.
ENGAGEMENT_LEVELS = (
    ('crowdsourcing', 'Crowdsourcing'),
    ('distributed-intelligence', 'Distributed intelligence'),
    ('participatory', 'Participatory'),
    ('collaborative', 'Collaborative'),
)

TRAINING_LEVELS = (
    ('no-prerequisite-knowledge', 'No prerequisite knowledge'),
    ('brief-training-needed', 'Brief training needed'),
    ('extensive-training-needed', 'Extensive training needed'),
    ('other', 'Other'),
)

TECHNOLOGIES_USED = (
    ('conventional-methods-manual-measurements', 'Conventional methods / manual measurements'),
    ('iot-sensors', 'IoT sensors'),
    ('diy-sensors', 'DIY sensors'),
    ('websites', 'Websites'),
    ('mobile-applications', 'Mobile applications'),
    ('wearables', 'Wearables'),
    ('drones', 'Drones'),
    ('mapping-technology', 'Mapping technology'),
    ('survey', 'Survey'),
    ('other', 'Other'),
)

STAKEHOLDER_TYPES = (
    ('citizens', 'Citizens'),
    ('researchers', 'Researchers'),
    ('policy-makers', 'Policy makers'),
    ('authorities', 'Authorities'),
    ('businesses', 'Businesses'),
    ('other', 'Other'),
)

COMMUNITY_IMPACT_TYPES = (
    ('awareness-raising', 'Awareness-raising'),
    ('policy-changes', 'Policy changes'),
    ('sustainable-practices', 'Sustainable practices'),
    ('other', 'Other'),
)

# Organisation types. The Django fixture seeds six; production serves a
# seventh, 'Intergovernmental', used by UNESCO IHP, UNESCO MAB and IHE Delft.
# The admins added it through the Django admin after the fixture was written.
# Dropping it would silently retype three organisations on import.
ORG_TYPES = (
    ('governmental', 'Governmental'),
    ('non-governmental', 'Non-governmental'),
    ('academic', 'Academic'),
    ('private-sector', 'Private sector'),
    ('community-led', 'Community-led'),
    ('consortium', 'Consortium'),
    ('intergovernmental', 'Intergovernmental'),
)

# Resource library themes (20, from resources/fixtures/themes.json).
RESOURCE_THEMES = (
    ('introduction-to-cs', 'Introduction to CS'),
    ('best-practices', 'Best practices'),
    ('project-management', 'Project management'),
    ('research-design-and-methods', 'Research design and methods'),
    ('engagement', 'Engagement'),
    ('co-creation', 'Co-creation'),
    ('communication', 'Communication'),
    ('event-planning', 'Event planning'),
    ('cs-stories', 'CS stories'),
    ('empowerment', 'Empowerment'),
    ('data-quality-and-standards', 'Data quality and standards'),
    ('instructions', 'Instructions'),
    ('link-with-formal-education', 'Link with formal education'),
    ('regulations-and-ethics', 'Regulations and ethics'),
    ('impact', 'Impact'),
    ('evaluation-of-citizen-science', 'Evaluation of citizen science'),
    ('project-sustainability', 'Project sustainability'),
    ('transferability', 'Transferability'),
    ('reflections-on-science', 'Reflections on science'),
    ('other', 'Other'),
)

RESOURCE_AUDIENCES = (
    ('community-members-citizens', 'Community Members & Citizens'),
    ('cs-project-leaders-initiators', 'CS Project Leaders & Initiators'),
    ('csos-ngos', 'CSOs & NGOs'),
    ('educators', 'Educators'),
    ('policy-decision-makers', 'Policy & Decision Makers'),
    ('researchers-academics', 'Researchers & Academics'),
    ('all-audiences', 'ALL Audiences'),
)

# Event delivery mode (events/models.py EVENT_TYPE_CHOICES).
# A blog post has no approval queue -- its author sets the state -- so this is
# a status, not a moderation decision. Defined here because the string was
# otherwise a bare literal in the importer, in query.py's visibility filter and
# in _common.is_visible, with nothing keeping the three in step.
POST_STATUSES = (
    ('draft', 'Draft'),
    ('published', 'Published'),
)

POST_STATUS_PUBLISHED = 'published'
POST_STATUS_DRAFT = 'draft'

EVENT_TYPES = (
    ('online', 'Online'),
    ('face-to-face', 'Face to face'),
    ('hybrid', 'Hybrid'),
)

# Lead partner type is a Django ``choices`` field on Project, not a table.
LEAD_PARTNER_TYPES = (
    ('academic', 'Academic'),
    ('government', 'Government'),
    ('ngo', 'Non-governmental organisation'),
    ('private', 'Private sector'),
    ('citizen', 'Citizen movement'),
    ('other', 'Other'),
)


# --------------------------------------------------------------------------- #
# Datasets (uploaded observation tables)
# --------------------------------------------------------------------------- #

# How the rows of an uploaded table are laid out. ``long`` is one measurement
# per row with a parameter-code column (GEMStat); ``wide`` is one sample per
# row with a column per parameter (FreshWater Watch).
DATASET_LAYOUTS = (
    ('long', 'One measurement per row (parameter, value, unit columns)'),
    ('wide', 'One sample per row (a column per parameter)'),
)

# The temporal grain the dashboard aggregates to. A period key is an integer
# of the shape yyyy, yyyymm or yyyymmdd -- see data/dates.py.
DATASET_GRAINS = (
    ('year', 'Year'),
    ('month', 'Month'),
    ('day', 'Day'),
)

# Lifecycle of the processing pipeline, in order. ``queued`` is the state a
# large file rests in until an operator (or a background worker) picks it up.
PROCESSING_STATUSES = (
    ('draft', 'Draft'),
    ('uploaded', 'File uploaded'),
    ('mapped', 'Columns mapped'),
    ('queued', 'Queued for processing'),
    ('processing', 'Processing'),
    ('ready', 'Ready'),
    ('failed', 'Processing failed'),
)

# Update frequency. The terms are the DCAT-AP / EU frequency authority codes
# in lowercase, so a later export to the IHP-WINS catalogue is a table
# lookup (FREQUENCY_EU_URIS) rather than a guess.
DATASET_FREQUENCIES = (
    ('cont', 'Continuous'),
    ('daily', 'Daily'),
    ('weekly', 'Weekly'),
    ('biweekly', 'Every two weeks'),
    ('monthly', 'Monthly'),
    ('quarterly', 'Quarterly'),
    ('annual', 'Annual'),
    ('irreg', 'Irregular'),
    ('never', 'Once, not updated'),
    ('unknown', 'Unknown'),
)

FREQUENCY_EU_URIS = {
    term: 'http://publications.europa.eu/resource/authority/frequency/%s'
    % term.upper()
    for term, _label in DATASET_FREQUENCIES
}

# Languages offered for the dataset metadata (ISO 639-1). Names are resolved
# through Babel in the helper, so only the codes live here.
DATASET_LANGUAGES = ('en', 'es', 'fr', 'ar', 'pt', 'de', 'it', 'nl', 'ru',
                     'zh')

# Licences offered in the wizard, as CKAN licence ids. The form reads the
# labels from CKAN's ``license_list`` so a site that renames one keeps its
# name; this tuple only fixes WHICH ids the portal accepts, so a bare
# ``other-closed`` cannot be smuggled onto a public dataset.
DATASET_LICENSES = (
    'cc-by',
    'cc-by-sa',
    'cc-zero',
    'odc-by',
    'odc-odbl',
    'odc-pddl',
    'other-open',
)
DATASET_DEFAULT_LICENSE = 'cc-by'

# The five stages of the data wizard. Same shape and same contract as
# PROJECT_FORM_STEPS: the indicator, the "which stage holds the first error"
# jump and the scaffold test all read this one definition.
#
# ``files`` and ``columns`` are handled by dedicated views (multipart upload
# and the mapping table) rather than a navl schema, so their field tuples
# name the inputs those pages render, not schema keys.
DATASET_FORM_STEPS = (
    {'step': 1, 'key': 'basics',
     'title': u'About the data',
     'hint': u'What was measured, where it belongs, and how to find it.',
     'fields': ('title', 'description', 'keywords', 'topic', 'water_type',
                'water_data_type', 'language', 'project_id',
                'organisation_id')},
    {'step': 2, 'key': 'files',
     'title': u'Files',
     'hint': u'The table of measurements, and any protocol or field sheet '
             u'that explains it.',
     'fields': ('data_file', 'attachments', 'file_note')},
    {'step': 3, 'key': 'columns',
     'title': u'Columns',
     'hint': u'Tell the dashboard which columns hold the site, the position, '
             u'the date and the measured values.',
     'fields': ('layout', 'grain', 'mapping_json', 'unit_note')},
    {'step': 4, 'key': 'details',
     'title': u'Coverage, method and licence',
     'hint': u'When and where the data was collected, how, and under which '
             u'terms it may be reused.',
     'fields': ('license_id', 'frequency', 'temporal_start', 'temporal_end',
                'country', 'geographic_extent', 'source_url', 'doi',
                'citation', 'provenance', 'methodology', 'technology_used',
                'data_quality_note', 'related_urls')},
    {'step': 5, 'key': 'contact_review',
     'title': u'Contact and review',
     'hint': u'Who to ask about the data, who made it, and a last look '
             u'before it goes to the reviewers.',
     'fields': ('contact_name', 'contact_email', 'contact_url', 'author',
                'author_email', 'publisher', 'attribution_text',
                'terms_accepted', 'licence_confirm')},
)

# Dataset fields that live in the JSON ``extras`` column. Kept next to the
# form steps so the schema and the storage cannot drift.
DATASET_EXTRA_FIELDS = (
    'geographic_extent',
    'methodology',
    'data_quality_note',
    'related_urls',
    'file_note',
    'unit_note',
    'contact_url',
    'attribution_text',
    'terms_accepted_at',
)

# Vocabularies a dataset carries as term links, and whether each is closed.
DATASET_TERM_VOCABULARIES = (
    'keyword',
    'country',
    'topic',
    'water_type',
    'water_data_type',
    'technology_used',
)

DATASET_ORDERINGS = (
    ('modified', 'Most Recent Updated'),
    ('title', 'A-Z'),
    ('created', 'Most Recent Created'),
    ('accesses', 'Total Accesses'),
    ('featured', 'Featured'),
)


# --------------------------------------------------------------------------- #
# The vocabulary registry
# --------------------------------------------------------------------------- #

# Closed vocabularies: a value outside the list is a validation error. The key
# is the ``vocabulary`` column of ``c4w_term_link``.
VOCABULARIES = {
    'topic': TOPICS,
    'has_tag': HAS_TAGS,
    'participation_task': PARTICIPATION_TASKS,
    'geographic_extent': GEOGRAPHIC_EXTENTS,
    'water_type': WATER_TYPES,
    'water_data_type': WATER_DATA_TYPES,
    'engagement_level': ENGAGEMENT_LEVELS,
    'technology_used': TECHNOLOGIES_USED,
    'stakeholder_type': STAKEHOLDER_TYPES,
    'community_impact_type': COMMUNITY_IMPACT_TYPES,
    'theme': RESOURCE_THEMES,
    'audience': RESOURCE_AUDIENCES,
}

# Open vocabularies: free text the user types, normalised into a slug but not
# checked against a list. ``country`` is open in shape but closed in practice
# -- its terms are ISO-3166 alpha-2 codes, validated by their own validator
# rather than by a 250-entry tuple duplicated here.
FREE_VOCABULARIES = (
    'keyword',
    'funding_body',
    'author',
    'education_level',
    'learning_resource_type',
    'language',
    'country',
)

# Single-valued closed fields stored as native columns, not as term links.
# Kept here so the form, the schema and the tests read one definition.
COLUMN_VOCABULARIES = {
    'status': STATUSES,
    'post_status': POST_STATUSES,
    'difficulty_level': DIFFICULTY_LEVELS,
    'training_level': TRAINING_LEVELS,
    'org_type': ORG_TYPES,
    'event_type': EVENT_TYPES,
    'lead_partner_type': LEAD_PARTNER_TYPES,
    'layout': DATASET_LAYOUTS,
    'grain': DATASET_GRAINS,
    'processing_status': PROCESSING_STATUSES,
    'frequency': DATASET_FREQUENCIES,
}


def vocabulary_terms(name):
    """Return the set of valid terms for a closed vocabulary, or None.

    ``None`` means "not closed" -- either a free vocabulary or an unknown
    name. Callers treat both the same way: accept the value as given.
    """
    pairs = VOCABULARIES.get(name) or COLUMN_VOCABULARIES.get(name)
    if pairs is None:
        return None
    return {term for term, _label in pairs}


def label_for(name, term):
    """Human label for a term, falling back to the term itself.

    Never raises: a term that predates a vocabulary edit still renders, as the
    stored slug, rather than blanking the field.
    """
    for pairs in (VOCABULARIES.get(name), COLUMN_VOCABULARIES.get(name)):
        if not pairs:
            continue
        for candidate, label in pairs:
            if candidate == term:
                return label
    return term


# --------------------------------------------------------------------------- #
# The project form
# --------------------------------------------------------------------------- #

# The six stages of /citizens4water/project/new, ported from the Django
# wizard in src/projects/templates/projects/project_form.html.
#
# ONE definition drives three things that would otherwise drift apart: the
# step indicator in the template, the server-side "which stage holds the first
# error" jump, and the scaffold test that asserts every field named here has
# an input in the template. A field added to the schema but forgotten here
# fails the test loudly instead of silently never rendering.
PROJECT_FORM_STEPS = (
    {'step': 1, 'key': 'identity',
     'title': u'Main information',
     'hint': u'What the project is called, what it aims to do, and where to '
             u'find it.',
     'fields': ('name', 'url', 'status', 'description', 'aim',
                'cs_aspects', 'keywords', 'data_url')},
    {'step': 2, 'key': 'classification',
     'title': u'Classification',
     'hint': u'Topics, tags, dates and the water it studies.',
     'fields': ('topic', 'has_tag', 'start_date', 'end_date',
                'water_type', 'water_data_type', 'water_parameters')},
    {'step': 3, 'key': 'participation',
     'title': u'Participation',
     'hint': u'Who can take part, what they do, and what they need to know.',
     'fields': ('participation_task', 'difficulty_level', 'training_level',
                'open_participation', 'engagement_level', 'target_group',
                'number_of_participants', 'duration_of_involvement',
                'language', 'how_to_participate', 'equipment',
                'technology_used', 'data_quality_initiatives')},
    {'step': 4, 'key': 'location',
     'title': u'Location',
     'hint': u'The extent of the project and the countries it covers.',
     'fields': ('geographic_extent', 'locality', 'country')},
    {'step': 5, 'key': 'impact',
     'title': u'Impact and insights',
     'hint': u'What the project achieved, for whom, and what it learned.',
     'fields': ('achievements', 'challenges', 'interesting_highlights',
                'stakeholder_type', 'community_impact_type',
                'community_impact_description', 'outreach_methods',
                'uses_ai', 'ai_description',
                'indigenous_knowledge', 'indigenous_description')},
    {'step': 6, 'key': 'leadership_images',
     'title': u'Contact, organisations and images',
     'hint': u'Who runs the project, who funds it, and how it looks.',
     'fields': ('author', 'author_email', 'main_organisation_id',
                'organisation', 'editors', 'lead_partner_type',
                'funding_body', 'funding_programme',
                'image1', 'image1_credit', 'image2', 'image2_credit',
                'image3', 'image3_credit')},
)

# Target box for each project image, matching the Django cropper config.
# (role, width, height). Without JavaScript the server fits the upload into
# this box; with JavaScript Cropper.js locks to this aspect ratio.
PROJECT_IMAGE_BOXES = {
    'image1': ('thumbnail', 600, 400),
    'image2': ('logo', 600, 400),
    'image3': ('heading', 1100, 400),
}


# --------------------------------------------------------------------------- #
# Listing behaviour
# --------------------------------------------------------------------------- #

# Ordering options per entity, as (value, label). The value is a key into the
# ORDER_COLUMNS map of logic/query.py -- it is never interpolated into SQL.
#
# 'Total Likes' is deliberately absent: likes are imported as a historical
# number but the like button is not implemented, so ordering by a frozen
# column would present a ranking that can never change.
PROJECT_ORDERINGS = (
    ('modified', 'Most Recent Updated'),
    ('accesses', 'Total Accesses'),
    ('name', 'A-Z'),
    ('created', 'Most Recent Created'),
    ('featured', 'Featured'),
)

DEFAULT_ORDERINGS = (
    ('modified', 'Most Recent Updated'),
    ('name', 'A-Z'),
    ('created', 'Most Recent Created'),
)

# Rows per page, matching the Django listings.
PAGE_SIZE = 18
PAGE_SIZE_CHRONOLOGICAL = 20
