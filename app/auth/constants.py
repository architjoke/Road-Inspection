ROLE_ADMINISTRATOR = 'Administrator'
ROLE_FIELD_WORKER = 'Field Worker'
ROLE_FIELD_OFFICER = 'Field Officer'
ROLE_CONTRACTOR = 'Contractor'
ROLE_SUPER_ADMIN = 'Super Admin'
ROLE_VIEWER = 'Viewer'

MUNICIPAL_ROLES = (
    ROLE_ADMINISTRATOR,
    ROLE_FIELD_WORKER,
    ROLE_FIELD_OFFICER,
    ROLE_CONTRACTOR,
    ROLE_VIEWER,
)

KIND_USER = 'user'
KIND_SUPER_ADMIN = 'super_admin'

# Live DBeaver names are snake_case: municipal_admin, field_worker, etc.
_ROLE_ALIASES = {
    'admin': ROLE_ADMINISTRATOR,
    'administrator': ROLE_ADMINISTRATOR,
    'municipal admin': ROLE_ADMINISTRATOR,
    'municipaladmin': ROLE_ADMINISTRATOR,
    'municipal_admin': ROLE_ADMINISTRATOR,
    'field worker': ROLE_FIELD_WORKER,
    'fieldworker': ROLE_FIELD_WORKER,
    'field officer': ROLE_FIELD_OFFICER,
    'fieldofficer': ROLE_FIELD_OFFICER,
    'contractor': ROLE_CONTRACTOR,
    'super admin': ROLE_SUPER_ADMIN,
    'superadmin': ROLE_SUPER_ADMIN,
    'super_admin': ROLE_SUPER_ADMIN,
    'viewer': ROLE_VIEWER,
}


def normalize_role(name):
    return ' '.join((name or '').strip().lower().replace('_', ' ').replace('-', ' ').split())


def canonical_role(name):
    key = normalize_role(name)
    if not key:
        return None
    compact = key.replace(' ', '')
    return _ROLE_ALIASES.get(key) or _ROLE_ALIASES.get(compact)
