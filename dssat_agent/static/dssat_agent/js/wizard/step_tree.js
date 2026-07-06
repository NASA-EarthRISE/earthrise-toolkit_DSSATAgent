/**
 * Declarative step tree per experiment type.
 *
 * Each top-level step has a single key (``'step1'``..``'step5'``) and a
 * component. The component implements:
 *
 *   render(root, store, controller) -> void
 *
 * and may also expose:
 *
 *   validate(store) -> {ok: bool, message?: string}
 *   onLockBeforeNext(store) -> Promise|void   // hook before lock+advance
 *
 * The controller dispatches on ``store.experimentType`` to pick the
 * variant of step 2 / step 3 / step 4. Steps 2 and 4 are the only ones
 * that genuinely fork; steps 1, 3.summary, 3.fields, 5 share components
 * across all five experiment types.
 */

export const TOP_LEVEL_ORDER = ['step1', 'step2', 'step3', 'step4', 'step5'];

export const STEP_LABELS = {
    step1: 'Type',
    step2: 'Location',
    step3: 'Fields',
    step4: 'Treatments',
    step5: 'Review',
};

/**
 * Resolve which component module handles ``stepKey`` for the given
 * experiment type. Returns a lazy import so we don't load every variant up
 * front; the controller awaits the import on the first transition into
 * each step.
 */
export function resolveStepModule(stepKey, experimentType) {
    switch (stepKey) {
        case 'step1':
            return import('./step1/type.js');
        case 'step2':
            if (experimentType === 'monte_carlo') return import('./step2/mc.js');
            if (experimentType === 'batch')       return import('./step2/batch.js');
            return import('./step2/single.js');
        case 'step3':
            return import('./step3/index.js');
        case 'step4':
            switch (experimentType) {
                case 'sensitivity': return import('./step4/sensitivity.js');
                case 'ensemble':    return import('./step4/ensemble.js');
                case 'monte_carlo': return import('./step4/mc.js');
                case 'batch':       return import('./step4/batch.js');
                default:            return import('./step4/single.js');
            }
        case 'step5':
            return import('./step5/review.js');
        default:
            return Promise.reject(new Error(`unknown step: ${stepKey}`));
    }
}

/**
 * Top-level helpers used by the controller and breadcrumb.
 */
export function topLevelOf(stepPath) {
    return (stepPath || 'step1').split('.', 1)[0];
}

export function indexOfTopLevel(stepPath) {
    return TOP_LEVEL_ORDER.indexOf(topLevelOf(stepPath));
}

export function nextTopLevel(stepPath) {
    const i = indexOfTopLevel(stepPath);
    if (i < 0 || i >= TOP_LEVEL_ORDER.length - 1) return null;
    return TOP_LEVEL_ORDER[i + 1];
}

export function prevTopLevel(stepPath) {
    const i = indexOfTopLevel(stepPath);
    if (i <= 0) return null;
    return TOP_LEVEL_ORDER[i - 1];
}
