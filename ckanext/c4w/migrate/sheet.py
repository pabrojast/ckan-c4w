# encoding: utf-8
"""Import citizen-science projects from a curated spreadsheet.

The source is the UNESCO list of water citizen-science projects (Koen
Verbist, 2026-11): one row per project, free-text cells, partial coverage.
It is not the legacy Django database, so it does not go through
``runner.Runner`` -- that runner is keyed on Django integer ids. This module
borrows its rules instead:

IDEMPOTENT. Every row carries ``extras.import_key``; a re-run finds the row by
that key and updates it in place. ``slug`` is assigned once and never moves.
``legacy_id`` stays NULL: it is reserved for the Django primary key.

NEVER OVERWRITE A HUMAN. A row that has been claimed (``claimable`` false) or
edited since the import (``modified`` newer than ``imported_at``) is skipped,
so re-running the import cannot undo a project manager's work.

DUPLICATES ARE SKIPPED, NOT MERGED INTO EXISTING PROJECTS. A row whose name
or website host matches a project already on the portal is reported and left
alone; rows duplicated inside the sheet are merged field by field first.

The mapping layer (``read_*``, ``map_row``, ``merge_duplicates``) is CKAN-free
so it can be tested and dry-run anywhere.
"""
import datetime
import html
import json
import re
import unicodedata

from ckanext.c4w.text import ensure_scheme, normalise_term, normalise_text, slugify

SOURCE = u'koen-cs-list-2026-11'
KEY_PREFIX = u'koen-cs-2026-11:'
MEDIA_PREFIX = u'koen-cs-2026-11/'

# Header -> key. Matched on a normalised header so trailing spaces or a
# changed capitalisation in a later version of the sheet still line up.
HEADERS = {
    'project name': 'name',
    'issue area': 'issue_area',
    'focused theme': 'theme',
    'region': 'region',
    'country': 'country',
    'data measurement': 'data_measurement',
    'data types': 'data_types',
    'website link': 'website',
    'project objectives': 'objectives',
    'outcomes': 'outcomes',
    'contact person and information': 'contact',
    'project status': 'status',
    'project partners': 'partners',
    'language(s)': 'languages',
    'citizen involvement': 'citizen_involvement',
    'ai technology used': 'ai_technology',
    'details of ai': 'ai_details',
    'others': 'others',
    'data portal': 'data_portal',
    'sdg relevance': 'sdg',
}

# Hosts that publish papers ABOUT a project rather than the project itself.
# A link there is kept as a reference, never as the project's website, and
# is never scraped for an image (a journal cover is not a project logo).
PUBLISHER_HOSTS = (
    'sciencedirect.com', 'mdpi.com', 'tandfonline.com', 'frontiersin.org',
    'researchgate.net', 'wiley.com', 'springer.com', 'link.springer.com',
    'doi.org', 'nature.com', 'plos.org', 'journals.plos.org',
    'iwaponline.com', 'sagepub.com', 'cambridge.org', 'oup.com',
    'academic.oup.com', 'jstor.org', 'scholar.google.com', 'proquest.com',
    'ncbi.nlm.nih.gov', 'pubmed.ncbi.nlm.nih.gov', 'europepmc.org',
    'semanticscholar.org', 'arxiv.org', 'biorxiv.org', 'ssrn.com',
    'ascelibrary.org', 'agupubs.onlinelibrary.wiley.com', 'copernicus.org',
    'hess.copernicus.org', 'iopscience.iop.org', 'acs.org', 'pubs.acs.org',
    'elsevier.com', 'cell.com', 'tos.org', 'theoryandpractice.citizenscienceassociation.org',
    'citizenscienceassociation.org', 'eartharxiv.org', 'zenodo.org',
)

