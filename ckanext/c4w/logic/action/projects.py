# encoding: utf-8
"""Project read actions.

The listing spec here is the single declaration of what /projects accepts and
what its sidebar renders. It mirrors the ten facets the live Django site
serves, verified against production rather than copied from the model:
Country, Status, Tags, Difficulty, Topic, Participation Task, Community
Impact, Water Type, Water Data Type, Stakeholder Type.
"""
import ckan.plugins.toolkit as tk

from ckanext.c4w import constants, db
from ckanext.c4w.logic import query as q


def _spec():
    """Build the listing spec.

    A function rather than a module constant because it references the mapped
    classes, and the mappers are wired lazily.
    """
    db.ensure_mappers()
    model_cls = db.C4wProject
    return q.ListingSpec(
        entity_type='project',
        model_cls=model_cls,
        # The Django search box matched the project name or any of its
        # keywords; keywords now live in c4w_term_link, so the aim and
        # description carry the free-text weight instead.
        search_columns=('name', 'description', 'aim'),
        native_filters={
            'status': 'status',
            'difficulty_level': 'difficulty_level',
        },
        bool_filters={'featured': 'featured'},
        term_facets=(
            'country',
            'topic',
            'has_tag',
            'participation_task',
            'water_type',
            'water_data_type',
            'stakeholder_type',
            'community_impact_type',
        ),
        orderings={
            'modified': lambda m: [m.modified.desc().nullslast(),
                                   m.name.asc()],
            'created': lambda m: [m.created.desc().nullslast(), m.name.asc()],
            'accesses': lambda m: [m.total_accesses.desc(), m.name.asc()],
            'name': lambda m: [m.name.asc()],
            # Featured first, then the default order within each group -- a
            # bare featured sort would leave the other 40 in insertion order.
            'featured': lambda m: [m.featured.desc(),
                                   m.modified.desc().nullslast()],
        },
        default_order='modified',
        page_size=constants.PAGE_SIZE,
        # Stored GeoJSON is served by /project/<slug>/geojson and must never be
        # loaded for a page of cards.
        defer_columns=('geom_geojson',),
    )


def _get_project(reference):
    """Fetch by slug, falling back to the Django id and then the uuid."""
    db.ensure_mappers()
    from ckan.model.meta import Session

    if not reference:
        return None
    project = (Session.query(db.C4wProject)
               .filter(db.C4wProject.slug == reference).first())
    if project is not None:
        return project
    # /project/<int> is the legacy URL; the view redirects, but resolving it
    # here as well means an API caller holding an old id still works.
    try:
        legacy_id = int(reference)
    except (TypeError, ValueError):
        pass
    else:
        project = (Session.query(db.C4wProject)
                   .filter(db.C4wProject.legacy_id == legacy_id).first())
        if project is not None:
            return project
    return (Session.query(db.C4wProject)
            .filter(db.C4wProject.id == reference).first())


def _visible_to(project, context):
    """Whether the requester may see an unapproved or hidden project."""
    if project.approved and not project.hidden:
        return True
    user = context.get('auth_user_obj')
    if user is None:
        return False
    if getattr(user, 'sysadmin', False):
        return True
    if project.created_by and project.created_by == user.id:
        return True
    from ckan.model.meta import Session
    return Session.query(db.C4wRelation.id).filter(
        db.C4wRelation.subject_type == u'project',
        db.C4wRelation.subject_id == project.id,
        db.C4wRelation.predicate == u'editor',
        db.C4wRelation.object_type == u'user',
        db.C4wRelation.object_id == user.id,
    ).first() is not None


def _dictize_detail(project):
    """Dictize a project plus the neighbours its detail page renders.

    Only the detail path pays for this. The listing uses the plain dictize,
    because a page of 18 cards must not resolve 18 organisations one by one.
    """
    from ckan.model.meta import Session

    out = db.entity_dictize('project', project)

    organisation_ids = list(out.get('relations', {}).get('organisation', []))
    if out.get('main_organisation_id'):
        organisation_ids.insert(0, out['main_organisation_id'])
    organisations = {}
    if organisation_ids:
        rows = (Session.query(db.C4wOrganisation)
                .filter(db.C4wOrganisation.id.in_(organisation_ids)).all())
        organisations = {row.id: db.entity_dictize('organisation', row)
                         for row in rows}
    out['main_organisation'] = organisations.get(out.get('main_organisation_id'))
    out['organisations'] = [organisations[i] for i in organisation_ids
                            if i in organisations
                            and i != out.get('main_organisation_id')]
    return out


@tk.side_effect_free
def c4w_project_show(context, data_dict):
    """One project by slug (or legacy id).

    A project the requester may not see raises NotFound, NOT NotAuthorized.
    A 403 -- or a redirect to the login page -- tells an anonymous visitor
    that the thing exists, which is an existence oracle over content that is
    deliberately not public yet.
    """
    tk.check_access('c4w_project_show', context, data_dict)
    project = _get_project(data_dict.get('id') or data_dict.get('slug'))
    if project is None or not _visible_to(project, context):
        raise tk.ObjectNotFound(tk._('Project not found'))
    return _dictize_detail(project)


@tk.side_effect_free
def c4w_project_list(context, data_dict):
    """The faceted /projects listing."""
    tk.check_access('c4w_project_list', context, data_dict)
    return q.build_listing(_spec(), data_dict or {})


@tk.side_effect_free
def c4w_project_facets(context, data_dict):
    """The facet definitions a sidebar needs, with their labels."""
    tk.check_access('c4w_project_facets', context, data_dict)
    spec = _spec()
    out = []
    for vocabulary in spec.term_facets:
        out.append({
            'name': vocabulary,
            'kind': 'term',
            'options': [{'term': t, 'label': l}
                        for t, l in (constants.VOCABULARIES.get(vocabulary)
                                     or ())],
        })
    for param, _column in spec.native_filters.items():
        out.append({
            'name': param,
            'kind': 'column',
            'options': [{'term': t, 'label': l}
                        for t, l in (constants.COLUMN_VOCABULARIES.get(param)
                                     or ())],
        })
    return {'facets': out,
            'orderings': [{'value': v, 'label': l}
                          for v, l in constants.PROJECT_ORDERINGS]}


def c4w_project_record_view(context, data_dict):
    """Increment a project's view counter.

    Deliberately NOT folded into ``c4w_project_show``: that action is
    side-effect-free and is called from listings, feeds and the API, none of
    which are a page view. The detail view calls this once, explicitly.
    """
    tk.check_access('c4w_project_record_view', context, data_dict)
    from ckan.model.meta import Session

    project = _get_project(data_dict.get('id'))
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))
    project.total_accesses = (project.total_accesses or 0) + 1
    Session.add(project)
    Session.commit()
    return {'total_accesses': project.total_accesses}


def get_actions():
    return {
        'c4w_project_show': c4w_project_show,
        'c4w_project_list': c4w_project_list,
        'c4w_project_facets': c4w_project_facets,
        'c4w_project_record_view': c4w_project_record_view,
    }
