"""The UNESCO citizen-science sheet: mapping, duplicates and idempotent upsert."""
import datetime

import pytest

pytest.importorskip('ckan')
from ckanext.c4w import db
from ckanext.c4w.migrate import sheet
from ckanext.c4w.tests.test_project_proposals import env  # noqa: F401


def row(**values):
    base = {
        '_row': 2, 'name': 'Bronx River Stewards (BxRCSS)',
        'issue_area': 'Water in science\n', 'theme': 'Water quality (pollutants)',
        'region': 'USA (New York State)', 'country': 'USA ',
        'data_measurement': 'Assessing physical and chemical characteristics',
        'data_types': 'Flow, turbidity, pH, dissolved oxygen, E. coli',
        'website': 'BronxRiverWater.org',
        'objectives': 'Baseline of water quality for the Bronx River',
        'status': 'Active', 'languages': 'English and Spanish',
        'contact': 'Phone: 870-480-8897 | Email: KRWP@example.org',
    }
    base.update(values)
    return base


def test_map_row_fills_columns_terms_and_provenance():
    mapped = sheet.map_row(row())
    columns, extras, terms = mapped['columns'], mapped['extras'], mapped['terms']
    assert columns['name'] == 'Bronx River Stewards (BxRCSS)'
    assert columns['url'] == 'https://BronxRiverWater.org'
    assert columns['status'] == 'active' and columns['approved']
    assert columns['author_email'] == 'KRWP@example.org'
    assert columns['aim'] == '<p>Baseline of water quality for the Bronx River</p>'
    assert columns['locality'] == 'USA (New York State)'
    assert terms['country'] == [('US', 'US')]
    assert [t for t, _ in terms['language']] == ['en', 'es']
    assert {t for t, _ in terms['water_data_type']} == {
        'water-quantity', 'physical-water-quality', 'chemical-water-quality',
        'biological-water-quality'}
    assert ('citizen-science', 'Citizen science') in terms['keyword']
    assert extras['claimable'] is True
    assert extras['import_key'] == sheet.KEY_PREFIX + mapped['slug']
    assert 'status_unverified' not in extras


def test_publisher_links_and_text_cells_never_become_the_website():
    paper = sheet.map_row(row(website='https://www.mdpi.com/2306-5338/10/6/133'))
    assert paper['columns']['url'] is None
    assert paper['extras']['reference_url'].startswith('https://www.mdpi.com/')
    text = sheet.map_row(row(website='Wendt_2018_Scientist Nonscientist.pdf'))
    assert text['columns']['url'] is None and 'reference_url' not in text['extras']


def test_wrapped_and_multiple_website_links():
    wrapped = sheet.map_row(row(
        website='https://freshwaterwatch.thewaterhub.org/\ncontent/data-map   '))
    assert wrapped['columns']['url'] == 'https://freshwaterwatch.thewaterhub.org/'
    assert wrapped['columns']['data_url'] == \
        'https://freshwaterwatch.thewaterhub.org/content/data-map'
    two = sheet.map_row(row(
        website='https://www.ala.org.au/ https://data.nt.gov.au/dataset/x '))
    assert two['columns']['url'] == 'https://www.ala.org.au/'
    assert two['columns']['data_url'] == 'https://data.nt.gov.au/dataset/x'
    phrase = sheet.map_row(row(website='Explore a lake (link expired)'))
    assert phrase['columns']['url'] is None


def test_web_search_fills_website_and_status_only_when_missing():
    found = {'project_url': 'https://project.example.org/',
             'project_status': 'completed', 'fields': ['website', 'status']}
    paper = sheet.map_row(row(website='https://www.mdpi.com/x', status=None),
                          found)
    assert paper['columns']['url'] == 'https://project.example.org/'
    assert paper['extras']['reference_url'] == 'https://www.mdpi.com/x'
    assert paper['columns']['status'] == 'completed'
    assert 'status_unverified' not in paper['extras']
    sheet_wins = sheet.map_row(row(status='Active'), found)
    assert sheet_wins['columns']['url'] == 'https://BronxRiverWater.org'
    assert sheet_wins['columns']['status'] == 'active'


def test_unknown_status_and_regions_are_flagged_not_guessed():
    mapped = sheet.map_row(row(status=None, country='Global', region='Global'))
    assert mapped['columns']['status'] == 'active'
    assert mapped['extras']['status_unverified'] is True
    assert 'country' not in mapped['terms']
    assert mapped['terms']['geographic_extent'] == [('global', 'Global')]
    assert sheet.countries('Japan and USA')[0] == ['JP', 'US']
    assert sheet.countries('UK and Taiwan')[0] == ['GB', 'TW']
    assert sheet.countries('Trinidad & Tobago') == (['TT'], [])
    assert sheet.countries('Bosnia and Herzegovina, Italy') == (['BA', 'IT'], [])