COUNTRY_ALIASES = {
    'usa': 'US', 'us': 'US', 'u.s.': 'US', 'u.s.a.': 'US',
    'united states of america': 'US', 'america': 'US',
    'uk': 'GB', 'u.k.': 'GB', 'england': 'GB', 'scotland': 'GB',
    'wales': 'GB', 'northern ireland': 'GB', 'great britain': 'GB',
    'britain': 'GB', 'taiwan': 'TW', 'south korea': 'KR', 'korea': 'KR',
    'russia': 'RU', 'vietnam': 'VN', 'viet nam': 'VN', 'iran': 'IR',
    'tanzania': 'TZ', 'bolivia': 'BO', 'venezuela': 'VE', 'laos': 'LA',
    'syria': 'SY', 'moldova': 'MD', 'czech republic': 'CZ',
    'ivory coast': 'CI', "cote d'ivoire": 'CI', 'the gambia': 'GM',
    'gambia': 'GM', 'the netherlands': 'NL', 'holland': 'NL',
    'drc': 'CD', 'dr congo': 'CD', 'democratic republic of the congo': 'CD',
    'eswatini': 'SZ', 'swaziland': 'SZ', 'macedonia': 'MK',
    'north macedonia': 'MK', 'palestine': 'PS', 'brunei': 'BN',
    'cape verde': 'CV', 'myanmar': 'MM', 'burma': 'MM', 'micronesia': 'FM', 'turkey': 'TR', 'turkiye': 'TR',
}
# Regions that are not a country: recorded as the region, never as a code.
NON_COUNTRIES = ('global', 'europe', 'africa', 'asia', 'worldwide',
                 'international', 'latin america', 'caribbean', 'pacific',
                 'oceania', 'north america', 'south america', 'arctic',
                 'antarctica', 'multiple', 'various', 'eu')

STATUS_MAP = (
    (('active', 'on-going', 'ongoing', 'on going', 'running'), 'active'),
    (('completed', 'inactive', 'finished', 'ended', 'closed'), 'completed'),
    (('periodically',), 'periodically-active'),
    (('on hold', 'paused'), 'on-hold'),
    (('abandoned', 'discontinued'), 'abandoned'),
)

# (pattern over theme + measurement + data types, term) for the closed
# vocabularies. Conservative on purpose: a missing tag costs an editor a
# click; a wrong one misleads every visitor who filters by it.
WATER_DATA_RULES = (
    (r'\b(water level|flow|discharge|stream ?flow|rain(fall)?|precipitation|'
     r'hydrolog|groundwater level|snow depth|runoff|stage)\b', 'water-quantity'),
    (r'\b(temperature|turbidity|transparency|secchi|clarity|suspended solids|'
     r'tss|colou?r|conductivity|salinity)\b', 'physical-water-quality'),
    (r'\b(nitrate|nitrite|phosph|ph\b|dissolved oxygen|oxygen|nutrient|metal|'
     r'chlorid|ammonia|pfas|pollut|chemical|pesticid|alkalinity|hardness|'
     r'microplastic|contaminant)', 'chemical-water-quality'),
    (r'\b(macroinvertebrat|invertebrat|bacteri|e\.? ?coli|enterococc|'
     r'coliform|algae|algal|cyanobacteri|bioassessment|biodiversity|fish|'
     r'amphibian|species|biological|diatom|ecolog)', 'biological-water-quality'),
)
WATER_TYPE_RULES = (
    (r'\b(river|stream|lake|pond|wetland|creek|estuar|coast|beach|reservoir|'
     r'waterway|watershed|surface water|canal|lagoon|bay)', 'surface-water'),
    (r'\b(groundwater|well|aquifer|spring)s?\b', 'groundwater'),
    (r'\b(snow|ice|glacier|permafrost)\b', 'snow-ice'),
    (r'\bsoil (water|moisture)\b', 'soil-water'),
)
TECHNOLOGY_RULES = (
    (r'\b(app|smartphone|mobile)\b', 'mobile-applications'),
    (r'\bdrones?\b', 'drones'),
    (r'\b(gis|mapping|map)\b', 'mapping-technology'),
    (r'\bsurvey', 'survey'),
)
AI_RE = re.compile(r'\b(ai|artificial intelligence|machine learning|deep '
                   r'learning|neural|computer vision|image recognition|'
                   r'automated identification)\b', re.I)
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
URL_RE = re.compile(r'^\s*(https?://|www\.)\S+\s*$', re.I)


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def _header_key(value):
    return HEADERS.get(u' '.join((u'%s' % (value or u'')).lower().split()))


