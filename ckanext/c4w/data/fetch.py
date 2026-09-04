# encoding: utf-8
"""Bring the raw file back from the object store for processing.

The URL comes from our own database, but it is still a URL the server is
about to open: the allowlist, the scheme check and the redirect handler
are what keep a tampered row from turning the pod into a proxy for the
cluster's internal addresses.
"""
import os
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from ckanext.c4w.data.errors import FetchError

CHUNK = 1024 * 1024
USER_AGENT = 'ckanext-c4w data fetch'


def check_url(url, allowed_hosts, allow_insecure=False):
    """Raise ``FetchError`` unless the URL is https on an allowed host."""
    parts = urllib.parse.urlsplit(url or u'')
    if parts.scheme != 'https' and not (allow_insecure and parts.scheme == 'http'):
        raise FetchError(u'The stored file is not on a secure address.')
    host = (parts.hostname or u'').lower()
    allowed = {h.lower().strip() for h in (allowed_hosts or ()) if h}
    if not host or host not in allowed:
        raise FetchError(u'The stored file is not on an approved storage host.')
    return parts


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts, allow_insecure):
        self.allowed_hosts = allowed_hosts
        self.allow_insecure = allow_insecure

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.allowed_hosts, self.allow_insecure)
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl)


def fetch_to_temp(url, allowed_hosts, max_bytes, dest_dir, timeout=600,
                  allow_insecure=False):
    """Download ``url`` to a new file in ``dest_dir`` and return its path.

    Streams in 1 MB chunks and stops at ``max_bytes`` whether or not the
    server declared a length. On any failure the partial file is removed.
    """
    check_url(url, allowed_hosts, allow_insecure)
    opener = urllib.request.build_opener(
        _AllowlistRedirectHandler(allowed_hosts, allow_insecure))
    request = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept-Encoding': 'identity',
    })
    fd, path = tempfile.mkstemp(prefix='c4w-fetch-', suffix='.dat', dir=dest_dir)
    total = 0
    try:
        with os.fdopen(fd, 'wb') as out:
            with opener.open(request, timeout=timeout) as response:
                declared = response.headers.get('Content-Length')
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise FetchError(u'The stored file is larger than the processing limit.')
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchError(
                            u'The stored file is larger than the processing limit.')
                    out.write(chunk)
    except FetchError:
        _remove(path)
        raise
    except urllib.error.HTTPError as exc:
        _remove(path)
        raise FetchError(u'The storage host answered with HTTP %s.' % exc.code)
    except (urllib.error.URLError, socket.timeout, OSError, ValueError):
        _remove(path)
        raise FetchError(u'The stored file could not be retrieved.')
    if total == 0:
        _remove(path)
        raise FetchError(u'The stored file is empty.')
    return path


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
