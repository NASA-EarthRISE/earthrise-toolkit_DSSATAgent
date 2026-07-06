/**
 * Step 4 (Custom Ensemble): N treatment protocols, one per ensemble member.
 *
 * UI:
 *   - Treatment-selector tab bar, one tab per protocol.
 *   - Inside each tab: the shared protocol_editor.
 *   - "Import from another treatment" dropdown lets the user copy any one
 *     of the five sections from a sibling protocol into the current one
 *     (matches the spec: avoid retyping when only one section differs).
 *   - Live count + 99-cap.
 */

import {h, clear, on, escapeHtml} from '../dom.js';
import {treatments as treatmentsAPI} from '../api.js';
import {renderSelector} from '../treatment_selector.js';
import {mount as mountEditor} from './protocol_editor.js';
import {
    resolveAutoPlantingDatesForPairs,
    pruneResolvedPlantingDates,
} from '../auto_planting_resolver.js';

const SECTION_KEYS = [
    ['crop_cultivar', 'Crop & Cultivar'],
    ['planting_harvest', 'Planting & Harvest'],
    ['initial_conditions', 'Initial Conditions'],
    ['simulation_controls', 'Simulation Controls'],
    ['management', 'Management'],
];

let editor = null;
let activeIdx = 0;

export async function render(root, store) {
    clear(root);
    const stepState = store.getStep('step4') || {};
    const protocols = stepState.protocols || [];
    if (!protocols.length) {
        protocols.push({name: 'Treatment 1'});
        store.setStep('step4', {protocols});
    }

    const panel = h('div', {class: 'wizard-step-panel'});
    panel.appendChild(h('h2', {class: 'step-title'}, 'Ensemble Treatments'));

    const countLabel = h('span', {class: 'wizard-pill'},
        `${protocols.length}/99 treatments`);
    panel.appendChild(h('p', {class: 'step-description'},
        'Build any number of treatment protocols. Each becomes one row in '
        + 'the FILEX TREATMENTS table. ', countLabel));

    const tabHost = h('div', {class: 'wizard-tselect-host'});
    panel.appendChild(tabHost);

    const importBar = h('div', {class: 'wizard-import-bar'});
    panel.appendChild(importBar);

    const editorHost = h('div', {class: 'protocol-editor-host'});
    panel.appendChild(editorHost);

    root.appendChild(panel);

    function getProtocols() {
        return (store.getStep('step4') || {}).protocols || [];
    }
    function setProtocols(items) {
        store.setStep('step4', {protocols: items});
        countLabel.textContent = `${items.length}/99 treatments`;
    }
    function rerenderTabs() {
        const items = getProtocols();
        renderSelector(tabHost, {
            items: items.map((p, i) => ({id: i, name: p.name || `Treatment ${i + 1}`})),
            activeIdx,
            label: 'Treatments:',
            addLabel: '+ Add treatment',
            onSelect: (i) => {
                // ``renderSelector`` paints ``active`` from the
                // ``activeIdx`` it was passed at render time — bumping
                // ``activeIdx`` alone leaves the old tab highlighted.
                // Re-render the bar so the highlight follows the click,
                // then mount the editor for the new treatment.
                activeIdx = i;
                rerenderTabs();
                mountEditorAt(i);
            },
            onAdd: () => {
                const cur = getProtocols();
                if (cur.length >= 99) {
                    window.alert('99-treatment cap reached.');
                    return;
                }
                const next = [...cur, {name: `Treatment ${cur.length + 1}`}];
                setProtocols(next);
                activeIdx = next.length - 1;
                rerenderTabs();
                mountEditorAt(activeIdx);
            },
            onRemove: (i) => {
                const cur = getProtocols().filter((_, idx) => idx !== i);
                setProtocols(cur);
                if (activeIdx >= cur.length) activeIdx = cur.length - 1;
                rerenderTabs();
                mountEditorAt(activeIdx);
            },
            onRename: (i, name) => {
                const cur = getProtocols().slice();
                cur[i] = {...cur[i], name};
                setProtocols(cur);
                rerenderTabs();
            },
        });
    }
    function rerenderImportBar() {
        clear(importBar);
        const cur = getProtocols();
        if (cur.length < 2) return;  // nothing to import from
        const sectionSel = h('select', {class: 'wizard-select'});
        for (const [key, label] of SECTION_KEYS) {
            sectionSel.appendChild(h('option', {value: key}, label));
        }
        const fromSel = h('select', {class: 'wizard-select'});
        cur.forEach((p, i) => {
            if (i === activeIdx) return;
            fromSel.appendChild(h('option', {value: i}, p.name || `Treatment ${i + 1}`));
        });
        const btn = h('button', {
            type: 'button', class: 'btn btn-sm btn-secondary',
        }, 'Import section');
        on(btn, 'click', () => {
            const sec = sectionSel.value;
            const fromIdx = parseInt(fromSel.value, 10);
            const all = getProtocols().slice();
            const src = all[fromIdx];
            const dst = {...all[activeIdx]};
            const SECTION_FIELDS = {
                crop_cultivar: ['crop_code', 'crop_name', 'cultivar_code'],
                planting_harvest: ['planting', 'harvest'],
                initial_conditions: ['initial_conditions'],
                simulation_controls: ['simulation_controls'],
                management: ['fertilizer', 'irrigation', 'tillage', 'chemical', 'residue'],
            };
            for (const f of SECTION_FIELDS[sec]) {
                dst[f] = src[f] != null ? JSON.parse(JSON.stringify(src[f])) : null;
            }
            all[activeIdx] = dst;
            setProtocols(all);
            mountEditorAt(activeIdx);
        });
        importBar.appendChild(h('span', {class: 'wizard-form-label'}, 'Import:'));
        importBar.appendChild(sectionSel);
        importBar.appendChild(h('span', {class: 'wizard-form-label'}, 'from'));
        importBar.appendChild(fromSel);
        importBar.appendChild(btn);
    }
    function mountEditorAt(i) {
        const cur = getProtocols();
        const p = cur[i] || {};
        const year = (store.getStep('step1') || {}).year;
        if (editor) editor.destroy();
        editor = mountEditor(editorHost, {
            value: p,
            onChange: (val) => {
                const arr = getProtocols().slice();
                arr[i] = {...val, name: arr[i]?.name || val.crop_code || `Treatment ${i + 1}`};
                setProtocols(arr);
            },
            options: {year, fields: store.fields},
        });
        rerenderImportBar();
    }

    rerenderTabs();
    mountEditorAt(activeIdx);
}

