/**
 * Step-trail at the top of the wizard.
 *
 * Renders one dot per top-level step. State per dot:
 *   - active   (current step)
 *   - locked   (in store.locked_steps)
 *   - pending  (default)
 *
 * Clicking a dot navigates to that step. Jumping to a *locked* step
 * triggers the controller's ``confirmEdit`` flow which re-uses the
 * lock dialog (server-side cascade-clear).
 */

import {h, clear, on} from './dom.js';
import {TOP_LEVEL_ORDER, STEP_LABELS, topLevelOf} from './step_tree.js';

export function renderBreadcrumb(root, store, controller) {
    clear(root);
    const current = topLevelOf(store.currentStep);
    const wrapper = h('div', {class: 'wizard-breadcrumb'});
    TOP_LEVEL_ORDER.forEach((key, i) => {
        const locked = store.isLocked(key);
        const active = key === current;
        const dot = h('button', {
            type: 'button',
            class: [
                'wizard-bc-dot',
                active ? 'active' : '',
                locked ? 'locked' : '',
            ].filter(Boolean).join(' '),
            dataset: {step: key},
        }, h('span', {class: 'wizard-bc-num'}, String(i + 1)),
           h('span', {class: 'wizard-bc-label'}, STEP_LABELS[key]));
        on(dot, 'click', () => controller.requestStepChange(key));
        wrapper.appendChild(dot);
        if (i < TOP_LEVEL_ORDER.length - 1) {
            wrapper.appendChild(h('span', {class: 'wizard-bc-sep'}));
        }
    });
    root.appendChild(wrapper);
}
