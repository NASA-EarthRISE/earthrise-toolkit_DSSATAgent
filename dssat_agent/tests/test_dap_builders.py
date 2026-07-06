"""Unit tests for DAP→absolute-date resolution in management builders.

The bulk of the coverage is exercised through ``_resolve_event_date``,
the shared helper invoked by every builder, and through end-to-end
calls to the builders that stub ``get_config`` so they don't touch the
database. End-to-end coverage uses ``_build_fertilizer`` /
``_build_irrigation`` because their DSSATTools event classes accept the
sparse kwargs we want to test with; the other builders share the same
helper, so the helper-level cases below already cover them.
"""

from datetime import date

import django
from django.conf import settings


def _ensure_django():
    if not settings.configured:
        # DJANGO_SETTINGS_MODULE is provided by the host project (pytest.ini or
        # the environment); the sub-agent's tests don't hardcode it.
        django.setup()


_ensure_django()


def _stub_config(monkeypatch, overrides=None):
    """Patch get_config / resolve_user so builders run without DB access."""
    overrides = overrides or {}

    def fake_get_config(key, default=None, user=None):
        return overrides.get(key, default)

    def fake_resolve_user(user_id):
        return None

    import dssat_agent.services.config as config_mod
    monkeypatch.setattr(config_mod, 'get_config', fake_get_config)
    monkeypatch.setattr(config_mod, 'resolve_user', fake_resolve_user)


# ---------------------------------------------------------------------------
# _resolve_event_date — covers logic shared by every builder.
# ---------------------------------------------------------------------------

def test_resolve_absolute_date_wins():
    from dssat_agent.services.experiment_service import _resolve_event_date

    ev = {'fdate': '2026-06-15', 'fdap': 30}
    pdate = date(2026, 4, 1)
    assert _resolve_event_date(ev, 'fdate', 'fdap', pdate, 'fertilizer') == date(2026, 6, 15)


def test_resolve_dap_resolves_against_pdate():
    from dssat_agent.services.experiment_service import _resolve_event_date

    ev = {'fdap': 30}
    assert _resolve_event_date(ev, 'fdate', 'fdap', date(2026, 4, 1),
                                'fertilizer') == date(2026, 5, 1)


def test_resolve_dap_zero():
    from dssat_agent.services.experiment_service import _resolve_event_date

    ev = {'tdap': 0}
    assert _resolve_event_date(ev, 'tdate', 'tdap', date(2026, 4, 1),
                                'tillage') == date(2026, 4, 1)


def test_resolve_dap_without_pdate_returns_none():
    from dssat_agent.services.experiment_service import _resolve_event_date

    assert _resolve_event_date({'fdap': 30}, 'fdate', 'fdap', None, 'fertilizer') is None


def test_resolve_negative_dap_returns_none():
    from dssat_agent.services.experiment_service import _resolve_event_date

    assert _resolve_event_date({'fdap': -5}, 'fdate', 'fdap', date(2026, 4, 1),
                                'fertilizer') is None


def test_resolve_non_int_dap_returns_none():
    from dssat_agent.services.experiment_service import _resolve_event_date

    assert _resolve_event_date({'fdap': 'soon'}, 'fdate', 'fdap',
                                date(2026, 4, 1), 'fertilizer') is None


def test_resolve_neither_key_returns_none():
    from dssat_agent.services.experiment_service import _resolve_event_date

    assert _resolve_event_date({'fmcd': 'FE005'}, 'fdate', 'fdap',
                                date(2026, 4, 1), 'fertilizer') is None


def test_resolve_invalid_absolute_date_falls_through_to_dap():
    from dssat_agent.services.experiment_service import _resolve_event_date

    # If the absolute key is unparseable, the DAP path is the fallback.
    ev = {'fdate': 'not-a-date', 'fdap': 30}
    assert _resolve_event_date(ev, 'fdate', 'fdap', date(2026, 4, 1),
                                'fertilizer') == date(2026, 5, 1)


# ---------------------------------------------------------------------------
# End-to-end builder calls (fertilizer + irrigation — sparse-kwarg-safe).
# ---------------------------------------------------------------------------

def test_fertilizer_resolves_fdap_against_pdate(monkeypatch):
    _stub_config(monkeypatch)
    from dssat_agent.services.experiment_service import _build_fertilizer

    fert = _build_fertilizer(
        [{'fdap': 30, 'fmcd': 'FE005', 'famn': 50}],
        planting_params={'pdate': '2026-04-01'},
    )
    assert fert is not None and len(fert.table) == 1
    assert fert.table[0]['fdate'] == date(2026, 5, 1)


def test_fertilizer_absolute_fdate_wins(monkeypatch):
    _stub_config(monkeypatch)
    from dssat_agent.services.experiment_service import _build_fertilizer

    fert = _build_fertilizer(
        [{'fdate': '2026-06-15', 'fmcd': 'FE005', 'famn': 50}],
        planting_params={'pdate': '2026-04-01'},
    )
    assert fert.table[0]['fdate'] == date(2026, 6, 15)


def test_fertilizer_skips_event_without_timing(monkeypatch):
    _stub_config(monkeypatch)
    from dssat_agent.services.experiment_service import _build_fertilizer

    fert = _build_fertilizer(
        [
            {'fdap': 0, 'fmcd': 'FE005', 'famn': 30},
            {'fmcd': 'FE005', 'famn': 50},  # no timing → skipped
        ],
        planting_params={'pdate': '2026-04-01'},
    )
    assert fert is not None
    assert len(fert.table) == 1


def test_fertilizer_skips_dap_without_pdate(monkeypatch):
    _stub_config(monkeypatch)
    from dssat_agent.services.experiment_service import _build_fertilizer

    fert = _build_fertilizer(
        [{'fdap': 30, 'fmcd': 'FE005', 'famn': 50}],
        planting_params=None,
    )
    assert fert is None  # only event was skipped → builder returns None


def test_negative_fdap_is_skipped(monkeypatch):
    _stub_config(monkeypatch)
    from dssat_agent.services.experiment_service import _build_fertilizer

    fert = _build_fertilizer(
        [{'fdap': -5, 'fmcd': 'FE005', 'famn': 50}],
        planting_params={'pdate': '2026-04-01'},
    )
    assert fert is None


def test_irrigation_fixed_resolves_idap(monkeypatch):
    _stub_config(monkeypatch)
    from dssat_agent.services.experiment_service import _build_irrigation

    cfg = {
        'method': 'fixed',
        'events': [{'idap': 14, 'irval': 25, 'irop': 'IR001'}],
    }
    irr = _build_irrigation(cfg, planting_params={'pdate': '2026-04-01'})
    assert irr is not None and len(irr.table) == 1
    assert irr.table[0]['idate'] == date(2026, 4, 15)


def test_irrigation_skips_idap_without_pdate(monkeypatch):
    _stub_config(monkeypatch)
    from dssat_agent.services.experiment_service import _build_irrigation

    cfg = {
        'method': 'fixed',
        'events': [{'idap': 14, 'irval': 25}],
    }
    irr = _build_irrigation(cfg, planting_params=None)
    assert irr is None