def read_xlsx(path, sheet_index=0):
    """Rows of the first sheet as dicts keyed by ``HEADERS`` values.

    Formulas are read as their CACHED value: a handful of Country cells are
    Google-Sheets REGEXEXTRACT formulas whose only usable content is the
    cached result.
    """
    import openpyxl
    book = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheet = book.worksheets[sheet_index]
    rows = sheet.iter_rows(values_only=True)
    header = [_header_key(cell) for cell in next(rows)]
    out = []
    for index, values in enumerate(rows, start=2):
        record = {key: values[pos] for pos, key in enumerate(header)
                  if key and pos < len(values)}
        if not normalise_text(_s(record.get('name'))):
            continue
        record['_row'] = index
        out.append(record)
    return out


def read_json(path):
    with open(path, encoding='utf-8') as handle:
        data = json.load(handle)
    return data['rows'] if isinstance(data, dict) else data


# --------------------------------------------------------------------------- #
# Small normalisers
# --------------------------------------------------------------------------- #

def _s(value):
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return u'%s' % value


def clean(value):
    """Trimmed single-spaced text, or None. Tabs and stray newlines go."""
    text = normalise_text(_s(value))
    if not text:
        return None
    text = u' '.join(text.split())
    if text.lower() in ('none', 'n/a', 'na', 'not specified', 'unknown', '-'):
        return None
    return text


def clean_block(value):
    """Like ``clean`` but keeps paragraph breaks."""
    text = normalise_text(_s(value))
    if not text:
        return None
    lines = [u' '.join(line.split()) for line in text.split(u'\n')]
    text = u'\n'.join(line for line in lines if line)
    if text.lower() in ('none', 'n/a', 'na', 'not specified', 'unknown', '-'):
        return None
    return text


def as_html(text):
    """Plain text -> safe paragraphs. Escaped, so it needs no sanitiser."""
    if not text:
        return None
    return u''.join(u'<p>%s</p>' % html.escape(part)
                    for part in text.split(u'\n') if part.strip())


def website(row):
    """The Website cell as a URL, or None.

    Some cells hold a paper's file name or a phrase ("explore a lake")
    instead of a link; those are not addresses and must not become one.
    """
    text = clean(row.get('website'))
    if not text or u' ' in text or u'.' not in text or text.startswith(
            ('=', '#')):
        return None
    return ensure_scheme(text)


def is_reference(url):
    """A link to a publication about the project, not to the project."""
    path = re.sub(r'[?#].*$', u'', (url or u'').lower())
    return is_publisher(url) or path.endswith(u'.pdf')


def host_of(url):
    url = (url or u'').strip().lower()
    url = re.sub(r'^[a-z]+://', u'', url)
    url = url.split(u'/')[0].split(u'?')[0].split(u':')[0]
    return url[4:] if url.startswith(u'www.') else url


def is_publisher(url):
    host = host_of(url)
    return bool(host) and any(host == p or host.endswith(u'.' + p)
                              for p in PUBLISHER_HOSTS)


def _acronym(text):
    """'WAV', 'S4W', 'BxRCSS', 'CoCoRaHS' -- not 'Wisconsin' or
    'Adopt-A-Stream'."""
    text = (text or u'').strip()
    letters = [c for c in text if c.isalpha()]
    return (bool(letters) and u' ' not in text and len(text) <= 12
            and sum(c.isupper() for c in letters) * 2 >= len(letters))


def name_key(value):
    value = unicodedata.normalize('NFKD', u'%s' % (value or u''))
    value = value.encode('ascii', 'ignore').decode('ascii')
    outside = re.sub(r'\([^)]*\)', u' ', value).strip()
    # An acronym in parentheses -- "(WAV)", "(S4W)" -- does not make two
    # projects different, and neither does spelling out a one-word acronym
    # ("CoCoRaHS (Community Collaborative ...)"). Any other parenthetical is
    # what tells "Water Quality Monitoring (The Gambia)" from "(Zimbabwe)".
    if len(outside.split()) <= 1 and _acronym(outside):
        value = outside
    else:
        value = re.sub(r'\(([^)]*)\)',
                       lambda m: u' ' if _acronym(m.group(1)) else m.group(0),
                       value)
    value = value.lower().replace(u'&', u' and ')
    value = re.sub(r'\b(the|program|programme|project)\b', u' ', value)
    return re.sub(r'[^a-z0-9]', u'', value)


