# encoding: utf-8
"""Errors the pipeline raises.

``str(exc)`` is always safe to show to the person who uploaded the file: it
names what was wrong with the data, never a path, a host or a traceback.
"""


class DataError(Exception):
    """Base class: the message is user-safe."""


class EncodingError(DataError):
    """The bytes are not text in any encoding we accept."""


class DelimiterError(DataError):
    """No consistent column separator could be found."""


class MappingError(DataError):
    """The column mapping does not fit the file."""


class LimitExceeded(DataError):
    """The file is larger than the configured caps allow."""


class FetchError(DataError):
    """The raw file could not be brought back from storage."""


class EmptyData(DataError):
    """Nothing usable survived the cleaning."""
