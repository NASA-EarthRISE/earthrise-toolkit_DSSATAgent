"""End-to-end submit-dispatch tests for all five experiment types.

These exercise ``dssat_agent.services.draft_submit.submit_draft`` — the
SPA submit path — by building a ``WizardDraft`` plus its ``Field`` /
``Treatment`` rows in-memory, then calling ``submit_draft`` with the
Celery task mocked out. The goal is "did the wizard succeed all the way
to the dispatch step?", not to run DSSAT itself.

Each test verifies:
  * No exception during the build → submit pipeline.
  * The returned envelope reports the right ``experiment_type`` and
    ``treatment_count``.
  * ``run_wizard_experiment_task.delay`` was called exactly once with the
    right ``experiment_type`` and ``wizard_params`` shape.
  * The draft's status flips to ``'submitted'``.

A separate test covers the chat wizard's ``advance_wizard`` for the
``single`` path (the only experiment type the chat wizard fully
supports today; the other four redirect to the SPA — see ``services/wizard.py``
lines 444-474).

We don't depend on seeded ``StoredSoilProfile`` rows: every test field
is created with ``soil_profile=None`` and an empty ``inline_soil`` to
keep the fixture surface minimal. The submit path doesn't validate soil
beyond "field has a soil_id or inline_soil"; downstream the run pipeline
would fail without one, but that's beyond the scope of these tests.
"""

from datetime import date
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase

from dssat_agent.models import (
    Field, Treatment, WizardDraft,
    WizardDraftField, WizardDraftTreatment,
)
from dssat_agent.services.draft_submit import submit_draft
from dssat_agent.services.workflow import run_full_simulation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_field(user, *, name='Test Field', lat=33.0, lon=-86.5,
                 elevation=100.0, weather='nasa_power'):
    return Field.objects.create(
        user=user,
        name=name,
        latitude=lat,
        longitude=lon,
        elevation=elevation,
        weather_source=weather,
        # Inline soil is enough to satisfy the run-pipeline shape.
        # Submission validation only requires "either soil_profile or
        # inline_soil"; downstream MC/single readers actually consume it.
        inline_soil={'soil_id': 'TEST00001', 'depth': 200},
        location_label=f"{name} loc",
    )


def _make_treatment(user, *, name='Test Treatment', crop='MZ',
                     cultivar='IB0001', planting_doy=120,
                     planting_mode='fixed', auto_source=None):
    """Construct a Treatment with a minimal valid planting block.

    ``planting_mode='fixed'`` with a ``pdoy`` is the typical Fixed-Date
    case. For Auto mode tests pass ``planting_mode='auto'`` with an
    ``auto_source`` string.
    """
    planting = {'mode': planting_mode, 'plme': 'S', 'ppop': 7.2,
                'plrs': 76, 'pldp': 5}
    if planting_mode == 'fixed':
        planting['pdoy'] = planting_doy
        planting['planting_month_day'] = '04-29'
    else:
        planting['auto_source'] = (
            auto_source or 'dssat_planting_date_lookup_v1')
    return Treatment.objects.create(
        user=user,
        name=name,
        crop_code=crop,
        cultivar_code=cultivar,
        dssat_model='MZCER',
        planting=planting,
        simulation_controls={
            'start_offset_days': -30,
            'num_years': 1,
            'num_reps': 1,
            'water': 'Y',
            'nitrogen': 'Y',
            'co2': 'M',
        },
    )


def _make_draft(user, experiment_type, *, year=2025):
    return WizardDraft.objects.create(
        user=user,
        experiment_type=experiment_type,
        step_state={'step1': {'year': year}},
    )


# ---------------------------------------------------------------------------
# Mocks for the Celery task + Chat-creation discovery + SUBPATH
# ---------------------------------------------------------------------------