def _split(value, extra=()):
    text = clean(value)
    if not text:
        return []
    pattern = r'\s*(?:,|;|/|\+|＋|\n|\band\b|&' + u''.join(
        u'|' + re.escape(e) for e in extra) + r')\s*'
    return [part.strip(u' .()（）') for part in re.split(pattern, text)
            if part and part.strip(u' .()（）')]


_TERRITORIES = None


def _territories():
    global _TERRITORIES
    if _TERRITORIES is None:
        try:
            from babel import Locale
            names = Locale.parse('en').territories
        except Exception:                      # pragma: no cover
            names = {}
        _TERRITORIES = {
            u'%s' % label: code for code, label in names.items()
            if len(code) == 2 and code.isalpha()}
        _TERRITORIES = {k.lower(): v for k, v in _TERRITORIES.items()}
    return _TERRITORIES


def countries(value, region=None):
    """ISO-3166 alpha-2 codes, plus the parts that did not resolve."""
    codes, unresolved = [], []
    text = clean(value) or u''
    # "Trinidad & Tobago", "Bosnia and Herzegovina": the separator inside a
    # country's own name must not split it in two.
    for label, code in sorted(_territories().items(), key=lambda i: -len(i[0])):
        if u' and ' in label or u' & ' in label:
            pattern = re.escape(label).replace(r'\ and\ ', r'\s*(?:and|&)\s*')
            pattern = pattern.replace(r'\ \&\ ', r'\s*(?:and|&)\s*')
            text, found = re.subn(pattern, u', ' + code + u', ', text, flags=re.I)
            if found and code not in codes:
                codes.append(code)
    for part in _split(text):
        lowered = part.lower()
        if part in codes:
            continue
        lowered = re.sub(r'\s*\(.*$', u'', lowered).strip()
        if not lowered or lowered in NON_COUNTRIES:
            continue
        code = COUNTRY_ALIASES.get(lowered) or _territories().get(lowered)
        if code is None and lowered.startswith('the '):
            code = _territories().get(lowered[4:])
        if code:
            if code not in codes:
                codes.append(code)
        else:
            unresolved.append(part)
    return codes, unresolved


def languages(value):
    """ISO-639 codes for the languages that resolve; the rest verbatim."""
    from ckanext.c4w.logic import legacy

    codes, unresolved = [], []
    for part in _split(value, extra=(u'(', u')', u'（', u'）')):
        lowered = part.lower()
        if not lowered or any(w in lowered for w in (
                'primary', 'possible', 'support', 'publication', 'maybe',
                'bilingual', 'terminology', 'contextual', 'translation',
                'community', 'e.g', 'unspecified', 'multiple', 'with ')):
            # Commentary, not a language. Kept in language_raw.
            continue
        code = legacy.language(part)
        if re.match(r'^[a-z]{2,3}$', code or u''):
            if code not in codes:
                codes.append(code)
        elif lowered not in ('indigenous languages', 'local languages'):
            unresolved.append(part)
    return codes, unresolved


def status(value):
    text = (clean(value) or u'').lower()
    if not text or text.startswith(u'unknown'):
        return u'active', True
    for needles, term in STATUS_MAP:
        if any(text.startswith(n) or n in text for n in needles):
            return term, False
    return u'active', True


def contact(value):
    """(name, email, leftover text). Never invents a name from an email."""
    text = clean(value)
    if not text:
        return None, None, None
    emails = EMAIL_RE.findall(text)
    email = emails[0].rstrip(u'.') if emails else None
    if URL_RE.match(text):
        return None, email, text
    rest = text
    for found in emails:
        rest = rest.replace(found, u' ')
    rest = re.sub(r'\b(e-?mail|email|phone|tel)\s*:?', u' ', rest, flags=re.I)
    rest = u' '.join(rest.strip(u' |,;:-').split()) or None
    name = rest if rest and len(rest) <= 120 else None
    return name, email, text


