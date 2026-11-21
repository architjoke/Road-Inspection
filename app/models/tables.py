"""Canonical list of tables created in DBeaver (20 total)."""

TABLE_NAMES = [
    'roles',
    'municipalities',
    'wards',
    'users',
    'super_admins',
    'municipality_oversight',
    'inspections',
    'inspection_media',
    'model_versions',
    'detections',
    'detection_reviews',
    'tasks',
    'task_assignments',
    'repair_records',
    'before_after_comparisons',
    'reports',
    'notifications',
    'audit_logs',
    'system_settings',
    'super_admin_audit_logs',
]

SPATIAL_TABLES = {
    "wards",
    "inspections",
    "detections",
}

# Mapped as declarative models; reflection covers remaining DBeaver tables.
EXPLICIT_ORM_TABLES = {
    'roles',
    'municipalities',
    'wards',
    'users',
    'inspections',
    'inspection_media',
}
