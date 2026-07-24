/*
 * Management Practices block — tabbed UI for fertilizer / irrigation /
 * harvest / tillage / chemical / residue. Used by:
 *   - DSSAT Preferences → Management Fallbacks tab (global fallback events)
 *   - DSSAT Preferences → My Crops (per-crop defaults inside each crop card)
 *   - DSSAT System Config → System Management Fallbacks
 *   - DSSAT System Config → System Per-Crop Defaults
 *
 * Fertilizer, tillage, chemical, residue → use the shared EventTable widget.
 * Irrigation                             → custom (None / Automatic / Fixed)
 *                                          with EventTable when mode=Fixed.
 * Harvest                                → custom 5-mode picker
 *                                          (Auto / Maturity / On Date /
 *                                          Growth Stage / DAP) + hcom/hsize/
 *                                          hpc/hbpc detail fields.
 *
 * Usage:
 *
 *   const mp = buildManagementPractices(container, {
 *       cropCode: 'MZ',              // null for global fallback scope
 *       initialData: {
 *           fertilizer: [{fdate, fmcd, ...}, ...],
 *           irrigation: {method: 'automatic', threshold: 50, efficiency: 90}
 *                    or {method: 'fixed', events: [{idate, irval, ...}]}
 *                    or null,
 *           harvest: {option: 'on_date', date: '2024-09-01',
 *                     stage: 'GS005', dap: 120, hcom, hsize, hpc, hbpc} or null,
 *           tillage: [...], chemical: [...], residue: [...],
 *       },
 *   });
 *   const data = mp.getValue();      // same shape as initialData
 *   await mp.setValue(data);         // replace
 */