def _rules(rules, haystack):
    found = []
    for pattern, term in rules:
        if re.search(pattern, haystack, re.I) and term not in found:
            found.append(term)
    return found


def theme_keywords(theme):
    """'Water quality (pollutants)/Biodiversity ...' -> ['Water quality',
    'Pollutants', 'Biodiversity ...']."""
    out = []
    for part in re.split(r'[,/;\n]|\(|\)', clean(theme) or u''):
        part = part.strip(u' .')
        if len(part) < 3:
            continue
        label = part[0].upper() + part[1:]
        if label.lower() not in [o.lower() for o in out]:
            out.append(label)
    return out


# --------------------------------------------------------------------------- #
# Mapping
# --------------------------------------------------------------------------- #

def map_row(row, enrichment=None):
    """One sheet row (+ optional enrichment) -> columns/extras/terms.

    Sheet values always win; enrichment only fills what the sheet left empty.
    """
    enrichment = enrichment or {}
    notes = []
    name = clean(row.get('name'))
    if not name:
        return {'skip': True, 'notes': ['row %s has no name' % row.get('_row')]}
    slug = slugify(name)

    website_url = website(row)
    reference_url = None
    if website_url and is_reference(website_url):
        reference_url, website_url = website_url, None
    if not website_url and enrichment.get('project_url'):
        website_url = ensure_scheme(enrichment['project_url'])

    objectives = clean_block(row.get('objectives'))
    measurement = clean_block(row.get('data_measurement'))
    data_types = clean_block(row.get('data_types'))
    site_description = clean_block(enrichment.get('description'))

    # The sheet always wins. A site's meta description is often about the
    # host organisation, a newsletter or a code repository, so it is only a
    # last resort for a row the sheet left empty.
    description = measurement or objectives or site_description
    aim = objectives or measurement or site_description

    term, unverified = status(row.get('status'))
    author, author_email, contact_raw = contact(row.get('contact'))
    if not author_email and enrichment.get('email'):
        author_email = enrichment['email']

    country_codes, unresolved_countries = countries(row.get('country'))
    region = clean(row.get('region'))
    if unresolved_countries:
        notes.append(u'%s: country not resolved: %s'
                     % (name, u', '.join(unresolved_countries)))
    language_codes, unresolved_languages = languages(row.get('languages'))

    haystack = u' '.join(filter(None, (
        clean(row.get('theme')), measurement, data_types, objectives)))

    data_url = ensure_scheme(clean(row.get('data_portal'))) \
        if clean(row.get('data_portal')) else None
    if not data_url and enrichment.get('data_url'):
        data_url = ensure_scheme(enrichment['data_url'])

    ai_text = u' '.join(filter(None, (clean(row.get('ai_technology')),
                                      clean(row.get('ai_details')))))
    uses_ai = bool(ai_text and AI_RE.search(ai_text))

    citizen = clean_block(row.get('citizen_involvement'))
    columns = {
        'name': name,
        'url': website_url,
        'description': as_html(description),
        'aim': as_html(aim),
        'cs_aspects': as_html(citizen) if citizen and not URL_RE.match(
            citizen) else None,
        'status': term,
        'data_url': data_url,
        'locality': region if region and region.lower() not in [
            c.lower() for c in _split(row.get('country'))] else None,
        'author': author,
        'author_email': author_email,
        'approved': True,
        'moderated': True,
        'hidden': False,
    }

    extras = {
        'import_key': KEY_PREFIX + slug,
        'source': SOURCE,
        'claimable': True,
        'water_parameters': as_html(data_types),
    }
    optional = {
        'issue_area': clean(row.get('issue_area')),
        'focused_theme': clean(row.get('theme')),
        'region': region,
        'partners_text': clean_block(row.get('partners')),
        'sdg_relevance': clean(row.get('sdg')),
        'outcome_type': clean(row.get('outcomes')),
        'source_notes': clean_block(row.get('others')),
        'contact_raw': contact_raw if contact_raw and (
            contact_raw != author and contact_raw != author_email) else None,
        'reference_url': reference_url,
        'country_raw': u', '.join(unresolved_countries) or None,
        'language_raw': clean(row.get('languages'))
        if (unresolved_languages or not language_codes)
        and clean(row.get('languages')) else None,
        'ai_technology_raw': ai_text or None,
        'citizen_involvement_url': citizen if citizen and URL_RE.match(
            citizen) else None,
    }
    extras.update({k: v for k, v in optional.items() if v})
    if unverified:
        extras['status_unverified'] = True
    if uses_ai:
        extras['uses_ai'] = True
        extras['ai_description'] = as_html(ai_text)
    for key in ('description', 'aim'):
        shadow = {'description': description, 'aim': aim}[key]
        if shadow:
            extras[key + '_text'] = shadow
    if enrichment.get('fields'):
        extras['enriched_fields'] = sorted(enrichment['fields'])

    keywords = theme_keywords(row.get('theme')) or [u'Water']
    keywords.append(u'Citizen science')
    topics = [u'water']
    if re.search(r'biodivers', haystack, re.I):
        topics.append(u'biodiversity')
    terms = {
        'keyword': [(normalise_term(k), k) for k in keywords],
        'topic': [(t, t.capitalize()) for t in topics],
        'water_data_type': [(t, t) for t in _rules(WATER_DATA_RULES, haystack)],
        'water_type': [(t, t) for t in _rules(WATER_TYPE_RULES, haystack)],
        'technology_used': [(t, t) for t in _rules(TECHNOLOGY_RULES, haystack)],
        'country': [(c, c) for c in country_codes],
        'language': [(c, c) for c in language_codes],
    }
    if (clean(row.get('country')) or u'').lower() == 'global' or (
            region or u'').lower() == 'global':
        terms['geographic_extent'] = [(u'global', u'Global')]
    terms = {k: v for k, v in terms.items() if v}

    image = None
    if enrichment.get('image'):
        image = {'path': enrichment['image'],
                 'credit': enrichment.get('image_credit')}

    return {
        'key': KEY_PREFIX + slug,
        'slug': slug,
        'columns': columns,
        'extras': extras,
        'terms': terms,
        'image': image,
        'notes': notes,
        'row': row.get('_row'),
    }


