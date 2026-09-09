"""C4W worker with one CKAN initialisation per process.

CKAN's standard fork worker loads all extensions again in the child. Some
installed extensions exit during that second initialisation, before RQ can
start the job or invoke its failure callback. The CLI already loaded CKAN.
The inherited execute_job disposes DB connections before the fork, and
perform_job removes the child's session and engine after each job.
"""
from ckan.lib.jobs import Worker
from rq import Worker as RQWorker


class C4wWorker(Worker):
    def main_work_horse(self, job, queue):
        return RQWorker.main_work_horse(self, job, queue)


def run(burst=False):
    import ckan.plugins.toolkit as tk
    queue = tk.config.get('ckanext.c4w.job_queue') or 'c4w'
    C4wWorker([queue]).work(burst=burst)
