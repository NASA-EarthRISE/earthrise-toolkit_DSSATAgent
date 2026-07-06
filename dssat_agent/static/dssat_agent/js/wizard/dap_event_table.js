/**
 * DAP-anchored management event table.
 *
 * Shape parallels the legacy ``event_table.js`` widget but every event
 * stores its date as ``*dap`` integer (days-after-planting) instead of an
 * absolute ``*date``. Treatments are stored DOY/DAP-canonical so they can
 * be reused across years; absolute dates are only materialised at submit
 * time inside ``services/draft_submit.py``.
 *
 * Per-event-type column schemas (kept aligned with legacy event_table.js
 * so codes resolve identically downstream):
 *
 *   fertilizer  fdap  fmcd  facd  fdep  famn
 *   irrigation  idap  irval irop
 *   tillage     tdap  timpl tdep
 *   chemical    cdap  chcod chme  chamt
 *   residue     rdap  rcod  ramt  rinp
 *
 * Material/method/implement codes still come from the existing
 * ``/dssat/api/codes/<category>/`` endpoint and render as <select>s with
 * descriptions; the only difference here is the date column.
 */

import {h, clear, on} from './dom.js';
import {SUBPATH} from './api.js';

const SCHEMAS = {
    fertilizer: {
        dapField: 'fdap',
        columns: [
            {key: 'fdap', label: 'DAP', type: 'int',
             help: 'Days after planting (negative = before)'},
            {key: 'fmcd', label: 'Material', type: 'code',
             category: 'fmcd'},
            {key: 'facd', label: 'Method',   type: 'code',
             category: 'facd', context: 'fertilizer'},
            {key: 'fdep', label: 'Depth (cm)', type: 'number'},
            {key: 'famn', label: 'N (kg/ha)',  type: 'number'},
        ],
    },
    irrigation: {
        dapField: 'idap',
        columns: [
            {key: 'idap',  label: 'DAP',         type: 'int'},
            {key: 'irval', label: 'Amount (mm)', type: 'number'},
            {key: 'irop',  label: 'Method',      type: 'code',
             category: 'irop'},
        ],
    },
    tillage: {
        dapField: 'tdap',
        columns: [
            {key: 'tdap',  label: 'DAP',          type: 'int'},
            {key: 'timpl', label: 'Implement',    type: 'code',
             category: 'timpl'},
            {key: 'tdep',  label: 'Depth (cm)',   type: 'number'},
        ],
    },
    chemical: {
        dapField: 'cdap',
        columns: [
            {key: 'cdap',  label: 'DAP',      type: 'int'},
            {key: 'chcod', label: 'Material', type: 'code',
             category: 'chcod'},
            {key: 'chme',  label: 'Method',   type: 'code',
             category: 'facd', context: 'chemical'},
            {key: 'chamt', label: 'Amount',   type: 'number'},
        ],
    },
    residue: {
        dapField: 'rdap',
        columns: [
            {key: 'rdap',  label: 'DAP',                type: 'int'},
            {key: 'rcod',  label: 'Material',           type: 'code',
             category: 'rcod'},
            {key: 'ramt',  label: 'Amount (kg/ha)',     type: 'number'},
            {key: 'rinp',  label: 'Incorporation (%)',  type: 'number'},
        ],
    },
};

const _codesCache = {};

async function loadCodes(category, context) {
    const key = context ? `${category}:${context}` : category;
    if (_codesCache[key]) return _codesCache[key];
    try {
        const url = SUBPATH + '/dssat/api/codes/' + category + '/'
            + (context ? '?context=' + encodeURIComponent(context) : '');
        const r = await fetch(url, {credentials: 'same-origin'});
        const j = await r.json();
        _codesCache[key] = j.codes || j.values || [];
    } catch (_e) {
        _codesCache[key] = [];
    }
    return _codesCache[key];
}

/**
 * Mount a DAP event editor into ``host`` for the given event type.
 *
 *   const ed = mount(host, 'fertilizer', initialRows, onChange)
 *   ed.getRows()  -> [{fdap: 0, fmcd: 'FE005', ...}, ...]
 *   ed.destroy()
 */
export function mount(host, eventType, initialRows = [], onChange) {
    const schema = SCHEMAS[eventType];
    if (!schema) throw new Error(`Unknown event type: ${eventType}`);

    let rows = (initialRows || []).map((r) => ({...r}));
    let element;

    function emit() {
        if (onChange) onChange(rows.slice());
    }

    function render() {
        const tbl = h('table', {class: 'dap-event-table'});
        const thead = h('thead');
        const headRow = h('tr');
        for (const col of schema.columns) {
            headRow.appendChild(h('th', {}, col.label));
        }
        headRow.appendChild(h('th', {}, ''));  // delete column
        thead.appendChild(headRow);
        tbl.appendChild(thead);

        const tbody = h('tbody');
        rows.forEach((row, idx) => {
            tbody.appendChild(renderRow(row, idx));
        });
        tbl.appendChild(tbody);

        const addBtn = h('button', {
            type: 'button', class: 'btn btn-sm btn-secondary',
        }, '+ Add ' + eventType + ' event');
        on(addBtn, 'click', () => {
            rows = rows.concat([{[schema.dapField]: 0}]);
            replace();
            emit();
        });

        const wrap = h('div', {class: 'dap-event-wrap'}, tbl, addBtn);
        return wrap;
    }

    function renderRow(row, idx) {
        const tr = h('tr');
        for (const col of schema.columns) {
            const td = h('td');
            td.appendChild(renderCell(row, idx, col));
            tr.appendChild(td);
        }
        const delBtn = h('button', {
            type: 'button', class: 'btn-icon-delete', title: 'Remove',
        }, '×');
        on(delBtn, 'click', () => {
            rows = rows.filter((_, i) => i !== idx);
            replace();
            emit();
        });
        tr.appendChild(h('td', {}, delBtn));
        return tr;
    }

    function renderCell(row, idx, col) {
        if (col.type === 'int' || col.type === 'number') {
            const inp = h('input', {
                type: 'number',
                class: 'wizard-input dap-event-input',
                step: col.type === 'int' ? '1' : 'any',
                value: row[col.key] != null ? row[col.key] : '',
                placeholder: col.help || '',
            });
            on(inp, 'input', () => {
                const v = col.type === 'int'
                    ? parseInt(inp.value, 10)
                    : parseFloat(inp.value);
                rows[idx] = {...rows[idx], [col.key]: isNaN(v) ? null : v};
                emit();
            });
            return inp;
        }
        if (col.type === 'code') {
            const sel = h('select', {class: 'wizard-select dap-event-input'},
                h('option', {value: ''}, '—'));
            on(sel, 'change', () => {
                rows[idx] = {...rows[idx], [col.key]: sel.value || null};
                emit();
            });
            loadCodes(col.category, col.context).then((codes) => {
                for (const c of codes) {
                    const code = c.code || c.value || c;
                    const desc = c.description || c.label || '';
                    const opt = h('option', {value: code},
                        `${code}${desc ? ' — ' + desc : ''}`);
                    sel.appendChild(opt);
                }
                if (row[col.key]) sel.value = row[col.key];
            });
            return sel;
        }
        return h('span', {}, String(row[col.key] || ''));
    }

    function replace() {
        const next = render();
        element.parentNode.replaceChild(next, element);
        element = next;
    }

    element = render();
    host.appendChild(element);

    return {
        getRows: () => rows.slice(),
        destroy: () => { if (element && element.parentNode) element.remove(); },
    };
}