def merge_duplicates(rows):
    """Collapse rows naming the same project; later rows fill earlier gaps."""
    merged, order, notes = {}, [], []
    for row in rows:
        key = name_key(row.get('name'))
        host = host_of(row.get('website'))
        match = key if key in merged else None
        if match is None and host and not is_publisher(row.get('website')):
            match = next((k for k, r in merged.items()
                          if _same_site(r, row)), None)
        if match is None:
            merged[key] = dict(row)
            order.append(key)
            continue
        target = merged[match]
        for field, value in row.items():
            if field.startswith('_'):
                continue
            if value not in (None, u'') and target.get(field) in (None, u''):
                target[field] = value
        notes.append(u'row %s (%s) merged into row %s (%s)' % (
            row.get('_row'), clean(row.get('name')), target.get('_row'),
            clean(target.get('name'))))
    return [merged[k] for k in order], notes


def _words(value):
    value = unicodedata.normalize('NFKD', u'%s' % (value or u''))
    value = value.encode('ascii', 'ignore').decode('ascii').lower()
    return set(re.findall(r'[a-z0-9]{3,}', value)) - {
        'the', 'and', 'for', 'program', 'programme', 'project'}


def _similar(a, b):
    """Two names that start alike or share most words ("Florida LAKEWATCH"
    and "LAKEWATCH Florida"). A shared host alone is not enough:
    extension.psu.edu or a state agency hosts many unrelated programmes."""
    if name_key(a)[:8] and name_key(a)[:8] == name_key(b)[:8]:
        return True
    left, right = _words(a), _words(b)
    return bool(left and right) and (
        len(left & right) * 2 >= max(len(left), len(right)))


