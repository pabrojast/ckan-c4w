#!/usr/bin/env python3
"""Prepare the UNESCO citizen-science sheet for ``ckan c4w import-sheet``.

Runs on a workstation, not in the CKAN pod: it needs outbound HTTP and
openpyxl, neither of which the portal image should depend on.

    uvx --with openpyxl --with Pillow --with babel \\
        python scripts/enrich-cs-sheet.py SHEET.xlsx OUTDIR [--no-fetch]

Writes into OUTDIR:

    rows.json       the sheet as JSON (the pod reads this, not the .xlsx)
    enriched.json   slug -> fields found on the project website
    media/koen-cs-2026-11/<slug>.jpg   one 600x400 JPEG per project found
    cache/          fetched pages, so a re-run does not hit the sites again
    summary.csv     one row per project: what was found, what was not

What is fetched, and what is not:

* Only the project's own website. Links to publishers (sciencedirect,
  mdpi, ...) are kept as references and never scraped -- a journal page has
  no project logo and blocks robots anyway.
* robots.txt is honoured; a disallowed page is reported, not fetched.
* Enrichment only FILLS GAPS. The import keeps every value the sheet has.
"""
import argparse
import concurrent.futures
import csv
import hashlib
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir))
from ckanext.c4w.migrate import sheet  # noqa: E402
from ckanext.c4w.text import slugify  # noqa: E402

USER_AGENT = ('Mozilla/5.0 (compatible; C4W-portal-catalogue/1.0; '
              '+https://data.dev-wins.com/citizens4water/)')
TIMEOUT = 15
MAX_PAGE = 8 * 1024 * 1024
MAX_IMAGE = 5 * 1024 * 1024
BOX = (600, 400)

DATA_HOSTS = ('datastream.org', 'waterqualitydata.us', 'gbif.org',
              'inaturalist.org', 'zenodo.org', 'hub.arcgis.com',
              'experience.arcgis.com', 'globe.gov', 'data.gov',
              'hydroshare.org', 'waterreporter.org', 'epicollect.net')
# A data host is only a data link when the PATH says so: datastream.org's
# news page or iNaturalist's "about" are not the project's data.
DATA_PATH = re.compile(r'/(data|datasets?|explorer|dashboard|projects?/|'
                       r'experience/|observations)', re.I)
# Story maps and survey forms live on data hosts but are not data.
NOT_DATA_HOSTS = ('storymaps.arcgis.com', 'survey123.arcgis.com')
NOT_DATA_PATH = re.compile(r'(polic|contributor|donat|documentation|'
                           r'instruction|news|about|privacy|%3a|'
                           r'web-tools/data-access-tools)', re.I)
DATA_TEXT = re.compile(r'\b(data (explorer|portal|viewer|map|download|'
                       r'access|hub|dashboard)|view (the )?data|access (the )?'
                       r'data|download (the )?data|our data|open data)\b',
                       re.I)
GENERIC_DESCRIPTION = re.compile(r'just another wordpress|coming soon|'
                                 r'^home$|^welcome', re.I)


