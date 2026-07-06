"""
Tests for role discovery + sync.

discover_roles() scans every installed app for a roles.yaml; sync_all_roles()
projects those definitions into Django Group + Permission rows, plus the two
global groups (administrator, developer). The sync must be idempotent because
it runs on every post_migrate.
"""

import pytest
from django.contrib.auth.models import Group, Permission

from accounts.role_discovery import (
    GLOBAL_GROUPS,
    discover_roles,
    get_available_roles,
    sync_all_roles,
)


def test_discover_roles_finds_agent_yaml_files():
    discovered = dict(discover_roles())
    # The three agent apps each ship a roles.yaml.
    assert "data_agent" in discovered
    assert "knowledge_agent" in discovered
    assert "dssat_agent" in discovered
    # accounts itself ships no roles.yaml.
    assert "accounts" not in discovered


def test_discover_roles_parses_group_definitions():
    discovered = dict(discover_roles())
    data_groups = {g["name"] for g in discovered["data_agent"].get("groups", [])}
    assert "data_admin" in data_groups


@pytest.mark.django_db
def test_sync_creates_global_groups():
    sync_all_roles()
    for g in GLOBAL_GROUPS:
        assert Group.objects.filter(name=g["name"]).exists()
    # The two required global groups spelled out explicitly.
    assert Group.objects.filter(name="administrator").exists()
    assert Group.objects.filter(name="developer").exists()


@pytest.mark.django_db
def test_sync_creates_agent_groups():
    sync_all_roles()
    for name in ("data_admin", "knowledge_admin", "dssat_admin",
                 "researcher", "farmer"):
        assert Group.objects.filter(name=name).exists(), f"missing group {name}"


@pytest.mark.django_db
def test_sync_creates_custom_permissions():
    sync_all_roles()
    # Custom (non-model) permissions declared in the agent roles.yaml files.
    assert Permission.objects.filter(codename="manage_sources").exists()
    assert Permission.objects.filter(codename="run_pipeline").exists()


@pytest.mark.django_db
def test_agent_group_has_declared_permissions():
    sync_all_roles()
    data_admin = Group.objects.get(name="data_admin")
    perm_codenames = set(data_admin.permissions.values_list("codename", flat=True))
    assert "manage_sources" in perm_codenames
    assert "manage_config" in perm_codenames


@pytest.mark.django_db
def test_global_groups_get_all_permissions():
    sync_all_roles()
    administrator = Group.objects.get(name="administrator")
    total_perms = Permission.objects.count()
    assert administrator.permissions.count() == total_perms


@pytest.mark.django_db
def test_sync_is_idempotent():
    sync_all_roles()
    groups_before = Group.objects.count()
    perms_before = Permission.objects.count()

    # Running again must not duplicate anything.
    sync_all_roles()

    assert Group.objects.count() == groups_before
    assert Permission.objects.count() == perms_before
    # Group names are unique — belt-and-suspenders on the admin group.
    assert Group.objects.filter(name="administrator").count() == 1


@pytest.mark.django_db
def test_get_available_roles_includes_global_and_agent_roles():
    names = {r["name"] for r in get_available_roles()}
    assert {"administrator", "developer"}.issubset(names)  # global
    assert "farmer" in names  # agent role
    sources = {r["source"] for r in get_available_roles()}
    assert "global" in sources