def _same_site(a, b):
    return (host_of(a.get('website')) == host_of(b.get('website'))
            and _similar(a.get('name'), b.get('name')))


def find_existing(mapped, projects):
    """The existing project a mapped row duplicates, by name or host.

    ``projects`` is ``[(id, name, url, import_key)]``. A project created by a
    previous run of this import is NOT a duplicate -- it is the row to update.
    """
    key = name_key(mapped['columns']['name'])
    host = host_of(mapped['columns'].get('url'))
    for project_id, name, url, import_key in projects:
        if import_key:
            continue
        if name_key(name) == key:
            return project_id, 'name'
        if host and host_of(url) == host and _similar(name, mapped['columns']['name']):
            return project_id, 'website'
    return None


def utcnow():
    return datetime.datetime.utcnow().replace(microsecond=0)


# --------------------------------------------------------------------------- #
# Writing (needs CKAN)
# --------------------------------------------------------------------------- #

# Every vocabulary this import owns. Rewritten wholesale on each run, even
# when the new mapping has no terms for it, so a corrected sheet can REMOVE
# a wrong tag. Vocabularies outside this list are never touched.
MANAGED_VOCABULARIES = ('keyword', 'topic', 'water_data_type', 'water_type',
                        'technology_used', 'country', 'language',
                        'geographic_extent')
# Extras the import owns. Anything else in a row's extras (added by an
# editor, or by another tool) survives a re-run.
MANAGED_EXTRAS = (
    'import_key', 'source', 'water_parameters', 'issue_area',
    'focused_theme', 'region', 'partners_text', 'sdg_relevance',
    'outcome_type', 'source_notes', 'contact_raw', 'reference_url',
    'country_raw', 'language_raw', 'ai_technology_raw',
    'citizen_involvement_url', 'status_unverified', 'uses_ai',
    'ai_description', 'description_text', 'aim_text', 'enriched_fields',
)
BATCH = 50


