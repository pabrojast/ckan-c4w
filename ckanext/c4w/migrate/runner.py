# encoding: utf-8
"""Orchestrates the one-way import from the legacy Django database.

Shape of a run: read the lookup tables, then each entity in dependency order,
mapping every row and upserting it by ``legacy_id``. Vocabulary links and
entity relations are rewritten wholesale per row. Media is a separate pass at
the end, once every row that references a file exists.

Three properties the design turns on.

IDEMPOTENT. Every row is keyed by ``legacy_id``, and ``id`` and ``slug`` are
assigned once and never recomputed -- so a second run updates in place and no
URL moves. Link rows are deleted and reinserted per entity and vocabulary, with
the unique constraint as the backstop.

RESUMABLE. Commits happen per batch, not once at the end, so a failure halfway
through keeps what it did and the next run picks up from there.

FAIL-FAST ON SANITISATION. The importer refuses to start without bleach.
sanitize.py fails CLOSED -- it strips every tag when bleach is missing -- which
is the right runtime safety net for a web request and catastrophic here,
because sanitisation happens BEFORE storage and would silently write 113 rows
of tagless text with no error to notice.
"""
import logging

from ckanext.c4w import db
from ckanext.c4w.migrate import mapping, media as media_module, source
from ckanext.c4w.text import html_to_text, normalise_term, slugify

log = logging.getLogger(__name__)

BATCH_SIZE = 200

# The order slugs are assigned in. Where two rows share a name, the one the
# public can actually see should win the un-suffixed URL -- otherwise the
# canonical address belongs to a hidden draft and the visible twin gets
# "-2".
SLUG_PRIORITY = ('approved', 'moderated', 'total_accesses')


class PreflightError(Exception):
    """A condition that must hold before the first write."""


def preflight():
    """Refuse to start if the run would silently corrupt what it imports."""
    problems = []
    try:
        import bleach  # noqa: F401
    except ImportError:
        problems.append(
            'bleach is not installed. The sanitiser fails closed and would '
            'strip every tag from every imported description and post body, '
            'before storage, with no error.')
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        problems.append('Pillow is not installed; media cannot be verified.')
    if problems:
        raise PreflightError('\n'.join(problems))


