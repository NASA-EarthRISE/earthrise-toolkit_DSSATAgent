/**
 * Bottom Back / Next / Submit bar.
 *
 * The controller owns step transitions; this module just renders three
 * buttons and dispatches their click events back to the controller.
 */

import {h, clear, on} from './dom.js';
import {topLevelOf, prevTopLevel, nextTopLevel} from './step_tree.js';

export function renderNav(root, store, controller) {
    clear(root);
    const current = topLevelOf(store.currentStep);
    const isLast = current === 'step5';
    const back = h('button', {
        type: 'button',
        class: 'wizard-btn btn-prev',
        disabled: current === 'step1',
    }, 'Back');
    on(back, 'click', () => controller.goPrev());

    const next = h('button', {
        type: 'button',
        class: 'wizard-btn btn-next',
    }, isLast ? 'Submit Experiment' : 'Next');
    on(next, 'click', () => {
        if (isLast) controller.submit();
        else controller.goNext();
    });

    root.appendChild(h('div', {class: 'wizard-nav'},
        back,
        h('div', {style: {flex: '1'}}),
        next,
    ));
}
