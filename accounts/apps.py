from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    # Management dropdown entries — aggregated by chat.context_processors.management_items
    management_items = [
        {'label': 'My Account', 'url_name': 'accounts:profile',
         'section': 'account', 'visibility': 'auth'},
        {'label': 'Change Password', 'url_name': 'accounts:password_change',
         'section': 'account', 'visibility': 'auth'},
        {'label': 'User Management', 'url_name': 'accounts:user_management',
         'section': 'account', 'visibility': 'admin'},
    ]

    def ready(self):
        from django.db.models.signals import post_migrate
        post_migrate.connect(_sync_roles_on_migrate, sender=self)


def _sync_roles_on_migrate(sender, **kwargs):
    """Sync roles from roles.yaml files after migrations."""
    from accounts.role_discovery import sync_all_roles
    sync_all_roles()