class Runner(object):

    def __init__(self, dsn, media_root=None, only=None, dry_run=False,
                 since=None, fallback_user=None):
        self.dsn = dsn
        self.media_root = media_root
        self.only = tuple(only) if only else None
        self.dry_run = dry_run
        self.since = since
        self.fallback_user = fallback_user
        self.report = {
            'entities': {},
            'notes': [],
            'unresolved_users': [],
            'terms_outside_vocabulary': {},
            'media': {},
            'skipped': [],
        }

    # --- helpers ---------------------------------------------------------- #

    def _wanted(self, entity):
        return self.only is None or entity in self.only

    def _commit(self):
        from ckan.model.meta import Session
        if self.dry_run:
            Session.flush()
        else:
            Session.commit()

    def _note(self, message):
        self.report['notes'].append(message)

    # --- the run ---------------------------------------------------------- #

    def run(self, reader=None, resolver=None):
        """Import everything.

        ``reader`` and ``resolver`` are injectable so the whole orchestration
        can be exercised against the corpus fixture without a live Django
        database or a configured CKAN site -- which is what makes the
        end-to-end behaviour (upsert, slug stability, link rewriting,
        idempotency) testable at all.
        """
        preflight()
        db.ensure_tables()

        if reader is not None:
            return self._run_with(reader, resolver)
        with source.connect(self.dsn) as connected:
            return self._run_with(connected, resolver)

    def _run_with(self, reader, resolver=None):
        lookups = reader.lookups()
        if resolver is None:
            resolver = self._make_resolver(reader)

        if self._wanted('resource'):
            self._import_categories(reader)

        for entity in source.IMPORT_ORDER:
            if not self._wanted(entity):
                continue
            self._import_entity(entity, reader, lookups, resolver)

        if self._wanted('project'):
            self._import_project_countries(reader)

        self._import_media(reader)

        self.report['unresolved_users'] = resolver.report()
        return self.report

    def _make_resolver(self, reader):
        from ckanext.c4w.migrate.users import UserResolver

        fallback_id = None
        if self.fallback_user:
            from ckan import model
            from ckan.model.meta import Session
            user = (Session.query(model.User)
                    .filter(model.User.name == self.fallback_user).first())
            if user is None:
                self._note('fallback user %r does not exist in CKAN; '
                           'unresolved rows will have no creator'
                           % self.fallback_user)
            else:
                fallback_id = user.id
        return UserResolver(reader.users(), fallback_user_id=fallback_id)

    # --- categories ------------------------------------------------------- #

    def _import_categories(self, reader):
        """Two passes: rows first, then parent links and paths.

        The tree is self-referencing, so a single pass would have to insert
        children before their parents exist.
        """
        from ckan.model.meta import Session

        rows = reader.categories()
        by_legacy = {}
        for row in rows:
            existing = (Session.query(db.C4wCategory)
                        .filter(db.C4wCategory.legacy_id == row['id']).first())
            if existing is None:
                existing = db.C4wCategory(legacy_id=row['id'])
            existing.text = (row.get('text') or u'').strip()
            Session.add(existing)
            by_legacy[row['id']] = existing
        Session.flush()

        for row in rows:
            node = by_legacy[row['id']]
            parent = by_legacy.get(row.get('parent_id'))
            node.parent_id = parent.id if parent else None
            node.depth = 1 if parent else 0
            # "Text : Report" -- one indexed LIKE resolves a whole branch.
            node.path = (u'%s : %s' % (parent.text, node.text)
                         if parent else node.text)
            Session.add(node)
        self._commit()
        self.report['entities']['category'] = {'imported': len(rows)}

    # --- entities --------------------------------------------------------- #

    def _import_entity(self, entity, reader, lookups, resolver):
        from ckan.model.meta import Session

        model_cls = db.ENTITY_CLASSES[entity]
        mapper = mapping.MAPPERS[entity]
        term_links = reader.term_links(entity)
        relation_links = reader.relation_links(entity)

        rows = list(reader.entity_rows(entity))
        rows = self._filter_since(rows)
        # Assign slugs to the most visible row first, so a duplicate name does
        # not hand the canonical URL to a hidden draft.
        rows.sort(key=self._slug_priority)

        imported = 0
        for index, row in enumerate(rows, start=1):
            result = mapper(row, lookups)
            if result.get('skip'):
                self.report['skipped'].extend(result.get('notes', []))
                continue
            self.report['notes'].extend(result.get('notes', []))

            obj = self._upsert(entity, model_cls, result, resolver)
            labels = self._write_terms(entity, obj, result, row, term_links,
                                       lookups)
            self._write_relations(entity, obj, row, relation_links, resolver)
            self._write_search_text(entity, obj, result, labels)
            self._stash_media(entity, obj, result)
            imported += 1
            if index % BATCH_SIZE == 0:
                self._commit()
        self._commit()
        self.report['entities'][entity] = {'imported': imported,
                                           'seen': len(rows)}

    def _filter_since(self, rows):
        """Keep rows changed at or after ``--since``.

        Compared as DATETIMES. Comparing the string forms looks like it works
        and does not: str(datetime) is '2026-08-29 03:42:00+00:00' while an
        operator types '2026-08-29T00:00:00', so the two diverge at the 'T'
        and every row changed that day is dropped -- with the report saying
        "seen 0", which reads like "nothing changed".

        A row with no timestamp is always kept: it cannot be shown to be
        older, and dropping it would lose data silently.
        """
        if not self.since:
            return rows
        cutoff = mapping._naive_utc(self.since)
        if cutoff is None:
            raise ValueError(
                '--since is not a timestamp I can parse: %r' % self.since)
        kept = []
        for row in rows:
            stamp = mapping._naive_utc(
                row.get('dateUpdated') or row.get('updated_on'))
            if stamp is None or stamp >= cutoff:
                kept.append(row)
        return kept

    @staticmethod
    def _slug_priority(row):
        return (
            0 if row.get('approved') else 1,
            0 if row.get('moderated') else 1,
            -int(row.get('totalAccesses') or 0),
            int(row.get('id') or 0),
        )

    def _upsert(self, entity, model_cls, result, resolver):
        from ckan.model.meta import Session

        columns = result['columns']
        obj = (Session.query(model_cls)
               .filter(model_cls.legacy_id == columns['legacy_id']).first())
        created = obj is None
        if created:
            obj = model_cls(legacy_id=columns['legacy_id'])

        media_targets = set(source.MEDIA_COLUMNS.get(entity, {}).values())
        for key, value in columns.items():
            if key == 'slug':
                continue
            # The mapper leaves media URLs None because the media pass fills
            # them later. Writing that None back would erase the image on
            # every re-run -- including the delta run at cutover, which the
            # runbook deliberately runs without --media-root.
            if key in media_targets and value is None:
                continue
            # Same for the two counters the live portal owns: total_accesses
            # keeps counting after the import, and re-running must not reset
            # it to whatever Django last recorded.
            if key in ('total_accesses',) and getattr(obj, key, None):
                continue
            if hasattr(obj, key):
                setattr(obj, key, value)

        # The slug is assigned ONCE. Renaming an entity must not move its URL:
        # that breaks every inbound link for a cosmetic edit.
        if not getattr(obj, 'slug', None):
            base = columns.get('slug') or slugify(
                columns.get('name') or columns.get('title') or u'')
            obj.slug = db.unique_slug(model_cls, base,
                                      exclude_id=getattr(obj, 'id', None))

        creator = resolver.resolve(result.get('legacy_author'))
        if hasattr(obj, 'created_by'):
            obj.created_by = creator
        elif hasattr(obj, 'author_id'):
            obj.author_id = creator

        obj.extras = db.dump_extras(result.get('extras') or {})
        Session.add(obj)
        Session.flush()
        return obj

    # --- links ------------------------------------------------------------ #

    def _write_terms(self, entity, obj, result, row, term_links, lookups):
        """Rewrite this row's vocabulary links.

        Deleted and reinserted per vocabulary rather than diffed: the corpus
        is small, the unique constraint guards it, and a diff would have to be
        right about ordering too.

        Returns the labels written, so the caller can build the search
        haystack without re-reading them -- see _write_search_text.
        """
        from ckan.model.meta import Session
        from ckanext.c4w import constants

        wanted = {}
        for vocabulary, values in (result.get('terms') or {}).items():
            wanted[vocabulary] = list(values)

        legacy_terms = term_links.get(row.get('id'), {})
        for vocabulary, ids in legacy_terms.items():
            table = lookups.get(vocabulary) or {}
            pairs = []
            for legacy_id in ids:
                # Looked up both ways: psycopg2 gives integer keys and a JSON
                # export gives strings, and matching only one silently drops
                # every link in the vocabulary.
                label = table.get(legacy_id)
                if label is None:
                    label = table.get(u'%s' % legacy_id)
                if not label:
                    continue
                pairs.append((normalise_term(label), label))
            if pairs:
                # resource_keyword and keyword are one vocabulary on the c4w
                # side: a keyword is a keyword whichever table it came from.
                target = 'keyword' if vocabulary == 'resource_keyword' \
                    else vocabulary
                wanted.setdefault(target, []).extend(pairs)

        written_labels = []
        for vocabulary, pairs in wanted.items():
            declared = constants.vocabulary_terms(vocabulary)
            if declared is not None:
                for term, label in pairs:
                    if term not in declared:
                        (self.report['terms_outside_vocabulary']
                         .setdefault(vocabulary, set()).add(u'%s (%s)'
                                                            % (term, label)))
            (Session.query(db.C4wTermLink)
             .filter(db.C4wTermLink.entity_type == entity,
                     db.C4wTermLink.entity_id == obj.id,
                     db.C4wTermLink.vocabulary == vocabulary)
             .delete(synchronize_session=False))
            seen = set()
            order = 0
            for term, label in pairs:
                if not term or term in seen:
                    continue
                seen.add(term)
                Session.add(db.C4wTermLink(
                    entity_type=entity, entity_id=obj.id,
                    vocabulary=vocabulary, term=term, label=label,
                    sort_order=order))
                written_labels.append(label)
                order += 1
        return written_labels

    def _write_search_text(self, entity, obj, result, labels=()):
        """Build the plain-text haystack the listing search reads.

        Every long-form field with its markup and entities resolved, plus
        every vocabulary LABEL -- so a visitor searching a keyword, a funding
        body or an author finds the row, exactly as the Django site did, and a
        phrase that spans a tag or an &nbsp; still matches.
        """
        from ckan.model.meta import Session

        if not hasattr(obj, 'search_text'):
            return
        parts = []
        for value in (result.get('columns') or {}).values():
            if isinstance(value, str) and len(value) > 2:
                parts.append(value)
        for key, value in (result.get('extras') or {}).items():
            if isinstance(value, str) and len(value) > 2:
                parts.append(value)
        # Passed in rather than re-queried: CKAN's session has autoflush
        # DISABLED, so the links written moments ago are invisible to a query
        # until something forces a flush -- and the haystack silently came out
        # with no vocabulary labels in it at all.
        parts.extend(label for label in labels if label)

        haystack = u' '.join(html_to_text(part) or u'' for part in parts)
        obj.search_text = u' '.join(haystack.split())[:200000] or None
        Session.add(obj)

    def _write_relations(self, entity, obj, row, relation_links, resolver):
        from ckan.model.meta import Session

        links = relation_links.get(row.get('id'), [])
        # Single-valued foreign keys are columns, but they arrive as legacy
        # ids and have to be translated to c4w ids here.
        for column, target_entity, legacy_key in (
                ('main_organisation_id', 'organisation', 'mainOrganisation_id'),
                ('project_id', 'project', 'project_id')):
            if not hasattr(obj, column):
                continue
            legacy_id = row.get(legacy_key)
            setattr(obj, column,
                    self._resolve_entity(target_entity, legacy_id))

        # The category is not an ENTITY_CLASSES member -- it has no slug and no
        # public surface of its own -- so it is resolved directly.
        if hasattr(obj, 'category_id') and row.get('category_id') is not None:
            match = (Session.query(db.C4wCategory.id)
                     .filter(db.C4wCategory.legacy_id
                             == row['category_id']).first())
            obj.category_id = match[0] if match else None

        (Session.query(db.C4wRelation)
         .filter(db.C4wRelation.subject_type == entity,
                 db.C4wRelation.subject_id == obj.id)
         .delete(synchronize_session=False))
        order = 0
        seen = set()
        for predicate, object_type, legacy_id in links:
            if object_type == 'user':
                object_id = resolver.resolve(legacy_id)
            else:
                object_id = self._resolve_entity(object_type, legacy_id)
            if not object_id:
                continue
            key = (predicate, object_type, object_id)
            if key in seen:
                continue
            seen.add(key)
            Session.add(db.C4wRelation(
                subject_type=entity, subject_id=obj.id, predicate=predicate,
                object_type=object_type, object_id=object_id,
                sort_order=order))
            order += 1
        Session.add(obj)

    def _resolve_entity(self, entity, legacy_id):
        from ckan.model.meta import Session

        if legacy_id is None:
            return None
        model_cls = db.ENTITY_CLASSES.get(entity)
        if model_cls is None:
            return None
        row = (Session.query(model_cls.id)
               .filter(model_cls.legacy_id == legacy_id).first())
        return row[0] if row else None

    # --- media ------------------------------------------------------------ #

    def _stash_media(self, entity, obj, result):
        self._media_jobs = getattr(self, '_media_jobs', [])
        for legacy_column, path in (result.get('media') or {}).items():
            if not path:
                continue
            target = source.MEDIA_COLUMNS.get(entity, {}).get(legacy_column)
            if target:
                self._media_jobs.append((entity, obj.id, target, path))
        # An inline image has no column to fill: uploading it and recording
        # the map row is the whole job, because the body still points at the
        # legacy path and /citizens4water/media/<path> resolves it from there.
        for path in (result.get('inline_media') or ()):
            self._media_jobs.append((entity, obj.id, None, path))

    def _import_media(self, reader):
        from ckan.model.meta import Session

        jobs = getattr(self, '_media_jobs', [])
        importer = media_module.MediaImporter(self.media_root,
                                              dry_run=self.dry_run)
        for index, (entity, object_id, column, path) in enumerate(jobs, 1):
            url = importer.resolve(path)
            if not url or column is None:
                continue
            model_cls = db.ENTITY_CLASSES[entity]
            obj = Session.query(model_cls).filter(
                model_cls.id == object_id).first()
            if obj is not None and hasattr(obj, column):
                setattr(obj, column, url)
                Session.add(obj)
            if index % BATCH_SIZE == 0:
                self._commit()

        importer.map_thumbnails(self._thumbnail_rows(reader))
        self._commit()
        self.report['media'] = importer.report()

    @staticmethod
    def _thumbnail_rows(reader):
        """(derivative, original) pairs from easy_thumbnails, if present."""
        try:
            return [(row['name'], row['source'])
                    for row in reader.rows(
                        'SELECT t.name, s.name AS source '
                        'FROM easy_thumbnails_thumbnail t '
                        'JOIN easy_thumbnails_source s ON s.id = t.source_id')]
        except Exception:
            # The table is optional; its absence is not an error.
            return []

    # --- project countries ------------------------------------------------ #

    def _import_project_countries(self, reader):
        """Country codes as term links, with their points in extras.

        The point list is NOT parallel to the term list -- each point carries
        its own ``code``, so consumers join on that rather than on position,
        and a country dropped from one list cannot silently shift the other.
        """
        from ckan.model.meta import Session

        catalogue = reader.project_countries()
        for legacy_id, entries in catalogue.items():
            obj = (Session.query(db.C4wProject)
                   .filter(db.C4wProject.legacy_id == legacy_id).first())
            if obj is None:
                continue
            (Session.query(db.C4wTermLink)
             .filter(db.C4wTermLink.entity_type == 'project',
                     db.C4wTermLink.entity_id == obj.id,
                     db.C4wTermLink.vocabulary == 'country')
             .delete(synchronize_session=False))
            points, seen, order = [], set(), 0
            for entry in entries:
                code = entry.get('code')
                if not code or len(code) != 2 or code in seen:
                    continue
                seen.add(code)
                Session.add(db.C4wTermLink(
                    entity_type='project', entity_id=obj.id,
                    vocabulary='country', term=code, label=code,
                    sort_order=order))
                order += 1
                if entry.get('lat') is not None:
                    points.append({'code': code, 'lat': entry['lat'],
                                   'lon': entry['lon']})
            extras = db.load_extras(obj.extras)
            if points:
                extras['country_points'] = points
            obj.extras = db.dump_extras(extras)
            Session.add(obj)
        self._commit()

        # Worth saying out loud rather than leaving as an empty key: every
        # projects_projectcountry row in production has a NULL latitude --
        # the update_countries_coordinates management command was never run --
        # so there are no per-country points to carry, and anything that wants
        # to draw a map will have to source centroids elsewhere.
        placed = sum(1 for entries in catalogue.values()
                     for entry in entries if entry.get('lat') is not None)
        if catalogue and not placed:
            self._note('no project country carries coordinates in the source; '
                       'country_points is empty and a map needs centroids '
                       'from elsewhere')


def summarise(report):
    """Flatten the report for printing, turning sets into sorted lists."""
    out = dict(report)
    out['terms_outside_vocabulary'] = {
        vocabulary: sorted(values)
        for vocabulary, values in report.get(
            'terms_outside_vocabulary', {}).items()}
    return out
