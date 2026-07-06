/**
 * Top-level wizard controller.
 *
 * Owns:
 *   - the active step component (loaded lazily via step_tree.resolveStepModule)
 *   - step transitions (next / prev / breadcrumb-jump)
 *   - lock + cascade-clear orchestration
 *   - error / loading flashes
 *
 * Components do not advance the step themselves — they call back into
 * ``controller.goNext()`` / ``controller.requestStepChange(key)`` so the
 * controller can run validation, persist state, and update the UI in one
 * place.
 */

import {h, clear} from './dom.js';
import {
    TOP_LEVEL_ORDER, STEP_LABELS, resolveStepModule,
    topLevelOf, prevTopLevel, nextTopLevel,
} from './step_tree.js';
import {renderBreadcrumb} from './breadcrumb.js';
import {renderNav} from './nav.js';
import {renderLockedOverlay, confirmEdit} from './lock.js';

export class WizardController {
    constructor({root, store, breadcrumbRoot, navRoot, statusRoot}) {
        this.root = root;
        this.store = store;
        this.breadcrumbRoot = breadcrumbRoot;
        this.navRoot = navRoot;
        this.statusRoot = statusRoot;

        this._currentRender = null;
        store.subscribe(() => this._onStateChange());
    }

    async start() {
        this._renderChrome();
        await this._renderActiveStep();
    }

    // ----- Chrome (breadcrumb, nav, status flash) -----
    _renderChrome() {
        renderBreadcrumb(this.breadcrumbRoot, this.store, this);
        renderNav(this.navRoot, this.store, this);
    }

    _flash(message, kind = 'info') {
        if (!this.statusRoot) return;
        clear(this.statusRoot);
        this.statusRoot.appendChild(h('div', {
            class: `wizard-flash wizard-flash-${kind}`,
        }, message));
        setTimeout(() => clear(this.statusRoot), 4000);
    }

    // ----- Step render -----
    async _renderActiveStep() {
        const stepKey = topLevelOf(this.store.currentStep);
        clear(this.root);

        // Resolve the step module first so we can ask it for a custom
        // lockSummary even when we're just rendering the read-only
        // overlay (locked state).
        let mod;
        try {
            mod = await resolveStepModule(stepKey, this.store.experimentType);
        } catch (e) {
            this.root.appendChild(h('div', {class: 'wizard-flash wizard-flash-error'},
                'Failed to load step module: ' + e.message));
            return;
        }

        if (this.store.isLocked(stepKey) && stepKey !== 'step5') {
            const summary = (mod && typeof mod.lockSummary === 'function')
                ? mod.lockSummary(this.store)
                : this._buildLockSummary(stepKey);
            renderLockedOverlay(this.root, stepKey, summary,
                () => this._handleEditRequest(stepKey));
            this._renderChrome();
            return;
        }

        if (!mod || !mod.render) {
            this.root.appendChild(h('div', {class: 'wizard-flash wizard-flash-error'},
                `Step module for ${stepKey} has no render() export`));
            return;
        }
        try {
            await mod.render(this.root, this.store, this);
        } catch (e) {
            console.error(e);
            this.root.appendChild(h('div', {class: 'wizard-flash wizard-flash-error'},
                'Step render error: ' + e.message));
        }
        this._currentRender = {stepKey, mod};
        this._renderChrome();
    }

