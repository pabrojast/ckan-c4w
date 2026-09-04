# encoding: utf-8
"""Headline counts for the Citizens4Water home page."""
import ckan.plugins.toolkit as tk


@tk.side_effect_free
def c4w_stats(context, data_dict):
    """Public row counts, one per entity.

    Counts only what a visitor can actually reach -- approved and not hidden --
    so the number on the front door matches the number of cards in the listing
    behind it.
    """
    tk.check_access('c4w_stats', context, data_dict)

    from ckan.model.meta import Session
    from ckanext.c4w import constants, db

    db.ensure_mappers()

    def _public(model_cls, training=None):
        query = Session.query(model_cls).filter(model_cls.approved.is_(True))
        if hasattr(model_cls, 'hidden'):
            query = query.filter(model_cls.hidden.isnot(True))
        if training is not None:
            query = query.filter(
                model_cls.is_training_resource.is_(training))
        return query.count()

    return {
        'projects': _public(db.C4wProject),
        'organisations': _public(db.C4wOrganisation),
        # The two resource surfaces are counted apart because they are two
        # separate sections of the site, not one list with a filter.
        'resources': _public(db.C4wResource, training=False),
        'training_resources': _public(db.C4wResource, training=True),
        'platforms': _public(db.C4wPlatform),
        'events': _public(db.C4wEvent),
        'datasets': _public(db.C4wDataset),
        'posts': (
            Session.query(db.C4wPost)
            .filter(db.C4wPost.status
                    == constants.POST_STATUS_PUBLISHED).count()
        ),
    }


def get_actions():
    return {'c4w_stats': c4w_stats}
