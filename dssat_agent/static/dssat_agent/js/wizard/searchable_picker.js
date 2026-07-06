/**
 * Searchable picker — search input + filtered grid of cards that
 * collapses to a single "selected" chip on click. Mirrors the V9
 * experiment-wizard pattern (search/filter/grid + selected-chip with a
 * Change button) so users get the same look-and-feel they were used to
 * for crop and cultivar selection. Key behaviors:
 *
 *   * Substring filter on `name` + `code`, case-insensitive, no debounce
 *     (matches V9; the lists are short enough that a debounce just adds
 *     visible lag).
 *   * On select, the picker collapses to a `.selected-chip`. Clearing
 *     the search input afterwards does NOT lose the chip — the chip is
 *     a sibling of the picker container, controlled by the persisted
 *     `value`, not by the search box state. This is the bug the v2 SPA
 *     had on its old crop picker that prompted this rewrite.
 *   * Cards capped at `maxResults` so a 200-cultivar list stays
 *     scroll-friendly; the more-results hint is rendered as a tail row.
 *
 * Reuses existing CSS classes:
 *   - `.item-grid`, `.item-card`, `.item-card-title`, `.item-card-sub`
 *   - `.selected-chip`, `.selected-chip-label`, `.selected-chip-btn`
 *
 * Public API:
 *
 *   const picker = mountSearchablePicker(host, {
 *     items,            // [item, ...]
 *     value,            // currently-selected code (string) or null
 *     onChange,         // (code|null, item|null) => void
 *     getCode,          // optional: (item) => string  (default: item.code)
 *     getLabel,         // optional: (item) => string  (default: item.name)
 *     getSubtitle,      // optional: (item) => string  (default: item.code)
 *     placeholder,      // optional search input placeholder
 *     emptyMsg,         // optional empty-result message
 *     chipPrefix,       // optional "Selected:" label before the chip
 *     maxResults,       // optional cap on rendered cards (default 60)
 *   });
 *
 *   picker.setItems(items)  // swap the items list (e.g. cultivars after
 *                            //   the user picks a different crop)
 *   picker.setValue(code)   // programmatically pick / clear (null clears)
 *   picker.destroy()        // tear down DOM + listeners
 */

import {h, clear, on, escapeHtml} from './dom.js';

const DEFAULTS = {
    getCode:     (it) => it.code,
    getLabel:    (it) => it.name,
    getSubtitle: (it) => it.code,
    placeholder: 'Search…',
    emptyMsg:    'No matches.',
    chipPrefix:  'Selected:',
    maxResults:  60,
};

export function mountSearchablePicker(host, opts = {}) {
    const cfg = {...DEFAULTS, ...opts};
    let items = (cfg.items || []).slice();
    let value = cfg.value != null ? cfg.value : null;

    clear(host);

    // Container layout: a wrapper holding both the picker (search + grid)
    // and the chip. Visibility flips between the two based on `value`.
    const wrapper = h('div', {class: 'searchable-picker'});
    host.appendChild(wrapper);

    const pickerEl = h('div', {class: 'searchable-picker-body'});
    const search = h('input', {
        type: 'search',
        class: 'wizard-input',
        placeholder: cfg.placeholder,
    });
    const grid = h('div', {class: 'item-grid'});
    pickerEl.appendChild(search);
    pickerEl.appendChild(grid);
    wrapper.appendChild(pickerEl);

    const chipEl = h('div', {
        class: 'selected-chip',
        style: {display: 'none'},
    });
    wrapper.appendChild(chipEl);

    function findItem(code) {
        if (code == null) return null;
        return items.find((it) => cfg.getCode(it) === code) || null;
    }

    function renderChip() {
        const item = findItem(value);
        clear(chipEl);
        if (!item) {
            chipEl.style.display = 'none';
            pickerEl.style.display = '';
            return;
        }
        const label = cfg.getLabel(item) || cfg.getCode(item) || '';
        const code = cfg.getCode(item);
        chipEl.appendChild(h('span', {class: 'selected-chip-label'},
            cfg.chipPrefix));
        chipEl.appendChild(h('strong', {}, label));
        if (code && code !== label) {
            chipEl.appendChild(h('span', {class: 'mono', style: {marginLeft: '0.4rem'}},
                `(${code})`));
        }
        const btn = h('button', {
            type: 'button',
            class: 'selected-chip-btn',
        }, 'Change');
        on(btn, 'click', () => {
            // Re-expand picker without clearing the value yet — the user
            // can pick a different one or just close the picker.
            chipEl.style.display = 'none';
            pickerEl.style.display = '';
            search.focus();
        });
        chipEl.appendChild(btn);
        chipEl.style.display = '';
        pickerEl.style.display = 'none';
    }

    function renderGrid() {
        const q = (search.value || '').trim().toLowerCase();
        const filtered = q
            ? items.filter((it) => {
                const code = (cfg.getCode(it) || '').toLowerCase();
                const label = (cfg.getLabel(it) || '').toLowerCase();
                return code.includes(q) || label.includes(q);
            })
            : items.slice();
        clear(grid);
        if (!filtered.length) {
            grid.appendChild(h('div', {class: 'empty-state small'},
                cfg.emptyMsg));
            return;
        }
        const limit = cfg.maxResults;
        for (const it of filtered.slice(0, limit)) {
            const code = cfg.getCode(it);
            const label = cfg.getLabel(it) || code || '';
            const sub = cfg.getSubtitle(it);
            const card = h('button', {
                type: 'button',
                class: 'item-card' + (value === code ? ' selected' : ''),
            },
                h('div', {class: 'item-card-title'}, label),
                h('div', {class: 'item-card-sub'}, sub || ''),
            );
            on(card, 'click', () => {
                value = code;
                renderChip();
                if (cfg.onChange) cfg.onChange(value, it);
            });
            grid.appendChild(card);
        }
        if (filtered.length > limit) {
            grid.appendChild(h('div', {
                class: 'empty-state small',
                style: {gridColumn: '1 / -1'},
            }, `+${filtered.length - limit} more — refine the search.`));
        }
    }

    on(search, 'input', renderGrid);

    // First render — start with the chip visible if a value is already
    // selected; otherwise show the picker.
    renderGrid();
    renderChip();

    return {
        setItems(next) {
            items = (next || []).slice();
            // If the persisted value isn't in the new list any more,
            // clear it so the chip doesn't show stale text.
            if (value != null && !findItem(value)) {
                value = null;
                if (cfg.onChange) cfg.onChange(null, null);
            }
            renderGrid();
            renderChip();
        },
        setValue(code) {
            const next = code != null ? code : null;
            if (next === value) return;
            value = next;
            renderGrid();
            renderChip();
        },
        getValue() { return value; },
        destroy() { clear(host); },
    };
}
