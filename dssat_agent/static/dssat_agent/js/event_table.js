/*
 * Reusable event-table widget for DSSAT management practices
 * (fertilizer, irrigation, tillage, chemical, residue).
 *
 * Usage:
 *
 *   const widget = new EventTable(container, {
 *       eventType: 'fertilizer',
 *       cropCode: 'MZ',
 *       initialRows: [{fdate: '2024-05-01', fmcd: 'FE005', fdep: 10, famn: 50}],
 *   });
 *   // ... user edits rows ...
 *   const rows = widget.getRows();      // [{fdate, fmcd, facd, fdep, famn}, ...]
 *
 * The widget's DSSAT-code dropdowns fetch codes from
 * `/dssat/api/codes/<category>/?crop=<cropCode>[&context=<ctx>]`.
 * When the response indicates a curated list, a "Show all" toggle appears
 * next to the dropdown; clicking it refetches without the crop filter.
 *
 * Schemas for each event type are defined in EVENT_SCHEMAS below. Adding a
 * new schema is a data-only change.
 */

(function (global) {
    'use strict';

    // -------------------------------------------------------------------------
    // Field type primitives
    // -------------------------------------------------------------------------
    // Each field in a schema has:
    //   name: <string>            — the JSON key stored in the serialized row
    //   label: <string>           — column header
    //   type: 'date'|'number'|'text'|'code'
    //   placeholder?: <string>
    //   step?: <number>           — for type='number'
    //   codes?: {
    //       category: <string>,   — API category (alias or DSSAT var)
    //       context?: <string>,   — optional context (e.g., 'fertilizer'/'chemical')
    //   }
    // -------------------------------------------------------------------------

    // Schemas are split per `mode` so the same widget can render either
    // experiment-time events (absolute dates, used by the wizard) or
    // stored defaults (days-after-planting integers, used by the
    // preferences and system-config pages).
    //
    // The default mode is 'experiment' — callers that don't pass a mode
    // (notably the experiment wizard) get the original column layout.
    const _DAP_FIELD_SHARED = {
        type: 'number', placeholder: '0', step: 1, min: 0,
    };

    const EVENT_SCHEMAS = {
        fertilizer: {
            tableFields: {
                experiment: [
                    {name: 'fdate', label: 'Date', type: 'date'},
                    {name: 'fmcd', label: 'Material', type: 'code',
                     codes: {category: 'fertilizer'}},
                    {name: 'facd', label: 'Method', type: 'code',
                     codes: {category: 'application_method', context: 'fertilizer'}},
                    {name: 'fdep', label: 'Depth (cm)', type: 'number',
                     placeholder: '10', step: 1},
                    {name: 'famn', label: 'N (kg/ha)', type: 'number',
                     placeholder: '50', step: 1},
                ],
                defaults: [
                    {name: 'fdap', label: 'Days after planting', ..._DAP_FIELD_SHARED},
                    {name: 'fmcd', label: 'Material', type: 'code',
                     codes: {category: 'fertilizer'}},
                    {name: 'facd', label: 'Method', type: 'code',
                     codes: {category: 'application_method', context: 'fertilizer'}},
                    {name: 'fdep', label: 'Depth (cm)', type: 'number',
                     placeholder: '10', step: 1},
                    {name: 'famn', label: 'N (kg/ha)', type: 'number',
                     placeholder: '50', step: 1},
                ],
            },
        },
        irrigation: {
            tableFields: {
                experiment: [
                    {name: 'idate', label: 'Date', type: 'date'},
                    {name: 'irval', label: 'Amount (mm)', type: 'number',
                     placeholder: '25', step: 1},
                    {name: 'irop', label: 'Method', type: 'code',
                     codes: {category: 'irrigation'}},
                ],
                defaults: [
                    {name: 'idap', label: 'Days after planting', ..._DAP_FIELD_SHARED},
                    {name: 'irval', label: 'Amount (mm)', type: 'number',
                     placeholder: '25', step: 1},
                    {name: 'irop', label: 'Method', type: 'code',
                     codes: {category: 'irrigation'}},
                ],
            },
        },
        tillage: {
            tableFields: {
                experiment: [
                    {name: 'tdate', label: 'Date', type: 'date'},
                    {name: 'timpl', label: 'Implement', type: 'code',
                     codes: {category: 'tillage'}},
                    {name: 'tdep', label: 'Depth (cm)', type: 'number',
                     placeholder: '20', step: 1},
                ],
                defaults: [
                    {name: 'tdap', label: 'Days after planting', ..._DAP_FIELD_SHARED},
                    {name: 'timpl', label: 'Implement', type: 'code',
                     codes: {category: 'tillage'}},
                    {name: 'tdep', label: 'Depth (cm)', type: 'number',
                     placeholder: '20', step: 1},
                ],
            },
        },
        chemical: {
            tableFields: {
                experiment: [
                    {name: 'cdate', label: 'Date', type: 'date'},
                    {name: 'chcod', label: 'Material', type: 'code',
                     codes: {category: 'chemical'}},
                    {name: 'chme', label: 'Method', type: 'code',
                     codes: {category: 'application_method', context: 'chemical'}},
                    {name: 'chamt', label: 'Amount', type: 'number',
                     placeholder: '1.0', step: 0.1},
                ],
                defaults: [
                    {name: 'cdap', label: 'Days after planting', ..._DAP_FIELD_SHARED},
                    {name: 'chcod', label: 'Material', type: 'code',
                     codes: {category: 'chemical'}},
                    {name: 'chme', label: 'Method', type: 'code',
                     codes: {category: 'application_method', context: 'chemical'}},
                    {name: 'chamt', label: 'Amount', type: 'number',
                     placeholder: '1.0', step: 0.1},
                ],
            },
        },
        residue: {
            tableFields: {
                experiment: [
                    {name: 'rdate', label: 'Date', type: 'date'},
                    {name: 'rcod', label: 'Material', type: 'code',
                     codes: {category: 'residue'}},
                    {name: 'ramt', label: 'Amount (kg/ha)', type: 'number',
                     placeholder: '1000', step: 1},
                    {name: 'rinp', label: 'Incorporation (%)', type: 'number',
                     placeholder: '50', step: 1},
                ],
                defaults: [
                    {name: 'rdap', label: 'Days after planting', ..._DAP_FIELD_SHARED},
                    {name: 'rcod', label: 'Material', type: 'code',
                     codes: {category: 'residue'}},
                    {name: 'ramt', label: 'Amount (kg/ha)', type: 'number',
                     placeholder: '1000', step: 1},
                    {name: 'rinp', label: 'Incorporation (%)', type: 'number',
                     placeholder: '50', step: 1},
                ],
            },
        },
    };

    // -------------------------------------------------------------------------
    // Code cache — keyed by `<category>|<crop>|<context>|<curated?>`.
    // Shared across all EventTable instances on the page.
    // -------------------------------------------------------------------------
    const _codeCache = new Map();

    async function fetchCodes(category, {crop = null, context = null, curated = true} = {}) {
        const key = `${category}|${crop || ''}|${context || ''}|${curated ? 'c' : 'a'}`;
        if (_codeCache.has(key)) return _codeCache.get(key);

        const params = new URLSearchParams();
        if (crop && curated) params.set('crop', crop);
        if (context) params.set('context', context);

        const qs = params.toString();
        const SUBPATH = window.SUBPATH || '';
        const url = `${SUBPATH}/dssat/api/codes/${encodeURIComponent(category)}/${qs ? '?' + qs : ''}`;

        const resp = await fetch(url);
        if (!resp.ok) {
            throw new Error(`Failed to fetch ${category} codes: HTTP ${resp.status}`);
        }
        const data = await resp.json();
        const result = {
            codes: data.codes || [],
            curated: !!data.curated,
        };
        _codeCache.set(key, result);
        return result;
    }

    function buildOptions(codes, {placeholder = '— select —'} = {}) {
        const opts = [`<option value="">${placeholder}</option>`];
        for (const {code, description} of codes) {
            const label = description ? `${code} — ${description}` : code;
            opts.push(`<option value="${code}">${label}</option>`);
        }
        return opts.join('');
    }

    // -------------------------------------------------------------------------
    // EventTable widget
    // -------------------------------------------------------------------------
    class EventTable {
        /**
         * @param {HTMLElement} container - the wrapper <div> (empty or pre-populated)
         * @param {Object} config
         * @param {string} config.eventType - e.g. 'fertilizer'
         * @param {string} [config.cropCode] - for curated-code filtering
         * @param {Array<Object>} [config.initialRows] - pre-populate rows
         * @param {'experiment'|'defaults'} [config.mode='experiment']
         *   'experiment' (default) shows an absolute date column — used by
         *   the experiment wizard. 'defaults' replaces it with a
         *   days-after-planting integer column — used by the preferences
         *   and system-config pages, where defaults are stored without an
         *   anchoring planting date.
         */
        constructor(container, config) {
            this.container = container;
            this.eventType = config.eventType;
            this.cropCode = config.cropCode || null;
            this.mode = (config.mode === 'defaults') ? 'defaults' : 'experiment';
            const fullSchema = EVENT_SCHEMAS[this.eventType];
            if (!fullSchema) {
                throw new Error(`Unknown event type: ${this.eventType}`);
            }
            // Resolve per-mode fields. Schemas are now keyed by mode; an
            // older flat-array shape falls through unchanged for safety.
            const fields = (fullSchema.tableFields && fullSchema.tableFields[this.mode])
                || (Array.isArray(fullSchema.tableFields) ? fullSchema.tableFields : null);
            if (!fields) {
                throw new Error(`No fields for ${this.eventType}/${this.mode}`);
            }
            this.schema = {tableFields: fields};

            // Per-field "curated vs full" state. Defaults to curated (true).
            this._curatedMode = {};
            for (const f of fields) {
                if (f.type === 'code') this._curatedMode[f.name] = true;
            }

            this._render();
            if (config.initialRows && config.initialRows.length) {
                for (const row of config.initialRows) this.addRow(row);
            }
        }

        _render() {
            const {tableFields} = this.schema;
            const headers = tableFields.map(f => `<th>${f.label}</th>`).join('');
            this.container.innerHTML = `
                <div class="event-table-widget" data-event-type="${this.eventType}">
                    <table class="event-table">
                        <thead><tr>${headers}<th></th></tr></thead>
                        <tbody class="event-rows"></tbody>
                    </table>
                    <button type="button" class="btn-add-event">+ Add ${_titleCase(this.eventType)} Event</button>
                </div>
            `;
            this._tbody = this.container.querySelector('.event-rows');
            this.container.querySelector('.btn-add-event').addEventListener(
                'click', () => this.addRow()
            );
        }

        /** Add an empty row (or populated from `values`). */
        async addRow(values = {}) {
            const {tableFields} = this.schema;
            const tr = document.createElement('tr');
            for (const field of tableFields) {
                const td = document.createElement('td');
                const cell = await this._buildCell(field, values[field.name]);
                td.appendChild(cell);
                tr.appendChild(td);
            }

            // Delete button
            const delTd = document.createElement('td');
            const delBtn = document.createElement('button');
            delBtn.type = 'button';
            delBtn.className = 'event-row-remove';
            delBtn.textContent = '×';
            delBtn.title = 'Remove this event';
            delBtn.addEventListener('click', () => tr.remove());
            delTd.appendChild(delBtn);
            tr.appendChild(delTd);

            this._tbody.appendChild(tr);
        }

        async _buildCell(field, value) {
            if (field.type === 'code') {
                return await this._buildCodeCell(field, value);
            }
            const input = document.createElement('input');
            input.dataset.field = field.name;
            input.className = 'event-field';
            if (field.type === 'date') {
                input.type = 'date';
                if (value) input.value = value;
            } else if (field.type === 'number') {
                input.type = 'number';
                if (field.placeholder) input.placeholder = field.placeholder;
                if (field.step != null) input.step = String(field.step);
                if (field.min != null) input.min = String(field.min);
                if (field.max != null) input.max = String(field.max);
                if (value != null) input.value = String(value);
            } else {
                input.type = 'text';
                if (field.placeholder) input.placeholder = field.placeholder;
                if (value != null) input.value = String(value);
            }
            return input;
        }

        async _buildCodeCell(field, value) {
            const wrap = document.createElement('div');
            wrap.className = 'curated-dropdown-wrapper';

            const select = document.createElement('select');
            select.dataset.field = field.name;
            select.className = 'event-field';

            // Toggle button (shown only when the fetched list is curated)
            const toggle = document.createElement('button');
            toggle.type = 'button';
            toggle.className = 'btn-toggle-curated';
            toggle.title = 'Show all DSSAT options instead of the curated list';

            wrap.appendChild(select);
            wrap.appendChild(toggle);

            const codesCfg = field.codes;
            const loadAndFill = async () => {
                const useCurated = this._curatedMode[field.name];
                const {codes, curated} = await fetchCodes(codesCfg.category, {
                    crop: this.cropCode,
                    context: codesCfg.context,
                    curated: useCurated,
                });
                select.innerHTML = buildOptions(codes);
                if (value) select.value = value;

                // Toggle visibility: only show if the widget has a crop AND we
                // detected a curation exists (i.e., curated fetch returned a
                // filtered list).
                if (this.cropCode && (curated || !useCurated)) {
                    toggle.style.display = '';
                    toggle.textContent = useCurated ? 'Show all' : 'Show curated';
                } else {
                    toggle.style.display = 'none';
                }
            };

            toggle.addEventListener('click', async () => {
                this._curatedMode[field.name] = !this._curatedMode[field.name];
                const prevValue = select.value;
                await loadAndFill();
                // Try to preserve selection if still available
                if (prevValue && [...select.options].some(o => o.value === prevValue)) {
                    select.value = prevValue;
                }
            });

            await loadAndFill();
            return wrap;
        }

        /** Serialize rows to an array of {field: value} objects. */
        getRows() {
            const rows = [];
            for (const tr of this._tbody.querySelectorAll('tr')) {
                const row = {};
                let hasValue = false;
                for (const field of this.schema.tableFields) {
                    const el = tr.querySelector(`[data-field="${field.name}"]`);
                    if (!el) continue;
                    const raw = el.value;
                    if (raw === '' || raw == null) continue;
                    if (field.type === 'number') {
                        const n = parseFloat(raw);
                        if (!isNaN(n)) {
                            row[field.name] = n;
                            hasValue = true;
                        }
                    } else {
                        row[field.name] = raw;
                        hasValue = true;
                    }
                }
                if (hasValue) rows.push(row);
            }
            return rows;
        }

        /** Replace all rows with a new set. */
        async setRows(rows) {
            this._tbody.innerHTML = '';
            for (const row of rows || []) await this.addRow(row);
        }

        /** Update the crop code (affects curated-dropdown filtering on new rows).
         *  Existing rows keep their current dropdown state. */
        setCropCode(cropCode) {
            this.cropCode = cropCode || null;
        }
    }

    function _titleCase(s) {
        return s.charAt(0).toUpperCase() + s.slice(1);
    }

    // Expose public API
    global.EventTable = EventTable;
    global.initEventTable = function (container, config) {
        return new EventTable(container, config);
    };

})(window);
