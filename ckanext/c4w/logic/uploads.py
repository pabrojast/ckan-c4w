# encoding: utf-8
"""File uploads for the data wizard: validate, hash, store, roll back.

Files go through CKAN's ``IUploader`` plugin interface exactly as
ckanext-pages' images do, so on the IHP-WINS deployment ckanext-asset-storage
writes them to the Azure container under ``static/c4w_data/``. The uploader
returns the public URL; the pipeline later reads the file back from it.

The uploader plugin does NOT validate content, and it has one trap: when the
incoming ``FileStorage`` carries a ``Content-Length`` header it uses that
header as the MIME type. Both are handled here -- the bytes are inspected
before anything is stored, and a fresh ``FileStorage`` without headers is
what the uploader receives.

Only CSV/TSV is accepted for data. XLSX would need a non-stdlib parser and
cannot be streamed; a spreadsheet user exports to CSV.
"""
import hashlib
import logging
import os
import re

import ckan.lib.uploader as ckan_uploader
import ckan.plugins.toolkit as tk

log = logging.getLogger(__name__)

UPLOAD_TO = 'c4w_data'

DATA_EXTENSIONS = ('.csv', '.tsv', '.txt')
ATTACHMENT_EXTENSIONS = ('.pdf', '.csv', '.tsv', '.txt', '.md')
ATTACHMENT_LIMIT = 5

# Signatures that can never be a text table, whatever the extension says.
_BINARY_MAGIC = (
    b'PK\x03\x04',          # zip / xlsx / docx
    b'%PDF',                # pdf
    b'\x89PNG',             # png
    b'\xff\xd8\xff',        # jpeg
    b'GIF8',                # gif
    b'\xd0\xcf\x11\xe0',    # OLE (xls, doc)
    b'\x1f\x8b',            # gzip
    b'7z\xbc\xaf',          # 7z
)
_HTML_RE = re.compile(br'^\s*<(?:!doctype|html|script|\?xml)', re.I)

HEAD_BYTES = 256 * 1024


class UploadError(Exception):
    """A failure the visitor may read verbatim."""


def uploads_enabled():
    """Whether the configured uploader can accept a web upload.

    An external upload plugin (ckanext-asset-storage) replaces CKAN's
    uploader and needs no local ``ckan.storage_path``.
    """
    configured = tk.config.get('ckan.uploads_enabled')
    if configured is not None and not tk.asbool(configured):
        return False
    if tk.config.get('ckan.storage_path'):
        return True
    try:
        uploader = ckan_uploader.get_uploader(UPLOAD_TO)
    except Exception:
        return False
    return bool(getattr(uploader, '_storage', None)
                or uploader.__class__.__module__ != ckan_uploader.__name__)


def max_data_upload_mb():
    return _int_config('ckanext.c4w.data_max_upload_mb', 256)


def max_attachment_mb():
    return _int_config('ckanext.c4w.attachment_max_upload_mb', 25)