class PageParser(HTMLParser):
    """Collects the handful of things worth reading from a home page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.icons = []
        self.links = []
        self._href = None
        self._text = []
        self.title = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = {k.lower(): (v or '') for k, v in attrs}
        if tag == 'meta':
            key = (attrs.get('property') or attrs.get('name') or '').lower()
            if key and attrs.get('content') and key not in self.meta:
                self.meta[key] = attrs['content'].strip()
        elif tag == 'link':
            rel = attrs.get('rel', '').lower()
            if 'icon' in rel and attrs.get('href'):
                size = re.findall(r'(\d+)x\d+', attrs.get('sizes', ''))
                self.icons.append((int(size[0]) if size else
                                   (180 if 'apple' in rel else 16),
                                   attrs['href']))
        elif tag == 'a':
            self._href = attrs.get('href')
            self._text = []
        elif tag == 'title':
            self._in_title = True

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)
        if self._in_title and self.title is None:
            self.title = data.strip()

    def handle_endtag(self, tag):
        if tag == 'a' and self._href is not None:
            self.links.append((self._href, ' '.join(
                ' '.join(self._text).split())))
            self._href = None
        elif tag == 'title':
            self._in_title = False


_robots = {}


def allowed(url):
    parts = urllib.parse.urlsplit(url)
    root = '%s://%s' % (parts.scheme, parts.netloc)
    if root not in _robots:
        parser = urllib.robotparser.RobotFileParser(root + '/robots.txt')
        try:
            request = urllib.request.Request(root + '/robots.txt',
                                             headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as handle:
                parser.parse(handle.read(200000).decode('utf-8', 'replace')
                             .splitlines())
        except Exception:
            parser = None          # no robots.txt: everything allowed
        _robots[root] = parser
    parser = _robots[root]
    return parser is None or parser.can_fetch(USER_AGENT, url)


def fetch(url, limit, accept='text/html,*/*;q=0.5'):
    request = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT, 'Accept': accept,
        'Accept-Language': 'en'})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as handle:
        body = handle.read(limit + 1)
        if len(body) > limit:
            raise ValueError('too large')
        return handle.geturl(), handle.headers.get_content_type(), body


def page(url, cache_dir):
    name = hashlib.sha1(url.encode()).hexdigest()
    path = os.path.join(cache_dir, name + '.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    if not allowed(url):
        result = {'error': 'robots.txt disallows'}
    else:
        try:
            final, ctype, body = fetch(url, MAX_PAGE)
            if 'html' not in ctype:
                result = {'error': 'not html (%s)' % ctype, 'final': final}
            else:
                result = {'final': final,
                          'html': body.decode('utf-8', 'replace')}
        except Exception as exc:
            result = {'error': '%s: %s' % (type(exc).__name__,
                                           str(exc)[:120])}
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(result, handle)
    return result


def prepare_image(payload):
    """Bytes -> a 600x400 JPEG, or None.

    A photo is cropped to fill the box; a logo or icon (squarish, or small)
    is fitted inside it on white, because cropping a logo cuts the name off.
    """
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(payload)) as probe:
        probe.verify()
    with Image.open(io.BytesIO(payload)) as source:
        if getattr(source, 'n_frames', 1) > 1:
            source.seek(0)
        image = ImageOps.exif_transpose(source).convert('RGBA')
    width, height = image.size
    if min(width, height) < 96:
        return None
    canvas = Image.new('RGB', image.size, 'white')
    canvas.paste(image, mask=image.getchannel('A'))
    ratio = width / float(height)
    if width >= 500 and 1.2 <= ratio <= 2.2:
        result = ImageOps.fit(canvas, BOX, centering=(0.5, 0.5))
    else:
        inner = (520, 320)
        scale = min(inner[0] / float(width), inner[1] / float(height), 2.0)
        resized = canvas.resize((max(1, int(width * scale)),
                                 max(1, int(height * scale))))
        result = Image.new('RGB', BOX, 'white')
        result.paste(resized, ((BOX[0] - resized.size[0]) // 2,
                               (BOX[1] - resized.size[1]) // 2))
    out = io.BytesIO()
    result.save(out, format='JPEG', quality=88)
    return out.getvalue()


def enrich(row, out_dir, cache_dir, found=None):
    """Look up one project. `found` is what a web search reported for it
    (website, data_url, contact_email, status); it only fills gaps."""
    hit = (found or {}).get(slugify(sheet.clean(row.get('name')))) or {}
    record = _enrich(row, out_dir, cache_dir, hit.get('website'))
    if not hit:
        return record
    record['search_evidence'] = hit.get('evidence')
    # A link the search checked against this project beats one guessed from
    # the site's navigation, which is often an agency-wide catalogue.
    searched = sheet.ensure_scheme(hit['data_url']) if hit.get('data_url') else None
    if searched and (sheet.host_of(searched) in NOT_DATA_HOSTS
                     or NOT_DATA_PATH.search(searched)):
        searched = None
    if searched and not sheet.clean(row.get('data_portal')):
        record['data_url'] = searched
        record['fields'].append('data_url')
    if hit.get('contact_email') and not sheet.contact(row.get('contact'))[1] \
            and not record.get('email'):
        record['email'] = hit['contact_email']
        record['fields'].append('author_email')
    if hit.get('status') in ('active', 'completed') and \
            sheet.status(row.get('status'))[1]:
        record['project_status'] = hit['status']
        record['fields'].append('status')
    record['fields'] = sorted(set(record['fields']))
    return record


def _enrich(row, out_dir, cache_dir, found_website=None):
    name = sheet.clean(row.get('name'))
    slug = slugify(name)
    record = {'slug': slug, 'name': name, 'fields': []}
    website = sheet.clean(row.get('website'))
    url = sheet.website(row)
    if found_website and (not url or sheet.is_reference(url)):
        url = sheet.ensure_scheme(found_website)
        record['project_url'] = url
        record['fields'].append('website')
    if not url:
        record['status'] = 'no website' if not website else 'not a URL'
        return record
    if sheet.is_reference(url):
        record['status'] = 'publisher link (reference only)'
        return record

    fetched = page(url, cache_dir)
    if fetched.get('error'):
        record['status'] = fetched['error']
        return record
    base = fetched['final']
    parser = PageParser()
    try:
        parser.feed(fetched['html'])
    except Exception:
        pass
    record['status'] = 'ok'
    record['final_url'] = base

    description = (parser.meta.get('og:description')
                   or parser.meta.get('description')
                   or parser.meta.get('twitter:description'))
    if description and len(description) >= 40 and not \
            GENERIC_DESCRIPTION.search(description):
        record['description'] = ' '.join(description.split())[:1500]
        record['fields'].append('description')

    host = sheet.host_of(base)
    if not sheet.EMAIL_RE.search(sheet.clean(row.get('contact')) or ''):
        for href, _text in parser.links:
            if href and href.lower().startswith('mailto:'):
                email = urllib.parse.unquote(href[7:].split('?')[0]).strip()
                domain = email.rsplit('@', 1)[-1].lower()
                if sheet.EMAIL_RE.fullmatch(email) and (
                        domain == host or host.endswith('.' + domain)
                        or domain.endswith('.' + host)):
                    record['email'] = email
                    record['fields'].append('author_email')
                    break

    if not sheet.clean(row.get('data_portal')):
        for href, text in parser.links:
            if not href or href.startswith(('#', 'mailto:', 'javascript:')):
                continue
            absolute = urllib.parse.urljoin(base, href)
            link_host = sheet.host_of(absolute)
            if link_host in NOT_DATA_HOSTS or NOT_DATA_PATH.search(
                    absolute.split('//', 1)[-1]):
                continue
            path = urllib.parse.urlsplit(absolute).path
            on_host = any(link_host == h or link_host.endswith('.' + h)
                          for h in DATA_HOSTS)
            if (on_host and DATA_PATH.search(path)) or \
                    DATA_TEXT.search(text or ''):
                if absolute.startswith(('http://', 'https://')):
                    record['data_url'] = absolute
                    record['data_url_text'] = text
                    record['fields'].append('data_url')
                    break

    candidates = []
    for key in ('og:image', 'og:image:url', 'og:image:secure_url',
                'twitter:image', 'twitter:image:src'):
        if parser.meta.get(key):
            candidates.append(parser.meta[key])
    for _size, href in sorted(parser.icons, reverse=True):
        if _size >= 96:
            candidates.append(href)
    for candidate in candidates:
        absolute = urllib.parse.urljoin(base, candidate.strip())
        if not absolute.startswith(('http://', 'https://')):
            continue
        try:
            _final, ctype, body = fetch(absolute, MAX_IMAGE,
                                        accept='image/*')
            if not ctype.startswith('image/') and ctype not in (
                    'application/octet-stream', 'binary/octet-stream'):
                continue
            jpeg = prepare_image(body)
        except Exception:
            continue
        if not jpeg:
            continue
        relative = sheet.MEDIA_PREFIX + slug + '.jpg'
        target = os.path.join(out_dir, 'media', relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as handle:
            handle.write(jpeg)
        record['image'] = relative
        record['image_source'] = absolute
        record['image_credit'] = '%s (project website)' % host
        record['fields'].append('image')
        break
    return record


def suggest(out_dir, paths):
    path = os.path.join(os.path.abspath(out_dir), 'enriched.json')
    with open(path, encoding='utf-8') as handle:
        results = json.load(handle)['projects']
    count = 0
    for source in paths:
        with open(source, encoding='utf-8') as handle:
            for hit in json.load(handle):
                if hit.get('confidence') != 'medium' or not (
                        hit.get('website') or hit.get('data_url')):
                    continue
                record = results.setdefault(hit['slug'], {
                    'slug': hit['slug'], 'status': 'suggestion only',
                    'fields': []})
                record['suggestion'] = {
                    'website': hit.get('website'),
                    'data_url': hit.get('data_url'),
                    'evidence': hit.get('evidence')}
                count += 1
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'projects': results}, handle, indent=1, ensure_ascii=False)
    print('suggestions attached: %d' % count)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('sheet')
    parser.add_argument('out_dir')
    parser.add_argument('--no-fetch', action='store_true',
                        help='Only convert the sheet to rows.json.')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--found', nargs='+', metavar='JSON',
                        help='Web-search results ([{slug, website, data_url, '
                        'contact_email, status, confidence}]). Only '
                        '"high" confidence entries are used, only those '
                        'projects are looked up again, and the result is '
                        'merged into the existing enriched.json.')
    parser.add_argument('--suggest', nargs='+', metavar='JSON',
                        help='Web-search results to offer for review: the '
                        '"medium" confidence entries are attached to the '
                        'existing enriched.json as suggestions, without '
                        'fetching anything. The import stores them for the '
                        '"Missing information" screen; they are never '
                        'published as the website.')
    args = parser.parse_args()
    if args.suggest:
        return suggest(args.out_dir, args.suggest)

    out_dir = os.path.abspath(args.out_dir)
    cache_dir = os.path.join(out_dir, 'cache')
    os.makedirs(cache_dir, exist_ok=True)

    rows = sheet.read_xlsx(args.sheet)
    with open(os.path.join(out_dir, 'rows.json'), 'w', encoding='utf-8') as h:
        json.dump({'source': os.path.basename(args.sheet), 'rows': rows}, h,
                  indent=1, default=str, ensure_ascii=False)
    print('rows.json: %d rows' % len(rows))
    if args.no_fetch:
        return

    merged, _notes = sheet.merge_duplicates(rows)
    results, found = {}, None
    if args.found:
        found = {}
        for path in args.found:
            with open(path, encoding='utf-8') as handle:
                for hit in json.load(handle):
                    if hit.get('confidence') == 'high':
                        found[hit['slug']] = hit
        with open(os.path.join(out_dir, 'enriched.json'),
                  encoding='utf-8') as handle:
            results = json.load(handle)['projects']
        merged = [row for row in merged
                  if slugify(sheet.clean(row.get('name'))) in found]
        print('web search: %d projects with a high-confidence match'
              % len(merged))
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(enrich, row, out_dir, cache_dir, found): row
                   for row in merged}
        for done, future in enumerate(
                concurrent.futures.as_completed(futures), 1):
            row = futures[future]
            try:
                record = future.result()
            except Exception as exc:
                record = {'slug': slugify(sheet.clean(row.get('name'))),
                          'name': sheet.clean(row.get('name')),
                          'status': 'error %s' % type(exc).__name__,
                          'fields': []}
            results[record['slug']] = record
            if done % 25 == 0:
                print('  %d/%d' % (done, len(merged)), flush=True)

    with open(os.path.join(out_dir, 'enriched.json'), 'w',
              encoding='utf-8') as handle:
        json.dump({'projects': results}, handle, indent=1, ensure_ascii=False)

    columns = ('slug', 'name', 'status', 'fields', 'final_url',
               'image_source', 'data_url', 'email')
    with open(os.path.join(out_dir, 'summary.csv'), 'w', newline='',
              encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns,
                                extrasaction='ignore')
        writer.writeheader()
        for record in sorted(results.values(), key=lambda r: r['slug']):
            writer.writerow(dict(record, fields=' '.join(record['fields'])))

    from collections import Counter
    statuses = Counter(r['status'].split(':')[0] for r in results.values())
    found = Counter(f for r in results.values() for f in r['fields'])
    print('status:', dict(statuses))
    print('found:', dict(found))


if __name__ == '__main__':
    main()
