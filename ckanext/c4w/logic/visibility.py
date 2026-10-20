"""The public visibility rule for ORM rows and API/template dictionaries."""
from ckanext.c4w import constants


def is_public(entity_type, item):
    def value(key, default=None):
        return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)
    published = (value('status') == constants.POST_STATUS_PUBLISHED
                 if entity_type == 'post' else bool(value('approved')))
    return published and not bool(value('hidden'))


def admin_section(entity_type, item=None):
    if entity_type == 'resource' and item and item.get('is_training_resource'):
        return 'training_resources'
    return next((section for section, entity, _label in constants.ADMIN_SECTIONS
                 if entity == entity_type), None)