    /** Generic lock summary fallback when a step module doesn't export a
     *  custom ``lockSummary``. Dumps non-empty step_state entries inline. */
    _buildLockSummary(stepKey) {
        const state = this.store.getStep(stepKey) || {};
        const items = Object.entries(state)
            .filter(([k, v]) => v != null && v !== '' && !(typeof v === 'object' && Object.keys(v).length === 0))
            .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);
        return items.length
            ? items.join('; ')
            : 'Selections recorded.';
    }

    // ----- Step transitions -----
    async goNext() {
        const stepKey = topLevelOf(this.store.currentStep);
        const current = this._currentRender;
        if (current && current.mod.validate) {
            const v = current.mod.validate(this.store);
            if (v && v.ok === false) {
                this._flash(v.message || 'Please complete this step before continuing.', 'error');
                return;
            }
        }

        // Show a context-aware loading overlay across the whole lock+
        // transition flow. Step modules can export ``lockProgressLabel``
        // to provide step-specific copy describing what's running and why
        // it might take a moment.
        const progress = this._buildProgressLabel(stepKey, current);
        this._showLoadingOverlay(progress.title, progress.detail);
        try {
            if (current && current.mod.onLockBeforeNext) {
                try { await current.mod.onLockBeforeNext(this.store, this); }
                catch (e) {
                    this._flash(e.message, 'error');
                    return;
                }
            }
            try {
                await this.store.lock(stepKey);
            } catch (e) {
                this._flash('Lock failed: ' + e.message, 'error');
                return;
            }
            const next = nextTopLevel(stepKey);
            if (next) {
                await this.store.setCurrentStep(next);
                await this._renderActiveStep();
            }
        } finally {
            this._hideLoadingOverlay();
        }
    }

    // ----- Loading overlay -----
    _buildProgressLabel(stepKey, current) {
        const mod = current && current.mod;
        if (mod && typeof mod.lockProgressLabel === 'function') {
            try {
                const out = mod.lockProgressLabel(this.store);
                if (out && out.title) return out;
            } catch (_e) { /* fall through to default */ }
        }
        return {
            title: `Saving ${STEP_LABELS[stepKey] || stepKey}…`,
            detail: 'Persisting your selections — this should only take a moment.',
        };
    }

    _showLoadingOverlay(title, detail) {
        if (this._loadingEl) this._hideLoadingOverlay();
        const card = h('div', {class: 'wizard-loading-card'},
            h('div', {class: 'wizard-loading-spinner'}),
            h('h3', {}, title || 'Working…'),
            detail ? h('p', {}, detail) : null,
        );
        const overlay = h('div', {class: 'wizard-loading-overlay'}, card);
        document.body.appendChild(overlay);
        this._loadingEl = overlay;
    }

    _hideLoadingOverlay() {
        if (!this._loadingEl) return;
        if (this._loadingEl.parentNode) {
            this._loadingEl.parentNode.removeChild(this._loadingEl);
        }
        this._loadingEl = null;
    }

    async goPrev() {
        const stepKey = topLevelOf(this.store.currentStep);
        const prev = prevTopLevel(stepKey);
        if (!prev) return;
        await this.store.setCurrentStep(prev);
        await this._renderActiveStep();
    }

    /** Breadcrumb-driven jump to a step. */
    async requestStepChange(targetKey) {
        const current = topLevelOf(this.store.currentStep);
        if (targetKey === current) return;
        const idxCurrent = TOP_LEVEL_ORDER.indexOf(current);
        const idxTarget = TOP_LEVEL_ORDER.indexOf(targetKey);

        // Backward jumps to a *locked* step trigger cascade-clear.
        if (idxTarget < idxCurrent && this.store.isLocked(targetKey)) {
            const downstream = TOP_LEVEL_ORDER.slice(idxTarget + 1);
            const ok = await confirmEdit(targetKey, downstream);
            if (!ok) return;
            try {
                await this.store.edit(targetKey);
            } catch (e) {
                this._flash('Edit failed: ' + e.message, 'error');
                return;
            }
        } else if (idxTarget > idxCurrent) {
            // Forward jumps only allowed if every intermediate step is
            // locked (i.e. the user has completed them).
            for (let i = idxCurrent; i < idxTarget; i++) {
                if (!this.store.isLocked(TOP_LEVEL_ORDER[i])) {
                    this._flash('Complete the current step first.', 'error');
                    return;
                }
            }
        }
        await this.store.setCurrentStep(targetKey);
        await this._renderActiveStep();
    }

    async _handleEditRequest(stepKey) {
        const idxTarget = TOP_LEVEL_ORDER.indexOf(stepKey);
        const downstream = TOP_LEVEL_ORDER.slice(idxTarget + 1);
        const ok = await confirmEdit(stepKey, downstream);
        if (!ok) return;
        try {
            await this.store.edit(stepKey);
        } catch (e) {
            this._flash('Edit failed: ' + e.message, 'error');
            return;
        }
        await this._renderActiveStep();
    }

    async submit() {
        try {
            const r = await this.store.submit();
            // Success — bounce to the chat the submit endpoint created.
            if (r && r.redirect_url) {
                location.href = r.redirect_url;
            } else if (r && r.chat_id) {
                location.href = `/chats/${r.chat_id}/`;
            }
        } catch (e) {
            this._flash('Submit failed: ' + e.message, 'error');
        }
    }

    _onStateChange() {
        // Re-draw chrome on every store mutation (cheap; no-ops if unchanged).
        this._renderChrome();
    }
}
