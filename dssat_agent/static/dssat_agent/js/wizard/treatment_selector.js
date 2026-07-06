/**
 * Tab bar for switching between multiple items (fields in Step 3, treatment
 * protocols in Step 4 ensemble/batch). Generic over any list of `{id, name}`.
 *
 * The host module owns the items array and the active index; this module
 * only renders the bar and emits callbacks for click / add / remove /
 * rename.
 */

import {h, clear, on} from './dom.js';

export function renderSelector(host, {
    items,
    activeIdx,
    onSelect,
    onAdd,
    onRemove,
    onRename,
    addLabel = '+ Add',
    canRemove = (i) => items.length > 1,
    label = '',
}) {
    clear(host);
    const bar = h('div', {class: 'wizard-tselect-bar'});
    if (label) bar.appendChild(h('span', {class: 'wizard-tselect-label'}, label));

    items.forEach((it, i) => {
        const tab = h('button', {
            type: 'button',
            class: 'wizard-tselect-tab' + (i === activeIdx ? ' active' : ''),
            dataset: {idx: i},
        }, it.name || `Item ${i + 1}`);
        on(tab, 'click', () => onSelect && onSelect(i));
        on(tab, 'dblclick', () => {
            if (!onRename) return;
            const next = window.prompt('Rename:', it.name || '');
            if (next != null && next.trim()) onRename(i, next.trim());
        });
        if (onRemove && canRemove(i)) {
            const x = h('span', {
                class: 'wizard-tselect-x',
                title: 'Remove',
            }, '×');
            on(x, 'click', (e) => {
                e.stopPropagation();
                if (window.confirm(`Remove ${it.name || 'this item'}?`)) {
                    onRemove(i);
                }
            });
            tab.appendChild(x);
        }
        bar.appendChild(tab);
    });

    if (onAdd) {
        const add = h('button', {
            type: 'button',
            class: 'wizard-tselect-add',
        }, addLabel);
        on(add, 'click', () => onAdd());
        bar.appendChild(add);
    }

    host.appendChild(bar);
}