(function (global) {
    'use strict';

    const EVENT_TABLE_CATEGORIES = ['fertilizer', 'tillage', 'chemical', 'residue'];

    const TAB_LABELS = {
        fertilizer: 'Fertilizer',
        irrigation: 'Irrigation',
        harvest: 'Harvest',
        tillage: 'Tillage',
        chemical: 'Chemical',
        residue: 'Residue',
    };
    const TAB_ORDER = ['fertilizer', 'irrigation', 'harvest', 'tillage', 'chemical', 'residue'];

    function buildManagementPractices(container, config) {
        const {
            cropCode = null,
            dssatModel = null,
            initialData = {},
            // 'defaults' = stored fallbacks/per-crop defaults (DAP-keyed,
            // no absolute dates, no harvest 'on_date' option). 'experiment'
            // is reserved for callers that surface this widget at run-time
            // (none today; the wizard uses EventTable directly).
            mode = 'defaults',
        } = config || {};

        container.classList.add('management-practices-widget');
        container.innerHTML = '';

        // --- Tab bar ----------------------------------------------------------
        const tabBar = document.createElement('div');
        tabBar.className = 'mp-tabs';
        const panels = {};

        for (const cat of TAB_ORDER) {
            const tabBtn = document.createElement('button');
            tabBtn.type = 'button';
            tabBtn.className = 'mp-tab' + (cat === 'fertilizer' ? ' active' : '');
            tabBtn.dataset.category = cat;
            tabBtn.textContent = TAB_LABELS[cat];
            tabBtn.addEventListener('click', () => {
                tabBar.querySelectorAll('.mp-tab').forEach(t => t.classList.remove('active'));
                tabBtn.classList.add('active');
                for (const k of Object.keys(panels)) {
                    panels[k].panel.classList.toggle('active', k === cat);
                }
            });
            tabBar.appendChild(tabBtn);
        }
        container.appendChild(tabBar);

        // --- Panels -----------------------------------------------------------
        const panelWrap = document.createElement('div');
        panelWrap.className = 'mp-panels';
        container.appendChild(panelWrap);

        // Event-table panels (fertilizer, tillage, chemical, residue)
        for (const cat of EVENT_TABLE_CATEGORIES) {
            const panel = document.createElement('div');
            panel.className = 'mp-panel' + (cat === 'fertilizer' ? ' active' : '');
            panel.dataset.category = cat;
            const tableContainer = document.createElement('div');
            panel.appendChild(tableContainer);
            panelWrap.appendChild(panel);

            let widget = null;
            try {
                widget = new EventTable(tableContainer, {
                    eventType: cat,
                    cropCode: cropCode,
                    mode: mode,
                    initialRows: Array.isArray(initialData[cat]) ? initialData[cat] : [],
                });
            } catch (err) {
                console.warn(`Failed to init ${cat} widget:`, err);
            }
            panels[cat] = {panel, widget};
        }

        // Irrigation panel (custom)
        panels.irrigation = _buildIrrigationPanel(
            panelWrap, cropCode, initialData.irrigation || null, mode,
        );

        // Harvest panel (custom — mode-based, crop-aware stages)
        panels.harvest = _buildHarvestPanel(
            panelWrap, cropCode, dssatModel, initialData.harvest || null, mode,
        );

        // --- API --------------------------------------------------------------
        function getValue() {
            const out = {};
            for (const cat of EVENT_TABLE_CATEGORIES) {
                const w = panels[cat] && panels[cat].widget;
                const rows = w ? w.getRows() : [];
                out[cat] = rows.length ? rows : null;
            }
            out.irrigation = panels.irrigation.getValue();
            out.harvest = panels.harvest.getValue();
            return out;
        }

        async function setValue(data) {
            data = data || {};
            for (const cat of EVENT_TABLE_CATEGORIES) {
                const w = panels[cat] && panels[cat].widget;
                const rows = Array.isArray(data[cat]) ? data[cat] : [];
                if (w) await w.setRows(rows);
            }
            await panels.irrigation.setValue(data.irrigation || null);
            panels.harvest.setValue(data.harvest || null);
        }

        function setCropCode(newCode, newModel) {
            for (const cat of EVENT_TABLE_CATEGORIES) {
                const w = panels[cat] && panels[cat].widget;
                if (w && typeof w.setCropCode === 'function') w.setCropCode(newCode);
            }
            panels.irrigation.setCropCode(newCode);
            if (panels.harvest && typeof panels.harvest.setCropModel === 'function') {
                panels.harvest.setCropModel(newCode, newModel);
            }
        }

        return {getValue, setValue, setCropCode, panels};
    }

    // ------------------------------------------------------------------------
    // Irrigation panel — mode select (None / Automatic / Fixed) with
    // conditional sub-form.
    // ------------------------------------------------------------------------
    function _buildIrrigationPanel(panelWrap, cropCode, initial, mode) {
        const panel = document.createElement('div');
        panel.className = 'mp-panel';
        panel.dataset.category = 'irrigation';
        panel.innerHTML = `
            <div class="mgmt-form">
                <div class="form-group">
                    <label>Irrigation Method</label>
                    <select data-irr-mode aria-label="Irrigation Method">
                        <option value="">None</option>
                        <option value="automatic">Automatic</option>
                        <option value="fixed">Fixed Schedule</option>
                    </select>
                </div>
                <div data-irr-auto class="form-grid" style="display:none">
                    <div class="form-group">
                        <label>Threshold (% depletion)</label>
                        <input type="number" data-irr-threshold min="0" max="100" step="1" aria-label="Irrigation threshold (% depletion)">
                    </div>
                    <div class="form-group">
                        <label>Efficiency (%)</label>
                        <input type="number" data-irr-efficiency min="0" max="100" step="1" aria-label="Irrigation efficiency (%)">
                    </div>
                </div>
                <div data-irr-fixed style="display:none">
                    <div class="event-table-widget-container"></div>
                </div>
            </div>
        `;
        panelWrap.appendChild(panel);

        const modeSel = panel.querySelector('[data-irr-mode]');
        const autoWrap = panel.querySelector('[data-irr-auto]');
        const fixedWrap = panel.querySelector('[data-irr-fixed]');
        const thresholdIn = panel.querySelector('[data-irr-threshold]');
        const efficiencyIn = panel.querySelector('[data-irr-efficiency]');
        const tableContainer = panel.querySelector('.event-table-widget-container');

        let widget = null;
        try {
            widget = new EventTable(tableContainer, {
                eventType: 'irrigation',
                cropCode: cropCode,
                mode: mode || 'defaults',
                initialRows: [],
            });
        } catch (err) {
            console.warn('Failed to init irrigation widget:', err);
        }

        function updateVisibility() {
            const mode = modeSel.value;
            autoWrap.style.display = mode === 'automatic' ? '' : 'none';
            fixedWrap.style.display = mode === 'fixed' ? '' : 'none';
        }
        modeSel.addEventListener('change', updateVisibility);

        async function applyInitial(value) {
            if (!value) {
                modeSel.value = '';
                thresholdIn.value = '';
                efficiencyIn.value = '';
                if (widget) await widget.setRows([]);
                updateVisibility();
                return;
            }
            modeSel.value = value.method || '';
            if (value.method === 'automatic') {
                thresholdIn.value = value.threshold != null ? value.threshold : '';
                efficiencyIn.value = value.efficiency != null ? value.efficiency : '';
                if (widget) await widget.setRows([]);
            } else if (value.method === 'fixed') {
                const events = value.events || value.table || [];
                if (widget) await widget.setRows(events);
            } else {
                if (widget) await widget.setRows([]);
            }
            updateVisibility();
        }

        // apply initial synchronously for mode; widget populate is async
        applyInitial(initial);

        function getValue() {
            const mode = modeSel.value;
            if (mode === 'automatic') {
                const thr = parseFloat(thresholdIn.value);
                const eff = parseFloat(efficiencyIn.value);
                const out = {method: 'automatic'};
                if (!isNaN(thr)) out.threshold = thr;
                if (!isNaN(eff)) out.efficiency = eff;
                return out;
            }
            if (mode === 'fixed') {
                const events = widget ? widget.getRows() : [];
                return events.length ? {method: 'fixed', events} : null;
            }
            return null;
        }

        function setCropCode(newCode) {
            if (widget && typeof widget.setCropCode === 'function') widget.setCropCode(newCode);
        }

        return {panel, getValue, setValue: applyInitial, setCropCode};
    }

    // ------------------------------------------------------------------------
    // Harvest panel — 5-mode selector (auto / maturity / on_date /
    // growth_stage / dap). The growth-stage dropdown is crop-aware — its
    // options come from CropModel.harvest_stages fetched from
    // /dssat/api/crop-model/<crop>/ for the current (cropCode, dssatModel)
    // pairing. The hcom / hsize / hpc / hbpc fields are always visible
    // (not collapsed in an "Advanced" section) per UX requirements.
    // ------------------------------------------------------------------------
    function _buildHarvestPanel(panelWrap, cropCode, dssatModel, initial, mode) {
        const SUBPATH = (window.SUBPATH || '').replace(/\/$/, '');
        const isDefaults = (mode || 'defaults') === 'defaults';

        // Stored defaults can't anchor an absolute date, so the 'on_date'
        // option (and its date input) are hidden when mode='defaults'.
        // Users pick 'dap' instead. The wizard does NOT use this panel —
        // it has its own template-defined harvest UI in
        // experiment_wizard.html where 'on_date' remains available.
        const onDateOption = isDefaults
            ? ''
            : '<option value="on_date">On Specified Date</option>';

        const panel = document.createElement('div');
        panel.className = 'mp-panel';
        panel.dataset.category = 'harvest';
        panel.innerHTML = `
            <div class="mgmt-form">
                <div class="form-grid">
                    <div class="form-group">
                        <label>Harvest Mode</label>
                        <select data-harvest-option aria-label="Harvest Mode">
                            <option value="">Auto (at maturity)</option>
                            <option value="maturity">Physiological Maturity</option>
                            ${onDateOption}
                            <option value="growth_stage">At Growth Stage</option>
                            <option value="dap">Days After Planting</option>
                        </select>
                    </div>
                    <div class="form-group" data-harvest-date-wrap style="display:none">
                        <label>Harvest Date</label>
                        <input type="date" data-harvest-date aria-label="Harvest Date">
                    </div>
                    <div class="form-group" data-harvest-stage-wrap style="display:none">
                        <label>Growth Stage</label>
                        <select data-harvest-stage aria-label="Growth Stage">
                            <option value="">(select)</option>
                        </select>
                        <span class="helptext" data-harvest-stage-hint></span>
                    </div>
                    <div class="form-group" data-harvest-dap-wrap style="display:none">
                        <label>Days After Planting</label>
                        <input type="number" data-harvest-dap min="1" max="730" step="1" aria-label="Harvest days after planting">
                    </div>
                </div>

                <h3 class="section-subheader">Harvest Detail</h3>
                <div class="form-grid">
                    <div class="form-group">
                        <label>Harvest Component</label>
                        <select data-harvest-hcom aria-label="Harvest Component">
                            <option value="">(default)</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Harvest Size</label>
                        <select data-harvest-hsize aria-label="Harvest Size">
                            <option value="">(default)</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Product Harvest %</label>
                        <input type="number" data-harvest-hpc min="0" max="100" step="0.1" aria-label="Product harvest %">
                    </div>
                    <div class="form-group">
                        <label>Byproduct Harvest %</label>
                        <input type="number" data-harvest-hbpc min="0" max="100" step="0.1" aria-label="Byproduct harvest %">
                    </div>
                </div>
            </div>
        `;
        panelWrap.appendChild(panel);

        const optSel       = panel.querySelector('[data-harvest-option]');
        const dateWrap     = panel.querySelector('[data-harvest-date-wrap]');
        const dateIn       = panel.querySelector('[data-harvest-date]');
        const stageWrap    = panel.querySelector('[data-harvest-stage-wrap]');
        const stageSel     = panel.querySelector('[data-harvest-stage]');
        const stageHint    = panel.querySelector('[data-harvest-stage-hint]');
        const dapWrap      = panel.querySelector('[data-harvest-dap-wrap]');
        const dapIn        = panel.querySelector('[data-harvest-dap]');
        const hcomSel      = panel.querySelector('[data-harvest-hcom]');
        const hsizeSel     = panel.querySelector('[data-harvest-hsize]');
        const hpcIn        = panel.querySelector('[data-harvest-hpc]');
        const hbpcIn       = panel.querySelector('[data-harvest-hbpc]');

        let currentCrop = cropCode;
        let currentModel = dssatModel;

        function updateVisibility() {
            const mode = optSel.value;
            dateWrap.style.display  = mode === 'on_date'        ? '' : 'none';
            stageWrap.style.display = mode === 'growth_stage'   ? '' : 'none';
            dapWrap.style.display   = mode === 'dap'            ? '' : 'none';
        }
        optSel.addEventListener('change', updateVisibility);

        function _populateSelect(sel, items, {keepValue = true} = {}) {
            const prev = keepValue ? sel.value : '';
            // Keep the first (placeholder) option, replace the rest.
            const placeholder = sel.firstElementChild;
            sel.innerHTML = '';
            if (placeholder) sel.appendChild(placeholder);
            for (const item of items || []) {
                const opt = document.createElement('option');
                opt.value = item.code;
                opt.textContent = item.name
                    ? `${item.code} — ${item.name}${item.description ? ': ' + item.description : ''}`
                    : (item.description ? `${item.code} — ${item.description}` : item.code);
                sel.appendChild(opt);
            }
            if (prev) sel.value = prev;
        }

        // Load stage + hcom/hsize lists for this (crop, model). Falls back
        // to DETAIL.CDE-backed /dssat/api/codes/ for hcom/hsize when
        // CropModel has nothing configured.
        async function _reloadCropModel() {
            if (!currentCrop) {
                _populateSelect(stageSel, []);
                _populateSelect(hcomSel, []);
                _populateSelect(hsizeSel, []);
                stageHint.textContent = '';
                return;
            }
            const modelQS = currentModel ? `?dssat_model=${encodeURIComponent(currentModel)}` : '';
            let cm = null;
            try {
                const resp = await fetch(`${SUBPATH}/dssat/api/crop-model/${encodeURIComponent(currentCrop)}/${modelQS}`);
                if (resp.ok) {
                    const j = await resp.json();
                    cm = j && j.data;
                }
            } catch (err) {
                console.warn('CropModel fetch failed:', err);
            }
            const stages = (cm && cm.harvest_stages) || [];
            _populateSelect(stageSel, stages);
            stageHint.textContent = stages.length
                ? `${stages.length} stage(s) available for ${currentCrop}${currentModel ? '/' + currentModel : ''}`
                : `No GRSTAGE entries seeded for ${currentCrop}${currentModel ? '/' + currentModel : ''} — run seed_crop_models.`;

            // Harvest components / sizes — prefer curated CropModel subset;
            // otherwise fall back to the full DSSAT code list.
            const curatedHcom = (cm && cm.harvest_components) || [];
            try {
                const resp = await fetch(`${SUBPATH}/dssat/api/codes/hcom/?crop=${encodeURIComponent(currentCrop)}`);
                if (resp.ok) {
                    const j = await resp.json();
                    const full = (j && j.codes) || [];
                    const filtered = curatedHcom.length
                        ? full.filter(c => curatedHcom.includes(c.code))
                        : full;
                    _populateSelect(hcomSel, filtered);
                }
            } catch (err) {
                console.warn('hcom fetch failed:', err);
            }
            try {
                const resp = await fetch(`${SUBPATH}/dssat/api/codes/hsize/`);
                if (resp.ok) {
                    const j = await resp.json();
                    _populateSelect(hsizeSel, (j && j.codes) || []);
                }
            } catch (err) {
                console.warn('hsize fetch failed:', err);
            }
        }

        function setValue(value) {
            value = value || {};
            // Back-compat: legacy fixed_date / reported_date → on_date.
            let option = value.option || '';
            if (option === 'fixed_date' || option === 'reported_date') option = 'on_date';
            // Stored defaults cannot use 'on_date'; older rows that still
            // carry it fall back to 'auto' (clears the option) so the user
            // picks a DAP-compatible mode explicitly.
            if (isDefaults && option === 'on_date') option = '';
            optSel.value = option;
            dateIn.value = value.date || '';
            stageSel.value = value.stage || value.hstg || '';
            dapIn.value = value.dap != null ? value.dap : '';
            hcomSel.value = value.hcom || '';
            hsizeSel.value = value.hsize || '';
            hpcIn.value = value.hpc != null ? value.hpc : '';
            hbpcIn.value = value.hbpc != null ? value.hbpc : '';
            updateVisibility();
        }
        setValue(initial);
        _reloadCropModel();

        function getValue() {
            const option = optSel.value;
            const detail = {};
            if (hcomSel.value) detail.hcom = hcomSel.value;
            if (hsizeSel.value) detail.hsize = hsizeSel.value;
            const hpc = parseFloat(hpcIn.value);
            const hbpc = parseFloat(hbpcIn.value);
            if (!isNaN(hpc)) detail.hpc = hpc;
            if (!isNaN(hbpc)) detail.hbpc = hbpc;

            // Auto: no HARVEST section; but if the user entered detail
            // fields (hcom/hsize/hpc/hbpc), surface them so the backend
            // can still write them.
            if (!option) {
                return Object.keys(detail).length ? {option: '', ...detail} : null;
            }

            const out = {option, ...detail};
            if (option === 'on_date') {
                out.date = dateIn.value || null;
            } else if (option === 'growth_stage') {
                out.stage = stageSel.value || null;
            } else if (option === 'dap') {
                const dap = parseInt(dapIn.value, 10);
                out.dap = isNaN(dap) ? null : dap;
            }
            return out;
        }

        function setCropModel(newCrop, newModel) {
            currentCrop = newCrop;
            currentModel = newModel;
            _reloadCropModel();
        }

        return {panel, getValue, setValue, setCropModel};
    }

    global.buildManagementPractices = buildManagementPractices;
})(window);