def _int_config(key, default):
    try:
        value = int(tk.config.get(key) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _stream_of(upload):
    stream = getattr(upload, 'stream', upload)
    for method in ('read', 'seek', 'tell'):
        if not hasattr(stream, method):
            raise UploadError(tk._(u'The upload could not be read.'))
    return stream


def _size_and_hash(stream, head_bytes=HEAD_BYTES):
    """``(size, sha256, head)`` in one pass; leaves the stream at 0."""
    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    head = b''
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        if len(head) < head_bytes:
            head += chunk[:head_bytes - len(head)]
        digest.update(chunk)
        size += len(chunk)
    stream.seek(0)
    return size, digest.hexdigest(), head


def _extension(filename):
    return os.path.splitext((filename or u'').lower())[1]


def _safe_stem(filename):
    stem = os.path.splitext(os.path.basename(filename or u'data'))[0]
    stem = re.sub(r'[^A-Za-z0-9._-]+', '-', stem).strip('-._') or u'data'
    return stem[:60]


def _check_text_table(head, filename):
    if not head:
        raise UploadError(tk._(u'The file is empty.'))
    if b'\x00' in head:
        raise UploadError(tk._(u'The file is not a text table (CSV/TSV).'))
    for magic in _BINARY_MAGIC:
        if head.startswith(magic):
            raise UploadError(tk._(
                u'%s is not a CSV/TSV file. Export your spreadsheet as CSV '
                u'and try again.') % (filename or u'The file'))
    if _HTML_RE.match(head):
        raise UploadError(tk._(u'The file is not a text table (CSV/TSV).'))


def _check_attachment(head, filename):
    ext = _extension(filename)
    if ext not in ATTACHMENT_EXTENSIONS:
        raise UploadError(tk._(u'Attachments must be PDF, CSV or text.'))
    if ext == '.pdf':
        if not head.startswith(b'%PDF'):
            raise UploadError(tk._(u'%s is not a PDF.') % filename)
    else:
        _check_text_table(head, filename)


def _fresh_upload(stream, filename, content_type):
    from werkzeug.datastructures import FileStorage
    stream.seek(0)
    return FileStorage(stream=stream, filename=filename,
                       content_type=content_type)


def _asset_reference(uploader):
    """Plugin-storage cleanup handle, or None for CKAN's own uploader."""
    storage = getattr(uploader, '_storage', None)
    filename = getattr(uploader, '_filename', None)
    object_type = getattr(uploader, '_object_type', None) or UPLOAD_TO
    if storage is None or not filename or not hasattr(storage, 'delete'):
        return None
    return storage, u'/'.join(p.strip('/') for p in (object_type, filename))


def _store(upload, stored_name, content_type, max_mb):
    """Hand one validated file to the configured uploader.

    Returns ``(url, stored_name, cleanup)``. ``cleanup`` is a zero-argument
    callable that deletes the blob again, for the caller's rollback.
    """
    if not uploads_enabled():
        raise UploadError(tk._(u'File uploads are not enabled on this site.'))
    stream = _stream_of(upload)
    fresh = _fresh_upload(stream, stored_name, content_type)
    data = {'url': u'', 'upload': fresh}
    uploader = ckan_uploader.get_uploader(UPLOAD_TO)
    uploader.update_data_dict(data, 'url', 'upload', 'clear')
    try:
        uploader.upload(max_mb)
    except tk.ValidationError as exc:
        text = u' '.join(u'%s' % m for v in exc.error_dict.values()
                         for m in (v if isinstance(v, list) else [v]))
        if 'too large' in text.lower():
            raise UploadError(tk._(u'The file is larger than %d MB.')
                              % max_mb)
        raise UploadError(tk._(u'The file could not be stored.'))
    url = data.get('url') or u''
    if not url:
        raise UploadError(tk._(u'The file could not be stored.'))
    final_name = getattr(uploader, '_filename', None) or stored_name
    reference = _asset_reference(uploader)
    filepath = getattr(uploader, 'filepath', None)

    def cleanup():
        try:
            if reference:
                reference[0].delete(reference[1])
            elif filepath and os.path.isfile(filepath):
                os.remove(filepath)
        except Exception:
            log.warning("ckanext-c4w: could not delete an orphaned upload")

    return _public_url(url), final_name, cleanup


def _public_url(value):
    """Keep backend URLs intact; expand CKAN's bare local filename."""
    value = u'%s' % (value or u'')
    if '://' in value or value.startswith('/'):
        return value
    return u'/uploads/%s/%s' % (UPLOAD_TO, value)


def store_data_file(upload, dataset_id):
    """Validate, sniff and store one CSV/TSV. Returns a file-row dict.

    The dict carries everything ``c4w_dataset_file`` needs plus ``cleanup``,
    which the caller invokes if its own database write fails.
    """
    from ckanext.c4w.data import sniff as sniffer
    from ckanext.c4w.data.errors import DataError

    filename = getattr(upload, 'filename', None) or u'data.csv'
    stream = _stream_of(upload)
    size, sha, head = _size_and_hash(stream)
    limit = max_data_upload_mb()
    if size > limit * 1024 * 1024:
        raise UploadError(tk._(u'The file is larger than %d MB.') % limit)
    _check_text_table(head, filename)
    try:
        sniffed = sniffer.sniff_bytes(head, size_bytes=size)
    except DataError as exc:
        raise UploadError(u'%s' % exc)
    fmt = 'tsv' if sniffed.get('delimiter') == '\t' else 'csv'
    stored_name = u'%s-%s-%s.%s' % (
        (dataset_id or u'')[:8], sha[:10], _safe_stem(filename), fmt)
    content_type = ('text/tab-separated-values' if fmt == 'tsv'
                    else 'text/csv')
    url, final_name, cleanup = _store(upload, stored_name, content_type,
                                      limit)
    return {
        'kind': u'data',
        'original_name': filename[:255],
        'stored_name': final_name,
        'url': url,
        'content_type': content_type,
        'size_bytes': size,
        'sha256': sha,
        'format': fmt,
        'encoding': sniffed.get('encoding'),
        'delimiter': sniffed.get('delimiter'),
        'quotechar': sniffed.get('quotechar'),
        'has_header': sniffed.get('has_header'),
        'row_estimate': sniffed.get('row_estimate'),
        'sniff': sniffed,
        'cleanup': cleanup,
    }


def store_attachment(upload, dataset_id):
    """Validate and store one protocol / field-sheet attachment."""
    filename = getattr(upload, 'filename', None) or u'attachment'
    stream = _stream_of(upload)
    size, sha, head = _size_and_hash(stream, head_bytes=8192)
    limit = max_attachment_mb()
    if size > limit * 1024 * 1024:
        raise UploadError(tk._(u'The file is larger than %d MB.') % limit)
    _check_attachment(head, filename)
    ext = _extension(filename)
    stored_name = u'%s-%s-%s%s' % (
        (dataset_id or u'')[:8], sha[:10], _safe_stem(filename), ext)
    content_type = {
        '.pdf': 'application/pdf',
        '.csv': 'text/csv',
        '.tsv': 'text/tab-separated-values',
    }.get(ext, 'text/plain')
    url, final_name, cleanup = _store(upload, stored_name, content_type,
                                      limit)
    return {
        'kind': u'attachment',
        'original_name': filename[:255],
        'stored_name': final_name,
        'url': url,
        'content_type': content_type,
        'size_bytes': size,
        'sha256': sha,
        'format': ext.lstrip('.'),
        'cleanup': cleanup,
    }


def delete_stored(stored_name):
    """Best-effort deletion of a blob this module stored earlier."""
    if not stored_name:
        return False
    try:
        uploader = ckan_uploader.get_uploader(UPLOAD_TO)
    except Exception:
        return False
    storage = getattr(uploader, '_storage', None)
    if storage is not None and hasattr(storage, 'delete'):
        try:
            storage.delete(u'%s/%s' % (UPLOAD_TO, stored_name))
            return True
        except Exception:
            log.warning("ckanext-c4w: could not delete a stored file")
            return False
    storage_path = tk.config.get('ckan.storage_path')
    if storage_path:
        path = os.path.join(storage_path, 'storage', 'uploads', UPLOAD_TO,
                            os.path.basename(stored_name))
        try:
            if os.path.isfile(path):
                os.remove(path)
                return True
        except OSError:
            return False
    return False


def storage_hosts():
    """Hostnames the pipeline may fetch a stored file from.

    The configured allowlist plus whatever host the uploader itself
    produces, discovered from a probe URL so a bucket move needs no config.
    """
    from urllib.parse import urlsplit

    hosts = set()
    for host in (tk.config.get('ckanext.c4w.data_fetch_hosts')
                 or u'').split():
        hosts.add(host.strip().lower())
    site = urlsplit(tk.config.get('ckan.site_url') or u'')
    if site.hostname:
        hosts.add(site.hostname.lower())
    try:
        uploader = ckan_uploader.get_uploader(UPLOAD_TO)
        storage = getattr(uploader, '_storage', None)
        probe = None
        if storage is not None and hasattr(storage, 'get_storage_uri'):
            probe = storage.get_storage_uri(u'probe.csv', UPLOAD_TO)
        if probe:
            host = urlsplit(u'%s' % probe).hostname
            if host:
                hosts.add(host.lower())
    except Exception:
        pass
    return hosts