export function validate(store) {
    const items = (store.getStep('step4') || {}).protocols || [];
    if (!items.length) return {ok: false, message: 'Add at least one treatment.'};
    if (items.length > 99) return {ok: false, message: '99-treatment cap exceeded.'};
    for (const [i, p] of items.entries()) {
        if (!p.crop_code) return {ok: false,
            message: `Treatment ${i + 1} ("${p.name}") has no crop selected.`};
        if (!p.planting || !p.planting.mode) {
            return {ok: false,
                message: `Treatment ${i + 1} ("${p.name}") has no planting-date mode.`};
        }
        if (p.planting.mode === 'fixed' && p.planting.pdoy == null) {
            return {ok: false,
                message: `Treatment ${i + 1} ("${p.name}") has no fixed planting date.`};
        }
        if (p.planting.mode === 'auto' && !p.planting.auto_source) {
            return {ok: false,
                message: `Treatment ${i + 1} ("${p.name}") has no Auto planting source.`};
        }
    }
    return {ok: true};
}

/** Lock hook: persist each protocol as a Treatment + attach + write pairs.
 *
 * Ensemble is 1 field × N treatments. Each (field, treatment) pair is one
 * row in FILEX *TREATMENTS, so we materialise all N pairs explicitly. */
export async function onLockBeforeNext(store) {
    const items = (store.getStep('step4') || {}).protocols || [];
    const field = store.fields[0] || {};
    const created = [];
    const pairs = [];
    for (let i = 0; i < items.length; i++) {
        const p = items[i];
        const r = await treatmentsAPI.create({
            name: p.name || `Treatment ${i + 1}`,
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
    // Resolve Auto-mode planting dates for every pair before committing —
    // throws if any pair fails so the controller blocks the transition.
    await resolveAutoPlantingDatesForPairs(store, pairs);
    await store.setTreatments(created);
    pruneResolvedPlantingDates(store, pairs);
}
