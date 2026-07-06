"""
Generic authorization mixins and helpers for class-based and function views.

These are framework-level authz *primitives* — they depend only on Django's
auth (`user.groups`, `user.is_superuser`, `user.has_perm`) and carry no
knowledge of the accounts app, any sub-agent, or any domain. They live here (not
in accounts) so the framework and every sub-agent can gate views without
importing the accounts plugin. The `administrator`/`developer` global groups and
per-agent admin groups they check are created by accounts' roles.yaml discovery.

Usage:
    class MyView(AdminRequiredMixin, View):
        ...

    class MyView(AgentAdminMixin, View):
        agent_admin_group = 'reports_admin'
        ...
"""

from django.contrib.auth.mixins import UserPassesTestMixin


class AdminRequiredMixin(UserPassesTestMixin):
    """Require user to be in the administrator or developer group."""

    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True
        return user.groups.filter(name__in=['administrator', 'developer']).exists()


class AgentAdminMixin(UserPassesTestMixin):
    """Require user to be in the agent's admin group or a global admin."""

    agent_admin_group = None  # Override in subclass, e.g. 'reports_admin'

    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True
        allowed = ['administrator', 'developer']
        if self.agent_admin_group:
            allowed.append(self.agent_admin_group)
        return user.groups.filter(name__in=allowed).exists()


class AgentPermissionMixin(UserPassesTestMixin):
    """Require a specific permission."""

    required_permission = None  # e.g. 'knowledge_agent.run_pipeline'

    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True
        if self.required_permission:
            return user.has_perm(self.required_permission)
        return True


def is_admin(user):
    """Helper: check if user is a global admin."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=['administrator', 'developer']).exists()


def is_agent_admin(user, agent_admin_group):
    """Helper: check if user is an agent admin or global admin."""
    if is_admin(user):
        return True
    return user.groups.filter(name=agent_admin_group).exists()
