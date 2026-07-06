"""Unit tests for stored-default validation.

Covers ``validate_default_management`` — the gate that rejects
absolute-date shapes when saving a CropDefault row or a
``fallback_*`` DSSATConfig row from the preferences/system-config UIs.
"""

from dssat_agent.services.validation import validate_default_management


def test_dap_event_passes():
    payload = {
        'fertilizer': [{'fdap': 0, 'fmcd': 'FE005', 'famn': 50}],
        'tillage': [{'tdap': 5, 'timpl': 'TI001', 'tdep': 20}],
        'chemical': [{'cdap': 30, 'chcod': 'CH001', 'chamt': 1.0}],
        'residue': [{'rdap': 0, 'rcod': 'RE001', 'ramt': 1000}],
        'irrigation': {
            'method': 'fixed',
            'events': [{'idap': 14, 'irval': 25, 'irop': 'IR001'}],
        },
        'harvest': {'option': 'dap', 'dap': 120},
    }
    assert validate_default_management(payload) == []


def test_absolute_date_rejected():
    payload = {'fertilizer': [{'fdate': '2026-04-15', 'famn': 50}]}
    errors = validate_default_management(payload)
    assert any('fdap' in e for e in errors)


def test_missing_dap_rejected():
    payload = {'fertilizer': [{'fmcd': 'FE005', 'famn': 50}]}
    errors = validate_default_management(payload)
    assert any('required' in e for e in errors)


def test_negative_dap_rejected():
    payload = {'tillage': [{'tdap': -3, 'timpl': 'TI001'}]}
    errors = validate_default_management(payload)
    assert any('non-negative' in e for e in errors)


def test_non_integer_dap_rejected():
    payload = {'chemical': [{'cdap': 'soon', 'chcod': 'CH001'}]}
    errors = validate_default_management(payload)
    assert any('integer' in e for e in errors)


def test_harvest_on_date_rejected_in_defaults():
    payload = {'harvest': {'option': 'on_date', 'date': '2026-09-01'}}
    errors = validate_default_management(payload)
    assert any('on_date is not allowed' in e for e in errors)


def test_harvest_with_absolute_date_rejected():
    payload = {'harvest': {'option': 'dap', 'dap': 100, 'date': '2026-09-01'}}
    errors = validate_default_management(payload)
    assert any('cannot carry an absolute date' in e for e in errors)


def test_harvest_auto_passes():
    assert validate_default_management({'harvest': {'option': 'auto'}}) == []
    assert validate_default_management({'harvest': {'option': 'maturity'}}) == []
    assert validate_default_management({'harvest': {'option': ''}}) == []


def test_harvest_growth_stage_passes():
    payload = {'harvest': {'option': 'growth_stage', 'stage': 'GS005'}}
    assert validate_default_management(payload) == []


def test_irrigation_automatic_passes():
    payload = {'irrigation': {'method': 'automatic', 'threshold': 50, 'efficiency': 90}}
    assert validate_default_management(payload) == []


def test_irrigation_fixed_with_idate_rejected():
    payload = {
        'irrigation': {
            'method': 'fixed',
            'events': [{'idate': '2026-05-01', 'irval': 25}],
        }
    }
    errors = validate_default_management(payload)
    assert any('idap' in e for e in errors)


def test_empty_payload_passes():
    assert validate_default_management({}) == []
    assert validate_default_management({'fertilizer': None}) == []
    assert validate_default_management({'fertilizer': []}) == []