class _SubmitDispatchBase(TestCase):
    """Shared setup for the per-type tests below."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username='testuser', email='t@e.com', password='x',
        )

    def _patch_dispatch(self):
        """Patch ``run_wizard_experiment_task.delay`` and discovery so
        the test runs without hitting Celery / agent registry.

        Returns ``(delay_mock, ctx)``; the caller uses ``ctx`` as a
        context manager wrapping the test body.
        """
        # ``submit_draft._create_chat_and_queue`` does an inline ``from
        # dssat_agent.tasks import run_wizard_experiment_task``; patching
        # the source module ``dssat_agent.tasks.run_wizard_experiment_task``
        # affects that fresh import. ``MagicMock(task_id='fake-task')``
        # gives the call a ``.id`` attribute the submit code reads.
        delay_mock = MagicMock(return_value=MagicMock(id='fake-task'))
        patches = [
            patch('dssat_agent.tasks.run_wizard_experiment_task.delay',
                  delay_mock),
            # NOTE: we intentionally do NOT stub discover_agents. It is a cheap
            # app-registry scan (imports nothing — see discovery.py), and
            # stubbing it with a partial agent set pollutes the module-level
            # URLconf that _register_agents() builds at import time (reached via
            # reverse() in the submit path), which then breaks other apps'
            # a2a-contract tests. The real discover_agents returns all agents,
            # so the full URLconf registers and the submit path still resolves
            # dssat_agent by app_label.
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return delay_mock


# ---------------------------------------------------------------------------
# Per-experiment-type tests
# ---------------------------------------------------------------------------

class SingleSubmitTest(_SubmitDispatchBase):

    def test_dispatches_to_run_pipeline(self):
        delay = self._patch_dispatch()
        draft = _make_draft(self.user, 'single')
        f = _make_field(self.user, name='Single Field')
        t = _make_treatment(self.user, name='MZ Treatment')
        WizardDraftField.objects.create(draft=draft, field=f, ordering=0)
        WizardDraftTreatment.objects.create(
            draft=draft, treatment=t, field=f, ordering=0)

        result = submit_draft(draft, user=self.user)

        self.assertEqual(result['experiment_type'], 'single')
        self.assertEqual(result['treatment_count'], 1)
        self.assertTrue(result['chat_id'])
        self.assertTrue(result['redirect_url'].startswith('/'))

        delay.assert_called_once()
        # Positional args: (chat_id, message_id, params, wizard_params, user_id)
        _, _, params, wizard_params, _ = delay.call_args.args
        self.assertEqual(wizard_params['experiment_type'], 'single')

        draft.refresh_from_db()
        self.assertEqual(draft.status, 'submitted')


class EnsembleSubmitTest(_SubmitDispatchBase):

    def test_n_treatments_one_field(self):
        delay = self._patch_dispatch()
        draft = _make_draft(self.user, 'ensemble')
        f = _make_field(self.user, name='Ensemble Field')
        WizardDraftField.objects.create(draft=draft, field=f, ordering=0)
        # 3 different treatments, all paired to the same field.
        for i, doy in enumerate([110, 120, 130]):
            t = _make_treatment(
                self.user, name=f'Ensemble T{i+1}', planting_doy=doy)
            WizardDraftTreatment.objects.create(
                draft=draft, treatment=t, field=f, ordering=i)

        result = submit_draft(draft, user=self.user)

        self.assertEqual(result['experiment_type'], 'ensemble')
        self.assertEqual(result['treatment_count'], 3)
        delay.assert_called_once()
        _, _, _, wizard_params, _ = delay.call_args.args
        self.assertEqual(wizard_params['experiment_type'], 'ensemble')
        self.assertEqual(len(wizard_params['treatments']), 3)


class SensitivitySubmitTest(_SubmitDispatchBase):

    def test_dispatches_with_sensitivity_meta(self):
        delay = self._patch_dispatch()
        draft = _make_draft(self.user, 'sensitivity')
        # Stash a sensitivity sweep config on the draft so
        # _build_sensitivity has something to surface.
        draft.step_state['step4'] = {
            'sensitivity': {
                'category': 'planting',
                'axes': {
                    'dateCentralDoy': 120, 'dateOffsetDays': 10, 'dateSteps': 3,
                    'popMin': 5, 'popMax': 9, 'popSteps': 1,
                    'rsMin': 76, 'rsMax': 76, 'rsSteps': 1,
                },
            },
            'baseline': {},
        }
        draft.save(update_fields=['step_state'])

        f = _make_field(self.user, name='Sensitivity Field')
        WizardDraftField.objects.create(draft=draft, field=f, ordering=0)
        # Sensitivity at lock time materialises N treatments; here we
        # just attach 3 pre-generated rows representing the sweep output.
        for i, doy in enumerate([110, 120, 130]):
            t = _make_treatment(
                self.user, name=f'T{i+1}: DOY={doy}', planting_doy=doy)
            WizardDraftTreatment.objects.create(
                draft=draft, treatment=t, field=f, ordering=i)

        result = submit_draft(draft, user=self.user)
        self.assertEqual(result['experiment_type'], 'sensitivity')
        self.assertEqual(result['treatment_count'], 3)

        delay.assert_called_once()
        _, _, _, wizard_params, _ = delay.call_args.args
        self.assertEqual(wizard_params['experiment_type'], 'sensitivity')
        self.assertEqual(wizard_params['sensitivity']['category'], 'planting')


class MonteCarloSubmitTest(_SubmitDispatchBase):

    def test_one_template_n_fields(self):
        delay = self._patch_dispatch()
        draft = _make_draft(self.user, 'monte_carlo')
        # Step 2 spatial config (kept around for metadata; the run
        # pipeline now consumes the explicit ``fields`` list instead).
        draft.step_state['step2'] = {
            'spatial_mode': 'circle',
            'center': {'lat': 33.0, 'lon': -86.5},
            'radius_km': 25,
            'grid_spacing': 0.1,
            'sampling_strategy': 'systematic',
            'n_points': 5,
        }
        draft.save(update_fields=['step_state'])

        # 5 grid points → 5 fields all paired to the single template treatment.
        template = _make_treatment(self.user, name='MZ MC template')
        for i, lat in enumerate([32.8, 32.9, 33.0, 33.1, 33.2]):
            f = _make_field(
                self.user, name=f'MC Point #{i+1}', lat=lat, lon=-86.5)
            WizardDraftField.objects.create(draft=draft, field=f, ordering=i)
            WizardDraftTreatment.objects.create(
                draft=draft, treatment=template, field=f, ordering=i)

        result = submit_draft(draft, user=self.user)
        self.assertEqual(result['experiment_type'], 'monte_carlo')
        self.assertEqual(result['treatment_count'], 5)

        delay.assert_called_once()
        _, _, _, wizard_params, _ = delay.call_args.args
        self.assertEqual(wizard_params['experiment_type'], 'monte_carlo')
        # The MC builder packs every paired field into ``fields`` so
        # ``run_monte_carlo`` consumes them directly.
        self.assertEqual(len(wizard_params['fields']), 5)

    def test_auto_planting_surfaces_top_level_planting_date(self):
        """Auto-mode planting on a Monte Carlo run resolves dates per
        field via the in-situ raster at Step 4 lock time. Without a
        top-level ``planting_date``, ``run_full_simulation``'s pre-build
        weather-prep call sees ``planting_date=''`` and aborts with
        "Invalid planting date:" before the MC service ever runs.
        Pin the fix that surfaces the first resolved per-field date as
        the representative top-level value."""
        delay = self._patch_dispatch()
        draft = _make_draft(self.user, 'monte_carlo', year=2024)

        template = Treatment.objects.create(
            user=self.user,
            name='MZ MC Auto template',
            crop_code='MZ',
            cultivar_code='IB0001',
            dssat_model='MZCER',
            # Auto-mode: pdoy/planting_month_day are deliberately None,
            # the source is the in-situ raster, dates land in
            # step4.resolved_planting_dates per (treatment, field).
            planting={
                'mode': 'auto',
                'auto_source': 'dssat_planting_date_lookup_v1',
                'pdoy': None, 'planting_month_day': None,
                'plme': 'S', 'ppop': 7.2, 'plrs': 76, 'pldp': 5,
            },
            simulation_controls={
                'start_offset_days': -30, 'num_years': 1, 'num_reps': 1,
            },
        )
        # 3 grid-point fields, each with a resolved Auto-mode date.
        fields = []
        resolved = {}
        for i, (lat, day) in enumerate([(32.8, 95), (32.9, 102), (33.0, 110)]):
            f = _make_field(
                self.user, name=f'MC Auto pt {i+1}', lat=lat, lon=-86.5)
            fields.append(f)
            WizardDraftField.objects.create(draft=draft, field=f, ordering=i)
            WizardDraftTreatment.objects.create(
                draft=draft, treatment=template, field=f, ordering=i)
            resolved[f"{template.id}:{f.id}"] = {
                'doy': day, 'lat': lat, 'lon': -86.5,
                'date': f"2024-{(day - 1) // 30 + 1:02d}-{(day - 1) % 30 + 1:02d}",
                'source': 'dssat_planting_date_lookup_v1',
            }
        draft.step_state['step4'] = {'resolved_planting_dates': resolved}
        draft.save(update_fields=['step_state'])

        submit_draft(draft, user=self.user)

        delay.assert_called_once()
        _, _, _, wizard_params, _ = delay.call_args.args
        # The top-level ``planting_date`` MUST be set so prepare_weather
        # has a valid window to fetch. Reading from any field's pdate
        # is fine — they all point to in-situ-resolved Auto-mode dates.
        self.assertTrue(
            wizard_params.get('planting_date'),
            f"top-level planting_date missing from MC payload; "
            f"prepare_weather will abort with 'Invalid planting date'. "
            f"keys present: {sorted(wizard_params.keys())}",
        )
        # Each field's ``pdate`` should reflect its own resolved date.
        field_pdates = [
            f.get('pdate') for f in (wizard_params.get('fields') or [])
        ]
        self.assertEqual(
            len([p for p in field_pdates if p]), 3,
            f"expected 3 per-field pdates, got {field_pdates}",
        )


class BatchSubmitTest(_SubmitDispatchBase):

    def test_matrix_pairs_become_pair_params(self):
        delay = self._patch_dispatch()
        draft = _make_draft(self.user, 'batch')
        # 3 fields × 2 protocols = 6 pairs.
        fields = [
            _make_field(self.user, name=f'Batch F{i+1}',
                        lat=32.5 + i * 0.5, lon=-86.0 - i * 0.3)
            for i in range(3)
        ]
        protocols = [
            _make_treatment(self.user, name=f'Batch P{i+1}',
                            planting_doy=120 + i * 10)
            for i in range(2)
        ]
        for i, f in enumerate(fields):
            WizardDraftField.objects.create(draft=draft, field=f, ordering=i)
        ordering = 0
        for f in fields:
            for p in protocols:
                WizardDraftTreatment.objects.create(
                    draft=draft, treatment=p, field=f, ordering=ordering)
                ordering += 1

        result = submit_draft(draft, user=self.user)
        self.assertEqual(result['experiment_type'], 'batch')
        self.assertEqual(result['treatment_count'], 6)

        delay.assert_called_once()
        _, _, _, wizard_params, _ = delay.call_args.args
        self.assertEqual(wizard_params['experiment_type'], 'batch')
        # New batch path packs one fully-resolved per-pair params dict
        # per (Field, Treatment) row — no shared-template flattening.
        self.assertEqual(len(wizard_params['pair_params']), 6)
        # Each pair carries crop+location merged.
        for pp in wizard_params['pair_params']:
            self.assertIn('crop_code', pp)
            self.assertIn('latitude', pp)
            self.assertIn('longitude', pp)


# ---------------------------------------------------------------------------
# Cap + bad-state guards
# ---------------------------------------------------------------------------

class SubmitGuardTest(_SubmitDispatchBase):

    def test_zero_pairs_rejected(self):
        self._patch_dispatch()
        draft = _make_draft(self.user, 'single')
        with self.assertRaises(ValueError) as cm:
            submit_draft(draft, user=self.user)
        # Single requires exactly 1 (field, treatment) pair; zero pairs trips
        # the per-type cardinality guard before the generic "nothing to run".
        self.assertIn('exactly 1', str(cm.exception).lower())

    def test_99_treatment_cap(self):
        self._patch_dispatch()
        draft = _make_draft(self.user, 'ensemble')
        f = _make_field(self.user)
        WizardDraftField.objects.create(draft=draft, field=f, ordering=0)
        for i in range(100):
            t = _make_treatment(self.user, name=f'T{i+1}',
                                planting_doy=100 + (i % 30))
            WizardDraftTreatment.objects.create(
                draft=draft, treatment=t, field=f, ordering=i)
        with self.assertRaises(ValueError) as cm:
            submit_draft(draft, user=self.user)
        self.assertIn('99', str(cm.exception))


# ---------------------------------------------------------------------------
# Chat wizard — tree-step machine, all five experiment types
# ---------------------------------------------------------------------------

class _RunMockProxy:
    """Wraps a (run_experiment_tool, run_full_simulation) mock pair so a
    test can assert on whichever one actually fired without knowing the
    routing. ``advance_wizard`` sends single through ``run_experiment_tool``
    and non-single through ``run_full_simulation`` — this proxy hides that
    split for the unit-test assertions."""

    def __init__(self, rt_mock, rfs_mock):
        self._rt = rt_mock
        self._rfs = rfs_mock

    def _active(self):
        if self._rt.called:
            return self._rt
        return self._rfs

    def assert_called_once(self):
        total = self._rt.call_count + self._rfs.call_count
        if total != 1:
            raise AssertionError(
                f"Expected exactly 1 run dispatch, got {total} "
                f"(run_experiment_tool={self._rt.call_count}, "
                f"run_full_simulation={self._rfs.call_count})"
            )

    @property
    def call_args(self):
        return self._active().call_args

    @property
    def dispatched_payload(self):
        """The per-type wizard payload, regardless of which path fired.

        Single goes through ``run_experiment_tool(raw)`` — args[0] is
        the raw chat-shaped input, which carries the per-type fields
        (crop, location, planting, ...).

        Non-single goes through ``run_full_simulation(legacy,
        wizard_params=...)`` — kwargs['wizard_params'] is the per-type
        run-args dict (with ``base``/``treatments``/``spatial``/etc.).
        """
        args, kwargs = self.call_args
        if 'wizard_params' in kwargs and kwargs['wizard_params'] is not None:
            return kwargs['wizard_params']
        return args[0]


class ChatWizardTreeStepTest(TestCase):
    """The chat wizard now walks per-experiment-type step trees
    (``services/wizard.py:WIZARD_TREES``). Each test fully populates the
    schema for its type and verifies that ``advance_wizard`` runs through
    every step and dispatches via ``run_experiment_tool`` (mocked so
    DSSAT never actually runs).
    """

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='chatuser', email='c@e.com', password='x',
        )

    def _patch_run_tool(self):
        """Mock the *post-collection* run path so the test never executes
        DSSAT. ``advance_wizard`` routes single through
        ``run_experiment_tool`` (which goes through schema validation,
        then ``run_full_simulation``) and routes non-single types
        directly to ``run_full_simulation``. Patching both sources
        keeps the assertion shape uniform across types.
        """
        envelope = {'status': 'completed', 'experiment_id': 'fake-id'}
        rt = patch(
            'dssat_agent.services.tool_wrapper.run_experiment_tool',
            return_value=envelope,
        )
        rfs = patch(
            'dssat_agent.services.workflow.run_full_simulation',
            return_value=envelope,
        )
        rt_mock = rt.start()
        rfs_mock = rfs.start()
        self.addCleanup(rt.stop)
        self.addCleanup(rfs.stop)
        return _RunMockProxy(rt_mock, rfs_mock)

    def test_unknown_type_reprompts(self):
        from dssat_agent.services.wizard import advance_wizard
        from dssat_agent.schemas.wizard import ExperimentWizardInput
        params = ExperimentWizardInput(experiment_type='nonsense')
        r = advance_wizard(params, user=self.user)
        self.assertEqual(r.get('status'), 'needs_input')
        self.assertEqual(r.get('current_step'), 'experiment_type')

    def test_single_dispatches(self):
        from dssat_agent.services.wizard import advance_wizard
        from dssat_agent.schemas.wizard import ExperimentWizardInput
        from data_agent.schemas.location import LocationPoint
        run_mock = self._patch_run_tool()
        params = ExperimentWizardInput(
            experiment_type='single',
            crop='MZ',
            location=LocationPoint(lat=33.0, lon=-86.5),
            planting_date=date(2025, 4, 29),
            shortcircuit=True,
        )
        r = advance_wizard(params, user=self.user)
        self.assertEqual(r.get('status'), 'completed')
        run_mock.assert_called_once()
        payload = run_mock.dispatched_payload
        self.assertEqual(payload['experiment_type'], 'single')

    def test_ensemble_dispatches(self):
        from dssat_agent.services.wizard import advance_wizard
        from dssat_agent.schemas.wizard import (
            ExperimentWizardInput, ProtocolInput,
        )
        from data_agent.schemas.location import LocationPoint
        run_mock = self._patch_run_tool()
        params = ExperimentWizardInput(
            experiment_type='ensemble',
            crop='MZ',
            location=LocationPoint(lat=33.0, lon=-86.5),
            planting_date=date(2025, 4, 29),
            soil_use_defaults=True,
            weather_use_defaults=True,
            cultivar_use_defaults=True,
            protocols=[
                ProtocolInput(name='High N', planting_date=date(2025, 4, 15)),
                ProtocolInput(name='Low N',  planting_date=date(2025, 5, 1)),
            ],
            protocols_complete=True,
            review_confirmed=True,
        )
        r = advance_wizard(params, user=self.user)
        self.assertEqual(r.get('status'), 'completed')
        run_mock.assert_called_once()
        payload = run_mock.dispatched_payload
        self.assertEqual(payload['experiment_type'], 'ensemble')
        self.assertEqual(len(payload['treatments']), 2)

    def test_sensitivity_dispatches(self):
        from dssat_agent.services.wizard import advance_wizard
        from dssat_agent.schemas.wizard import (
            ExperimentWizardInput, SensitivitySweep,
        )
        from data_agent.schemas.location import LocationPoint
        run_mock = self._patch_run_tool()
        params = ExperimentWizardInput(
            experiment_type='sensitivity',
            crop='MZ',
            location=LocationPoint(lat=33.0, lon=-86.5),
            planting_date=date(2025, 4, 29),
            soil_use_defaults=True,
            weather_use_defaults=True,
            cultivar_use_defaults=True,
            sensitivity=SensitivitySweep(
                category='planting',
                date_central_doy=120, date_offset_days=10, date_steps=3,
                pop_steps=1, rs_steps=1,
            ),
            planting_use_defaults=True,
            management_use_defaults=True,
            initial_use_defaults=True,
            review_confirmed=True,
        )
        r = advance_wizard(params, user=self.user)
        self.assertEqual(r.get('status'), 'completed')
        run_mock.assert_called_once()
        payload = run_mock.dispatched_payload
        self.assertEqual(payload['experiment_type'], 'sensitivity')
        self.assertEqual(payload['sensitivity']['category'], 'planting')
        # 3 date steps × 1 pop × 1 rs = 3 generated treatments.
        self.assertEqual(len(payload['treatments']), 3)

    def test_monte_carlo_dispatches(self):
        from dssat_agent.services.wizard import advance_wizard
        from dssat_agent.schemas.wizard import (
            ExperimentWizardInput, SpatialConfig,
        )
        run_mock = self._patch_run_tool()
        params = ExperimentWizardInput(
            experiment_type='monte_carlo',
            crop='MZ',
            spatial=SpatialConfig(
                mode='circle',
                center_lat=33.0, center_lon=-86.5, radius_km=25,
                sampling_strategy='systematic', grid_spacing=0.1,
            ),
            soil_use_defaults=True,
            weather_use_defaults=True,
            cultivar_use_defaults=True,
            auto_planting=False,
            planting_date=date(2025, 4, 29),
            planting_use_defaults=True,
            management_use_defaults=True,
            initial_use_defaults=True,
            review_confirmed=True,
        )
        r = advance_wizard(params, user=self.user)
        self.assertEqual(r.get('status'), 'completed')
        run_mock.assert_called_once()
        payload = run_mock.dispatched_payload
        self.assertEqual(payload['experiment_type'], 'monte_carlo')
        self.assertEqual(payload['spatial_mode'], 'circle')
        self.assertEqual(payload['grid_spacing'], 0.1)

    def test_batch_dispatches(self):
        from dssat_agent.services.wizard import advance_wizard
        from dssat_agent.schemas.wizard import (
            ExperimentWizardInput, ProtocolInput,
        )
        from data_agent.schemas.location import LocationPoint
        run_mock = self._patch_run_tool()
        params = ExperimentWizardInput(
            experiment_type='batch',
            crop='MZ',
            locations=[
                LocationPoint(lat=33.0, lon=-86.5),
                LocationPoint(lat=33.5, lon=-87.0),
                LocationPoint(lat=32.5, lon=-86.0),
            ],
            locations_complete=True,
            planting_date=date(2025, 4, 29),
            protocols=[
                ProtocolInput(name='P1', planting_date=date(2025, 4, 15)),
                ProtocolInput(name='P2', planting_date=date(2025, 5, 1)),
            ],
            protocols_complete=True,
            review_confirmed=True,
        )
        r = advance_wizard(params, user=self.user)
        self.assertEqual(r.get('status'), 'completed')
        run_mock.assert_called_once()
        payload = run_mock.dispatched_payload
        self.assertEqual(payload['experiment_type'], 'batch')
        self.assertEqual(payload['location_selection_mode'], 'points')
        self.assertEqual(len(payload['batch_locations']), 3)


# ---------------------------------------------------------------------------
# Run-pipeline routing — verify each experiment type dispatches to the right
# skill (run_simulation / run_ensemble / run_monte_carlo / run_batch) and
# returns a completed envelope. Uses the actual ``submit_draft`` build path
# so the wizard_params shape matches production exactly, then runs the
# captured ``(params, wizard_params)`` pair through ``run_full_simulation``
# with ``dispatch_skill`` and the weather-prep call mocked at the lowest
# level the routing logic uses.
# ---------------------------------------------------------------------------

_FAKE_EXP_UUIDS = {
    'single': '11111111-1111-1111-1111-111111111111',
    'ensemble': '22222222-2222-2222-2222-222222222222',
    'monte_carlo': '33333333-3333-3333-3333-333333333333',
    'batch': '44444444-4444-4444-4444-444444444444',
}


def _make_dispatch_router(call_log):
    """Return a fake ``dispatch_skill`` that records every call and returns
    a minimally-realistic envelope per skill name.

    ``call_log`` is mutated in place so the test can inspect the order /
    set of skills dispatched after the pipeline runs. Experiment IDs are
    real UUID strings so the URL ``reverse()`` calls inside the workflow
    don't crash on int-only patterns.
    """
    def fake(skill_name, params):
        call_log.append((skill_name, params))
        if skill_name == 'list_soils':
            return {'soils': [{'soil_id': 'IB00000001'}]}
        if skill_name == 'list_cultivars':
            return {
                'cultivars': [{'code': 'IB0001', 'name': 'PIO 3046'}],
            }
        if skill_name == 'run_simulation':
            return {
                'status': 'completed',
                'experiment_id': _FAKE_EXP_UUIDS['single'],
                'overview': '',
                'summary': {'harwt': 5000, 'flo': 65, 'mat': 110, 'rain': 400},
                'plant_growth': [],
                'soil_water': [],
            }
        if skill_name == 'run_ensemble':
            return {
                'status': 'completed',
                'experiment_id': _FAKE_EXP_UUIDS['ensemble'],
                'overview': '',
                'treatments': [
                    {'name': 'T1', 'summary': {'harwt': 5000}},
                    {'name': 'T2', 'summary': {'harwt': 4500}},
                ],
                'aggregate': {
                    'mean_yield': 4750, 'min_yield': 4500,
                    'max_yield': 5000, 'std_yield': 250,
                },
                'treatment_count': 2,
            }
        if skill_name == 'run_monte_carlo':
            return {
                'status': 'completed',
                'experiment_id': _FAKE_EXP_UUIDS['monte_carlo'],
                'overview': '',
                'spatial_mode': 'circle',
                'treatments_run': 5,
                'quantiles': {
                    'yield_kg_ha': {
                        'p5': 3500, 'p10': 4000, 'p50': 5000,
                        'p90': 6000, 'p95': 6200, 'mean': 5000, 'std': 600,
                    },
                },
                'treatments': [{'summary': {'harwt': 5000}} for _ in range(5)],
            }
        if skill_name == 'run_batch':
            return {
                'status': 'queued',
                'batch_id': _FAKE_EXP_UUIDS['batch'],
                'batch_label': 'Test batch',
                'locations': ['L1', 'L2', 'L3', 'L4', 'L5', 'L6'],
                'total_locations': 6,
                'sub_experiment_type': 'single',
                'detail_url': f"/explorer/batch/{_FAKE_EXP_UUIDS['batch']}/",
            }
        # Unknown skill → empty envelope; callers default-handle this.
        return {'status': 'completed'}
    return fake


class RunPipelineRoutingTest(_SubmitDispatchBase):
    """For each experiment type, build wizard_params via the real
    ``submit_draft`` path, then run ``run_full_simulation`` against
    those params and assert the right skill was dispatched."""

    def _patch_pipeline(self, call_log):
        """Mock dispatch_skill (records to call_log) + weather prep so
        the run pipeline never hits NASA POWER or the A2A registry."""
        dispatch = patch(
            'dssat_agent.services.executor.dispatch_skill',
            side_effect=_make_dispatch_router(call_log),
        )
        weather = patch(
            'dssat_agent.services.workflow.prepare_weather',
            return_value={'status': 'ready', 'weather_data': {}},
        )
        for p in (dispatch, weather):
            p.start()
            self.addCleanup(p.stop)

    def _capture_params_from_submit(self, draft):
        """Run submit_draft and return the (params, wizard_params) pair
        the Celery task would have received."""
        delay = self._patch_dispatch()
        submit_draft(draft, user=self.user)
        delay.assert_called_once()
        _, _, params, wizard_params, _ = delay.call_args.args
        return params, wizard_params

    # ---- Single ----
    def test_single_routes_to_run_simulation(self):
        draft = _make_draft(self.user, 'single')
        f = _make_field(self.user, name='Single Field')
        t = _make_treatment(self.user, name='MZ T1')
        WizardDraftField.objects.create(draft=draft, field=f, ordering=0)
        WizardDraftTreatment.objects.create(
            draft=draft, treatment=t, field=f, ordering=0)

        params, wizard_params = self._capture_params_from_submit(draft)

        call_log = []
        self._patch_pipeline(call_log)
        envelope = run_full_simulation(
            params=params, wizard_params=wizard_params,
            user_id=self.user.id,
        )
        self.assertEqual(envelope.get('status'), 'completed')

        skills = [name for name, _ in call_log]
        self.assertIn('run_simulation', skills)
        self.assertNotIn('run_ensemble', skills)
        self.assertNotIn('run_monte_carlo', skills)
        self.assertNotIn('run_batch', skills)

    # ---- Ensemble ----
    def test_ensemble_routes_to_run_ensemble(self):
        draft = _make_draft(self.user, 'ensemble')
        f = _make_field(self.user, name='Ensemble Field')
        WizardDraftField.objects.create(draft=draft, field=f, ordering=0)
        for i, doy in enumerate([110, 120, 130]):
            t = _make_treatment(self.user, name=f'T{i+1}', planting_doy=doy)
            WizardDraftTreatment.objects.create(
                draft=draft, treatment=t, field=f, ordering=i)

        params, wizard_params = self._capture_params_from_submit(draft)

        call_log = []
        self._patch_pipeline(call_log)
        envelope = run_full_simulation(
            params=params, wizard_params=wizard_params,
            user_id=self.user.id,
        )
        self.assertEqual(envelope.get('status'), 'completed')

        skills = [name for name, _ in call_log]
        self.assertIn('run_ensemble', skills)
        self.assertNotIn('run_simulation', skills)
        self.assertNotIn('run_monte_carlo', skills)
        self.assertNotIn('run_batch', skills)

    # ---- Sensitivity (sensitivity falls into ensemble's run pipeline since
    # it's an ensemble of materialised sweep treatments) ----
    def test_sensitivity_routes_to_run_ensemble(self):
        draft = _make_draft(self.user, 'sensitivity')
        draft.step_state['step4'] = {
            'sensitivity': {
                'category': 'planting',
                'axes': {
                    'dateCentralDoy': 120, 'dateOffsetDays': 10, 'dateSteps': 3,
                    'popMin': 7.2, 'popMax': 7.2, 'popSteps': 1,
                    'rsMin': 76, 'rsMax': 76, 'rsSteps': 1,
                },
            },
            'baseline': {},
        }
        draft.save(update_fields=['step_state'])
        f = _make_field(self.user, name='Sens Field')
        WizardDraftField.objects.create(draft=draft, field=f, ordering=0)
        for i, doy in enumerate([110, 120, 130]):
            t = _make_treatment(
                self.user, name=f'T{i+1}: DOY={doy}', planting_doy=doy)
            WizardDraftTreatment.objects.create(
                draft=draft, treatment=t, field=f, ordering=i)

        params, wizard_params = self._capture_params_from_submit(draft)

        call_log = []
        self._patch_pipeline(call_log)
        envelope = run_full_simulation(
            params=params, wizard_params=wizard_params,
            user_id=self.user.id,
        )
        self.assertEqual(envelope.get('status'), 'completed')
        # Sensitivity rides on the ensemble run path because the swept
        # treatments are materialised at lock time and dispatched as an
        # ensemble of N alternatives.
        skills = [name for name, _ in call_log]
        self.assertTrue(
            'run_ensemble' in skills or 'run_simulation' in skills,
            f"Expected run_ensemble/run_simulation in dispatched skills, got {skills}",
        )
        self.assertNotIn('run_monte_carlo', skills)
        self.assertNotIn('run_batch', skills)

    # ---- Monte Carlo ----
    def test_monte_carlo_routes_to_run_monte_carlo(self):
        draft = _make_draft(self.user, 'monte_carlo')
        draft.step_state['step2'] = {
            'spatial_mode': 'circle',
            'center': {'lat': 33.0, 'lon': -86.5},
            'radius_km': 25,
            'grid_spacing': 0.1,
            'sampling_strategy': 'systematic',
            'n_points': 5,
        }
        draft.save(update_fields=['step_state'])
        template = _make_treatment(self.user, name='MC template')
        for i, lat in enumerate([32.8, 32.9, 33.0, 33.1, 33.2]):
            f = _make_field(
                self.user, name=f'MC Point #{i+1}', lat=lat, lon=-86.5)
            WizardDraftField.objects.create(draft=draft, field=f, ordering=i)
            WizardDraftTreatment.objects.create(
                draft=draft, treatment=template, field=f, ordering=i)

        params, wizard_params = self._capture_params_from_submit(draft)

        call_log = []
        self._patch_pipeline(call_log)
        envelope = run_full_simulation(
            params=params, wizard_params=wizard_params,
            user_id=self.user.id,
        )
        self.assertEqual(envelope.get('status'), 'completed')

        skills = [name for name, _ in call_log]
        self.assertIn('run_monte_carlo', skills)
        self.assertNotIn('run_simulation', skills)
        self.assertNotIn('run_ensemble', skills)
        self.assertNotIn('run_batch', skills)

    # ---- Batch ----
    def test_batch_routes_to_run_batch(self):
        draft = _make_draft(self.user, 'batch')
        fields = [
            _make_field(self.user, name=f'Batch F{i+1}',
                        lat=32.5 + i * 0.5, lon=-86.0 - i * 0.3)
            for i in range(3)
        ]
        protocols = [
            _make_treatment(self.user, name=f'P{i+1}',
                            planting_doy=120 + i * 10)
            for i in range(2)
        ]
        for i, f in enumerate(fields):
            WizardDraftField.objects.create(draft=draft, field=f, ordering=i)
        ordering = 0
        for f in fields:
            for p in protocols:
                WizardDraftTreatment.objects.create(
                    draft=draft, treatment=p, field=f, ordering=ordering)
                ordering += 1

        params, wizard_params = self._capture_params_from_submit(draft)

        call_log = []
        self._patch_pipeline(call_log)
        envelope = run_full_simulation(
            params=params, wizard_params=wizard_params,
            user_id=self.user.id,
        )
        # Batch returns 'completed' (the pipeline wraps the queued
        # response). Verify the right skill was the dispatch target.
        self.assertEqual(envelope.get('status'), 'completed')

        skills = [name for name, _ in call_log]
        self.assertIn('run_batch', skills)
        self.assertNotIn('run_simulation', skills)
        self.assertNotIn('run_ensemble', skills)
        self.assertNotIn('run_monte_carlo', skills)
        # And the batch payload carried the per-pair list (M × N = 6).
        batch_calls = [params for name, params in call_log if name == 'run_batch']
        self.assertEqual(len(batch_calls), 1)
        self.assertEqual(len(batch_calls[0].get('pair_params', [])), 6)


# ---------------------------------------------------------------------------
# Integration tests — actually run DSSAT for each experiment type.
# These do NOT mock dispatch_skill; they let the real handlers fire.
# Weather is mocked at prepare_weather() to keep the test hermetic (no
# NASA POWER calls). Soil + cultivar use real seeded catalog rows so DSSAT
# can build a valid FILEX. The async batch fan-out is mocked so the test
# runs synchronously.
#
# When a test here fails, the assertion message includes the run envelope's
# error/errors so the underlying DSSAT/build failure surfaces directly —
# these tests are designed to reproduce real prod failures, not paper over
# them.
# ---------------------------------------------------------------------------

def _make_real_field(user, soil_id, *, name='Real Field', lat=33.0, lon=-86.5,
                      elevation=100.0, weather='nasa_power'):
    """Build a Field bound to a real seeded StoredSoilProfile."""
    from dssat_agent.models import StoredSoilProfile, Field as FieldModel
    soil = StoredSoilProfile.objects.get(soil_id=soil_id)
    return FieldModel.objects.create(
        user=user,
        name=name,
        latitude=lat,
        longitude=lon,
        elevation=elevation,
        weather_source=weather,
        soil_profile=soil,
        location_label=f"{name} loc",
    )


def _make_real_treatment(user, *, name='Real Treatment', crop='MZ',
                          cultivar='IB0011', dssat_model='MZCER',
                          planting_doy=120):
    """Build a Treatment using a real seeded DSSATCultivar."""
    return Treatment.objects.create(
        user=user,
        name=name,
        crop_code=crop,
        cultivar_code=cultivar,
        dssat_model=dssat_model,
        planting={
            'mode': 'fixed', 'plme': 'S', 'ppop': 7.2,
            'plrs': 76, 'pldp': 5,
            'pdoy': planting_doy,
            'planting_month_day': '04-30',
        },
        simulation_controls={
            'start_offset_days': -30,
            'num_years': 1,
            'num_reps': 1,
            'water': 'Y',
            'nitrogen': 'Y',
            'co2': 'M',
        },
    )


def _synthesize_weather(lat=33.0, lon=-86.5, elev=100.0,
                         start_year=2024, end_year=2027):
    """Build a dict matching the format ``run_simulation`` expects when
    ``params['weather_data']`` is pre-set. Constant plausible values
    (Alabama growing-season-ish): tmax=28, tmin=18, rain=3, srad=18 MJ.
    Coverage spans multiple years to handle any sdate the test produces.
    """
    from datetime import datetime as _dt, timedelta as _td
    cur = _dt(start_year, 1, 1)
    end = _dt(end_year, 12, 31)
    records = []
    while cur <= end:
        records.append({
            'date': cur.strftime('%Y-%m-%d'),
            'tmax': 28.0, 'tmin': 18.0, 'rain': 3.0, 'srad': 18.0,
        })
        cur += _td(days=1)
    return {
        'station': {
            'lat': lat, 'lon': lon, 'elev': elev,
            'name': 'TEST', 'insi': 'TEST',
        },
        'records': records,
    }


class RunPipelineIntegrationTest(_SubmitDispatchBase):
    """End-to-end: real DSSAT runs through the actual ``dispatch_skill``
    handlers, with only weather + the batch async hand-off mocked."""

    # 9-char soil_id exercises the auto-pad path inside DSSATTools'
    # SoilProfile setter (installed by ``soil_service``).
    SOIL_ID = 'AL1147544'
    MZ_CULTIVAR = 'IB0011'  # DEKALBXL45 (MZCER) — confirmed seeded
    MZ_DSSAT_MODEL = 'MZCER'

    def setUp(self):
        super().setUp()
        # ``dispatch_skill`` calls ``close_old_connections()`` which under
        # the default CONN_MAX_AGE=0 closes the TestCase's transaction
        # connection mid-test. Patch it out so the run pipeline doesn't
        # rip the rug out from under our test fixtures.
        # ``dispatch_skill`` does ``from django.db import close_old_connections``
        # inside the function body, so we patch the source module — not
        # an attribute on the executor module.
        p = patch('django.db.close_old_connections', lambda: None)
        p.start()
        self.addCleanup(p.stop)

    def _patch_weather(self):
        """Inject synthetic weather records at every layer the run pipeline
        could ask for them. ``prepare_weather`` covers single/ensemble;
        ``get_weather_for_point`` covers Monte Carlo (which fetches
        per-grid-point weather directly from PostGIS, bypassing
        ``prepare_weather``)."""
        weather_data = _synthesize_weather()
        prep = patch(
            'dssat_agent.services.workflow.prepare_weather',
            return_value={'status': 'ready', 'weather_data': weather_data},
        )
        # Per-point weather (used by Monte Carlo's per-grid-point lookups).
        # ``get_weather_for_point`` returns a list of daily records.
        per_point = patch(
            'dssat_agent.services.spatial_data_service.get_weather_for_point',
            return_value=weather_data['records'],
        )
        for p in (prep, per_point):
            p.start()
            self.addCleanup(p.stop)

    def _patch_batch_async(self):
        """Replace ``run_batch_async.delay`` with a no-op recorder. The
        batch BatchExperiment row is created synchronously inside
        ``_handle_run_batch``; verifying that is enough — the per-child
        DSSAT runs are exercised via the single-experiment integration
        test."""
        m = MagicMock(return_value=MagicMock(id='fake-batch-task'))
        p = patch(
            'dssat_agent.tasks.run_batch_async.delay', m,
        )
        p.start()
        self.addCleanup(p.stop)
        return m

    def _capture_params_from_submit(self, draft):
        delay = self._patch_dispatch()
        submit_draft(draft, user=self.user)
        delay.assert_called_once()
        _, _, params, wizard_params, _ = delay.call_args.args
        return params, wizard_params

    # ---- Single ----
    def test_single_runs_dssat_to_completion(self):
        from dssat_agent.models import ExperimentSession, SimulationResult
        draft = _make_draft(self.user, 'single')
        f = _make_real_field(self.user, self.SOIL_ID, name='IT-Single')
        t = _make_real_treatment(self.user, name='IT-MZ-T1')
        WizardDraftField.objects.create(draft=draft, field=f, ordering=0)
        WizardDraftTreatment.objects.create(
            draft=draft, treatment=t, field=f, ordering=0)
        params, wizard_params = self._capture_params_from_submit(draft)

        self._patch_weather()
        envelope = run_full_simulation(
            params=params, wizard_params=wizard_params,
            user_id=self.user.id,
        )
        self.assertEqual(
            envelope.get('status'), 'completed',
            f"single run did not complete. envelope={envelope}",
        )
        sess = ExperimentSession.objects.get(pk=envelope['experiment_id'])
        self.assertEqual(
            sess.status, 'completed',
            f"ExperimentSession.status={sess.status}, "
            f"validation_errors={sess.validation_errors}",
        )
        results = SimulationResult.objects.filter(experiment=sess)
        self.assertEqual(results.count(), 1, "expected 1 SimulationResult")
        sr = results.first()
        self.assertTrue(
            sr.summary, f"SimulationResult.summary is empty: {sr.summary!r}",
        )

    # ---- Ensemble ----
    def test_ensemble_runs_dssat_to_completion(self):
        from dssat_agent.models import ExperimentSession, SimulationResult
        draft = _make_draft(self.user, 'ensemble')
        f = _make_real_field(self.user, self.SOIL_ID, name='IT-Ens')
        WizardDraftField.objects.create(draft=draft, field=f, ordering=0)
        for i, doy in enumerate([110, 120, 130]):
            t = _make_real_treatment(
                self.user, name=f'IT-Ens-T{i+1}', planting_doy=doy)
            WizardDraftTreatment.objects.create(
                draft=draft, treatment=t, field=f, ordering=i)
        params, wizard_params = self._capture_params_from_submit(draft)

        self._patch_weather()
        envelope = run_full_simulation(
            params=params, wizard_params=wizard_params,
            user_id=self.user.id,
        )
        self.assertEqual(
            envelope.get('status'), 'completed',
            f"ensemble run did not complete. envelope={envelope}",
        )
        sess = ExperimentSession.objects.get(pk=envelope['experiment_id'])
        self.assertEqual(sess.status, 'completed')
        # One SimulationResult per treatment.
        sr_count = SimulationResult.objects.filter(experiment=sess).count()
        self.assertEqual(sr_count, 3,
            f"expected 3 SimulationResults, got {sr_count}")

    # ---- Sensitivity with >9 treatments + tabular records (regression
    # for DSSATBatch._build_tabular_section's level-padding bug, where
    # levels 10+ shifted FERTILIZERS columns right by 1 char and
    # tripped IPFERT). ----
    def test_sensitivity_with_more_than_nine_fertilizer_treatments(self):
        from dssat_agent.models import ExperimentSession, SimulationResult
        draft = _make_draft(self.user, 'sensitivity')
        draft.step_state['step4'] = {
            'sensitivity': {
                'category': 'management',
                'axes': {
                    'practice': 'fertilizer',
                    'amountMin': 10, 'amountMax': 30, 'amountSteps': 5,
                    'appsMin': 2, 'appsMax': 3, 'gapDaysMin': 10,
                    'gapDaysMax': 20, 'gapSteps': 2,
                },
            },
            'baseline': {},
        }
        draft.save(update_fields=['step_state'])
        f = _make_real_field(self.user, self.SOIL_ID, name='IT-Sens-Nf')
        WizardDraftField.objects.create(draft=draft, field=f, ordering=0)
        # 12 treatments, each with 2-3 fertilizer events. Without the
        # padding fix, T10/T11/T12 each emit a FERTILIZERS row whose
        # treatment-level number column is 1 char too wide, shifting
        # FDATE/FMCD/FACD right and crashing IPFERT at FILEX line ~76.
        for i in range(12):
            t = Treatment.objects.create(
                user=self.user,
                name=f'IT-SF-T{i+1}',
                crop_code='MZ',
                cultivar_code=self.MZ_CULTIVAR,
                dssat_model=self.MZ_DSSAT_MODEL,
                planting={
                    'mode': 'fixed', 'plme': 'S', 'ppop': 7.2,
                    'plrs': 76, 'pldp': 5,
                    'pdoy': 120, 'planting_month_day': '04-30',
                },
                simulation_controls={
                    'start_offset_days': -30, 'num_years': 1,
                    'num_reps': 1, 'water': 'Y', 'nitrogen': 'Y',
                    'co2': 'M',
                },
                fertilizer=[
                    {'fdap': 0,  'famn': 10 + i, 'fmcd': 'FE005', 'facd': 'AP002', 'fdep': 10},
                    {'fdap': 30, 'famn': 10 + i, 'fmcd': 'FE005', 'facd': 'AP002', 'fdep': 10},
                ],
            )
            WizardDraftTreatment.objects.create(
                draft=draft, treatment=t, field=f, ordering=i)
        params, wizard_params = self._capture_params_from_submit(draft)

        self._patch_weather()
        envelope = run_full_simulation(
            params=params, wizard_params=wizard_params,
            user_id=self.user.id,
        )
        self.assertEqual(
            envelope.get('status'), 'completed',
            f"sensitivity (12 trt + fert) did not complete. envelope={envelope}",
        )
        sess = ExperimentSession.objects.get(pk=envelope['experiment_id'])
        self.assertEqual(sess.status, 'completed')
        # 12 treatments → 12 SimulationResult rows.
        sims = SimulationResult.objects.filter(experiment=sess).order_by('id')
        self.assertEqual(sims.count(), 12,
            f"expected 12 SimulationResults, got {sims.count()}")

        # Yields must actually vary across treatments. The DSSATBatch
        # MANAGEMENT line previously hardcoded ``FERTI=D`` (an invalid
        # DSSAT code) which caused DSSAT to silently ignore the
        # *FERTILIZERS section, producing identical near-zero yields
        # across every treatment regardless of fertilizer rate. Pin
        # this so a regression in management-mode derivation is caught
        # by the test suite.
        yields = []
        for sr in sims:
            s = sr.summary or {}
            y = s.get('harwt') or s.get('hwam') or s.get('hwah')
            yields.append(y)
        non_null_yields = [y for y in yields if y not in (None, 0)]
        self.assertGreater(
            len(non_null_yields), 0,
            f"all yields are null/zero — DSSAT may not be applying "
            f"fertilizer (FERTI mode bug). yields={yields}",
        )
        self.assertGreater(
            len(set(non_null_yields)), 1,
            f"all yields identical — DSSAT is ignoring per-treatment "
            f"fertilizer rate variation. yields={yields}",
        )

        # Per-treatment plant_growth must be populated. ``DSSATBatch``'s
        # raw run() output only sets per-run ``summary``; ensemble
        # service has to parse PlantGro.OUT into per-run records so the
        # ensemble crop-growth overlay chart can render. A regression
        # would silently return an empty growth chart in the gallery.
        with_growth = [
            sr for sr in sims if sr.plant_growth and len(sr.plant_growth) > 1
        ]
        self.assertGreater(
            len(with_growth), 0,
            "no SimulationResult has plant_growth records — "
            "ensemble crop-growth chart would be missing.",
        )

        # Maturity must be a sensible day count (not a YYYYDDD date).
        # Catches regressions where ``mdat`` (e.g. 2024202) leaks
        # through as if it were "days to maturity".
        mat_values = [
            (sr.summary or {}).get('mat')
            for sr in sims
            if (sr.summary or {}).get('mat') is not None
        ]
        self.assertGreater(
            len(mat_values), 0,
            "no SimulationResult has a 'mat' (days-to-maturity) value.",
        )
        for mv in mat_values:
            self.assertLess(
                mv, 1000,
                f"mat looks like a YYYYDDD date, not days-after-planting "
                f"(value={mv}); _clean_summary may be broken.",
            )

    # ---- Sensitivity (rides on the ensemble run pipeline) ----
    def test_sensitivity_runs_dssat_to_completion(self):
        from dssat_agent.models import ExperimentSession, SimulationResult
        draft = _make_draft(self.user, 'sensitivity')
        draft.step_state['step4'] = {
            'sensitivity': {
                'category': 'planting',
                'axes': {
                    'dateCentralDoy': 120, 'dateOffsetDays': 10, 'dateSteps': 3,
                    'popMin': 7.2, 'popMax': 7.2, 'popSteps': 1,
                    'rsMin': 76, 'rsMax': 76, 'rsSteps': 1,
                },
            },
            'baseline': {},
        }
        draft.save(update_fields=['step_state'])
        f = _make_real_field(self.user, self.SOIL_ID, name='IT-Sens')
        WizardDraftField.objects.create(draft=draft, field=f, ordering=0)
        for i, doy in enumerate([110, 120, 130]):
            t = _make_real_treatment(
                self.user, name=f'IT-Sens-T{i+1}: DOY={doy}',
                planting_doy=doy)
            WizardDraftTreatment.objects.create(
                draft=draft, treatment=t, field=f, ordering=i)
        params, wizard_params = self._capture_params_from_submit(draft)

        self._patch_weather()
        envelope = run_full_simulation(
            params=params, wizard_params=wizard_params,
            user_id=self.user.id,
        )
        self.assertEqual(
            envelope.get('status'), 'completed',
            f"sensitivity run did not complete. envelope={envelope}",
        )
        sess = ExperimentSession.objects.get(pk=envelope['experiment_id'])
        self.assertEqual(sess.status, 'completed')
        sr_count = SimulationResult.objects.filter(experiment=sess).count()
        self.assertEqual(sr_count, 3,
            f"expected 3 SimulationResults, got {sr_count}")

    # ---- Monte Carlo ----
    def test_monte_carlo_runs_dssat_to_completion(self):
        from dssat_agent.models import ExperimentSession, SimulationResult
        draft = _make_draft(self.user, 'monte_carlo')
        draft.step_state['step2'] = {
            'spatial_mode': 'circle',
            'center': {'lat': 33.0, 'lon': -86.5},
            'radius_km': 25,
            'grid_spacing': 0.1,
            'sampling_strategy': 'systematic',
            'n_points': 3,
        }
        draft.save(update_fields=['step_state'])
        template = _make_real_treatment(self.user, name='IT-MC-template')
        for i, lat in enumerate([32.95, 33.0, 33.05]):
            f = _make_real_field(
                self.user, self.SOIL_ID,
                name=f'IT-MC-Pt{i+1}', lat=lat, lon=-86.5)
            WizardDraftField.objects.create(draft=draft, field=f, ordering=i)
            WizardDraftTreatment.objects.create(
                draft=draft, treatment=template, field=f, ordering=i)
        params, wizard_params = self._capture_params_from_submit(draft)

        self._patch_weather()
        envelope = run_full_simulation(
            params=params, wizard_params=wizard_params,
            user_id=self.user.id,
        )
        self.assertEqual(
            envelope.get('status'), 'completed',
            f"monte_carlo run did not complete. envelope={envelope}",
        )
        sess = ExperimentSession.objects.get(pk=envelope['experiment_id'])
        self.assertEqual(sess.status, 'completed')
        # MC writes one SimulationResult per sampled point.
        sr_count = SimulationResult.objects.filter(experiment=sess).count()
        self.assertGreaterEqual(sr_count, 1,
            f"expected at least 1 SimulationResult for MC, got {sr_count}")

    # ---- Batch ----
    def test_batch_creates_batch_experiment_and_children(self):
        """Batch's per-pair fan-out is async (Celery chord). This test
        verifies the synchronous part: BatchExperiment row created with
        the right pair count + the run_batch_async hand-off was scheduled.
        Per-pair DSSAT execution is covered by the single integration
        test above.
        """
        from dssat_agent.models import BatchExperiment
        draft = _make_draft(self.user, 'batch')
        fields = [
            _make_real_field(
                self.user, self.SOIL_ID,
                name=f'IT-Batch-F{i+1}',
                lat=32.5 + i * 0.5, lon=-86.0 - i * 0.3)
            for i in range(3)
        ]
        protocols = [
            _make_real_treatment(
                self.user, name=f'IT-Batch-P{i+1}',
                planting_doy=120 + i * 10)
            for i in range(2)
        ]
        for i, f in enumerate(fields):
            WizardDraftField.objects.create(draft=draft, field=f, ordering=i)
        ordering = 0
        for f in fields:
            for p in protocols:
                WizardDraftTreatment.objects.create(
                    draft=draft, treatment=p, field=f, ordering=ordering)
                ordering += 1
        params, wizard_params = self._capture_params_from_submit(draft)

        self._patch_weather()
        async_mock = self._patch_batch_async()
        envelope = run_full_simulation(
            params=params, wizard_params=wizard_params,
            user_id=self.user.id,
        )
        self.assertEqual(
            envelope.get('status'), 'completed',
            f"batch dispatch did not complete. envelope={envelope}",
        )
        # BatchExperiment row created with 6 pairs (3 fields × 2 protocols).
        batch_id = envelope.get('batch_id')
        self.assertIsNotNone(batch_id, f"no batch_id in envelope: {envelope}")
        batch = BatchExperiment.objects.get(pk=batch_id)
        self.assertEqual(batch.total_locations, 6)
        # Async fan-out was scheduled (one .delay call).
        async_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Chat workflow integration — drives the LLM-style entry point
# (``ExperimentWizardInput`` → ``advance_wizard`` → ``run_experiment_tool``
# → ``run_full_simulation``). The chat path goes through Pydantic schema
# validation against ``ExperimentInput`` BEFORE reaching the run pipeline,
# so failures here surface bugs in either the chat-side run-arg builders
# or the schema/model alignment.
#
# Same hermetic mocks as the SPA integration tests: only weather +
# run_batch_async.delay are mocked. DSSAT actually runs.
# ---------------------------------------------------------------------------

class ChatPipelineIntegrationTest(TestCase):
    """End-to-end chat workflow with real DSSAT runs."""

    # 9-char soil_id exercises the auto-pad path inside DSSATTools'
    # SoilProfile setter (installed by ``soil_service``), proving real
    # short-IDs from the catalog round-trip cleanly through DSSATBatch.
    SOIL_ID = 'AL1147544'
    MZ_CULTIVAR = 'IB0011'
    MZ_DSSAT_MODEL = 'MZCER'

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='chatituser', email='ci@e.com', password='x',
        )
        # Same close_old_connections defense as the SPA integration tests.
        # ``dispatch_skill`` does ``from django.db import close_old_connections``
        # inside the function body, so we patch the source module — not
        # an attribute on the executor module.
        p = patch('django.db.close_old_connections', lambda: None)
        p.start()
        self.addCleanup(p.stop)

    def _patch_weather(self):
        weather_data = _synthesize_weather()
        prep = patch(
            'dssat_agent.services.workflow.prepare_weather',
            return_value={'status': 'ready', 'weather_data': weather_data},
        )
        per_point = patch(
            'dssat_agent.services.spatial_data_service.get_weather_for_point',
            return_value=weather_data['records'],
        )
        for p in (prep, per_point):
            p.start()
            self.addCleanup(p.stop)

    def _patch_batch_async(self):
        m = MagicMock(return_value=MagicMock(id='fake-batch-task'))
        p = patch('dssat_agent.tasks.run_batch_async.delay', m)
        p.start()
        self.addCleanup(p.stop)
        return m

    # ---- Single ----
    def test_chat_single_runs_dssat_to_completion(self):
        from dssat_agent.services.wizard import advance_wizard
        from dssat_agent.schemas.wizard import ExperimentWizardInput
        from data_agent.schemas.location import LocationPoint
        from dssat_agent.models import ExperimentSession, SimulationResult

        self._patch_weather()
        params = ExperimentWizardInput(
            experiment_type='single',
            crop='MZ',
            soil_id=self.SOIL_ID,
            location=LocationPoint(lat=33.0, lon=-86.5),
            planting_date=date(2025, 4, 30),
            shortcircuit=True,
        )
        envelope = advance_wizard(params, user=self.user)
        self.assertEqual(
            envelope.get('status'), 'completed',
            f"chat single did not complete. envelope={envelope}",
        )
        sess = ExperimentSession.objects.get(pk=envelope['experiment_id'])
        self.assertEqual(
            sess.status, 'completed',
            f"ExperimentSession.status={sess.status}, "
            f"validation_errors={sess.validation_errors}",
        )
        self.assertTrue(
            SimulationResult.objects.filter(experiment=sess).exists(),
            "expected at least 1 SimulationResult for chat single",
        )

    # ---- Ensemble ----
    def test_chat_ensemble_runs_dssat_to_completion(self):
        from dssat_agent.services.wizard import advance_wizard
        from dssat_agent.schemas.wizard import (
            ExperimentWizardInput, ProtocolInput,
        )
        from data_agent.schemas.location import LocationPoint
        from dssat_agent.models import ExperimentSession, SimulationResult

        self._patch_weather()
        params = ExperimentWizardInput(
            experiment_type='ensemble',
            crop='MZ',
            soil_id=self.SOIL_ID,
            location=LocationPoint(lat=33.0, lon=-86.5),
            planting_date=date(2025, 4, 30),
            soil_use_defaults=True,
            weather_use_defaults=True,
            cultivar_use_defaults=True,
            protocols=[
                ProtocolInput(name='High N', planting_date=date(2025, 4, 15)),
                ProtocolInput(name='Low N',  planting_date=date(2025, 5, 1)),
            ],
            protocols_complete=True,
            review_confirmed=True,
        )
        envelope = advance_wizard(params, user=self.user)
        self.assertEqual(
            envelope.get('status'), 'completed',
            f"chat ensemble did not complete. envelope={envelope}",
        )
        sess = ExperimentSession.objects.get(pk=envelope['experiment_id'])
        self.assertEqual(sess.status, 'completed')
        sr_count = SimulationResult.objects.filter(experiment=sess).count()
        self.assertGreaterEqual(
            sr_count, 1,
            f"expected ≥1 SimulationResult for chat ensemble, got {sr_count}",
        )

    # ---- Sensitivity ----
    def test_chat_sensitivity_runs_dssat_to_completion(self):
        from dssat_agent.services.wizard import advance_wizard
        from dssat_agent.schemas.wizard import (
            ExperimentWizardInput, SensitivitySweep,
        )
        from data_agent.schemas.location import LocationPoint
        from dssat_agent.models import ExperimentSession, SimulationResult

        self._patch_weather()
        params = ExperimentWizardInput(
            experiment_type='sensitivity',
            crop='MZ',
            soil_id=self.SOIL_ID,
            location=LocationPoint(lat=33.0, lon=-86.5),
            planting_date=date(2025, 4, 30),
            soil_use_defaults=True,
            weather_use_defaults=True,
            cultivar_use_defaults=True,
            sensitivity=SensitivitySweep(
                category='planting',
                date_central_doy=120, date_offset_days=10, date_steps=3,
                pop_steps=1, rs_steps=1,
            ),
            planting_use_defaults=True,
            management_use_defaults=True,
            initial_use_defaults=True,
            review_confirmed=True,
        )
        envelope = advance_wizard(params, user=self.user)
        self.assertEqual(
            envelope.get('status'), 'completed',
            f"chat sensitivity did not complete. envelope={envelope}",
        )
        sess = ExperimentSession.objects.get(pk=envelope['experiment_id'])
        self.assertEqual(sess.status, 'completed')
        sr_count = SimulationResult.objects.filter(experiment=sess).count()
        self.assertGreaterEqual(sr_count, 1)

    # ---- Monte Carlo ----
    def test_chat_monte_carlo_runs_dssat_to_completion(self):
        from dssat_agent.services.wizard import advance_wizard
        from dssat_agent.schemas.wizard import (
            ExperimentWizardInput, SpatialConfig,
        )
        from dssat_agent.models import ExperimentSession, SimulationResult

        self._patch_weather()
        params = ExperimentWizardInput(
            experiment_type='monte_carlo',
            crop='MZ',
            soil_id=self.SOIL_ID,
            spatial=SpatialConfig(
                mode='circle',
                center_lat=33.0, center_lon=-86.5, radius_km=25,
                sampling_strategy='systematic', grid_spacing=0.1,
                n_points=3,
            ),
            soil_use_defaults=True,
            weather_use_defaults=True,
            cultivar_use_defaults=True,
            auto_planting=False,
            planting_date=date(2025, 4, 30),
            planting_use_defaults=True,
            management_use_defaults=True,
            initial_use_defaults=True,
            review_confirmed=True,
        )
        envelope = advance_wizard(params, user=self.user)
        self.assertEqual(
            envelope.get('status'), 'completed',
            f"chat monte_carlo did not complete. envelope={envelope}",
        )
        sess = ExperimentSession.objects.get(pk=envelope['experiment_id'])
        self.assertEqual(sess.status, 'completed')
        sr_count = SimulationResult.objects.filter(experiment=sess).count()
        self.assertGreaterEqual(sr_count, 1)

    # ---- Batch ----
    def test_chat_batch_creates_batch_experiment(self):
        from dssat_agent.services.wizard import advance_wizard
        from dssat_agent.schemas.wizard import (
            ExperimentWizardInput, ProtocolInput,
        )
        from data_agent.schemas.location import LocationPoint
        from dssat_agent.models import BatchExperiment

        self._patch_weather()
        async_mock = self._patch_batch_async()
        params = ExperimentWizardInput(
            experiment_type='batch',
            crop='MZ',
            soil_id=self.SOIL_ID,
            locations=[
                LocationPoint(lat=33.0, lon=-86.5),
                LocationPoint(lat=33.5, lon=-87.0),
                LocationPoint(lat=32.5, lon=-86.0),
            ],
            locations_complete=True,
            planting_date=date(2025, 4, 30),
            protocols=[
                ProtocolInput(name='P1', planting_date=date(2025, 4, 15)),
                ProtocolInput(name='P2', planting_date=date(2025, 5, 1)),
            ],
            protocols_complete=True,
            review_confirmed=True,
        )
        envelope = advance_wizard(params, user=self.user)
        self.assertEqual(
            envelope.get('status'), 'completed',
            f"chat batch did not complete. envelope={envelope}",
        )
        # Either BatchExperiment row was created (new wizard pair_params
        # path) OR the chat path used the legacy template+location_config
        # path. Either way, exactly one batch row should exist.
        self.assertEqual(
            BatchExperiment.objects.count(), 1,
            "expected exactly 1 BatchExperiment row",
        )
        async_mock.assert_called_once()
