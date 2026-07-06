/**
 * Step 1: Experiment Type
 *
 * Single-select grid of five cards. On change, ``store.setExperimentType()``
 * also fires the cascade-clear server-side so any half-filled state from a
 * previous type pick gets blown away. Clicking Next on the controller's
 * nav locks Step 1.
 */

import {h, clear, on} from '../dom.js';

const TYPES = [
    {
        key: 'single',
        badge: 'SINGLE',
        title: 'Single Simulation',
        desc: 'One run with full parameterization. Best for focused '
            + 'experiments at a single site.',
    },
    {
        key: 'sensitivity',
        badge: 'SWEEP',
        title: 'Sensitivity Analysis',
        desc: 'Pick one variable category (planting, cultivar, or one '
            + 'management practice) and sweep it Cartesian-style within that '
            + 'category. Categories never mix.',
    },
    {
        key: 'ensemble',
        badge: 'CUSTOM',
        title: 'Custom Ensemble',
        desc: 'Build multiple treatment protocols by hand. Each treatment is '
            + 'its own bundle of crop, planting, IC, sim controls, and '
            + 'management.',
    },
    {
        key: 'monte_carlo',
        badge: 'SPATIAL',
        title: 'Monte Carlo',
        desc: 'Spatial uncertainty analysis — sample many points across a '
            + 'region with one shared protocol. Per-field planting can be '
            + 'pulled from the in-situ raster at run time.',
    },
    {
        key: 'batch',
        badge: 'MATRIX',
        title: 'Batch / Multi-Location',
        desc: 'Multiple protocols mapped to multiple fields via a checkbox '
            + 'matrix. Each (field, protocol) cell becomes one DSSAT '
            + 'treatment.',
    },
];

export function render(root, store, controller) {
    clear(root);
    const panel = h('div', {class: 'wizard-step-panel'},
        h('h2', {class: 'step-title'}, 'Experiment Type'),
        h('p', {class: 'step-description'},
          'Choose the type of simulation and the calendar year to run it '
          + 'in. Switching either after Step 1 is locked will cascade-clear '
          + 'every later step.'),
    );

    // Year input — anchors every downstream date. Treatments store
    // planting as DOY and management events as DAP integers; this year
    // is the only place we record absolute time. Default to current year.
    const step1State = store.getStep('step1') || {};
    const currentYear = new Date().getFullYear();
    const year = step1State.year != null ? step1State.year : currentYear;
    if (step1State.year == null) {
        store.setStep('step1', {year: currentYear});
    }
    const yearInput = h('input', {
        type: 'number', class: 'wizard-input',
        min: '1900', max: '2100', step: '1',
        value: year,
        style: {maxWidth: '8rem'},
    });
    on(yearInput, 'input', () => {
        const v = parseInt(yearInput.value, 10);
        if (!isNaN(v)) store.setStep('step1', {year: v});
    });
    panel.appendChild(h('div', {class: 'wizard-form-group'},
        h('label', {}, 'Experiment Year'),
        yearInput,
        h('div', {class: 'step-description', style: {marginTop: '0.25rem'}},
          'Used to anchor every date in the run. Saved treatment '
          + 'protocols are year-less (DOY for planting, DAP for events) so '
          + 'they reuse cleanly across years.'),
    ));

    const grid = h('div', {class: 'type-cards'});
    const current = store.experimentType;
    for (const t of TYPES) {
        const card = h('div', {
            class: 'type-card' + (current === t.key ? ' selected' : ''),
            dataset: {type: t.key},
        },
            h('div', {class: 'type-card-badge'}, t.badge),
            h('div', {class: 'type-card-title'}, t.title),
            h('div', {class: 'type-card-desc'}, t.desc),
        );
        on(card, 'click', () => {
            store.setExperimentType(t.key);
            grid.querySelectorAll('.type-card').forEach((c) => {
                c.classList.toggle('selected', c.dataset.type === t.key);
            });
        });
        grid.appendChild(card);
    }
    panel.appendChild(grid);
    root.appendChild(panel);
}

export function validate(store) {
    if (!store.experimentType) {
        return {ok: false, message: 'Pick an experiment type to continue.'};
    }
    const s = store.getStep('step1') || {};
    if (!s.year || isNaN(parseInt(s.year, 10))) {
        return {ok: false, message: 'Set an experiment year.'};
    }
    return {ok: true};
}

/** Custom lock summary so the read-only overlay shows the chosen type +
 *  year (the type lives on the draft, not in step_state, so the generic
 *  dump misses it). */
export function lockSummary(store) {
    const s = store.getStep('step1') || {};
    const t = TYPES.find((x) => x.key === store.experimentType);
    const typeLabel = t ? t.title : (store.experimentType || '?');
    const year = s.year != null ? s.year : '?';
    return `${typeLabel} · ${year}`;
}