class SheetImporter(object):
    """Upserts mapped sheet rows into ``c4w_project``."""

    def __init__(self, dry_run=False, media_root=None, enrichment=None,
                 ensure_tables=True):
        self.ensure_tables = ensure_tables
        self.dry_run = dry_run
        self.media_root = media_root
        self.enrichment = enrichment or {}
        self.report = []
        self.notes = []
        self._media = None

    def _commit(self):
        from ckanext.c4w import db
        if self.dry_run:
            db.Session.flush()
        else:
            db.Session.commit()

    def _existing(self):
        from ckanext.c4w import db
        rows = db.Session.query(db.C4wProject).all()
        by_key, others = {}, []
        for obj in rows:
            extras = db.load_extras(obj.extras)
            key = extras.get('import_key')
            if key and key.startswith(KEY_PREFIX):
                by_key[key] = obj
            others.append((obj.id, obj.name, obj.url, key))
        return by_key, others

    def run(self, rows):
        from ckanext.c4w import db

        if self.ensure_tables:
            db.ensure_tables()
        db.ensure_mappers()
        rows, merge_notes = merge_duplicates(rows)
        self.notes.extend(merge_notes)
        by_key, others = self._existing()
        now = utcnow()

        for index, row in enumerate(rows, start=1):
            slug = slugify(clean(row.get('name')) or u'')
            mapped = map_row(row, self.enrichment.get(slug))
            if mapped.get('skip'):
                self.notes.extend(mapped.get('notes', []))
                continue
            self.notes.extend(mapped['notes'])
            obj = by_key.get(mapped['key'])
            entry = {'row': mapped['row'], 'name': mapped['columns']['name'],
                     'key': mapped['key']}
            if obj is None:
                duplicate = find_existing(mapped, others)
                if duplicate:
                    entry.update(action='skip-duplicate',
                                 existing_id=duplicate[0],
                                 matched_on=duplicate[1])
                    self.report.append(entry)
                    continue
                action = 'create'
            else:
                reason = self._protected(obj)
                if reason:
                    entry.update(action='skip-' + reason, slug=obj.slug)
                    self.report.append(entry)
                    continue
                action = 'update'

            obj = self._upsert(obj, mapped, now)
            labels = self._write_terms(obj, mapped['terms'])
            self._write_search_text(obj, mapped, labels)
            image = self._write_image(obj, mapped.get('image'))
            entry.update(action=action, slug=obj.slug, id=obj.id,
                         image=image, terms=sorted(mapped['terms']))
            self.report.append(entry)
            if index % BATCH == 0:
                self._commit()
        self._commit()
        if self.dry_run:
            db.Session.rollback()
        return self.summary()

    @staticmethod
    def _protected(obj):
        """Why an imported row must not be rewritten, or None."""
        from ckanext.c4w import db
        extras = db.load_extras(obj.extras)
        if not extras.get('claimable', True) or extras.get('claimed_by'):
            return 'claimed'
        imported_at = extras.get('imported_at')
        if imported_at and obj.modified:
            try:
                stamp = datetime.datetime.strptime(imported_at,
                                                   '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                stamp = None
            if stamp and obj.modified > stamp + datetime.timedelta(minutes=5):
                return 'edited'
        return None

    def _upsert(self, obj, mapped, now):
        from ckanext.c4w import db

        created = obj is None
        if created:
            obj = db.C4wProject(created=now)
            obj.slug = db.unique_slug(db.C4wProject, mapped['slug'])
        for key, value in mapped['columns'].items():
            setattr(obj, key, value)
        extras = db.load_extras(obj.extras) if not created else {}
        for key in MANAGED_EXTRAS:
            extras.pop(key, None)
        extras.update(mapped['extras'])
        extras['imported_at'] = now.strftime('%Y-%m-%dT%H:%M:%S')
        obj.extras = db.dump_extras(extras)
        obj.modified = now
        db.Session.add(obj)
        db.Session.flush()
        return obj

    def _write_terms(self, obj, terms):
        from ckanext.c4w import constants, db

        (db.Session.query(db.C4wTermLink)
         .filter(db.C4wTermLink.entity_type == 'project',
                 db.C4wTermLink.entity_id == obj.id,
                 db.C4wTermLink.vocabulary.in_(MANAGED_VOCABULARIES))
         .delete(synchronize_session=False))
        labels = []
        for vocabulary in MANAGED_VOCABULARIES:
            seen = set()
            for order, (term, label) in enumerate(terms.get(vocabulary, ())):
                if not term or term in seen:
                    continue
                seen.add(term)
                if constants.vocabulary_terms(vocabulary) is not None:
                    label = constants.label_for(vocabulary, term)
                db.Session.add(db.C4wTermLink(
                    entity_type='project', entity_id=obj.id,
                    vocabulary=vocabulary, term=term, label=label,
                    sort_order=order))
                labels.append(label)
        return labels

    @staticmethod
    def _write_search_text(obj, mapped, labels):
        from ckanext.c4w import db
        from ckanext.c4w.text import html_to_text

        parts = [v for v in mapped['columns'].values()
                 if isinstance(v, str) and len(v) > 2]
        parts += [v for v in mapped['extras'].values()
                  if isinstance(v, str) and len(v) > 2]
        parts += [label for label in labels if label]
        haystack = u' '.join(html_to_text(p) or u'' for p in parts)
        obj.search_text = u' '.join(haystack.split())[:200000] or None
        db.Session.add(obj)

    def _write_image(self, obj, image):
        """Upload the prepared image once; never replace an existing one."""
        if not image or not self.media_root or obj.image1_url:
            return None
        from ckanext.c4w import db
        from ckanext.c4w.migrate import media

        if self._media is None:
            self._media = media.MediaImporter(self.media_root,
                                              dry_run=self.dry_run)
        url = self._media.resolve(image['path'])
        if not url:
            return None
        obj.image1_url = url
        obj.image1_credit = image.get('credit')
        db.Session.add(obj)
        return url

    def summary(self):
        from collections import Counter
        counts = Counter(entry['action'] for entry in self.report)
        return {
            'dry_run': self.dry_run,
            'counts': dict(counts),
            'with_image': sum(1 for e in self.report if e.get('image')),
            'notes': self.notes,
            'media': self._media.report() if self._media else None,
            'rows': self.report,
        }
