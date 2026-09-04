# encoding: utf-8
"""The three passes end to end, with a scratch directory that is always
removed -- a failed run must not leave a hundred megabytes of spools
behind on a pod with no disk to spare."""
import shutil
import tempfile

from ckanext.c4w.data import bundle, ingest, mapping
from ckanext.c4w.data.errors import EmptyData

DEFAULT_LIMITS = {
    'max_rows': 10000000,
    'max_sites': 200000,
    'max_parameters': 300,
}
DEFAULT_OPTIONS = dict(bundle.DEFAULT_OPTIONS)


class PipelineResult(object):
    __slots__ = ('files', 'summary')

    def __init__(self, files, summary):
        self.files = files
        self.summary = summary


def _explain(rejected):
    top = sorted(rejected.items(), key=lambda kv: -kv[1])[:3]
    if not top:
        return u''
    return u' Most common reasons: ' + u', '.join(
        u'%s (%d)' % (k, n) for k, n in top) + u'.'


def run(paths, spec, dataset_meta, limits=None, options=None, workdir=None,
        progress=None):
    """Process ``paths`` under ``spec`` into a bundle.

    Raises a ``DataError`` subclass with a user-safe message when nothing
    usable comes out; row-level problems are reported in the summary.
    """
    spec = mapping.normalise(spec)
    merged_limits = dict(DEFAULT_LIMITS)
    merged_limits.update(limits or {})
    merged_options = dict(DEFAULT_OPTIONS)
    merged_options.update(options or {})

    scratch = tempfile.mkdtemp(prefix='c4w-data-', dir=workdir)
    try:
        result = ingest.ingest(paths, spec, scratch, merged_limits, progress)
        if result.row_count == 0:
            raise EmptyData(u'The file has no data rows.')
        files, summary = bundle.build(result, spec, dataset_meta, merged_options)
        if summary['parameter_count'] == 0:
            raise EmptyData(
                u'No parameter had enough valid measurements to chart.'
                + _explain(summary['rejected']))
        return PipelineResult(files, summary)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
