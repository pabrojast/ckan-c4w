from copy import deepcopy

import pytest

from ckanext.c4w.archive.presentation import COPY, detail_dataset


@pytest.mark.parametrize('language', ['en', 'es', 'fr', 'ar'])
def test_about_uses_observed_manifest_counts_and_preserves_legacy(language):
    dataset = {'record_count': 42, 'site_count': 2, 'parameter_count': 1,
               'temporal_end': '2023-12-31', 'grain': 'year',
               'license_id': 'notspecified', 'data_files': [{'id': 'legacy-file'}],
               'bundle_generation': 7, 'bundle_ready': True}
    original = deepcopy(dataset)
    manifest = {'version': 'v3', 'observations': 110, 'observed_parameter_codes': 3,
                'dimensions': {'station_ids': 8, 'countries': ['France', 'Argentina']},
                'parameters': [{'min_year': 1906, 'max_year': 2024},
                               {'min_year': None, 'max_year': None}],
                'license': 'CC-BY-4.0', 'doi': '10.5281/zenodo.18459694',
                'source_url': 'https://example.org/archive.zip', 'zip_bytes': 12345}
    updated, archive = detail_dataset(dataset, manifest, language)
    assert dataset == original
    assert (updated['record_count'], updated['site_count'], updated['parameter_count']) == (110, 8, 3)
    assert (updated['temporal_start'], updated['temporal_end']) == ('1906', '2024')
    assert updated['grain'] == ''
    assert updated['license_id'] == 'CC-BY-4.0'
    assert updated['data_files'] == original['data_files']
    assert updated['bundle_generation'] == 7
    assert updated['bundle_ready'] is True
    assert archive['labels'] == COPY[language]
    assert archive['countries'] == ['France', 'Argentina']
    assert '<' not in updated['data_quality_note']  # rich-text template is safe
    assert all(set(values) == set(COPY['en']) for values in COPY.values())
