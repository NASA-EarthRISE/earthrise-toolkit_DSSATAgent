/**
 * Reactive draft store with debounced server sync.
 *
 * The store wraps a ``WizardDraft`` snapshot fetched from the API. Components
 * read step state via ``getStep(path)``, mutate via ``setStep(path, partial)``,
 * and subscribe via ``subscribe(fn)`` to re-render on changes. Mutations are
 * applied to the local snapshot synchronously and PATCHed to
 * ``/dssat/api/wizard-drafts/<id>/`` on a 300 ms debounce.
 *
 * Lock + cascade-clear go through the dedicated ``/lock/`` and ``/edit/``
 * endpoints to keep the cascade rules server-authoritative.
 */

import {drafts as draftAPI} from './api.js';

const DEBOUNCE_MS = 300;

export class Store {
    constructor(initialDraft) {
        this.draft = initialDraft;
        this._listeners = new Set();
        this._pendingPatch = null;
        this._patchTimer = null;
        this._inflightPatch = false;
    }

    // ----- Subscribe -----
    subscribe(fn) {
        this._listeners.add(fn);
        return () => this._listeners.delete(fn);
    }
    _emit() {
        for (const fn of this._listeners) {
            try { fn(this.draft); } catch (e) { console.error(e); }
        }
    }

    // ----- Read accessors -----
    get id() { return this.draft.id; }
    get experimentType() { return this.draft.experiment_type; }
    get currentStep() { return this.draft.current_step || 'step1'; }
    get lockedSteps() { return this.draft.locked_steps || []; }
    get fields() { return this.draft.fields || []; }
    /** Each treatment row is a (field, treatment) pair — see
     *  WizardDraftTreatment. `field_id` may be null while the user is
     *  staging Step 4 mid-flow; the lock hook always populates it. */
    get treatments() { return this.draft.treatments || []; }
    get status() { return this.draft.status; }

    isLocked(stepPath) {
        // A step is locked if it (or any prefix of it) is in locked_steps.
        const head = (stepPath || '').split('.', 1)[0];
        return (this.draft.locked_steps || []).some(
            (s) => s === stepPath || s === head || s.startsWith(stepPath + '.')
        );
    }

    getStep(stepPath) {
        const state = this.draft.step_state || {};
        return state[stepPath] || {};
    }

    // ----- Mutations (local + queued sync) -----
    setStep(stepPath, partial) {
        const merged = {...(this.draft.step_state || {})};
        merged[stepPath] = {...(merged[stepPath] || {}), ...partial};
        this.draft = {...this.draft, step_state: merged};
        this._queuePatch({step_state: {[stepPath]: merged[stepPath]}});
        this._emit();
    }

    setExperimentType(type) {
        this.draft = {...this.draft, experiment_type: type};
        this._queuePatch({experiment_type: type});
        this._emit();
    }

    setCurrentStep(stepPath) {
        this.draft = {...this.draft, current_step: stepPath};
        this._queuePatch({current_step: stepPath});
        this._emit();
    }

    async setFields(items) {
        // Replace attached field set. Server is source of truth so this
        // round-trips immediately rather than queuing.
        const r = await draftAPI.patch(this.id, {fields: items});
        this.draft = r.draft;
        this._emit();
    }

    /** Replace the draft's WizardDraftTreatment rows.
     *
     * Each item is ``{treatment_id, field_id?, ordering?}``. ``field_id``
     * is the per-row pairing — for single/ensemble/sensitivity it'll be
     * the same field on every row; for MC the same treatment_id on every
     * row; for batch a free matrix.
     */
    async setTreatments(items) {
        const r = await draftAPI.patch(this.id, {treatments: items});
        this.draft = r.draft;
        this._emit();
    }

    // ----- Lock / cascade-clear -----
    async lock(stepPath) {
        await this._flushPatches();
        const r = await draftAPI.lock(this.id, stepPath);
        this.draft = r.draft;
        this._emit();
    }

    async edit(stepPath) {
        await this._flushPatches();
        const r = await draftAPI.edit(this.id, stepPath);
        this.draft = r.draft;
        this._emit();
        return r;
    }

    async submit() {
        await this._flushPatches();
        return draftAPI.submit(this.id);
    }

    // ----- Internal: debounced PATCH -----
    _queuePatch(partial) {
        // Merge into pending so multiple setStep() calls within the debounce
        // window collapse into one PATCH.
        if (!this._pendingPatch) this._pendingPatch = {};
        for (const [k, v] of Object.entries(partial)) {
            if (k === 'step_state') {
                this._pendingPatch.step_state =
                    {...(this._pendingPatch.step_state || {}), ...v};
            } else {
                this._pendingPatch[k] = v;
            }
        }
        if (this._patchTimer) clearTimeout(this._patchTimer);
        this._patchTimer = setTimeout(() => this._flushPatches(), DEBOUNCE_MS);
    }

    /** Force-flush any debounced patches before continuing. Used by the
     *  resolve-at-lock path to make sure resolved_planting_dates lands on
     *  the server *before* setTreatments overwrites the local draft with
     *  the server's response. */
    async flushPending() {
        return this._flushPatches();
    }

    async _flushPatches() {
        if (this._patchTimer) {
            clearTimeout(this._patchTimer);
            this._patchTimer = null;
        }
        if (!this._pendingPatch) return;
        const payload = this._pendingPatch;
        this._pendingPatch = null;
        if (this._inflightPatch) {
            // Re-queue on top of the inflight to avoid losing data.
            const reschedule = () => this._queuePatch(payload);
            return new Promise((resolve) => {
                const tick = () => {
                    if (this._inflightPatch) setTimeout(tick, 50);
                    else { reschedule(); resolve(); }
                };
                tick();
            });
        }
        this._inflightPatch = true;
        try {
            const r = await draftAPI.patch(this.id, payload);
            // Reconcile attachments back from server (in case ordering shifted).
            this.draft = {
                ...this.draft,
                ...r.draft,
                // Local step_state may be ahead of server if more PATCHes
                // queued since this request started.
                step_state: {
                    ...(r.draft.step_state || {}),
                    ...(this.draft.step_state || {}),
                },
            };
        } catch (e) {
            console.error('Draft patch failed:', e);
        } finally {
            this._inflightPatch = false;
        }
    }
}
