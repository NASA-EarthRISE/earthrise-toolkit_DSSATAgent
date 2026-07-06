"""
Role discovery — scans INSTALLED_APPS for roles.yaml files.

Mirrors the pattern in chat/agent/discovery.py but for authorization.
Each subagent can declare groups, permissions, and role definitions
in a roles.yaml file. These are synced to Django's Group and Permission
models idempotently.
"""

import logging
import os
from typing import Dict, List, Tuple

import yaml
from django.apps import apps

logger = logging.getLogger(__name__)

# Global roles not tied to any subagent
GLOBAL_GROUPS = [
    {
        'name': 'administrator',
        'display_name': 'Administrator',
        'description': 'Full access to all operations and all data',
    },
    {
        'name': 'developer',
        'display_name': 'Developer',
        'description': 'Full access (development purposes)',
    },
]


def discover_roles() -> List[Tuple[str, dict]]:
    """
    Scan INSTALLED_APPS for roles.yaml files.

    Returns list of (app_label, roles_definition) tuples.
    """
    results = []
    for app_config in apps.get_app_configs():
        roles_path = os.path.join(app_config.path, 'roles.yaml')
        if os.path.isfile(roles_path):
            with open(roles_path) as f:
                roles_def = yaml.safe_load(f)
            if roles_def:
                results.append((app_config.label, roles_def))
                logger.info(
                    "Discovered roles for %s: %d groups",
                    app_config.label,
                    len(roles_def.get('groups', [])),
                )
    return results


def sync_all_roles():
    """
    Idempotent sync of all discovered roles to Django Group/Permission models.

    Called via post_migrate signal and manage.py sync_roles command.
    """
    from django.contrib.auth.models import Group, Permission
    from django.contrib.contenttypes.models import ContentType

    all_discovered = discover_roles()

    # --- 1. Create custom permissions from each app's roles.yaml ---
    for app_label, roles_def in all_discovered:
        custom_perms = roles_def.get('custom_permissions', [])
        if not custom_perms:
            continue

        # Get the first model's content type for this app, or use a proxy
        ct = _get_content_type_for_app(app_label)
        if not ct:
            logger.warning(
                "No ContentType found for app %s, skipping custom permissions",
                app_label,
            )
            continue

        for perm_def in custom_perms:
            codename = perm_def['codename']
            name = perm_def.get('name', codename)
            Permission.objects.get_or_create(
                codename=codename,
                content_type=ct,
                defaults={'name': name},
            )

    # --- 2. Create/update groups from each app's roles.yaml ---
    all_group_names = set()

    for app_label, roles_def in all_discovered:
        for group_def in roles_def.get('groups', []):
            group_name = group_def['name']
            all_group_names.add(group_name)

            group, created = Group.objects.get_or_create(name=group_name)
            if created:
                logger.info("Created group: %s", group_name)

            # Assign permissions (additive — don't remove manually added ones)
            perm_strings = group_def.get('permissions', [])
            for perm_str in perm_strings:
                perm = _resolve_permission(perm_str, app_label)
                if perm:
                    group.permissions.add(perm)

    # --- 3. Create global groups (administrator, developer) ---
    all_permissions = Permission.objects.all()
    for global_def in GLOBAL_GROUPS:
        group, created = Group.objects.get_or_create(name=global_def['name'])
        if created:
            logger.info("Created global group: %s", global_def['name'])
        # Global groups get ALL permissions
        group.permissions.set(all_permissions)

    logger.info(
        "Role sync complete: %d agent groups + %d global groups",
        len(all_group_names),
        len(GLOBAL_GROUPS),
    )


def _agent_display_name(app_label: str) -> str:
    """Human display name for an app label, read from its AppConfig
    (``display_name`` → ``verbose_name`` → the raw label). Keeps accounts
    domain-agnostic: no agent names are hardcoded here — each agent app
    declares its own display name on its AppConfig.
    """
    from django.apps import apps as django_apps
    try:
        cfg = django_apps.get_app_config(app_label)
    except LookupError:
        return app_label
    return str(getattr(cfg, 'display_name', None)
               or getattr(cfg, 'verbose_name', None)
               or app_label)


def get_grouped_roles() -> List[Dict]:
    """
    Return roles organized into display groups for UI rendering.

    Grouping rules:
      - Global roles (administrator, developer) → "System"
      - Any role ending in "_admin" → "Admin Roles"
      - Otherwise → "{AgentDisplayName} Roles" (e.g. "DSSAT Roles")

    Returns an ordered list of {'label': str, 'roles': [...]}.
    """
    all_roles = get_available_roles()
    buckets = {}

    for role in all_roles:
        if role['source'] == 'global':
            key = 'System'
        elif role['name'].endswith('_admin'):
            key = 'Admin Roles'
        else:
            agent_label = _agent_display_name(role['source'])
            key = f'{agent_label} Roles'
        buckets.setdefault(key, []).append(role)

    # Display order: System first, Admin Roles second, then agent buckets alphabetically
    preferred = ['System', 'Admin Roles']
    remaining = sorted(k for k in buckets if k not in preferred)
    ordered = [k for k in preferred if k in buckets] + remaining

    return [
        {'label': k, 'roles': buckets[k]}
        for k in ordered
        if buckets[k]
    ]


def get_available_roles() -> List[Dict]:
    """
    Return all available roles for display in registration/management UI.

    Returns list of dicts with name, display_name, description, source.
    """
    roles = []

    # Global roles
    for g in GLOBAL_GROUPS:
        roles.append({
            'name': g['name'],
            'display_name': g['display_name'],
            'description': g['description'],
            'source': 'global',
        })

    # Agent roles
    for app_label, roles_def in discover_roles():
        for group_def in roles_def.get('groups', []):
            roles.append({
                'name': group_def['name'],
                'display_name': group_def.get('display_name', group_def['name']),
                'description': group_def.get('description', ''),
                'source': app_label,
            })

    return roles


def _get_content_type_for_app(app_label: str):
    """Get a ContentType for the given app (uses first model found)."""
    from django.contrib.contenttypes.models import ContentType

    cts = ContentType.objects.filter(app_label=app_label)
    if cts.exists():
        return cts.first()
    return None


def _resolve_permission(perm_str: str, default_app_label: str):
    """
    Resolve a permission string to a Permission object.

    Accepts:
      - 'codename' (uses default_app_label)
      - 'app_label.codename'
    """
    from django.contrib.auth.models import Permission

    if '.' in perm_str:
        app_label, codename = perm_str.split('.', 1)
    else:
        app_label = default_app_label
        codename = perm_str

    try:
        return Permission.objects.get(
            codename=codename,
            content_type__app_label=app_label,
        )
    except Permission.DoesNotExist:
        logger.warning(
            "Permission not found: %s.%s", app_label, codename,
        )
        return None
    except Permission.MultipleObjectsReturned:
        return Permission.objects.filter(
            codename=codename,
            content_type__app_label=app_label,
        ).first()
