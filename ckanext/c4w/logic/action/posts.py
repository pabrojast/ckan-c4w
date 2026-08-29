# encoding: utf-8
"""News (blog) read actions.

A post has no approval queue -- its author sets draft or published -- so it is
the one entity without approve/reject actions.
"""
import ckan.plugins.toolkit as tk

from ckanext.c4w import constants, db
from ckanext.c4w.logic import query as q
from ckanext.c4w.logic.action import _common


def spec():
    db.ensure_mappers()
    return q.ListingSpec(
        entity_type='post',
        model_cls=db.C4wPost,
        search_columns=('title', 'excerpt', 'content'),
        native_filters={},
        term_facets=(),
        orderings={
            # Sticky posts lead, then newest first inside each group.
            'created': lambda m: [m.sticky.desc(),
                                  m.created_on.desc().nullslast()],
            'title': lambda m: [m.title.asc()],
        },
        default_order='created',
        page_size=constants.PAGE_SIZE_CHRONOLOGICAL,
    )


c4w_post_show = _common.make_show('post', db.C4wPost, 'c4w_post_show')
c4w_post_list = _common.make_list(spec, 'c4w_post_list')


def get_actions():
    return {
        'c4w_post_show': c4w_post_show,
        'c4w_post_list': c4w_post_list,
    }


def get_auth_functions():
    return _common.public_read_auth('c4w_post_show', 'c4w_post_list')