def test_existing_project_with_reordered_name_and_same_site_is_a_duplicate():
    mapped = sheet.map_row(row(name='Florida LAKEWATCH',
                               website='https://lakewatch.ifas.ufl.edu/'))
    existing = [('p1', 'LAKEWATCH Florida', 'https://lakewatch.ifas.ufl.edu/', None),
                ('p2', 'Citrus Survey', 'https://lakewatch.ifas.ufl.edu/x', None)]
    assert sheet.find_existing(mapped, existing) == ('p1', 'website')
    other = sheet.map_row(row(name='Lake Apopka Restoration',
                              website='https://lakewatch.ifas.ufl.edu/'))
    assert sheet.find_existing(other, existing) is None


def test_enrichment_only_fills_gaps():
    found = {'description': 'Site text', 'email': 'site@example.org',
             'data_url': 'https://example.org/data', 'image': 'x/y.jpg',
             'fields': ['description', 'author_email', 'data_url', 'image']}
    mapped = sheet.map_row(row(), found)
    assert mapped['columns']['author_email'] == 'KRWP@example.org'
    assert 'Site text' not in mapped['columns']['description']
    assert mapped['columns']['data_url'] == 'https://example.org/data'
    assert mapped['image']['path'] == 'x/y.jpg'
    empty = sheet.map_row(row(contact=None, data_measurement=None,
                              objectives=None), found)
    assert empty['columns']['author_email'] == 'site@example.org'
    assert empty['columns']['description'] == '<p>Site text</p>'


def test_duplicates_merge_acronyms_but_keep_distinct_places_apart():
    rows = [row(_row=2, name='Water Action Volunteers (WAV)', contact=None),
            row(_row=3, name='Water Action Volunteers', contact='a@b.org'),
            row(_row=4, name='Water Quality Monitoring (The Gambia)', website=None),
            row(_row=5, name='Water Quality Monitoring (Zimbabwe)', website=None)]
    merged, notes = sheet.merge_duplicates(rows)
    assert [r['_row'] for r in merged] == [2, 4, 5]
    assert merged[0]['contact'] == 'a@b.org' and len(notes) == 1


def test_import_is_idempotent_skips_existing_and_protects_claimed(env):
    db.Session.add(db.C4wProject(slug='lakewatch', name='Florida LAKEWATCH',
                                 approved=True, extras='{}'))
    db.Session.commit()
    rows = [row(), row(_row=3, name='Florida LAKEWATCH', website=None)]
    first = sheet.SheetImporter(ensure_tables=False).run(rows)
    assert first['counts'] == {'create': 1, 'skip-duplicate': 1}
    project = db.Session.query(db.C4wProject).filter(
        db.C4wProject.name.like('Bronx%')).one()
    slug = project.slug
    links = db.Session.query(db.C4wTermLink).filter_by(
        entity_id=project.id, vocabulary='country').all()
    assert [link.term for link in links] == ['US']
    assert 'bronx' in project.search_text.lower()

    second = sheet.SheetImporter(ensure_tables=False).run(
        [row(objectives='Corrected objectives')])
    assert second['counts'] == {'update': 1}
    db.Session.refresh(project)
    assert project.slug == slug and 'Corrected' in project.aim
    assert db.Session.query(db.C4wProject).count() == 2

    extras = db.load_extras(project.extras)
    extras.update(claimable=False, claimed_by=['owner'])
    project.extras = db.dump_extras(extras)
    db.Session.commit()
    third = sheet.SheetImporter(ensure_tables=False).run(
        [row(objectives='Would overwrite the manager')])
    assert third['counts'] == {'skip-claimed': 1}
    db.Session.refresh(project)
    assert 'Corrected' in project.aim


def test_edited_rows_are_left_alone(env):
    sheet.SheetImporter(ensure_tables=False).run([row()])
    project = db.Session.query(db.C4wProject).one()
    project.modified = project.modified + datetime.timedelta(days=1)
    db.Session.commit()
    result = sheet.SheetImporter(ensure_tables=False).run([row()])
    assert result['counts'] == {'skip-edited': 1}


def test_dry_run_writes_nothing(env):
    result = sheet.SheetImporter(dry_run=True, ensure_tables=False).run([row()])
    assert result['counts'] == {'create': 1}
    assert db.Session.query(db.C4wProject).count() == 0
