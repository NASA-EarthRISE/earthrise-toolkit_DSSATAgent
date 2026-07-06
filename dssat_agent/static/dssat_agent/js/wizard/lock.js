/**
 * Lock + cascade-clear dialog.
 *
 * Two responsibilities:
 *
 *   1. ``renderLockedOverlay(root, stepLabel, onEdit)`` — draws the
 *      read-only summary card with an "Edit & clear" button when the
 *      current step is locked.
 *
 *   2. ``confirmEdit(stepKey, downstream)`` — modal dialog asking
 *      "this will clear N downstream selections, continue?" before the
 *      controller calls ``store.edit(stepKey)``.
 */

import {h, on} from './dom.js';
import {STEP_LABELS} from './step_tree.js';

export function renderLockedOverlay(host, stepKey, summary, onEdit) {
    const overlay = h('div', {class: 'wizard-lock-overlay'},
        h('div', {class: 'wizard-lock-icon'}, '🔒'),
        h('div', {class: 'wizard-lock-title'},
          STEP_LABELS[stepKey] + ' is locked'),
        h('div', {class: 'wizard-lock-summary'}, summary || ''),
        h('button', {
            type: 'button',
            class: 'wizard-btn btn-edit-locked',
        }, 'Edit & clear future steps'),
    );
    on(overlay.querySelector('button'), 'click', onEdit);
    host.appendChild(overlay);
}

export function confirmEdit(stepKey, downstreamKeys) {
    const downstreamLabels = (downstreamKeys || [])
        .map((k) => STEP_LABELS[k] || k)
        .join(', ');
    const msg = downstreamLabels
        ? `Editing ${STEP_LABELS[stepKey] || stepKey} will clear: `
          + `${downstreamLabels}. This cannot be undone. Continue?`
        : `Re-open ${STEP_LABELS[stepKey] || stepKey} for editing?`;
    return Promise.resolve(window.confirm(msg));
}
