"""Content administration is local to C4W, never a CKAN sysadmin grant."""


def is_content_admin(user):
    if user is None or not getattr(user, 'id', None):
        return False
    if getattr(user, 'is_anonymous', False) or getattr(user, 'state', 'active') != 'active':
        return False
    if getattr(user, 'sysadmin', False):
        return True
    from ckanext.c4w import db
    db.ensure_mappers()
    return db.Session.query(db.C4wPortalAdmin.user_id).filter_by(user_id=user.id).first() is not None
