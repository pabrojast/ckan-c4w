#!/usr/bin/env python3
"""Guarded, idempotent repair of one imported event (dry run by default).

Back up the target database first. The source date is documented at
https://www.scivil.be/en/event/citizen-science-fair . No other fields change.
Run inside the matching CKAN container with --config, --expected-site-url,
and --apply only after checking the dry-run result.
"""
import argparse
import configparser
import datetime
import json
import os
import psycopg2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--expected-site-url', required=True,
                        choices=['https://data.dev-wins.com', 'https://ihp-wins.unesco.org'])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    config = configparser.ConfigParser(interpolation=None)
    config.read(args.config)
    section = config['app:main']
    assert (os.environ.get('CKAN_SITE_URL') or section['ckan.site_url']) == args.expected_site_url
    connection = psycopg2.connect(os.environ.get('CKAN_SQLALCHEMY_URL') or section['sqlalchemy.url'],
        connect_timeout=15, options='-c lock_timeout=5000 -c statement_timeout=60000')
    event_id = 'c8276ff2-997a-42e2-9a2f-2ae65e46caae'
    expected = datetime.datetime(2925, 6, 18)
    corrected = datetime.datetime(2025, 6, 18)
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT row_to_json(t) FROM c4w_event t WHERE id=%s FOR UPDATE', (event_id,))
                found = cursor.fetchone()
                assert found, 'Expected event does not exist'
                before = found[0]
                assert before['slug'] == 'citizen-science-fair' and before['title'] == 'Citizen Science Fair'
                assert datetime.datetime.fromisoformat(before['start_date']) == corrected
                current = datetime.datetime.fromisoformat(before['end_date'])
                assert current in (expected, corrected), 'Unexpected end date; inspect before changing it'
                cursor.execute("SELECT payload FROM c4w_content_revision WHERE entity_type='event' AND entity_id=%s", (event_id,))
                for payload, in cursor.fetchall():
                    assert not str(json.loads(payload).get('end_date', '')).startswith('2925-'), 'A draft also contains the old date; review it separately'
                if args.apply and current == expected:
                    cursor.execute('UPDATE c4w_event SET end_date=%s WHERE id=%s AND end_date=%s', (corrected, event_id, expected))
                    assert cursor.rowcount == 1
                cursor.execute('SELECT row_to_json(t) FROM c4w_event t WHERE id=%s', (event_id,))
                after = cursor.fetchone()[0]
                assert {k:v for k,v in before.items() if k!='end_date'} == {k:v for k,v in after.items() if k!='end_date'}
                print(json.dumps(dict(applied=args.apply, changed=before!=after, event_id=event_id,
                    before=before['end_date'], after=after['end_date'], other_fields_preserved=True)))
    finally:
        connection.close()


if __name__ == '__main__':
    main()
