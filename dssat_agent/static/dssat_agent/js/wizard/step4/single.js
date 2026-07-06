/**
 * Step 4 (Single experiment): one Treatment record.
 *
 * Wraps the shared protocol_editor and persists the bundle as a Treatment
 * row + attaches it to the draft on Lock.
 */

import {h, clear} from '../dom.js';
import {treatments as treatmentsAPI} from '../api.js';
import {mount as mountEditor} from './protocol_editor.js';
import {
    resolveAutoPlantingDatesForPairs,
    pruneResolvedPlantingDates,
} from '../auto_planting_resolver.js';

let editor = null;
let currentValue = null;

export async function render(root, store) {
    clear(root);
    const stepState = store.getStep('step4') || {};
    const seed = stepState.protocol || {};
    const year = (store.getStep('step1') || {}).year;

    const panel = h('div', {class: 'wizard-step-panel'},
        h('h2', {class: 'step-title'}, 'Treatment'),
        h('p', {class: 'step-description'},
          'Configure the single treatment for this run. All five sections '
          + '(crop & cultivar, planting & harvest, IC, simulation controls, '
          + 'management) feed into one row of the FILEX TREATMENTS table.'),
    );
    const editorHost = h('div');
    panel.appendChild(editorHost);
    root.appendChild(panel);

    editor = mountEditor(editorHost, {
        value: seed,
        onChange: (val) => {
            currentValue = val;
            store.setStep('step4', {protocol: val});
        },
        options: {year, fields: store.fields},
    });
    currentValue = editor.getValue();
}

export function validate(store) {
    const v = currentValue || (store.getStep('step4') || {}).protocol;
    if (!v || !v.crop_code) {
        return {ok: false, message: 'Pick a crop to continue.'};
    }
    if (!v.planting || !v.planting.mode) {
        return {ok: false, message: 'Pick a planting-date mode to continue.'};
    }
    if (v.planting.mode === 'fixed' && v.planting.pdoy == null) {
        return {ok: false, message: 'Pick a planting date to continue.'};
    }
    if (v.planting.mode === 'auto' && !v.planting.auto_source) {
        return {ok: false, message: 'Pick an Auto planting-date source.'};
    }
    return {ok: true};
}

/** Lock hook: persist the protocol as a Treatment record + attach + write pair.
 *  Resolves any Auto-mode planting dates against the in-situ raster
 *  before committing the pair; aborts the lock if any pair fails to
 *  resolve. */
export async function onLockBeforeNext(store) {
    const v = currentValue || (store.getStep('step4') || {}).protocol || {};
    const name = `${v.crop_code} ${v.cultivar_code || ''}`.trim() || 'Treatment';
    const r = await treatmentsAPI.create({
        name,
        crop_code: v.crop_code,
        cultivar_code: v.cultivar_code || '',
        dssat_model: v.dssat_model || '',
        planting: v.planting || {},
        harvest: v.harvest || null,
        initial_conditions: v.initial_conditions || null,
        simulation_controls: v.simulation_controls || {},
        fertilizer: v.fertilizer || null,
        irrigation: v.irrigation || null,
        residue: v.residue || null,
        chemical: v.chemical || null,
        tillage: v.tillage || null,
    });
    const field = store.fields[0] || {};
    const pair = {
        treatment_id: r.treatment.id,
        field_id: field.id,
        treatment_payload: v,
        field,
    };
    // Resolve Auto-mode planting dates first; throws on any failure so
    // the controller surfaces the error and stays on Step 4.
    await resolveAutoPlantingDatesForPairs(store, [pair]);
    await store.setTreatments([{
        treatment_id: pair.treatment_id,
        field_id: pair.field_id,
        ordering: 0,
    }]);
    pruneResolvedPlantingDates(store, [pair]);
}
