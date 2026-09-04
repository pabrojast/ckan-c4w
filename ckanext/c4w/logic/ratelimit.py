# encoding: utf-8
"""A per-worker sliding-window rate limiter for the public POST forms.

CKAN offers extensions no shared rate-limit service, so this is a
best-effort first line of defence in front of registration, the
verification resend and the login form; reCAPTCHA and the ingress may add
stronger, shared controls. Blocked attempts do not extend the window, so a
client is always released after the advertised Retry-After.

Ported from ckanext-csunesco. The class is CKAN-free (see tests); only
``retry_after`` reads the site configuration.
"""
import math
import threading
import time
from collections import defaultdict, deque

DEFAULT_MAX = 10
DEFAULT_WINDOW = 300


class SlidingWindowLimiter(object):

    def __init__(self):
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def consume(self, key, maximum, window, now=None):
        """Record one attempt for ``key``.

        Returns None when allowed, else the seconds to wait.
        """
        now = time.monotonic() if now is None else now
        cutoff = now - window
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= maximum:
                return max(1, int(math.ceil(events[0] + window - now)))
            events.append(now)
            if len(self._events) > 10000:
                stale = [name for name, values in self._events.items()
                         if not values or values[-1] <= cutoff]
                for name in stale:
                    self._events.pop(name, None)
            return None

    def clear(self):
        with self._lock:
            self._events.clear()


limiter = SlidingWindowLimiter()


def retry_after(scope, key=None):
    """Consume one attempt for ``scope`` from the current client.

    ``scope`` names the form ('register', 'resend', 'login'); ``key``
    defaults to the client address. Never trusts X-Forwarded-For: the
    address has already passed through CKAN's configured proxy handling.
    """
    import ckan.plugins.toolkit as tk

    if not tk.asbool(tk.config.get('ckanext.c4w.rate_limit_enabled', True)):
        return None
    maximum = _positive(tk.config.get('ckanext.c4w.rate_limit_max'),
                        DEFAULT_MAX)
    window = _positive(tk.config.get('ckanext.c4w.rate_limit_window'),
                       DEFAULT_WINDOW)
    if key is None:
        try:
            from flask import request
            key = request.remote_addr or u'unknown'
        except Exception:
            key = u'unknown'
    return limiter.consume(u'%s:%s' % (scope, key), maximum, window)


def _positive(value, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default
