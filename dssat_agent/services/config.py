"""
DSSAT configuration helper.

Provides get_config() with user -> system -> hardcoded fallback lookup chain.
"""

import logging

logger = logging.getLogger(__name__)

# In-memory cache: {(user_id_or_none, key): value}
_cache = {}


def get_config(key, default=None, user=None):
    """
    Get a DSSAT config value.

    Lookup order:
    1. User-specific DSSATConfig row (if user provided)
    2. System default DSSATConfig row (user=NULL)
    3. Hardcoded default parameter

    Args:
        key: Configuration key (e.g., 'default_weather_source')
        default: Fallback value if not found in DB
        user: Django User instance (optional)

    Returns:
        The config value.
    """
    from dssat_agent.models import DSSATConfig

    user_id = user.pk if user else None

    # Check user-specific override first
    if user_id:
        cache_key = (user_id, key)
        if cache_key not in _cache:
            try:
                row = DSSATConfig.objects.get(user_id=user_id, key=key)
                _cache[cache_key] = row.value
            except DSSATConfig.DoesNotExist:
                _cache[cache_key] = _SENTINEL

        val = _cache[cache_key]
        if val is not _SENTINEL:
            return val

    # Check system default
    system_key = (None, key)
    if system_key not in _cache:
        try:
            row = DSSATConfig.objects.get(user__isnull=True, key=key)
            _cache[system_key] = row.value
        except DSSATConfig.DoesNotExist:
            _cache[system_key] = _SENTINEL

    val = _cache[system_key]
    if val is not _SENTINEL:
        return val

    return default


def get_crop_default(crop_code, user=None):
    """
    Get per-crop defaults. Returns user's CropDefault if exists,
    then system CropDefault, then None.
    """
    from dssat_agent.models import CropDefault

    # User-specific
    if user:
        try:
            return CropDefault.objects.get(user=user, crop_code=crop_code)
        except CropDefault.DoesNotExist:
            pass

    # System default
    try:
        return CropDefault.objects.get(user__isnull=True, crop_code=crop_code)
    except CropDefault.DoesNotExist:
        return None


def resolve_user(user_id):
    """Resolve a user_id (int PK) to a Django User instance, or None."""
    if not user_id:
        return None
    try:
        from django.contrib.auth import get_user_model
        return get_user_model().objects.get(pk=user_id)
    except Exception:
        return None


def invalidate_cache(user=None):
    """Clear config cache. Call after config changes."""
    global _cache
    if user:
        _cache = {k: v for k, v in _cache.items() if k[0] != user.pk}
    else:
        _cache.clear()


# Sentinel for cache misses (distinguishes "not in cache" from "cached as None")
class _SentinelType:
    pass

_SENTINEL = _SentinelType()
