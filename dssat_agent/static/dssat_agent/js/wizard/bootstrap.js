/**
 * Wizard SPA entry point.
 *
 * Resolves an active draft (or creates one), instantiates the store, and
 * starts the controller.
 */

import {drafts} from './api.js';
import {Store} from './store.js';
import {WizardController} from './controller.js';

async function boot() {
    const root = document.getElementById('wizard-step');
    const breadcrumbRoot = document.getElementById('wizard-breadcrumb');
    const navRoot = document.getElementById('wizard-nav');
    const statusRoot = document.getElementById('wizard-status');
    if (!root || !breadcrumbRoot || !navRoot) {
        console.error('Wizard SPA shell elements missing.');
        return;
    }

    let draft = null;
    try {
        const r = await drafts.active();
        draft = r && r.draft;
    } catch (e) {
        console.warn('No active draft (will create):', e.message);
    }
    if (!draft) {
        try {
            const r = await drafts.create({experiment_type: 'single'});
            draft = r.draft;
        } catch (e) {
            root.innerHTML = `<div class="wizard-flash wizard-flash-error">`
                + `Could not start a wizard draft: ${e.message}</div>`;
            return;
        }
    }

    const store = new Store(draft);
    const controller = new WizardController({
        root, breadcrumbRoot, navRoot, statusRoot, store,
    });
    window._wizard = {store, controller};  // exposed for debugging
    await controller.start();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
} else {
    boot();
}
