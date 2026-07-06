/**
 * Step 4 (Batch): protocol catalog + (Field × Protocol) pairing matrix.
 *
 * Two stages on this page:
 *   1. Build a catalog of treatment protocols (treatment-selector tab bar +
 *      shared protocol_editor inside each tab).
 *   2. Pair them to fields via a checkbox matrix. Each checked cell becomes
 *      one DSSAT treatment. Live count + 99-cap enforced before submit.
 *
 * Heads-up: even when this UI ships, submission via the new draft path
 * still raises 400 because services/batch_service.py needs a follow-up to
 * consume WizardDraftPair rows directly (see plan deferral). The chat-side
 * legacy batch path still works for users who need it now.
 */

import {h, clear, on, escapeHtml} from '../dom.js';
import {treatments as treatmentsAPI} from '../api.js';
import {renderSelector} from '../treatment_selector.js';
import {mount as mountEditor} from './protocol_editor.js';
import {
    resolveAutoPlantingDatesForPairs,
    pruneResolvedPlantingDates,
} from '../auto_planting_resolver.js';

let editor = null;
let activeIdx = 0;

export async function render(root, store) {
    clear(root);
    const stepState = store.getStep('step4') || {};
    const protocols = stepState.protocols || [];
    const matrix = stepState.matrix || {};  // {field_id: Set<protocol_idx>}

    if (!protocols.length) {
        protocols.push({name: 'Protocol 1'});
        store.setStep('step4', {protocols});
    }

    // Default-checked matrix: when the user lands on Step 4 batch with no
    // pairings yet, populate every (field, protocol) cell so they don't
    // have to manually tick the matrix to ship a "everything paired"
    // experiment. Only fires when the matrix is empty (no field keys),
    // so a user who deliberately unchecked things won't have their work
    // re-overwritten on re-entry. (Empty-matrix-by-deliberate-unchecking
    // is degenerate anyway — it'd fail the "at least one cell" validate.)
    if (!Object.keys(matrix).length
        && (store.fields || []).length
        && protocols.length) {
        const init = {};
        for (const f of store.fields) {
            init[f.id] = protocols.map((_, i) => i);
        }
        store.setStep('step4', {matrix: init});
    }

    const panel = h('div', {class: 'wizard-step-panel'});
    panel.appendChild(h('h2', {class: 'step-title'},
        'Batch Protocols + Pairing Matrix'));

    const cap = h('span', {class: 'wizard-pill'});
    panel.appendChild(h('p', {class: 'step-description'},
        'Build any number of protocols, then check cells in the matrix '
        + 'below to pair each protocol with one or more fields. Each cell '
        + 'becomes a single DSSAT treatment. ', cap));

    // 1. Catalog (tab bar + editor)
    panel.appendChild(h('h3', {class: 'wizard-form-subhead'}, 'Protocol catalog'));
    const tabHost = h('div', {class: 'wizard-tselect-host'});
    panel.appendChild(tabHost);
    const editorHost = h('div', {class: 'protocol-editor-host'});
    panel.appendChild(editorHost);

    // 2. Matrix — wrapped in a chip-style block so the visual language
    //    matches the protocol editor's accordion below.
    const matrixChip = h('div', {class: 'protocol-chip protocol-chip-empty'});
    matrixChip.appendChild(h('div', {class: 'protocol-chip-header'},
        h('span', {class: 'protocol-chip-icon'}, '🔗'),
        h('span', {class: 'protocol-chip-label'}, 'Field × Protocol pairings'),
        h('span', {class: 'protocol-chip-summary'},
            'Check cells to pair each protocol with one or more fields.'),
    ));
    const matrixHost = h('div', {class: 'protocol-chip-body'});
    matrixChip.appendChild(matrixHost);
    panel.appendChild(matrixChip);

    root.appendChild(panel);

    function getProtocols() {
        return (store.getStep('step4') || {}).protocols || [];
    }
    function setProtocols(items) {
        store.setStep('step4', {protocols: items});
    }
    function getMatrix() {
        return (store.getStep('step4') || {}).matrix || {};
    }
    function setMatrix(m) {
        store.setStep('step4', {matrix: m});
    }
    function countPairs() {
        const m = getMatrix();
        let n = 0;
        for (const v of Object.values(m)) n += (v || []).length;
        return n;
    }
    function updateCap() {
        const n = countPairs();
        cap.textContent = `${n}/99 treatments`;
        cap.className = 'wizard-pill' + (n > 99 ? ' wizard-pill-error' : '');
    }

    function rerenderTabs() {
        const items = getProtocols();
        renderSelector(tabHost, {
            items: items.map((p, i) => ({id: i, name: p.name || `Protocol ${i + 1}`})),
            activeIdx,
            label: 'Protocols:',
            addLabel: '+ Add protocol',
            onSelect: (i) => {
                // Bumping ``activeIdx`` alone leaves the old tab
                // highlighted because ``renderSelector`` paints active
                // state at render time. Re-render the bar so the
                // highlight follows the click.
                activeIdx = i;
                rerenderTabs();
                mountEditorAt(i);
            },
            onAdd: () => {
                const cur = getProtocols();
                const next = [...cur, {name: `Protocol ${cur.length + 1}`}];
                setProtocols(next);
                activeIdx = next.length - 1;
                // Default the new protocol to checked across every
                // existing field — consistent with the default-all-checked
                // initialization at first render.
                const newIdx = next.length - 1;
                const m = {...getMatrix()};
                for (const f of (store.fields || [])) {
                    const list = (m[f.id] || []).slice();
                    if (!list.includes(newIdx)) list.push(newIdx);
                    m[f.id] = list;
                }
                setMatrix(m);
                rerenderTabs();
                mountEditorAt(activeIdx);
                rerenderMatrix();
            },
            onRemove: (i) => {
                const cur = getProtocols().filter((_, idx) => idx !== i);
                setProtocols(cur);
                // Clean up matrix references
                const m = {...getMatrix()};
                for (const fid of Object.keys(m)) {
                    m[fid] = (m[fid] || [])
                        .filter((idx) => idx !== i)
                        .map((idx) => idx > i ? idx - 1 : idx);
                }
                setMatrix(m);
                if (activeIdx >= cur.length) activeIdx = Math.max(0, cur.length - 1);
                rerenderTabs();
                mountEditorAt(activeIdx);
                rerenderMatrix();
            },
            onRename: (i, name) => {
                const cur = getProtocols().slice();
                cur[i] = {...cur[i], name};
                setProtocols(cur);
                rerenderTabs();
                rerenderMatrix();
            },
        });
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
                arr[i] = {...val, name: arr[i]?.name || val.crop_code || `Protocol ${i + 1}`};
                setProtocols(arr);
                rerenderMatrix();  // header labels may change
            },
            options: {year, fields: store.fields},
        });
    }

    function rerenderMatrix() {
        clear(matrixHost);
        const fields = store.fields || [];
        const protos = getProtocols();
        if (!fields.length) {
            matrixHost.appendChild(h('div', {class: 'empty-state small'},
                'No fields attached. Add fields in Step 2.'));
            return;
        }
        if (!protos.length) {
            matrixHost.appendChild(h('div', {class: 'empty-state small'},
                'Add at least one protocol to start pairing.'));
            return;
        }
        const m = getMatrix();
        const tbl = h('table', {class: 'data-table batch-matrix'});
        // Header row
        const thead = h('thead');
        const headRow = h('tr', {}, h('th', {}, 'Field'));
        protos.forEach((p, idx) => {
            headRow.appendChild(h('th', {},
                p.name || `Protocol ${idx + 1}`));
        });
        thead.appendChild(headRow);
        tbl.appendChild(thead);
        // Body rows
        const tbody = h('tbody');
        for (const f of fields) {
            const tr = h('tr', {},
                h('td', {},
                    h('strong', {}, f.name),
                    h('div', {class: 'mono text-muted small'},
                        `${f.latitude.toFixed(2)}, ${f.longitude.toFixed(2)}`),
                ),
            );
            protos.forEach((_p, pIdx) => {
                const td = h('td', {class: 'matrix-cell'});
                const cb = h('input', {
                    type: 'checkbox',
                    checked: ((m[f.id] || []).includes(pIdx)),
                });
                on(cb, 'change', () => {
                    const cur = {...getMatrix()};
                    const list = (cur[f.id] || []).slice();
                    if (cb.checked) {
                        if (!list.includes(pIdx)) list.push(pIdx);
                    } else {
                        const i = list.indexOf(pIdx);
                        if (i >= 0) list.splice(i, 1);
                    }
                    cur[f.id] = list;
                    setMatrix(cur);
                    updateCap();
                });
                td.appendChild(cb);
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        }
        tbl.appendChild(tbody);
        matrixHost.appendChild(tbl);
        updateCap();
    }

    rerenderTabs();
    mountEditorAt(activeIdx);
    rerenderMatrix();
    updateCap();
}

export function validate(store) {
    const stepState = store.getStep('step4') || {};
    const protos = stepState.protocols || [];
    if (!protos.length) return {ok: false, message: 'Define at least one protocol.'};
    for (const p of protos) {
        if (!p.crop_code) return {ok: false,
            message: `Protocol "${p.name}" has no crop selected.`};
        if (!p.planting || !p.planting.mode) return {ok: false,
            message: `Protocol "${p.name}" has no planting-date mode.`};
        if (p.planting.mode === 'fixed' && p.planting.pdoy == null) {
            return {ok: false,
                message: `Protocol "${p.name}" has no fixed planting date.`};
        }
        if (p.planting.mode === 'auto' && !p.planting.auto_source) {
            return {ok: false,
                message: `Protocol "${p.name}" has no Auto planting source.`};
        }
    }
    let n = 0;
    for (const v of Object.values(stepState.matrix || {})) n += (v || []).length;
    if (n === 0) return {ok: false,
        message: 'Check at least one cell in the matrix.'};
    if (n > 99) return {ok: false, message: `Matrix has ${n} pairs; max 99.`};
    return {ok: true};
}

export async function onLockBeforeNext(store) {
    const stepState = store.getStep('step4') || {};
    const protos = stepState.protocols || [];
    const matrix = stepState.matrix || {};
    const fieldsById = {};
    for (const f of (store.fields || [])) fieldsById[f.id] = f;

    // 1. Persist each protocol as a reusable Treatment record.
    const treatmentIds = [];
    for (let i = 0; i < protos.length; i++) {
        const p = protos[i];
        const r = await treatmentsAPI.create({
            name: p.name || `Protocol ${i + 1}`,
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
        treatmentIds.push(r.treatment.id);
    }

    // 2. Walk the matrix — every checked cell becomes one
    //    WizardDraftTreatment row carrying both ids. Build pair list
    //    in parallel so we can resolve Auto-mode planting dates.
    const rows = [];
    const pairs = [];
    let order = 0;
    for (const [fieldId, protoIdxList] of Object.entries(matrix)) {
        for (const pIdx of protoIdxList) {
            const tid = treatmentIds[pIdx];
            if (!tid) continue;
            rows.push({
                treatment_id: tid,
                field_id: fieldId,
                ordering: order++,
            });
            pairs.push({
                treatment_id: tid,
                field_id: fieldId,
                treatment_payload: protos[pIdx],
                field: fieldsById[fieldId] || {id: fieldId},
            });
        }
    }
    await resolveAutoPlantingDatesForPairs(store, pairs);
    await store.setTreatments(rows);
    pruneResolvedPlantingDates(store, pairs);
}
