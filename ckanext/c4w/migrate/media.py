# encoding: utf-8
"""Re-hosting the legacy media files.

Each Django ImageField holds a path relative to the old MEDIA_ROOT. This
module reads the file, verifies it really is an image, uploads it through
CKAN's uploader -- which ckanext-asset-storage intercepts, so it lands in the
object store the portal already uses -- and records the mapping.

``c4w_media_map`` does two jobs. It makes the pass IDEMPOTENT: a second run
finds the row and skips the upload rather than filling the store with
duplicates. And it backs the ``/citizens4water/media/<path>`` redirect, so an
inbound link to an old image keeps resolving after the cutover.

THE THUMBNAIL PROBLEM. The URLs the public site actually served for logos were
never the stored path -- Django rendered every one through easy_thumbnails,
which writes a derivative alongside the original and links to THAT. So every
cached page, search result and shared screenshot points at a derivative path,
and mapping only the originals leaves all of them 404. The derivative paths
are therefore recorded too, all pointing at the single re-uploaded original:
the image is the same picture, and a reader following an old link wants the
picture, not the exact pixel dimensions.

A missing file is reported and skipped, never fatal. Some rows reference files
that are simply gone, and aborting the whole migration over one of them would
be the wrong trade.
"""
import hashlib
import logging
import os

log = logging.getLogger(__name__)

UPLOAD_TO = 'c4w'
PUBLIC_PREFIX = '/uploads/{0}/'.format(UPLOAD_TO)

# Pillow format -> the extension the stored file gets.
IMAGE_EXTENSIONS = {
    'JPEG': '.jpg', 'PNG': '.png', 'GIF': '.gif', 'WEBP': '.webp',
    'BMP': '.bmp', 'TIFF': '.tiff',
}


class MediaImporter(object):
    """Uploads legacy media once and remembers where each file went."""

    def __init__(self, media_root, dry_run=False, max_size_mb=None):
        self.media_root = media_root
        self.dry_run = dry_run
        self.max_size_mb = max_size_mb
        self.missing = []          # paths with no file behind them
        self.failed = []           # (path, reason)
        self.uploaded = 0
        self.reused = 0
        self.thumbnails_mapped = 0
        self._cache = {}           # legacy_path -> new_url

    # --- the map ---------------------------------------------------------- #

    def _existing(self, legacy_path):
        from ckan.model.meta import Session
        from ckanext.c4w import db

        db.ensure_mappers()
        row = (Session.query(db.C4wMediaMap)
               .filter(db.C4wMediaMap.legacy_path == legacy_path).first())
        return row.new_url if row is not None else None

    def _record(self, legacy_path, new_url, digest=None):
        from ckan.model.meta import Session
        from ckanext.c4w import db

        db.ensure_mappers()
        row = (Session.query(db.C4wMediaMap)
               .filter(db.C4wMediaMap.legacy_path == legacy_path).first())
        if row is None:
            row = db.C4wMediaMap(legacy_path=legacy_path)
        row.new_url = new_url
        row.sha256 = digest
        Session.add(row)

    # --- the pass --------------------------------------------------------- #

    def resolve(self, legacy_path):
        """Return the hosted URL for a legacy media path, uploading if needed.

        Returns None when the file is absent or unusable; the caller leaves
        the column NULL rather than pointing at something that will 404.
        """
        legacy_path = (legacy_path or u'').strip()
        if not legacy_path:
            return None
        if legacy_path in self._cache:
            return self._cache[legacy_path]

        existing = self._existing(legacy_path)
        if existing:
            self.reused += 1
            self._cache[legacy_path] = existing
            return existing

        if not self.media_root:
            self.missing.append(legacy_path)
            return None

        source = os.path.join(self.media_root, legacy_path)
        if not os.path.isfile(source):
            self.missing.append(legacy_path)
            return None

        try:
            new_url, digest = self._upload(source, legacy_path)
        except Exception as exc:
            # One unreadable file must not take down the migration.
            self.failed.append((legacy_path, type(exc).__name__))
            log.error('ckanext-c4w: could not upload a media file')
            return None

        if new_url and not self.dry_run:
            self._record(legacy_path, new_url, digest)
        self.uploaded += 1
        self._cache[legacy_path] = new_url
        return new_url

    def _upload(self, source, legacy_path):
        """Verify the bytes, then hand the file to CKAN's uploader."""
        from io import BytesIO

        import ckan.lib.uploader as ckan_uploader
        from PIL import Image
        from werkzeug.datastructures import FileStorage

        with open(source, 'rb') as handle:
            payload = handle.read()
        digest = hashlib.sha256(payload).hexdigest()

        # The FORMAT is read from the bytes, never from the name: the corpus
        # holds .JPEG, .JPG and .PNG in mixed case, and a name is not evidence
        # of content. The stored extension is then pinned to what was
        # verified, so whatever serves the file cannot infer a content type
        # from a misleading suffix.
        with Image.open(BytesIO(payload)) as image:
            image.verify()
            image_format = image.format
        extension = IMAGE_EXTENSIONS.get(image_format)
        if not extension:
            raise ValueError('unsupported image format %r' % image_format)

        stem = os.path.splitext(os.path.basename(legacy_path))[0] or 'image'
        filename = stem + extension

        if self.dry_run:
            return PUBLIC_PREFIX + filename, digest

        data = {'url': u'', 'upload': FileStorage(
            stream=BytesIO(payload), filename=filename)}
        uploader = ckan_uploader.get_uploader(UPLOAD_TO)
        uploader.update_data_dict(data, 'url', 'upload', 'clear')
        uploader.upload(self.max_size_mb or _max_image_size())
        return _stored_url(data.get('url')), digest

    # --- thumbnails ------------------------------------------------------- #

    def map_thumbnails(self, thumbnail_rows):
        """Point every easy_thumbnails derivative at its re-hosted original.

        ``thumbnail_rows`` is ``[(derivative_path, original_path), ...]``.
        Only derivatives whose original was actually uploaded get a row --
        mapping one to a URL that does not exist would replace a 404 with a
        broken image, which is worse because it looks like the site's fault.
        """
        for derivative, original in thumbnail_rows:
            derivative = (derivative or u'').strip()
            original = (original or u'').strip()
            if not derivative or not original:
                continue
            target = self._cache.get(original) or self._existing(original)
            if not target:
                continue
            if self._existing(derivative):
                continue
            if not self.dry_run:
                self._record(derivative, target)
            self.thumbnails_mapped += 1

    def report(self):
        return {
            'uploaded': self.uploaded,
            'reused': self.reused,
            'thumbnails_mapped': self.thumbnails_mapped,
            'missing': sorted(set(self.missing)),
            'failed': self.failed,
        }


def _max_image_size():
    import ckan.plugins.toolkit as tk
    try:
        return int(tk.config.get('ckan.max_image_size') or 2)
    except (TypeError, ValueError):
        return 2


def _stored_url(value):
    """Keep a backend URL intact; expand CKAN's bare local filename."""
    try:
        from urllib.parse import urlsplit
    except ImportError:                      # pragma: no cover - py2
        from urlparse import urlsplit
    value = u'%s' % (value or u'')
    value = value.strip()
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or '/' in value or '\\' in value:
        return value
    return PUBLIC_PREFIX + value
