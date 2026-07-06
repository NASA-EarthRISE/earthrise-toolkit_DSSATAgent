/**
 * Step 4 (Sensitivity Analysis): single-category Cartesian generator.
 *
 * The sensitivity UI now lives inside the protocol-editor accordion as
 * a chip after Crop & Cultivar (because the cultivar list, planting
 * date axes, and management practice axes all depend on the chosen
 * crop's model). This wrapper just mounts ``protocol_editor`` with
 * ``options.experimentType = 'sensitivity'`` and persists the merged
 * state back to ``step4.baseline`` + ``step4.sensitivity`` (kept as
 * separate keys so the run-pipeline translator
 * ``draft_submit._build_sensitivity`` can read them in the legacy shape
 * it already expects).
 *
 * Lock hook calls ``generate(...)`` to materialise N treatments and
 * persists them as Treatment records, all paired to the single field.
 */

import {h, clear} from '../dom.js';
import {treatments as treatmentsAPI} from '../api.js';
import {mount as mountEditor} from './protocol_editor.js';
import {generate} from './sensitivity_axes.js';
import {
    resolveAutoPlantingDatesForPairs,
    pruneResolvedPlantingDates,
} from '../auto_planting_resolver.js';

let editor = null;

export async function render(root, store) {
    clear(root);
    const stepState = store.getStep('step4') || {};
    const seed = {
        ...(stepState.baseline || {}),
        // Sensitivity sweep config lives inside the protocol's state
        // object during edit; the wrapper splits it out again on save.
        sensitivity: stepState.sensitivity || {},
    };
    const year = (store.getStep('step1') || {}).year;

    const panel = h('div', {class: 'wizard-step-panel'},
        h('h2', {class: 'step-title'}, 'Sensitivity Analysis'),
        h('p', {class: 'step-description'},
            'Pick a crop & cultivar baseline, then configure the sweep '
            + 'axis (Planting / Cultivar / Management). The Cartesian '
            + 'generator emits one Treatment per combination — capped '
            + 'at 99.'),
    );
    const editorHost = h('div');
    panel.appendChild(editorHost);
    root.appendChild(panel);

    editor = mountEditor(editorHost, {
        value: seed,
        onChange: (val) => {
            const {sensitivity, ...baseline} = val;
            store.setStep('step4', {baseline, sensitivity: sensitivity || {}});
        },
        options: {year, fields: store.fields, experimentType: 'sensitivity'},
    });
}

export function validate(store) {
    const stepState = store.getStep('step4') || {};
    const sens = stepState.sensitivity || {};
    const base = stepState.baseline || {};
    if (!base.crop_code) {
        return {ok: false,
                message: 'Pick a crop in the Crop & Cultivar section.'};
    }
    if (!sens.category) {
        return {ok: false,
                message: 'Pick a sensitivity sweep category.'};
    }
    const generated = generate(sens, base);
    if (!generated.length) {
        return {ok: false,
                message: 'Sweep produces zero treatments — fill in the axes.'};
    }
    if (generated.length > 99) {
        return {ok: false,
                message: `Sweep produces ${generated.length} treatments; max 99.`};
    }
    return {ok: true};
}

/** Lock hook: persist each generated treatment + attach + write pairs.
 *
 * Sensitivity is 1 field × N generated treatments. Same pairing model as
 * ensemble: one (field, treatment) row per generated variant. */
export async function onLockBeforeNext(store) {
    const stepState = store.getStep('step4') || {};
    const generated = generate(stepState.sensitivity || {}, stepState.baseline || {});
    const field = store.fields[0] || {};
    const created = [];
    const pairs = [];
    for (let i = 0; i < generated.length; i++) {
        const p = generated[i];
        const r = await treatmentsAPI.create({
            name: p.name || `Sensitivity ${i + 1}`,
            crop_code: p.crop_code,
            cultivar_code: p.cultivar_code || '',
            dssat_model: p.dssat_model || '',
            planting: p.planting || {},
            harvest: p.harvest || null,
            initial_conditions: p.initial_conditions || null,
            simulation_controls: p.simulation_controls || {},
            fertilizer: p.fertilizer || null,
            irrigation: p.irrigation || null,
            residue: p.residue || null,
            chemical: p.chemical || null,
            tillage: p.tillage || null,
        });
        created.push({
            treatment_id: r.treatment.id,
            field_id: field.id,
            ordering: i,
        });
        pairs.push({
            treatment_id: r.treatment.id,
            field_id: field.id,
            treatment_payload: p,
            field,
        });
    }
    await resolveAutoPlantingDatesForPairs(store, pairs);
    await store.setTreatments(created);
    pruneResolvedPlantingDates(store, pairs);
}
