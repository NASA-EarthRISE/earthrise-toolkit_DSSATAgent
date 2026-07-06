/**
 * Library picker — small dialog/dropdown for choosing a saved Field or
 * Treatment from the user's library. Reused from Step 3 (per-field) and
 * Step 4 (per-treatment) entry points.
 */

import {h, clear, on, escapeHtml} from './dom.js';
import {fields as fieldsAPI, treatments as treatmentsAPI} from './api.js';

/**
 * Render the picker as a modal-style overlay. Resolves with the selected
 * record or null on cancel.
 */
export function pickField({preferQuery = ''} = {}) {
    return _genericPicker({
        title: 'Pick a saved field',
        listFn: (q) => fieldsAPI.list(q).then((r) => r.fields || []),
        renderRow: (f) => `${escapeHtml(f.name)}`
            + ` <span class="text-muted small">${f.latitude.toFixed(3)}, ${f.longitude.toFixed(3)}</span>`,
        preferQuery,
    });
}

export function pickTreatment({cropCode = '', preferQuery = ''} = {}) {
    return _genericPicker({
        title: 'Pick a saved treatment',
        listFn: (q) => treatmentsAPI.list(q, cropCode).then((r) => r.treatments || []),
        renderRow: (t) => `${escapeHtml(t.name)}`
            + ` <span class="text-muted small">${escapeHtml(t.crop_code)}`
            + (t.cultivar_code ? '/' + escapeHtml(t.cultivar_code) : '') + '</span>',
        preferQuery,
    });
}

function _genericPicker({title, listFn, renderRow, preferQuery}) {
    return new Promise((resolve) => {
        const overlay = h('div', {class: 'wizard-modal-overlay'});
        const modal = h('div', {class: 'wizard-modal'},
            h('div', {class: 'wizard-modal-header'},
                h('h3', {}, title),
                h('button', {class: 'wizard-modal-close'}, '×'),
            ),
            h('div', {class: 'wizard-modal-body'},
                h('input', {
                    type: 'search', class: 'wizard-input',
                    placeholder: 'Search...', value: preferQuery,
                }),
                h('div', {class: 'wizard-picker-list'},
                    h('div', {class: 'empty-state small'}, 'Loading…')),
            ),
        );
        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        const search = modal.querySelector('input');
        const list = modal.querySelector('.wizard-picker-list');
        let dirty = false;

        function close(value) {
            document.body.removeChild(overlay);
            resolve(value);
        }
        on(modal.querySelector('.wizard-modal-close'), 'click', () => close(null));
        on(overlay, 'click', (e) => { if (e.target === overlay) close(null); });
        on(document, 'keydown', function escListener(e) {
            if (e.key === 'Escape') {
                document.removeEventListener('keydown', escListener);
                close(null);
            }
        });

        async function refresh() {
            list.innerHTML = '<div class="empty-state small">Loading…</div>';
            try {
                const items = await listFn(search.value || '');
                if (!items.length) {
                    list.innerHTML = '<div class="empty-state small">'
                        + 'Nothing in the library yet.</div>';
                    return;
                }
                clear(list);
                for (const it of items) {
                    const row = h('button', {
                        type: 'button',
                        class: 'wizard-picker-row',
                    });
                    row.innerHTML = renderRow(it);
                    on(row, 'click', () => close(it));
                    list.appendChild(row);
                }
            } catch (e) {
                list.innerHTML = `<div class="empty-state small">${escapeHtml(e.message)}</div>`;
            }
            dirty = false;
        }

        let timer = null;
        on(search, 'input', () => {
            dirty = true;
            clearTimeout(timer);
            timer = setTimeout(refresh, 200);
        });

        refresh();
    });
}
