/**
 * Experiment Wizard Controller
 *
 * Manages an 8-step wizard for building DSSAT experiment configurations.
 * Supports four experiment types: single, sensitivity, management, custom.
 * Communicates with the backend API for crop/cultivar/soil data and
 * submits the final configuration to create a chat session.
 */

const wizard = (() => {
    // =========================================================================
    // Constants
    // =========================================================================

    const TOTAL_STEPS = 9;

    // Fields that live per-treatment (vs. shared at data.*)
    // Note: initial_conditions is intentionally NOT here — it lives on the
    // soil page (step 5) and applies to all treatments uniformly.
    const PLANTING_FIELDS = ['planting_date', 'plant_population', 'row_spacing', 'planting_method'];
    const MANAGEMENT_FIELDS = [
        'fertilizer', 'irrigation', 'harvest',
        'tillage', 'chemical', 'residue',
    ];
    const TREATMENT_FIELDS = [...PLANTING_FIELDS, ...MANAGEMENT_FIELDS];

    // =========================================================================
    // Sensitivity Variables
    // =========================================================================

    const SENSITIVITY_VARIABLES = [
        // Planting — decimals indicates how many decimal places DSSAT accepts
        { key: 'planting_date', label: 'Planting Date', unit: 'days', type: 'date_offset', min: -21, max: 21, step: 1, decimals: 0 },
        { key: 'plant_population', label: 'Plant Population', unit: 'plants/m²', type: 'numeric', min: 2, max: 14, step: 0.5, decimals: 1 },
        { key: 'row_spacing', label: 'Row Spacing', unit: 'cm', type: 'numeric', min: 30, max: 120, step: 5, decimals: 1 },
        // Fertilizer
        { key: 'fertilizer_scale', label: 'Fertilizer Amount', unit: '% of baseline', type: 'scale', min: 0, max: 300, step: 10, decimals: 0,
          requires: 'fertilizer', hint: 'Scales all fertilizer event amounts proportionally' },
        // Irrigation
        { key: 'irrigation_threshold', label: 'Irrigation Trigger', unit: '% soil depletion', type: 'numeric', min: 20, max: 80, step: 5, decimals: 0,
          hint: 'Auto-irrigation threshold (generates automatic irrigation treatments)' },
        // Tillage
        { key: 'tillage_depth', label: 'Tillage Depth', unit: 'cm', type: 'numeric', min: 5, max: 40, step: 5, decimals: 1,
          requires: 'tillage', hint: 'Varies depth across all tillage events' },
        // Chemical
        { key: 'chemical_scale', label: 'Chemical Application', unit: '% of baseline', type: 'scale', min: 0, max: 300, step: 10, decimals: 0,
          requires: 'chemical', hint: 'Scales all chemical event amounts proportionally' },
    ];

    // =========================================================================
    // State
    // =========================================================================

    let currentStep = 1;
    let runMode = 'single';  // 'single' | 'batch'
    const stepStatus = {};  // step -> 'completed' | 'skipped' | null

    // Collected data
    const data = {
        experiment_type: 'single',  // 'single' | 'sensitivity' | 'management' | 'custom'
        _activeTreatmentIdx: 0,     // active treatment index for multi-treatment types

        // Shared fields (always at top level)
        crop_code: null,
        crop_name: null,
        cultivar_code: null,
        latitude: null,
        longitude: null,
        elevation: null,
        soil_id: null,
        inline_soil: null,
        weather_source: null,

        // Per-treatment fields (at top level for single/sensitivity; in treatments[] for management/custom)
        planting_date: null,
        plant_population: null,
        row_spacing: null,
        planting_method: null,
        fertilizer: [],
        irrigation: null,
        harvest: null,
        initial_conditions: null,
        tillage: [],
        chemical: [],
        residue: [],

        // Simulation controls
        simulation_controls: {},

        // Sensitivity config (sensitivity type only)
        sensitivity: { parameter: 'plant_population', min: null, max: null, steps: 5 },

        // Treatment array (management/custom types only)
        treatments: [],

        // Monte Carlo config
        monte_carlo: {
            spatial_mode: 'circle',
            center: { lat: null, lon: null },
            radius_km: 25,
            bbox: { min_lat: null, max_lat: null, min_lon: null, max_lon: null },
            grid_spacing: 0.1,
            admin_name: null,
            admin_level: 'admin1',
            nens: 30,
            sampling_strategy: 'random',
        },

        // Site Reference Data (Step 3) — selected source per parameter, plus
        // a preview cache so the Apply button has something to commit.
        in_situ_selections: { soil: null, planting_date: null },
        in_situ_preview_cache: {},
    };

    // Cached API data
    let cropsCache = null;
    let cultivarsCache = {};
    let soilsCache = null;
    const soilProfileCache = {};  // soil_id -> full profile (with layers)
    let plantingCodesLoaded = false;

    // Per-category codes cache: {category: [{code, description}, ...]}
    const codesCache = {};

    // Shared event-table widgets (fertilizer, irrigation, tillage, chemical, residue).
    // Built lazily on first entry to Step 8. Access via getEventWidget(type).
    const eventWidgets = {};

    function getEventWidget(type) {
        return eventWidgets[type] || null;
    }

    function initEventWidgets() {
        const specs = [
            {type: 'fertilizer', containerId: 'fert-widget'},
            {type: 'irrigation', containerId: 'irr-widget'},
            {type: 'tillage', containerId: 'tillage-widget'},
            {type: 'chemical', containerId: 'chemical-widget'},
            {type: 'residue', containerId: 'residue-widget'},
        ];
        const cropCode = data.crop_code || '';
        for (const spec of specs) {
            if (eventWidgets[spec.type]) continue;
            const container = document.getElementById(spec.containerId);
            if (!container) continue;
            try {
                eventWidgets[spec.type] = new EventTable(container, {
                    eventType: spec.type,
                    cropCode: cropCode,
                    initialRows: [],
                });
            } catch (e) {
                console.error(`Failed to init ${spec.type} widget:`, e);
            }
        }
    }

    function refreshWidgetCropCode() {
        const cropCode = data.crop_code || '';
        for (const w of Object.values(eventWidgets)) {
            if (w && typeof w.setCropCode === 'function') w.setCropCode(cropCode);
        }
    }

    async function loadCodesForCategory(category) {
        if (codesCache[category]) return codesCache[category];
        try {
            const result = await api(`/dssat/api/codes/${category}/`);
            const codes = result.codes || [];
            codesCache[category] = codes;
            return codes;
        } catch (e) {
            console.error(`Failed to load codes for ${category}:`, e);
            return [];
        }
    }

    function buildSelectOptions(codes, includeEmpty) {
        let html = includeEmpty ? '<option value="">--</option>' : '';
        for (const c of codes) {
            const code = c.code || c;
            const desc = c.description || '';
            const label = desc ? `${code} - ${desc}` : code;
            html += `<option value="${code}">${label}</option>`;
        }
        return html;
    }

    // Deployment subpath prefix (e.g. "/subpath" or ""). Injected by base
    // template from the SUBPATH env var; prepended to all module-absolute URLs.
    const SUBPATH = window.SUBPATH || '';

    // CSRF token
    function csrfToken() {
        const el = document.querySelector('[name=csrfmiddlewaretoken]');
        return el ? el.value : '';
    }

    // =========================================================================
    // API Helpers
    // =========================================================================

    async function api(url, options = {}) {
        // `url` is expected to start with the module prefix, e.g. "/dssat/api/...".
        // SUBPATH is prepended so URLs resolve correctly under a subpath deployment.
        const fullUrl = url.startsWith('/') ? SUBPATH + url : url;
        const defaults = {
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        };
        const resp = await fetch(fullUrl, { ...defaults, ...options });
        return resp.json();
    }

    function showLoading(text) {
        const el = document.getElementById('wizard-loading');
        document.getElementById('loading-text').textContent = text || 'Loading...';
        el.classList.add('active');
    }

    function hideLoading() {
        document.getElementById('wizard-loading').classList.remove('active');
    }

    // =========================================================================
    // Treatment Helpers
    // =========================================================================

    function hasMultipleTreatments() {
        return data.experiment_type === 'management' || data.experiment_type === 'custom';
    }

    function getActiveTreatment() {
        if (hasMultipleTreatments() && data.treatments.length > 0) {
            return data.treatments[data._activeTreatmentIdx];
        }
        return data;  // single/sensitivity: per-treatment data lives at top level
    }

    function createEmptyTreatment(name) {
        return {
            name: name || 'Treatment 1',
            planting_date: null,
            plant_population: null,
            row_spacing: null,
            planting_method: null,
            fertilizer: [],
            irrigation: null,
            harvest: null,
            tillage: [],
            chemical: [],
            residue: [],
        };
    }

    function deepCopyTreatment(src) {
        return JSON.parse(JSON.stringify(src));
    }

    // =========================================================================
    // Step Navigation
    // =========================================================================

    function goToStep(step) {
        if (step < 1 || step > TOTAL_STEPS) return;
        currentStep = step;
        updateUI();
    }

    function nextStep() {
        saveCurrentStepData();
        if (currentStep < TOTAL_STEPS) {
            if (!stepStatus[currentStep]) stepStatus[currentStep] = 'completed';
            goToStep(currentStep + 1);
            onStepEnter(currentStep);
        }
    }

    function prevStep() {
        if (currentStep > 1) {
            goToStep(currentStep - 1);
        }
    }

    function skipStep() {
        if (currentStep > 1 && currentStep < TOTAL_STEPS) {
            stepStatus[currentStep] = 'skipped';
            goToStep(currentStep + 1);
            onStepEnter(currentStep);
        }
    }

    function updateUI() {
        // Step content visibility
        document.querySelectorAll('.step-content').forEach(el => {
            el.classList.toggle('active', parseInt(el.dataset.step) === currentStep);
        });

        // Step dots
        document.querySelectorAll('.step-dot').forEach(dot => {
            const s = parseInt(dot.dataset.step);
            dot.classList.remove('active', 'completed', 'skipped');
            if (s === currentStep) dot.classList.add('active');
            else if (stepStatus[s] === 'completed') dot.classList.add('completed');
            else if (stepStatus[s] === 'skipped') dot.classList.add('skipped');
        });

        // Connectors
        const connectors = document.querySelectorAll('.step-connector');
        connectors.forEach((conn, i) => {
            conn.classList.toggle('completed', stepStatus[i + 1] === 'completed');
        });

        // Buttons
        document.getElementById('btn-prev').disabled = currentStep === 1;
        document.getElementById('btn-skip').style.display = (currentStep > 1 && currentStep < TOTAL_STEPS) ? 'inline-block' : 'none';
        document.getElementById('btn-next').style.display = currentStep < TOTAL_STEPS ? 'inline-block' : 'none';
        document.getElementById('btn-submit').style.display = currentStep === TOTAL_STEPS ? 'inline-block' : 'none';

        // Run-mode toggle: only visible on Step 1
        const runModeToggle = document.getElementById('run-mode-toggle');
        if (runModeToggle) runModeToggle.style.display = currentStep === 1 ? '' : 'none';

        // Type card highlighting
        document.querySelectorAll('.type-card').forEach(c => {
            c.classList.toggle('selected', c.dataset.type === data.experiment_type);
        });

        // Type help boxes
        ['sensitivity', 'management', 'custom', 'monte_carlo'].forEach(t => {
            const el = document.getElementById('type-help-' + t);
            if (el) el.style.display = (data.experiment_type === t) ? 'block' : 'none';
        });
    }

    // =========================================================================
    // Step Enter Hooks
    // =========================================================================

    function onStepEnter(step) {
        switch (step) {
            case 2:
                // Always reset all three location sections first
                document.getElementById('location-single-point').style.display = 'none';
                document.getElementById('location-spatial-grid').style.display = 'none';
                document.getElementById('location-batch').style.display = 'none';

                if (runMode === 'batch') {
                    document.getElementById('location-batch').style.display = 'block';
                    document.getElementById('step2-description').textContent = 'Select the locations for your batch experiment. Each location will run an independent simulation.';
                    loadBatchAdminStates();
                } else if (data.experiment_type === 'monte_carlo') {
                    document.getElementById('location-spatial-grid').style.display = 'block';
                    document.getElementById('step2-description').textContent = 'Define the spatial grid for Monte Carlo sampling.';
                    switchSpatialMode(data.monte_carlo.spatial_mode);
                } else {
                    document.getElementById('location-single-point').style.display = 'block';
                    document.getElementById('step2-description').textContent = 'Enter the geographic coordinates for your experiment site.';
                }
                updateLocationMapForStep();
                break;
            case 3:
                /* Site Reference Data tab — loads in-situ reference sources for the location */
                if (typeof loadInSituSources === 'function') {
                    loadInSituSources();
                }
                break;
            case 4: loadCrops(); break;
            case 5:
                if (data.experiment_type === 'custom') {
                    loadPlantingCodes();
                    renderTreatmentSelector('step5-treatment-selector');
                    loadTreatmentData();
                } else {
                    hideTreatmentSelector('step5-treatment-selector');
                    // Restore values first so the method dropdown's
                    // currentVal-preservation in loadPlantingCodes keeps them.
                    loadPlantingStepData();
                    loadPlantingCodes().then(loadPlantingStepData);
                }
                break;
            case 6:
                loadSoils();
                loadICStepData();
                break;
            case 7: calculateWeatherDates(); break;
            case 8:
                // Initialize shared event-table widgets on first entry;
                // subsequent entries refresh their crop-code binding.
                initEventWidgets();
                refreshWidgetCropCode();
                if (hasMultipleTreatments()) {
                    renderTreatmentSelector('step8-treatment-selector');
                    loadTreatmentData();
                } else {
                    hideTreatmentSelector('step8-treatment-selector');
                }
                if (data.experiment_type === 'management') {
                    showPlantingSummary();
                } else {
                    document.getElementById('planting-summary-panel').style.display = 'none';
                }
                break;
            case 9:
                buildReviewSummary();
                if (data.experiment_type === 'sensitivity') {
                    showSensitivityConfig();
                } else {
                    document.getElementById('sensitivity-config').style.display = 'none';
                }
                if (data.experiment_type === 'monte_carlo') {
                    document.getElementById('monte-carlo-config').style.display = 'block';
                    // Populate from saved state
                    document.getElementById('mc-nens').value = data.monte_carlo.nens;
                    document.getElementById('mc-nens-display').textContent = data.monte_carlo.nens;
                    document.getElementById('mc-sampling').value = data.monte_carlo.sampling_strategy;
                } else {
                    document.getElementById('monte-carlo-config').style.display = 'none';
                }
                break;
        }
    }

    // =========================================================================
    // Step 1: Experiment Type
    // =========================================================================

    function selectType(type) {
        const oldType = data.experiment_type;

        // Migrate data when switching between multi-treatment and single-treatment modes
        const wasMulti = oldType === 'management' || oldType === 'custom';
        const isMulti = type === 'management' || type === 'custom';

        if (!wasMulti && isMulti) {
            // Switching TO multi-treatment: migrate top-level fields into treatments[0]
            const t = createEmptyTreatment('Treatment 1');
            TREATMENT_FIELDS.forEach(f => {
                if (data[f] !== null && data[f] !== undefined) {
                    t[f] = deepCopyTreatment(data[f]);
                }
            });
            data.treatments = [t];
            data._activeTreatmentIdx = 0;
        } else if (wasMulti && !isMulti) {
            // Switching FROM multi-treatment: migrate treatments[0] back to top-level
            if (data.treatments.length > 0) {
                const t = data.treatments[0];
                TREATMENT_FIELDS.forEach(f => {
                    if (t[f] !== null && t[f] !== undefined) {
                        data[f] = deepCopyTreatment(t[f]);
                    }
                });
            }
            data.treatments = [];
            data._activeTreatmentIdx = 0;
        }

        data.experiment_type = type;
        updateUI();
    }

    function setRunMode(mode) {
        // Batch mode is temporarily disabled — ignore requests to enter it.
        if (mode === 'batch') return;
        runMode = mode;
        // Update toggle button styles
        document.querySelectorAll('.run-mode-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });
        // Show/hide batch hint
        const hint = document.getElementById('run-mode-hint');
        if (hint) hint.style.display = mode === 'batch' ? 'block' : 'none';
        // Update step 1 description
        const desc = document.querySelector('[data-step="1"] .step-description');
        if (desc) {
            desc.textContent = mode === 'batch'
                ? 'Choose the experiment type to run at each location in the batch.'
                : 'Choose the type of simulation you want to run.';
        }
    }

    // =========================================================================
    // Treatment CRUD (management + custom types)
    // =========================================================================

    function addTreatment(sourceIdx) {
        if (!hasMultipleTreatments()) return;
        const src = (sourceIdx !== undefined && data.treatments[sourceIdx])
            ? data.treatments[sourceIdx]
            : data.treatments[data.treatments.length - 1] || createEmptyTreatment('Treatment 1');
        const copy = deepCopyTreatment(src);
        copy.name = `Treatment ${data.treatments.length + 1}`;
        data.treatments.push(copy);
        setActiveTreatmentIdx(data.treatments.length - 1);
    }

    function removeTreatment(idx) {
        if (!hasMultipleTreatments() || data.treatments.length <= 1) return;
        data.treatments.splice(idx, 1);
        // Adjust active index
        if (data._activeTreatmentIdx >= data.treatments.length) {
            data._activeTreatmentIdx = data.treatments.length - 1;
        }
        // Re-render selector and load data
        if (currentStep === 5) {
            renderTreatmentSelector('step5-treatment-selector');
        } else if (currentStep === 8) {
            renderTreatmentSelector('step8-treatment-selector');
        }
        loadTreatmentData();
    }

    function renameTreatment(idx) {
        if (!data.treatments[idx]) return;
        const name = prompt('Treatment name:', data.treatments[idx].name);
        if (name) {
            data.treatments[idx].name = name;
            if (currentStep === 5) renderTreatmentSelector('step5-treatment-selector');
            if (currentStep === 8) renderTreatmentSelector('step8-treatment-selector');
        }
    }

    function setActiveTreatmentIdx(idx) {
        if (idx < 0 || idx >= data.treatments.length) return;
        // Save current treatment data before switching
        saveTreatmentData();
        data._activeTreatmentIdx = idx;
        // Load new treatment data into form
        loadTreatmentData();
        // Update selectors
        if (currentStep === 5) renderTreatmentSelector('step5-treatment-selector');
        if (currentStep === 8) renderTreatmentSelector('step8-treatment-selector');
    }

    // =========================================================================
    // Treatment Selector Rendering
    // =========================================================================

    function renderTreatmentSelector(containerId) {
        const container = document.getElementById(containerId);
        if (!container || !hasMultipleTreatments()) {
            if (container) container.style.display = 'none';
            return;
        }

        container.style.display = 'flex';
        let html = '';
        data.treatments.forEach((t, i) => {
            const active = i === data._activeTreatmentIdx ? 'active' : '';
            const closeBtn = data.treatments.length > 1
                ? `<button class="treatment-tab-close" onclick="event.stopPropagation();wizard.removeTreatment(${i})" title="Remove">&times;</button>`
                : '';
            html += `<div class="treatment-tab ${active}" onclick="wizard.setActiveTreatmentIdx(${i})" ondblclick="wizard.renameTreatment(${i})">
                ${t.name}${closeBtn}
            </div>`;
        });
        html += `<button class="treatment-tab-add" onclick="wizard.addTreatment()" title="Add treatment">+</button>`;
        container.innerHTML = html;
    }

    function hideTreatmentSelector(containerId) {
        const el = document.getElementById(containerId);
        if (el) el.style.display = 'none';
    }

    // =========================================================================
    // Form State Sync (save/load treatment data to/from forms)
    // =========================================================================

    function saveTreatmentData() {
        if (!hasMultipleTreatments()) return;
        const t = getActiveTreatment();
        if (!t) return;

        const type = data.experiment_type;

        // For custom: save both planting and management
        // For management: only save management (planting is shared at data.*)
        if (type === 'custom' && currentStep === 5) {
            t.planting_date = document.getElementById('wiz-pdate').value || null;
            t.plant_population = parseFloat(document.getElementById('wiz-ppop').value) || null;
            t.row_spacing = parseFloat(document.getElementById('wiz-plrs').value) || null;
            t.planting_method = document.getElementById('wiz-planting-method').value || null;
        }

        if (currentStep === 8) {
            t.fertilizer = getEventWidget('fertilizer')?.getRows() || [];
            t.tillage = getEventWidget('tillage')?.getRows() || [];
            t.chemical = getEventWidget('chemical')?.getRows() || [];
            t.residue = getEventWidget('residue')?.getRows() || [];

            // Irrigation
            const irrMethod = document.getElementById('wiz-irr-method').value;
            if (irrMethod === 'automatic') {
                t.irrigation = {
                    method: 'automatic',
                    threshold: parseFloat(document.getElementById('wiz-irr-threshold').value) || 50,
                    efficiency: parseFloat(document.getElementById('wiz-irr-efficiency').value) || 90,
                };
            } else if (irrMethod === 'fixed') {
                t.irrigation = {
                    method: 'fixed',
                    events: getEventWidget('irrigation')?.getRows() || [],
                };
            } else {
                t.irrigation = null;
            }

            // Harvest — 5-mode payload (see _collectHarvestData below).
            t.harvest = _collectHarvestData();
            // Initial conditions are saved on Step 6 (Soil page), not per treatment.
        }
    }

    function _collectHarvestData() {
        const option = document.getElementById('wiz-harvest-option').value;
        const detail = {};
        const hcom = document.getElementById('wiz-harvest-hcom').value;
        const hsize = document.getElementById('wiz-harvest-hsize').value;
        const hpc = parseFloat(document.getElementById('wiz-harvest-hpc').value);
        const hbpc = parseFloat(document.getElementById('wiz-harvest-hbpc').value);
        if (hcom) detail.hcom = hcom;
        if (hsize) detail.hsize = hsize;
        if (!isNaN(hpc)) detail.hpc = hpc;
        if (!isNaN(hbpc)) detail.hbpc = hbpc;

        if (!option) {
            // Auto with no detail → nothing to send.
            return Object.keys(detail).length ? { option: '', ...detail } : null;
        }
        const out = { option, ...detail };
        if (option === 'on_date') {
            out.date = document.getElementById('wiz-harvest-date').value || null;
        } else if (option === 'growth_stage') {
            out.stage = document.getElementById('wiz-harvest-stage').value || null;
        } else if (option === 'dap') {
            const dap = parseInt(document.getElementById('wiz-harvest-dap').value, 10);
            out.dap = isNaN(dap) ? null : dap;
        }
        return out;
    }

    function _restoreHarvestData(h) {
        const optSel = document.getElementById('wiz-harvest-option');
        if (!h) {
            optSel.value = '';
            document.getElementById('wiz-harvest-date').value = '';
            document.getElementById('wiz-harvest-stage').value = '';
            document.getElementById('wiz-harvest-dap').value = '';
            document.getElementById('wiz-harvest-hcom').value = '';
            document.getElementById('wiz-harvest-hsize').value = '';
            document.getElementById('wiz-harvest-hpc').value = '';
            document.getElementById('wiz-harvest-hbpc').value = '';
        } else {
            // Back-compat: legacy fixed_date / reported_date → on_date.
            let option = h.option || '';
            if (option === 'fixed_date' || option === 'reported_date') option = 'on_date';
            optSel.value = option;
            document.getElementById('wiz-harvest-date').value = h.date || '';
            document.getElementById('wiz-harvest-stage').value = h.stage || h.hstg || '';
            document.getElementById('wiz-harvest-dap').value = h.dap != null ? h.dap : '';
            document.getElementById('wiz-harvest-hcom').value = h.hcom || '';
            document.getElementById('wiz-harvest-hsize').value = h.hsize || '';
            document.getElementById('wiz-harvest-hpc').value = h.hpc != null ? h.hpc : '';
            document.getElementById('wiz-harvest-hbpc').value = h.hbpc != null ? h.hbpc : '';
        }
        _updateHarvestModeVisibility();
    }

    async function loadTreatmentData() {
        if (!hasMultipleTreatments()) return;
        const t = getActiveTreatment();
        if (!t) return;

        const type = data.experiment_type;

        // Load planting fields (custom type, step 4)
        if (type === 'custom' && currentStep === 5) {
            document.getElementById('wiz-pdate').value = t.planting_date || '';
            document.getElementById('wiz-ppop').value = t.plant_population || '';
            document.getElementById('wiz-plrs').value = t.row_spacing || '';
            document.getElementById('wiz-planting-method').value = t.planting_method || '';
        }

        // Load management fields (step 8)
        if (currentStep === 8) {
            await getEventWidget('fertilizer')?.setRows(t.fertilizer || []);
            await getEventWidget('tillage')?.setRows(t.tillage || []);
            await getEventWidget('chemical')?.setRows(t.chemical || []);
            await getEventWidget('residue')?.setRows(t.residue || []);

            // Irrigation
            if (t.irrigation) {
                document.getElementById('wiz-irr-method').value = t.irrigation.method || '';
                document.getElementById('irr-auto-config').style.display = t.irrigation.method === 'automatic' ? 'block' : 'none';
                document.getElementById('irr-fixed-config').style.display = t.irrigation.method === 'fixed' ? 'block' : 'none';
                if (t.irrigation.method === 'automatic') {
                    document.getElementById('wiz-irr-threshold').value = t.irrigation.threshold || '';
                    document.getElementById('wiz-irr-efficiency').value = t.irrigation.efficiency || '';
                } else if (t.irrigation.method === 'fixed') {
                    await getEventWidget('irrigation')?.setRows(t.irrigation.events || []);
                }
            } else {
                document.getElementById('wiz-irr-method').value = '';
                document.getElementById('irr-auto-config').style.display = 'none';
                document.getElementById('irr-fixed-config').style.display = 'none';
                await getEventWidget('irrigation')?.setRows([]);
            }

            // Harvest — full 5-mode payload incl. detail fields.
            _restoreHarvestData(t.harvest);
            // Initial conditions are loaded on Step 6 (Soil page), not per treatment.
        }
    }

    // populateTableRows removed — replaced by EventTable widget's async setRows().

    // =========================================================================
    // Planting Summary (management comparison)
    // =========================================================================

    function showPlantingSummary() {
        const panel = document.getElementById('planting-summary-panel');
        const content = document.getElementById('planting-summary-content');
        const parts = [];
        if (data.planting_date) parts.push(`Date: ${data.planting_date}`);
        if (data.plant_population) parts.push(`${data.plant_population} plants/m\u00B2`);
        if (data.row_spacing) parts.push(`${data.row_spacing} cm spacing`);
        content.textContent = parts.length ? parts.join(' \u2022 ') : 'No planting configuration set (Step 4)';
        panel.style.display = 'block';
    }

    // =========================================================================
    // Step 3: Site Reference Data (in-situ lookups)
    // =========================================================================

    async function loadInSituSources() {
        const batchHint = document.getElementById('insitu-batch-hint');
        const noLocHint = document.getElementById('insitu-no-location-hint');
        const content = document.getElementById('insitu-content');
        if (!batchHint || !noLocHint || !content) return;

        // Batch mode: hide content, show hint, exit early.
        if (runMode === 'batch') {
            batchHint.style.display = 'block';
            noLocHint.style.display = 'none';
            content.style.display = 'none';
            return;
        }
        batchHint.style.display = 'none';

        // For MC, the representative point may still need to be resolved from
        // an admin centroid; for circle/bbox it's already in data.latitude/lon.
        await _ensureMCRepresentativePoint();

        const lat = _resolveInSituLat();
        const lon = _resolveInSituLon();
        if (lat == null || lon == null) {
            content.style.display = 'none';
            noLocHint.style.display = 'block';
            return;
        }
        noLocHint.style.display = 'none';
        content.style.display = 'block';

        showLoading('Checking available in-situ sources...');
        try {
            const result = await api(`/dssat/api/insitu/sources/?lat=${lat}&lon=${lon}`);
            _renderInSituSources(result.sources || {});
        } catch (e) {
            console.error('Failed to load in-situ sources:', e);
            _renderInSituSources({});
        }
        hideLoading();
    }

    function _resolveInSituLat() { return data.latitude; }
    function _resolveInSituLon() { return data.longitude; }

    async function _ensureMCRepresentativePoint() {
        if (data.experiment_type !== 'monte_carlo') return;
        const mc = data.monte_carlo;
        if (mc.spatial_mode === 'circle') {
            data.latitude = mc.center && mc.center.lat;
            data.longitude = mc.center && mc.center.lon;
        } else if (mc.spatial_mode === 'bbox') {
            if (mc.bbox.min_lat != null && mc.bbox.max_lat != null) {
                data.latitude = (mc.bbox.min_lat + mc.bbox.max_lat) / 2;
                data.longitude = (mc.bbox.min_lon + mc.bbox.max_lon) / 2;
            }
        } else if (mc.spatial_mode === 'admin') {
            if (!mc.admin_name) {
                data.latitude = null;
                data.longitude = null;
                return;
            }
            try {
                const r = await api(
                    `/dssat/api/admin-units/centroid/?level=${encodeURIComponent(mc.admin_level)}&name=${encodeURIComponent(mc.admin_name)}`
                );
                if (r && r.success) {
                    data.latitude = r.lat;
                    data.longitude = r.lon;
                }
            } catch (e) {
                console.warn('Admin centroid lookup failed:', e);
            }
        }
    }

    function _renderInSituSources(sources) {
        // sources = { soil: [...], planting_date: [...] }
        ['soil', 'planting_date'].forEach(param => {
            const sel = document.getElementById(`insitu-${param}-source`);
            if (!sel) return;
            // Reset
            sel.innerHTML = '';
            const skip = document.createElement('option');
            skip.value = '';
            const skipStep = (param === 'soil') ? 6 : 5;
            skip.textContent = `— Skip (configure manually in Step ${skipStep}) —`;
            sel.appendChild(skip);

            const items = sources[param] || [];
            items.forEach(src => {
                const opt = document.createElement('option');
                opt.value = src.source;
                const badge = src.in_coverage ? '✓ in coverage' : '✗ outside coverage';
                opt.textContent = `${src.label}  (${badge})`;
                if (!src.in_coverage) opt.disabled = true;
                sel.appendChild(opt);
            });

            // Restore saved selection if still valid
            const saved = data.in_situ_selections[param];
            if (saved && Array.from(sel.options).some(o => o.value === saved && !o.disabled)) {
                sel.value = saved;
            }
            onInSituSourceChange(param);
        });
    }

    function onInSituSourceChange(param) {
        const sel = document.getElementById(`insitu-${param}-source`);
        const previewBtn = document.getElementById(`insitu-${param}-preview-btn`);
        const applyBtn = document.getElementById(`insitu-${param}-apply-btn`);
        const previewDiv = document.getElementById(`insitu-${param}-preview`);
        const status = document.getElementById(`insitu-${param}-status`);
        if (!sel || !previewBtn || !applyBtn) return;

        data.in_situ_selections[param] = sel.value || null;
        delete data.in_situ_preview_cache[param];
        if (previewDiv) previewDiv.innerHTML = '';
        if (status) status.textContent = '';

        const isMC = data.experiment_type === 'monte_carlo';
        if (isMC) {
            previewBtn.textContent = 'Check Coverage';
            applyBtn.style.display = 'none';
        } else {
            previewBtn.textContent = 'Preview';
            applyBtn.style.display = '';
        }

        const has = !!sel.value;
        previewBtn.disabled = !has;
        applyBtn.disabled = true;  // requires preview first
    }

    async function previewInSituLookup(param) {
        if (data.experiment_type === 'monte_carlo') {
            return _coverageInSituLookup(param);
        }
        const sel = document.getElementById(`insitu-${param}-source`);
        const previewDiv = document.getElementById(`insitu-${param}-preview`);
        const applyBtn = document.getElementById(`insitu-${param}-apply-btn`);
        if (!sel || !sel.value || !previewDiv) return;

        const lat = _resolveInSituLat();
        const lon = _resolveInSituLon();
        if (lat == null || lon == null) return;

        previewDiv.innerHTML = '<span style="color:var(--text-muted);">Loading…</span>';
        try {
            const result = await api('/dssat/api/insitu/preview/', {
                method: 'POST',
                body: JSON.stringify({
                    lat, lon,
                    parameter: param,
                    source: sel.value,
                    year: _resolveInSituYear(),
                }),
            });
            if (result.error) {
                previewDiv.innerHTML = `<div style="color:var(--color-hover);">${_escape(result.error)}</div>`;
                applyBtn.disabled = true;
                return;
            }
            data.in_situ_preview_cache[param] = result;
            previewDiv.innerHTML = _renderInSituPreview(param, result);
            applyBtn.disabled = false;
        } catch (e) {
            console.error(e);
            previewDiv.innerHTML = `<div style="color:var(--color-hover);">Lookup failed: ${_escape(e.message || String(e))}</div>`;
            applyBtn.disabled = true;
        }
    }

    async function _coverageInSituLookup(param) {
        const sel = document.getElementById(`insitu-${param}-source`);
        const previewDiv = document.getElementById(`insitu-${param}-preview`);
        if (!sel || !sel.value || !previewDiv) return;

        previewDiv.innerHTML = '<span style="color:var(--text-muted);">Checking coverage across grid points…</span>';
        try {
            const result = await api('/dssat/api/insitu/coverage/', {
                method: 'POST',
                body: JSON.stringify({
                    monte_carlo: data.monte_carlo,
                    parameter: param,
                    source: sel.value,
                    year: _resolveInSituYear(),
                }),
            });
            if (result.error) {
                previewDiv.innerHTML = `<div style="color:var(--color-hover);">${_escape(result.error)}</div>`;
                return;
            }
            data.in_situ_preview_cache[param] = { mode: 'coverage', ...result };
            previewDiv.innerHTML = _renderInSituCoverage(result);
        } catch (e) {
            console.error(e);
            previewDiv.innerHTML = `<div style="color:var(--color-hover);">Coverage check failed: ${_escape(e.message || String(e))}</div>`;
        }
    }

    function _renderInSituCoverage(result) {
        const total = result.total_points || 0;
        const covered = result.covered || 0;
        const ratio = result.coverage_ratio || 0;
        const pct = Math.round(ratio * 100);
        const full = total > 0 && covered === total;
        const color = full ? '#15803d' : (covered > 0 ? '#b45309' : 'var(--color-hover)');
        const badge = full ? '✓ full coverage' : (covered > 0 ? 'partial coverage' : '✗ no coverage');
        const sampledNote = result.sampled
            ? ` <span style="color:var(--text-muted);font-size:0.8rem;">(sampled subset)</span>`
            : '';
        let html = `<div style="color:${color};">`
            + `<strong>${covered}/${total}</strong> points covered (${pct}%) — ${badge}${sampledNote}`
            + `</div>`;
        const uncov = result.uncovered || [];
        if (uncov.length) {
            const shown = uncov.slice(0, 6)
                .map(p => `[${p[0].toFixed(3)}, ${p[1].toFixed(3)}]`)
                .join(', ');
            const extra = uncov.length > 6 ? ` … (+${uncov.length - 6} more)` : '';
            html += `<div style="margin-top:0.35rem;color:var(--text-muted);font-size:0.8rem;">`
                + `Uncovered (lat, lon): ${_escape(shown)}${extra}`
                + `</div>`;
        }
        return html;
    }

    function _resolveInSituYear() {
        // Best-guess year for converting Julian days to ISO dates.
        // Prefer a planting date already set; otherwise current year.
        if (data.planting_date) {
            return parseInt(data.planting_date.slice(0, 4), 10);
        }
        return new Date().getFullYear();
    }

    function _renderInSituPreview(param, result) {
        if (param === 'soil') {
            const dom = result.dominant;
            const alts = result.alternates || [];
            if (!dom) {
                return '<div style="color:var(--text-muted);">No soil data at this location for the selected source.</div>';
            }
            let html = `<div><strong>Dominant:</strong> ${_escape(dom.soil_id)} — ${_escape(dom.description || '')}</div>`;
            if (alts.length) {
                html += `<div style="margin-top:0.25rem;"><strong>Alternates:</strong></div><ul style="margin:0.25rem 0 0 1.25rem;">`;
                alts.forEach(a => {
                    html += `<li>${_escape(a.soil_id)} — ${_escape(a.description || '')}</li>`;
                });
                html += '</ul>';
            }
            if (result.cached) {
                html += `<div style="margin-top:0.25rem;color:var(--text-muted);font-size:0.8rem;">Cached lookup</div>`;
            }
            return html;
        }
        if (param === 'planting_date') {
            if (!result.planting_date) {
                return '<div style="color:var(--text-muted);">No planting date data at this location.</div>';
            }
            return `<div><strong>Typical planting date:</strong> ${_escape(result.planting_date)} (Julian day ${result.julian_day})</div>`;
        }
        return '';
    }

    function applyInSituLookup(param) {
        // In MC mode there is no single value to commit; the backend resolves
        // per-point at run time. The coverage check is the "applied" state.
        if (data.experiment_type === 'monte_carlo') return;

        const result = data.in_situ_preview_cache[param];
        const status = document.getElementById(`insitu-${param}-status`);
        if (!result) return;

        if (param === 'soil') {
            if (result.dominant && result.dominant.soil_id) {
                data.soil_id = result.dominant.soil_id;
                data.inline_soil = null;
                if (status) status.textContent = `✓ applied: ${result.dominant.soil_id}`;
                // Reflect the auto-fill in Step 6 immediately, even though
                // the user is still on Step 3 — the next time they navigate
                // to Step 6 it will already be collapsed.
                _collapseSoilPicker();
            }
        } else if (param === 'planting_date') {
            if (result.planting_date) {
                data.planting_date = result.planting_date;
                if (status) status.textContent = `✓ applied: ${result.planting_date}`;
            }
        }
    }

    function _escape(s) {
        if (s == null) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // =========================================================================
    // Step 4: Crop & Cultivar
    // =========================================================================

    async function loadCrops() {
        if (!cropsCache) {
            showLoading('Loading crops...');
            try {
                const result = await api('/dssat/api/crops/');
                cropsCache = result.crops || [];
                _populateCropGroupFilter(cropsCache);
                renderCrops(cropsCache);
            } catch (e) {
                console.error('Failed to load crops:', e);
            }
            hideLoading();
        } else {
            renderCrops(cropsCache);
        }
        // Respect any crop the user has already picked: start collapsed and
        // re-render the cultivar section (which in turn collapses to a chip
        // if a cultivar was also picked).
        if (data.crop_code) {
            selectCrop(data.crop_code, data.crop_name);
        } else {
            _expandCropPicker();
        }
    }

    function _populateCropGroupFilter(crops) {
        const sel = document.getElementById('crop-group-filter');
        if (!sel) return;
        const groups = Array.from(new Set(
            (crops || []).map(c => c.crop_group || '').filter(Boolean)
        )).sort();
        const prev = sel.value;
        // Rebuild while preserving the "All groups" placeholder at index 0.
        sel.innerHTML = '<option value="">All groups</option>';
        for (const g of groups) {
            const opt = document.createElement('option');
            opt.value = g;
            opt.textContent = g.charAt(0).toUpperCase() + g.slice(1);
            sel.appendChild(opt);
        }
        if (prev) sel.value = prev;
    }

    function renderCrops(crops) {
        const grid = document.getElementById('crop-grid');
        grid.innerHTML = crops.map(c => `
            <div class="item-card ${data.crop_code === c.code ? 'selected' : ''}"
                 data-code="${c.code}" data-name="${c.name}"
                 data-group="${c.crop_group || ''}"
                 onclick="wizard.selectCrop('${c.code}', '${c.name}')">
                <div class="item-card-title">${c.name}</div>
                <div class="item-card-sub">${c.code}${c.crop_group ? ' · ' + c.crop_group : ''}</div>
            </div>
        `).join('');
    }

    function filterCrops(_query) {
        // Called with no args from both the text input and the group select;
        // reads current values directly so the two filters compose.
        if (!cropsCache) return;
        const searchEl = document.getElementById('crop-search');
        const groupEl = document.getElementById('crop-group-filter');
        const q = (searchEl ? searchEl.value : '').toLowerCase().trim();
        const g = groupEl ? groupEl.value : '';
        const filtered = cropsCache.filter(c => {
            if (g && (c.crop_group || '') !== g) return false;
            if (!q) return true;
            return c.name.toLowerCase().includes(q) || c.code.toLowerCase().includes(q);
        });
        renderCrops(filtered);
    }

    async function selectCrop(code, name) {
        const sameCrop = data.crop_code === code;
        data.crop_code = code;
        data.crop_name = name;
        // Only clear the cultivar when the crop actually changed — re-entering
        // Step 4 calls this path to re-render, and we want to keep the prior
        // cultivar (and its collapsed chip).
        if (!sameCrop) data.cultivar_code = null;
        renderCrops(cropsCache || []);
        _collapseCropPicker(code, name);

        // Refresh crop-aware dropdowns (growth stage / hcom / hsize) when
        // the user picks a crop. Safe to call even before step 8 is
        // rendered — the function no-ops if the selects don't exist yet.
        try { reloadHarvestOptions(); } catch (_e) {}

        document.getElementById('cultivar-section').style.display = 'block';
        showLoading('Loading cultivars...');
        try {
            if (!cultivarsCache[code]) {
                const result = await api(`/dssat/api/cultivars/${code}/`);
                cultivarsCache[code] = result.cultivars || [];
            }
            renderCultivars(cultivarsCache[code]);
            if (data.cultivar_code) {
                const cv = cultivarsCache[code].find(c => c.code === data.cultivar_code);
                _collapseCultivarPicker(data.cultivar_code, cv ? (cv.name || cv.code) : data.cultivar_code);
            } else {
                _expandCultivarPicker();
            }
        } catch (e) {
            console.error('Failed to load cultivars:', e);
        }
        hideLoading();
    }

    function changeCrop() {
        // Reopen the picker so the user can pick a different crop. The
        // existing selection stays in `data.crop_code` until they click a new
        // card, so cancelling out of the picker is a no-op.
        _expandCropPicker();
    }

    function _collapseCropPicker(code, name) {
        const picker = document.getElementById('crop-picker');
        const chip = document.getElementById('crop-selected');
        const nameEl = document.getElementById('crop-selected-name');
        const codeEl = document.getElementById('crop-selected-code');
        if (!picker || !chip) return;
        picker.style.display = 'none';
        chip.style.display = 'inline-flex';
        if (nameEl) nameEl.textContent = name || code || '';
        if (codeEl) codeEl.textContent = code ? `(${code})` : '';
    }

    function _expandCropPicker() {
        const picker = document.getElementById('crop-picker');
        const chip = document.getElementById('crop-selected');
        if (!picker || !chip) return;
        picker.style.display = '';
        chip.style.display = 'none';
    }

    function renderCultivars(cultivars) {
        const grid = document.getElementById('cultivar-grid');
        grid.innerHTML = cultivars.map(cv => `
            <div class="item-card ${data.cultivar_code === cv.code ? 'selected' : ''}"
                 onclick="wizard.selectCultivar('${cv.code}')">
                <div class="item-card-title">${cv.name || cv.code}</div>
                <div class="item-card-sub">${cv.code}</div>
            </div>
        `).join('');
    }

    function filterCultivars(query) {
        const cultivars = cultivarsCache[data.crop_code] || [];
        const filtered = cultivars.filter(cv =>
            (cv.name || '').toLowerCase().includes(query.toLowerCase()) ||
            cv.code.toLowerCase().includes(query.toLowerCase())
        );
        renderCultivars(filtered);
    }

    function selectCultivar(code) {
        data.cultivar_code = code;
        // Capture the cultivar's DSSAT model so the harvest-stage dropdown
        // (and anything else keyed on the model) can resolve correctly.
        const cv = (cultivarsCache[data.crop_code] || []).find(c => c.code === code);
        if (cv && cv.dssat_model) {
            data.dssat_model = cv.dssat_model;
        }
        renderCultivars(cultivarsCache[data.crop_code] || []);
        _collapseCultivarPicker(code, cv ? (cv.name || cv.code) : code);
        try { reloadHarvestOptions(); } catch (_e) {}
    }

    function changeCultivar() {
        _expandCultivarPicker();
    }

    function _collapseCultivarPicker(code, name) {
        const picker = document.getElementById('cultivar-picker');
        const chip = document.getElementById('cultivar-selected');
        const nameEl = document.getElementById('cultivar-selected-name');
        const codeEl = document.getElementById('cultivar-selected-code');
        if (!picker || !chip) return;
        picker.style.display = 'none';
        chip.style.display = 'inline-flex';
        if (nameEl) nameEl.textContent = name || code || '';
        if (codeEl) codeEl.textContent = code ? `(${code})` : '';
    }

    function _expandCultivarPicker() {
        const picker = document.getElementById('cultivar-picker');
        const chip = document.getElementById('cultivar-selected');
        if (!picker || !chip) return;
        picker.style.display = '';
        chip.style.display = 'none';
    }

    // =========================================================================
    // Step 6: Soil
    // =========================================================================

    async function loadSoils() {
        if (soilsCache) {
            await _ensureAppliedSoilInCache();
            renderSoils(soilsCache);
        } else {
            showLoading('Loading soils...');
            try {
                const result = await api('/dssat/api/soils/?page_size=100');
                soilsCache = result.profiles || result.soils || [];
                await _ensureAppliedSoilInCache();
                renderSoils(soilsCache);
            } catch (e) {
                console.error('Failed to load soils:', e);
            }
            hideLoading();
        }
        // Honour any soil already chosen in an earlier step (in-situ apply,
        // wizard auto-fill, manual select on a previous visit). Start collapsed.
        if (data.soil_id || data.inline_soil) {
            _collapseSoilPicker();
        } else {
            _expandSoilPicker();
        }
    }

    function changeSoil() {
        // Reopen the picker so the user can pick differently. Existing
        // selection stays in `data.soil_id` / `data.inline_soil` until they
        // commit a new choice, so cancelling out is a no-op.
        _expandSoilPicker();
    }

    function _collapseSoilPicker() {
        const picker = document.getElementById('soil-picker');
        const chip = document.getElementById('soil-selected');
        const nameEl = document.getElementById('soil-selected-name');
        const sourceEl = document.getElementById('soil-selected-source');
        if (!picker || !chip) return;

        let label = '';
        let source = '';
        if (data.soil_id) {
            label = data.soil_id;
            // Best-effort: include the soil's display name from the cache.
            const profile = (soilsCache || []).find(s => s.soil_id === data.soil_id);
            if (profile && (profile.name || profile.classification)) {
                source = `(${profile.name || profile.classification})`;
            }
        } else if (data.inline_soil) {
            const s = data.inline_soil;
            const bits = [];
            if (s.clay_pct != null) bits.push(`${s.clay_pct}% clay`);
            if (s.silt_pct != null) bits.push(`${s.silt_pct}% silt`);
            if (s.sand_pct != null) bits.push(`${s.sand_pct}% sand`);
            label = 'Estimated soil';
            source = bits.length ? `(${bits.join(', ')})` : '';
        } else {
            return;  // nothing to collapse
        }

        if (nameEl) nameEl.textContent = label;
        if (sourceEl) sourceEl.textContent = source;
        picker.style.display = 'none';
        chip.style.display = 'inline-flex';
    }

    function _expandSoilPicker() {
        const picker = document.getElementById('soil-picker');
        const chip = document.getElementById('soil-selected');
        if (!picker || !chip) return;
        picker.style.display = '';
        chip.style.display = 'none';
    }

    // If data.soil_id was set earlier (e.g. via in-situ apply in Step 3) but
    // isn't in the first page of soils, fetch it directly and prepend so the
    // user sees it pre-selected here.
    async function _ensureAppliedSoilInCache() {
        if (!data.soil_id || !Array.isArray(soilsCache)) return;
        if (soilsCache.some(s => s.soil_id === data.soil_id)) return;
        try {
            const result = await api(`/dssat/api/soils/${encodeURIComponent(data.soil_id)}/`);
            const profile = result.profile || result;
            if (profile && profile.soil_id) {
                soilsCache = [profile, ...soilsCache];
            }
        } catch (e) {
            console.warn('Could not fetch applied soil', data.soil_id, e);
        }
    }

    function renderSoils(soils) {
        const grid = document.getElementById('soil-grid');
        grid.innerHTML = soils.map(s => `
            <div class="item-card ${data.soil_id === s.soil_id ? 'selected' : ''}"
                 onclick="wizard.selectSoil('${s.soil_id}')">
                <div class="item-card-title">${s.soil_id}</div>
                <div class="item-card-sub">${s.name || s.classification || ''}</div>
            </div>
        `).join('');
    }

    function filterSoils(query) {
        if (!soilsCache) return;
        const filtered = soilsCache.filter(s =>
            s.soil_id.toLowerCase().includes(query.toLowerCase()) ||
            (s.name || '').toLowerCase().includes(query.toLowerCase())
        );
        renderSoils(filtered);
    }

    async function selectSoil(soilId) {
        data.soil_id = soilId;
        data.inline_soil = null;
        renderSoils(soilsCache || []);
        _collapseSoilPicker();
        await loadAndRenderICLayers(soilId);
    }

    async function loadFullSoilProfile(soilId) {
        if (soilProfileCache[soilId]) return soilProfileCache[soilId];
        try {
            const result = await api(`/dssat/api/soils/${encodeURIComponent(soilId)}/`);
            // SoilDetailAPI returns the dict from get_soil_profile()
            const profile = result.profile || result;
            soilProfileCache[soilId] = profile;
            return profile;
        } catch (e) {
            console.error('Failed to load soil profile', soilId, e);
            return null;
        }
    }

    async function loadAndRenderICLayers(soilId) {
        const section = document.getElementById('ic-section');
        const tbody = document.getElementById('ic-layer-rows');
        const empty = document.getElementById('ic-layers-empty');
        if (!section || !tbody) return;

        section.style.display = 'block';
        tbody.innerHTML = '';

        const profile = await loadFullSoilProfile(soilId);
        const layers = (profile && profile.layers) || [];

        if (!layers.length) {
            empty.style.display = 'block';
            return;
        }
        empty.style.display = 'none';

        // Pre-populate row values from any saved IC, keyed by depth
        const savedByDepth = {};
        if (data.initial_conditions && Array.isArray(data.initial_conditions.layers)) {
            data.initial_conditions.layers.forEach(l => {
                if (l && l.icbl != null) savedByDepth[Number(l.icbl)] = l;
            });
        }

        layers.forEach((layer, idx) => {
            const depth = Number(layer.slb);
            // Default vol. water to a midpoint between LL and DUL if both are
            // available; otherwise fall back to DUL or 0.25.
            let defaultH2o = layer.sdul ?? 0.25;
            if (layer.slll != null && layer.sdul != null) {
                defaultH2o = (Number(layer.slll) + Number(layer.sdul)) / 2;
            }
            const saved = savedByDepth[depth];
            const sh2o = saved && saved.sh2o != null ? saved.sh2o : defaultH2o.toFixed(3);
            const snh4 = saved && saved.snh4 != null ? saved.snh4 : '';
            const sno3 = saved && saved.sno3 != null ? saved.sno3 : '';

            const tr = document.createElement('tr');
            tr.dataset.depth = depth;
            tr.innerHTML = `
                <td class="mono">${depth}</td>
                <td><input type="number" class="wizard-input ic-layer-sh2o" step="0.01" min="0" max="1" value="${sh2o}"></td>
                <td><input type="number" class="wizard-input ic-layer-snh4" step="0.1" min="0" value="${snh4}" placeholder="optional"></td>
                <td><input type="number" class="wizard-input ic-layer-sno3" step="0.1" min="0" value="${sno3}" placeholder="optional"></td>
            `;
            tbody.appendChild(tr);
        });
    }

    function clearICLayers() {
        const section = document.getElementById('ic-section');
        const tbody = document.getElementById('ic-layer-rows');
        if (tbody) tbody.innerHTML = '';
        if (section) section.style.display = 'none';
    }

    function getDefaultICDate() {
        // Default ICDAT to planting_date - 15 days. If planting hasn't been
        // chosen yet, leave the input blank.
        if (!data.planting_date) return '';
        return _offsetDate(data.planting_date, -15);
    }

    function maybeFillDefaultICDate() {
        const el = document.getElementById('wiz-ic-icdat');
        if (!el) return;
        if (el.value) return;  // user already set it
        const def = getDefaultICDate();
        if (def) el.value = def;
    }

    function loadICStepData() {
        // Restore IC fields from data.initial_conditions when entering step 4.
        const ic = data.initial_conditions || {};
        const pcr = document.getElementById('wiz-ic-pcr');
        const icdat = document.getElementById('wiz-ic-icdat');
        if (pcr) pcr.value = ic.pcr || 'MZ';
        if (icdat) icdat.value = ic.icdat || '';

        const fields = [
            ['wiz-ic-icres', 'icres'],
            ['wiz-ic-icren', 'icren'],
            ['wiz-ic-icrip', 'icrip'],
            ['wiz-ic-icrid', 'icrid'],
        ];
        fields.forEach(([elId, key]) => {
            const el = document.getElementById(elId);
            if (el) el.value = ic[key] != null ? ic[key] : '';
        });

        maybeFillDefaultICDate();

        if (data.soil_id) {
            loadAndRenderICLayers(data.soil_id);
        } else {
            clearICLayers();
        }
    }

    function saveICStepData() {
        // Read all IC inputs and assemble data.initial_conditions.
        // Only persists when at least one meaningful field is filled.
        const pcr = document.getElementById('wiz-ic-pcr').value || 'MZ';
        const icdat = document.getElementById('wiz-ic-icdat').value || getDefaultICDate() || null;
        const icres = parseFloat(document.getElementById('wiz-ic-icres').value);
        const icren = parseFloat(document.getElementById('wiz-ic-icren').value);
        const icrip = parseFloat(document.getElementById('wiz-ic-icrip').value);
        const icrid = parseFloat(document.getElementById('wiz-ic-icrid').value);

        const layerRows = document.querySelectorAll('#ic-layer-rows tr');
        const layers = [];
        layerRows.forEach(tr => {
            const depth = parseFloat(tr.dataset.depth);
            const sh2o = parseFloat(tr.querySelector('.ic-layer-sh2o').value);
            const snh4Raw = tr.querySelector('.ic-layer-snh4').value;
            const sno3Raw = tr.querySelector('.ic-layer-sno3').value;
            if (isNaN(depth) || isNaN(sh2o)) return;
            const layer = { icbl: depth, sh2o };
            if (snh4Raw !== '') {
                const v = parseFloat(snh4Raw);
                if (!isNaN(v)) layer.snh4 = v;
            }
            if (sno3Raw !== '') {
                const v = parseFloat(sno3Raw);
                if (!isNaN(v)) layer.sno3 = v;
            }
            layers.push(layer);
        });

        const hasSurfaceData = !isNaN(icres) || !isNaN(icren) || !isNaN(icrip) || !isNaN(icrid);
        const hasAnyData = layers.length > 0 || hasSurfaceData || icdat;

        if (!hasAnyData) {
            data.initial_conditions = null;
            return;
        }

        const ic = { pcr };
        if (icdat) ic.icdat = icdat;
        if (!isNaN(icres)) ic.icres = icres;
        if (!isNaN(icren)) ic.icren = icren;
        if (!isNaN(icrip)) ic.icrip = icrip;
        if (!isNaN(icrid)) ic.icrid = icrid;
        if (layers.length) ic.layers = layers;

        data.initial_conditions = ic;
    }

    function switchSoilTab(tab) {
        document.querySelectorAll('#soil-tabs .mgmt-tab').forEach(t => t.classList.remove('active'));
        document.getElementById('soil-existing-panel').classList.toggle('active', tab === 'existing');
        document.getElementById('soil-estimate-panel').classList.toggle('active', tab === 'estimate');
        const autoPanel = document.getElementById('soil-auto-panel');
        if (autoPanel) autoPanel.classList.toggle('active', tab === 'auto');

        // Activate the correct tab button based on text content (more robust
        // than nth-child as the tab count grows).
        document.querySelectorAll('#soil-tabs .mgmt-tab').forEach(btn => {
            const onclick = btn.getAttribute('onclick') || '';
            if (onclick.includes(`'${tab}'`)) btn.classList.add('active');
        });

        // Lazy-load auto-fill source list on first activation.
        if (tab === 'auto') {
            _ensureSoilAutoSourcesLoaded();
        }
    }

    // -------------------------------------------------------------------------
    // Per-step "Auto-fill from Location" escape hatch (Soil tab)
    // -------------------------------------------------------------------------

    let _soilAutoSourcesCache = null;

    async function _ensureSoilAutoSourcesLoaded() {
        const sel = document.getElementById('soil-auto-source');
        if (!sel) return;
        const lat = data.latitude;
        const lon = data.longitude;
        if (lat == null || lon == null) {
            sel.innerHTML = '<option value="">Set a location in Step 2 first</option>';
            return;
        }
        if (_soilAutoSourcesCache) return;

        try {
            const result = await api(`/dssat/api/insitu/sources/?lat=${lat}&lon=${lon}`);
            const items = (result.sources && result.sources.soil) || [];
            _soilAutoSourcesCache = items;
            sel.innerHTML = '<option value="">— Select a source —</option>';
            items.forEach(src => {
                const opt = document.createElement('option');
                opt.value = src.source;
                const badge = src.in_coverage ? '✓ in coverage' : '✗ outside coverage';
                opt.textContent = `${src.label}  (${badge})`;
                if (!src.in_coverage) opt.disabled = true;
                sel.appendChild(opt);
            });
        } catch (e) {
            console.error('Failed to load soil auto sources:', e);
            sel.innerHTML = '<option value="">Failed to load sources</option>';
        }
    }

    function onSoilAutoSourceChange() {
        const sel = document.getElementById('soil-auto-source');
        const previewBtn = document.getElementById('soil-auto-preview-btn');
        const applyBtn = document.getElementById('soil-auto-apply-btn');
        const previewDiv = document.getElementById('soil-auto-preview');
        const status = document.getElementById('soil-auto-status');
        if (previewBtn) previewBtn.disabled = !sel.value;
        if (applyBtn) applyBtn.disabled = true;
        if (previewDiv) previewDiv.innerHTML = '';
        if (status) status.textContent = '';
    }

    async function previewSoilAuto() {
        const sel = document.getElementById('soil-auto-source');
        const previewDiv = document.getElementById('soil-auto-preview');
        const applyBtn = document.getElementById('soil-auto-apply-btn');
        if (!sel || !sel.value) return;
        const lat = data.latitude, lon = data.longitude;
        if (lat == null || lon == null) return;

        previewDiv.innerHTML = '<span style="color:var(--text-muted);">Loading…</span>';
        try {
            const result = await api('/dssat/api/insitu/preview/', {
                method: 'POST',
                body: JSON.stringify({
                    lat, lon,
                    parameter: 'soil',
                    source: sel.value,
                    year: _resolveInSituYear(),
                }),
            });
            if (result.error) {
                previewDiv.innerHTML = `<div style="color:var(--color-hover);">${_escape(result.error)}</div>`;
                applyBtn.disabled = true;
                return;
            }
            data.in_situ_preview_cache.soil = result;
            previewDiv.innerHTML = _renderInSituPreview('soil', result);
            applyBtn.disabled = false;
        } catch (e) {
            previewDiv.innerHTML = `<div style="color:var(--color-hover);">Lookup failed: ${_escape(e.message || String(e))}</div>`;
            applyBtn.disabled = true;
        }
    }

    function applySoilAuto() {
        applyInSituLookup('soil');
        const status = document.getElementById('soil-auto-status');
        if (status && data.soil_id) {
            status.textContent = `✓ applied: ${data.soil_id}`;
        }
    }

    // -------------------------------------------------------------------------
    // Auto-fill planting date (escape hatch on the Planting step)
    // -------------------------------------------------------------------------

    async function autoFillPlantingDate() {
        const status = document.getElementById('planting-auto-status');
        const lat = data.latitude, lon = data.longitude;
        if (lat == null || lon == null) {
            if (status) status.textContent = 'Set a location in Step 2 first.';
            return;
        }
        if (status) status.textContent = 'Looking up…';

        try {
            const result = await api('/dssat/api/insitu/preview/', {
                method: 'POST',
                body: JSON.stringify({
                    lat, lon,
                    parameter: 'planting_date',
                    source: 'dssat_planting_date_lookup_v1',
                    year: _resolveInSituYear(),
                }),
            });
            if (result.error) {
                if (status) status.textContent = `Error: ${result.error}`;
                return;
            }
            if (result.planting_date) {
                document.getElementById('wiz-pdate').value = result.planting_date;
                data.planting_date = result.planting_date;
                if (status) status.textContent = `✓ set to ${result.planting_date} (Julian day ${result.julian_day})`;
            } else {
                if (status) status.textContent = 'No planting-date data at this location.';
            }
        } catch (e) {
            if (status) status.textContent = `Lookup failed: ${e.message || e}`;
        }
    }

    async function estimateSoil() {
        const clay = parseFloat(document.getElementById('wiz-clay').value);
        const silt = parseFloat(document.getElementById('wiz-silt').value);
        const bd = parseFloat(document.getElementById('wiz-bd').value) || null;
        const oc = parseFloat(document.getElementById('wiz-oc').value) || null;

        if (isNaN(clay) || isNaN(silt)) {
            alert('Please enter clay and silt percentages.');
            return;
        }

        document.getElementById('wiz-sand').value = Math.max(0, 100 - clay - silt).toFixed(1);

        showLoading('Estimating soil properties...');
        try {
            const body = { clay_pct: clay, silt_pct: silt };
            if (bd) body.bulk_density = bd;
            if (oc) body.organic_carbon = oc;

            const result = await api('/dssat/api/soils/estimate/', { method: 'POST', body: JSON.stringify(body) });
            const el = document.getElementById('soil-estimate-result');
            if (result.success || result.properties) {
                const props = result.properties || result;
                data.inline_soil = { clay_pct: clay, silt_pct: silt, bulk_density: bd, organic_carbon: oc, ...props };
                data.soil_id = null;
                clearICLayers();  // estimated soils have no layered structure
                el.innerHTML = `<div style="padding:0.75rem;background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:0.5rem;font-size:0.85rem;color:#15803d;">
                    Soil properties estimated successfully. This inline soil will be used for your experiment. Initial conditions cannot be configured for estimated soils.
                </div>`;
                _collapseSoilPicker();
            } else {
                el.innerHTML = `<div style="padding:0.75rem;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:0.5rem;font-size:0.85rem;color:var(--color-hover);">
                    ${result.error || 'Failed to estimate soil properties.'}
                </div>`;
            }
        } catch (e) {
            console.error('Soil estimation failed:', e);
        }
        hideLoading();
    }

    // =========================================================================
    // Step 5: Planting
    // =========================================================================

    function loadPlantingStepData() {
        // Populate Step 5 inputs from data.* so values applied in earlier
        // steps (e.g. data.planting_date set from Step 3 in-situ apply) are
        // preserved when the user navigates forward through this step.
        const pdate = document.getElementById('wiz-pdate');
        const ppop = document.getElementById('wiz-ppop');
        const plrs = document.getElementById('wiz-plrs');
        const method = document.getElementById('wiz-planting-method');
        if (pdate) pdate.value = data.planting_date || '';
        if (ppop) ppop.value = data.plant_population != null ? data.plant_population : '';
        if (plrs) plrs.value = data.row_spacing != null ? data.row_spacing : '';
        if (method) method.value = data.planting_method || '';
    }

    async function loadPlantingCodes() {
        if (plantingCodesLoaded) return;
        try {
            const codes = await loadCodesForCategory('planting');
            const select = document.getElementById('wiz-planting-method');
            const currentVal = select.value;
            // Rebuild options: keep the empty default, add loaded codes
            select.innerHTML = '<option value="">-- Select --</option>' + buildSelectOptions(codes, false);
            if (currentVal) select.value = currentVal;
            plantingCodesLoaded = true;
        } catch (e) {
            // Codes endpoint may not exist yet
        }
    }

    // =========================================================================
    // Step 7: Weather (auto-calculated dates)
    // =========================================================================

    let weatherDatesCache = null;

    /**
     * Get the planting date(s) to use for weather calculation.
     * For custom ensemble: collect ALL planting dates from treatments.
     * For all other types: use the single data.planting_date.
     */
    function getPlantingDatesForWeather() {
        if (data.experiment_type === 'custom' && data.treatments.length > 0) {
            const dates = data.treatments
                .map(t => t.planting_date)
                .filter(d => d);
            return [...new Set(dates)].sort();
        }
        return data.planting_date ? [data.planting_date] : [];
    }

    async function calculateWeatherDates() {
        const infoEl = document.getElementById('weather-dates-info');
        document.getElementById('weather-check-result').innerHTML = '';

        const plantingDates = getPlantingDatesForWeather();

        if (!data.crop_code || plantingDates.length === 0) {
            const missing = [];
            if (!data.crop_code) missing.push('crop (Step 4)');
            if (plantingDates.length === 0) missing.push('planting date (Step 5)');
            infoEl.innerHTML = `<div style="padding:0.75rem;background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:0.5rem;font-size:0.85rem;color:#b45309;">
                Set ${missing.join(' and ')} first to calculate the required weather date range.
            </div>`;
            weatherDatesCache = null;
            return;
        }

        showLoading('Calculating weather date range...');
        try {
            // For multiple dates (custom ensemble), use earliest and latest
            const earliest = plantingDates[0];
            const latest = plantingDates[plantingDates.length - 1];

            // Calculate for earliest planting date (gets start_date)
            const resultEarliest = await api('/dssat/api/experiment/weather-dates/', {
                method: 'POST',
                body: JSON.stringify({ crop_code: data.crop_code, planting_date: earliest, num_years: 1 }),
            });

            let startDate, endDate, displayResult;

            if (plantingDates.length > 1 && earliest !== latest) {
                // Also calculate for latest planting date (gets end_date)
                const resultLatest = await api('/dssat/api/experiment/weather-dates/', {
                    method: 'POST',
                    body: JSON.stringify({ crop_code: data.crop_code, planting_date: latest, num_years: 1 }),
                });

                if (resultEarliest.success && resultLatest.success) {
                    startDate = resultEarliest.start_date;
                    endDate = resultLatest.end_date;
                    displayResult = {
                        ...resultEarliest,
                        end_date: endDate,
                        planting_date: `${earliest} to ${latest} (${plantingDates.length} treatments)`,
                    };
                    // Recalculate total days
                    const d1 = new Date(startDate);
                    const d2 = new Date(endDate);
                    displayResult.total_days = Math.ceil((d2 - d1) / (1000 * 60 * 60 * 24));
                }
            } else {
                displayResult = resultEarliest;
            }

            if ((displayResult || resultEarliest).success && (displayResult || resultEarliest).start_date) {
                const r = displayResult || resultEarliest;
                weatherDatesCache = r;
                infoEl.innerHTML = `<div style="padding:0.75rem;background:rgba(1,112,185,0.1);border:1px solid rgba(1,112,185,0.3);border-radius:var(--radius);font-size:0.85rem;">
                    <div style="color:var(--primary-color);font-weight:600;margin-bottom:0.35rem;">Required Weather Date Range</div>
                    <div style="display:grid;grid-template-columns:auto 1fr;gap:0.2rem 0.75rem;color:var(--text-secondary);">
                        <span>Crop:</span><span style="color:var(--text-primary);">${r.crop_name} (${r.crop_code})</span>
                        <span>Planting:</span><span style="color:var(--text-primary);">${r.planting_date}</span>
                        <span>Data needed:</span><span style="color:var(--text-primary);">${r.start_date} to ${r.end_date}</span>
                        <span>Total days:</span><span style="color:var(--text-primary);">${r.total_days}${r.pre_plant_buffer_days ? ` (${r.pre_plant_buffer_days}d buffer + ${r.growth_duration_days}d growth + 30d post-harvest)` : ''}</span>
                    </div>
                </div>`;
            } else {
                infoEl.innerHTML = `<div style="padding:0.75rem;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:0.5rem;font-size:0.85rem;color:var(--color-hover);">
                    ${resultEarliest.error || 'Failed to calculate weather date range.'}
                </div>`;
                weatherDatesCache = null;
            }
        } catch (e) {
            console.error('Weather date calculation failed:', e);
            infoEl.innerHTML = `<div style="padding:0.75rem;background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:0.5rem;font-size:0.85rem;color:#b45309;">
                Could not reach the SimulationAgent to calculate dates. Weather dates will be computed at run time.
            </div>`;
            weatherDatesCache = null;
        }
        hideLoading();
    }

    async function checkWeather() {
        const source = document.getElementById('wiz-weather-source').value || 'nasa_power';
        const startDate = weatherDatesCache ? weatherDatesCache.start_date : null;
        const endDate = weatherDatesCache ? weatherDatesCache.end_date : null;

        if (!startDate) {
            document.getElementById('weather-check-result').innerHTML =
                '<div style="padding:0.75rem;background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:0.5rem;font-size:0.85rem;color:#b45309;">Date range not yet calculated. Set crop and planting date first.</div>';
            return;
        }

        showLoading('Checking weather availability...');
        try {
            const body = { source };
            if (startDate) body.start_date = startDate;
            if (endDate) body.end_date = endDate;

            const result = await api('/data/api/check/', { method: 'POST', body: JSON.stringify(body) });
            const el = document.getElementById('weather-check-result');
            if (result.success) {
                const r = result.result || result;
                el.innerHTML = `<div style="padding:0.75rem;background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:0.5rem;font-size:0.85rem;color:#15803d;">
                    Data source available. ${r.date_range ? 'Date range: ' + r.date_range.start + ' to ' + r.date_range.end : ''}
                </div>`;
            } else {
                el.innerHTML = `<div style="padding:0.75rem;background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:0.5rem;font-size:0.85rem;color:#b45309;">
                    ${result.error || 'Data may need to be fetched. It will be retrieved automatically when the experiment runs.'}
                </div>`;
            }
        } catch (e) {
            console.error('Weather check failed:', e);
        }
        hideLoading();
    }

    // =========================================================================
    // Step 8: Management Event Rows
    // =========================================================================

    function switchMgmtTab(panel) {
        document.querySelectorAll('#mgmt-tabs .mgmt-tab').forEach(t => t.classList.remove('active'));
        document.querySelector(`#mgmt-tabs .mgmt-tab[data-panel="${panel}"]`).classList.add('active');
        document.querySelectorAll('.step-content[data-step="8"] .mgmt-panel').forEach(p => {
            p.classList.toggle('active', p.dataset.panel === panel);
        });
    }

    // Event-table row management lives in the shared EventTable widget
    // (see dssat_agent/static/dssat_agent/js/event_table.js) — used here via
    // `eventWidgets` / `getEventWidget()` above.

    // Irrigation method toggle
    document.getElementById('wiz-irr-method').addEventListener('change', function() {
        document.getElementById('irr-auto-config').style.display = this.value === 'automatic' ? 'block' : 'none';
        document.getElementById('irr-fixed-config').style.display = this.value === 'fixed' ? 'block' : 'none';
    });

    // Harvest mode toggle — show/hide the mode-specific extra input.
    function _updateHarvestModeVisibility() {
        const mode = document.getElementById('wiz-harvest-option').value;
        document.getElementById('harvest-date-config').style.display  = mode === 'on_date'      ? 'block' : 'none';
        document.getElementById('harvest-stage-config').style.display = mode === 'growth_stage' ? 'block' : 'none';
        document.getElementById('harvest-dap-config').style.display   = mode === 'dap'          ? 'block' : 'none';
    }
    document.getElementById('wiz-harvest-option').addEventListener('change', _updateHarvestModeVisibility);

    // Populate the growth-stage + harvest-component + harvest-size dropdowns
    // from the CropModel for the current (crop, dssat_model). Idempotent — safe
    // to call whenever the crop or model changes.
    async function reloadHarvestOptions() {
        const cropCode = data.crop_code || '';
        const dssatModel = data.dssat_model || '';
        const stageSel  = document.getElementById('wiz-harvest-stage');
        const stageHint = document.getElementById('wiz-harvest-stage-hint');
        const hcomSel   = document.getElementById('wiz-harvest-hcom');
        const hsizeSel  = document.getElementById('wiz-harvest-hsize');
        if (!stageSel) return;  // step 8 not yet rendered

        const _fill = (sel, items) => {
            const prev = sel.value;
            sel.innerHTML = `<option value="">(select)</option>`;
            for (const it of items || []) {
                const opt = document.createElement('option');
                opt.value = it.code;
                opt.textContent = it.name
                    ? `${it.code} — ${it.name}${it.description ? ': ' + it.description : ''}`
                    : (it.description ? `${it.code} — ${it.description}` : it.code);
                sel.appendChild(opt);
            }
            if (prev) sel.value = prev;
        };

        if (!cropCode) {
            _fill(stageSel, []);
            stageHint.textContent = '';
            _fill(hcomSel, []);
            _fill(hsizeSel, []);
            return;
        }

        try {
            const qs = dssatModel ? `?dssat_model=${encodeURIComponent(dssatModel)}` : '';
            const resp = await fetch(`${SUBPATH}/dssat/api/crop-model/${encodeURIComponent(cropCode)}/${qs}`);
            const j = resp.ok ? await resp.json() : null;
            const cm = (j && j.data) || null;
            const stages = (cm && cm.harvest_stages) || [];
            _fill(stageSel, stages);
            stageHint.textContent = stages.length
                ? `${stages.length} stage(s) for ${cropCode}${dssatModel ? '/' + dssatModel : ''}`
                : `No GRSTAGE entries seeded for ${cropCode}${dssatModel ? '/' + dssatModel : ''}`;
            const curatedHcom = (cm && cm.harvest_components) || [];
            // hcom full list + optional curation filter.
            try {
                const r = await fetch(`${SUBPATH}/dssat/api/codes/hcom/?crop=${encodeURIComponent(cropCode)}`);
                if (r.ok) {
                    const data2 = await r.json();
                    const full = (data2 && data2.codes) || [];
                    const filtered = curatedHcom.length
                        ? full.filter(c => curatedHcom.includes(c.code))
                        : full;
                    _fill(hcomSel, filtered);
                }
            } catch (_e) { /* ignore */ }
            try {
                const r = await fetch(`${SUBPATH}/dssat/api/codes/hsize/`);
                if (r.ok) {
                    const data2 = await r.json();
                    _fill(hsizeSel, (data2 && data2.codes) || []);
                }
            } catch (_e) { /* ignore */ }
        } catch (err) {
            console.warn('reloadHarvestOptions failed:', err);
        }
    }

    // Clay/silt auto-calculate sand
    ['wiz-clay', 'wiz-silt'].forEach(id => {
        document.getElementById(id).addEventListener('input', () => {
            const clay = parseFloat(document.getElementById('wiz-clay').value) || 0;
            const silt = parseFloat(document.getElementById('wiz-silt').value) || 0;
            document.getElementById('wiz-sand').value = Math.max(0, 100 - clay - silt).toFixed(1);
        });
    });

    // =========================================================================
    // Monte Carlo: Spatial Mode
    // =========================================================================

    function switchSpatialMode(mode) {
        data.monte_carlo.spatial_mode = mode;
        document.getElementById('mc-circle-config').style.display = mode === 'circle' ? 'block' : 'none';
        document.getElementById('mc-bbox-config').style.display = mode === 'bbox' ? 'block' : 'none';
        document.getElementById('mc-admin-config').style.display = mode === 'admin' ? 'block' : 'none';
        if (mode === 'admin') {
            loadAdminUnits(document.getElementById('mc-admin-level').value);
        }
        updateLocationMapForStep();
    }

    // =========================================================================
    // Step 2: Interactive Location Map
    // =========================================================================

    let locationMap = null;
    let locationMarker = null;
    let locationCircle = null;
    let locationRect = null;
    let locationDrawRectHandler = null;
    let locationAdminLayer = null;
    let locationAdminSelected = null;  // the currently-selected Leaflet layer
    let locationAdminLoadedLevel = null;
    let locationMapMode = null;  // 'single' | 'mc-circle' | 'mc-bbox' | 'mc-admin' | null

    const _adminBaseStyle   = { color: '#fff',    weight: 1.2, opacity: 0.7, fillColor: 'transparent', fillOpacity: 0 };
    const _adminHoverStyle  = { color: '#0170B9', weight: 2.5, opacity: 1,   fillColor: '#0170B9',     fillOpacity: 0.12 };
    const _adminSelectStyle = { color: '#f59e0b', weight: 3,   opacity: 1,   fillColor: '#f59e0b',     fillOpacity: 0.18 };

    function _setLocField(id, val) {
        const el = document.getElementById(id);
        if (!el) return;
        el.value = val;
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function _bindLocInputSync(id, handler) {
        const el = document.getElementById(id);
        if (!el || el.dataset.mapSynced) return;
        el.addEventListener('input', handler);
        el.dataset.mapSynced = '1';
    }

    function _setPointMarker(lat, lon) {
        if (!locationMap) return;
        if (locationMarker) {
            locationMarker.setLatLng([lat, lon]);
        } else {
            locationMarker = L.marker([lat, lon]).addTo(locationMap);
        }
    }
    function _clearMarker()  { if (locationMarker) { locationMap.removeLayer(locationMarker); locationMarker = null; } }
    function _clearCircle()  { if (locationCircle) { locationMap.removeLayer(locationCircle); locationCircle = null; } }
    function _clearRect()    { if (locationRect)   { locationMap.removeLayer(locationRect);   locationRect   = null; } }
    function _clearAdminLayer() {
        if (locationAdminLayer) { locationMap.removeLayer(locationAdminLayer); locationAdminLayer = null; }
        locationAdminSelected = null;
        locationAdminLoadedLevel = null;
    }

    function _adminLabel(props) {
        const parts = [];
        if (props.country) parts.push(props.country);
        if (props.admin1)  parts.push(props.admin1);
        if (props.admin2)  parts.push(props.admin2);
        return parts.join(' > ');
    }

    function _adminLeafName(level, props) {
        if (level === 'admin2') return props.admin2 || '';
        if (level === 'admin1') return props.admin1 || '';
        return props.country || '';
    }

    function _loadAdminBoundariesOnMap(level) {
        if (!locationMap || !level) return;
        if (locationAdminLoadedLevel === level && locationAdminLayer) return;

        _clearAdminLayer();
        locationAdminLoadedLevel = level;

        locationAdminLayer = L.geoJSON(null, {
            style: _adminBaseStyle,
            onEachFeature: function (feature, layer) {
                const props = feature.properties || {};
                const label = _adminLabel(props);
                const leaf = _adminLeafName(level, props);

                layer.on('mouseover', function () {
                    if (layer === locationAdminSelected) return;
                    layer.setStyle(_adminHoverStyle);
                    layer.bringToFront();
                    layer.bindTooltip(label, { sticky: true, direction: 'top', opacity: 0.9 }).openTooltip();
                });
                layer.on('mouseout', function () {
                    layer.closeTooltip();
                    layer.unbindTooltip();
                    if (layer !== locationAdminSelected && locationAdminLayer) {
                        locationAdminLayer.resetStyle(layer);
                    }
                });
                layer.on('click', function (e) {
                    L.DomEvent.stopPropagation(e);
                    if (locationMapMode === 'mc-admin') {
                        if (locationAdminLayer) locationAdminLayer.resetStyle();
                        layer.setStyle(_adminSelectStyle);
                        locationAdminSelected = layer;
                        _selectAdminOption(leaf);
                    } else if (locationMapMode === 'single' || locationMapMode === 'mc-circle') {
                        // Display-only: forward the click so the map handler drops a point.
                        locationMap.fire('click', e);
                    }
                });
            },
        }).addTo(locationMap);

        // NDJSON streaming (same endpoint as data_explorer)
        const url = SUBPATH + '/data/api/map/admin/?level=' + encodeURIComponent(level) + '&stream=1';
        fetch(url, { headers: { 'Accept': 'application/x-ndjson' } })
            .then(function (response) {
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                function pump(result) {
                    if (result.done) {
                        if (buffer.trim() && locationAdminLayer) {
                            try { locationAdminLayer.addData(JSON.parse(buffer.trim())); } catch (e) {}
                        }
                        // Only restore the selection highlight when the mode uses it.
                        if (locationMapMode === 'mc-admin') _reapplyAdminSelection();
                        return;
                    }
                    buffer += decoder.decode(result.value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop();
                    lines.forEach(function (line) {
                        if (!line.trim() || !locationAdminLayer) return;
                        try { locationAdminLayer.addData(JSON.parse(line)); } catch (e) {}
                    });
                    return reader.read().then(pump);
                }
                return reader.read().then(pump);
            })
            .catch(function (err) { console.error('Admin boundary load error:', err); });
    }

    function _selectAdminOption(name) {
        const select = document.getElementById('mc-admin-name');
        if (!select) return;
        // If the option doesn't exist yet (boundaries loaded before units), add it.
        let found = false;
        for (const opt of select.options) {
            if (opt.value === name) { found = true; break; }
        }
        if (!found && name) {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            select.appendChild(opt);
        }
        select.value = name;
        select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function _reapplyAdminSelection() {
        if (!locationAdminLayer) return;
        const level = locationAdminLoadedLevel;
        const selectedName = (document.getElementById('mc-admin-name') || {}).value;
        if (!selectedName) return;
        locationAdminLayer.eachLayer(function (layer) {
            const leaf = _adminLeafName(level, layer.feature.properties || {});
            if (leaf === selectedName) {
                layer.setStyle(_adminSelectStyle);
                locationAdminSelected = layer;
                locationMap.fitBounds(layer.getBounds(), { padding: [30, 30], maxZoom: 8 });
            }
        });
    }

    function _updateCircleOverlay() {
        if (!locationMap) return;
        const lat = parseFloat(document.getElementById('mc-center-lat').value);
        const lon = parseFloat(document.getElementById('mc-center-lon').value);
        const radKm = parseFloat(document.getElementById('mc-radius').value) || 25;
        _clearCircle();
        if (!isNaN(lat) && !isNaN(lon)) {
            locationCircle = L.circle([lat, lon], {
                radius: radKm * 1000,
                color: '#0170B9', weight: 2, fillOpacity: 0.1,
            }).addTo(locationMap);
        }
    }

    function _syncPointFromInputs(latId, lonId) {
        if (!locationMap) return;
        const lat = parseFloat(document.getElementById(latId).value);
        const lon = parseFloat(document.getElementById(lonId).value);
        if (isNaN(lat) || isNaN(lon)) { _clearMarker(); return; }
        _setPointMarker(lat, lon);
        locationMap.setView([lat, lon], Math.max(locationMap.getZoom(), 8));
    }

    function _syncBboxFromInputs() {
        if (!locationMap) return;
        const s = parseFloat(document.getElementById('mc-bbox-minlat').value);
        const n = parseFloat(document.getElementById('mc-bbox-maxlat').value);
        const w = parseFloat(document.getElementById('mc-bbox-minlon').value);
        const e = parseFloat(document.getElementById('mc-bbox-maxlon').value);
        _clearRect();
        if ([s, n, w, e].some(isNaN)) return;
        locationRect = L.rectangle([[s, w], [n, e]], {
            color: '#0170B9', weight: 2, fillOpacity: 0.1,
        }).addTo(locationMap);
        locationMap.fitBounds(locationRect.getBounds(), { padding: [20, 20] });
    }

    function _initLocationMap() {
        if (locationMap) return;
        const el = document.getElementById('wizard-location-map');
        if (!el || typeof L === 'undefined') return;

        locationMap = L.map(el, { center: [33.0, -86.5], zoom: 5, zoomControl: true });
        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
            subdomains: 'abcd',
            maxZoom: 19,
        }).addTo(locationMap);

        // Leaflet.Draw rectangle handler for MC-bbox mode
        if (typeof L.Draw !== 'undefined') {
            locationDrawRectHandler = new L.Draw.Rectangle(locationMap, {
                shapeOptions: { color: '#0170B9', weight: 2, fillOpacity: 0.1 },
            });
            locationMap.on(L.Draw.Event.CREATED, function (e) {
                _clearRect();
                locationRect = e.layer;
                locationRect.addTo(locationMap);
                const b = locationRect.getBounds();
                _setLocField('mc-bbox-minlat', b.getSouth().toFixed(4));
                _setLocField('mc-bbox-maxlat', b.getNorth().toFixed(4));
                _setLocField('mc-bbox-minlon', b.getWest().toFixed(4));
                _setLocField('mc-bbox-maxlon', b.getEast().toFixed(4));
                // Re-enable handler for next draw
                setTimeout(function () {
                    if (locationMapMode === 'mc-bbox' && locationDrawRectHandler) {
                        locationDrawRectHandler.enable();
                    }
                }, 0);
            });
        }

        // Map click — drop point (single or mc-circle). Bbox uses Leaflet.Draw.
        locationMap.on('click', function (e) {
            if (locationMapMode === 'single') {
                _setPointMarker(e.latlng.lat, e.latlng.lng);
                _setLocField('wiz-lat', e.latlng.lat.toFixed(4));
                _setLocField('wiz-lon', e.latlng.lng.toFixed(4));
            } else if (locationMapMode === 'mc-circle') {
                _setPointMarker(e.latlng.lat, e.latlng.lng);
                _setLocField('mc-center-lat', e.latlng.lat.toFixed(4));
                _setLocField('mc-center-lon', e.latlng.lng.toFixed(4));
                _updateCircleOverlay();
            }
        });

        // Input → map sync
        _bindLocInputSync('wiz-lat',       function () { if (locationMapMode === 'single')    _syncPointFromInputs('wiz-lat', 'wiz-lon'); });
        _bindLocInputSync('wiz-lon',       function () { if (locationMapMode === 'single')    _syncPointFromInputs('wiz-lat', 'wiz-lon'); });
        _bindLocInputSync('mc-center-lat', function () { if (locationMapMode === 'mc-circle') { _syncPointFromInputs('mc-center-lat', 'mc-center-lon'); _updateCircleOverlay(); } });
        _bindLocInputSync('mc-center-lon', function () { if (locationMapMode === 'mc-circle') { _syncPointFromInputs('mc-center-lat', 'mc-center-lon'); _updateCircleOverlay(); } });
        _bindLocInputSync('mc-radius',     function () { if (locationMapMode === 'mc-circle') _updateCircleOverlay(); });
        ['mc-bbox-minlat', 'mc-bbox-maxlat', 'mc-bbox-minlon', 'mc-bbox-maxlon'].forEach(function (id) {
            _bindLocInputSync(id, function () { if (locationMapMode === 'mc-bbox') _syncBboxFromInputs(); });
        });

        // Admin-level change — reload boundaries for the new level.
        const adminLevelSel = document.getElementById('mc-admin-level');
        if (adminLevelSel && !adminLevelSel.dataset.mapSynced) {
            adminLevelSel.addEventListener('change', function () {
                if (locationMapMode === 'mc-admin') _loadAdminBoundariesOnMap(adminLevelSel.value);
            });
            adminLevelSel.dataset.mapSynced = '1';
        }
        // Admin-name select change — reapply selection highlight.
        const adminNameSel = document.getElementById('mc-admin-name');
        if (adminNameSel && !adminNameSel.dataset.mapSynced) {
            adminNameSel.addEventListener('change', function () {
                if (locationMapMode === 'mc-admin') _reapplyAdminSelection();
            });
            adminNameSel.dataset.mapSynced = '1';
        }
    }

    function updateLocationMapForStep() {
        const wrap = document.getElementById('wizard-location-map-wrap');
        const hint = document.getElementById('wizard-location-map-hint');
        if (!wrap) return;

        let mode = null;
        if (runMode === 'batch') {
            mode = null;
        } else if (data.experiment_type === 'monte_carlo') {
            const sm = data.monte_carlo.spatial_mode;
            if (sm === 'circle')      mode = 'mc-circle';
            else if (sm === 'bbox')   mode = 'mc-bbox';
            else if (sm === 'admin')  mode = 'mc-admin';
        } else {
            mode = 'single';
        }

        if (!mode) {
            wrap.style.display = 'none';
            if (locationDrawRectHandler) locationDrawRectHandler.disable();
            locationMapMode = null;
            return;
        }

        wrap.style.display = '';
        _initLocationMap();
        locationMapMode = mode;

        if (locationDrawRectHandler) locationDrawRectHandler.disable();
        _clearMarker(); _clearCircle(); _clearRect();
        // Admin layer is used for both mc-admin (selection) and single (display-only).
        // Clear it only when neither mode wants it.
        if (mode !== 'mc-admin' && mode !== 'single') {
            _clearAdminLayer();
        } else if (mode === 'single') {
            // Reset any selection highlight carried over from mc-admin.
            if (locationAdminLayer) locationAdminLayer.resetStyle();
            locationAdminSelected = null;
        }

        if (mode === 'single') {
            hint.textContent = 'Click the map to drop a point. Admin boundaries shown for reference.';
            _loadAdminBoundariesOnMap('admin1');
            _syncPointFromInputs('wiz-lat', 'wiz-lon');
        } else if (mode === 'mc-circle') {
            hint.textContent = 'Click the map to set the grid center. The circle shows the sampling radius.';
            _syncPointFromInputs('mc-center-lat', 'mc-center-lon');
            _updateCircleOverlay();
        } else if (mode === 'mc-bbox') {
            hint.textContent = 'Drag on the map to draw a bounding box, or type coordinates below.';
            _syncBboxFromInputs();
            if (locationDrawRectHandler) locationDrawRectHandler.enable();
        } else if (mode === 'mc-admin') {
            hint.textContent = 'Click an admin boundary on the map to select it.';
            const level = (document.getElementById('mc-admin-level') || {}).value || 'admin1';
            _loadAdminBoundariesOnMap(level);
        }

        // Leaflet needs a resize when its container was previously hidden
        setTimeout(function () { if (locationMap) locationMap.invalidateSize(); }, 60);
    }

    let adminUnitsCache = {};

    async function loadAdminUnits(level, parent) {
        const cacheKey = `${level}:${parent || ''}`;
        if (adminUnitsCache[cacheKey]) {
            renderAdminUnits(adminUnitsCache[cacheKey]);
            return;
        }
        showLoading('Loading admin units...');
        try {
            let url = `/dssat/api/admin-units/?level=${level}`;
            if (parent) url += `&parent=${encodeURIComponent(parent)}`;
            const result = await api(url);
            const units = result.units || [];
            adminUnitsCache[cacheKey] = units;
            renderAdminUnits(units);
        } catch (e) {
            console.error('Failed to load admin units:', e);
        }
        hideLoading();
    }

    function renderAdminUnits(units) {
        const select = document.getElementById('mc-admin-name');
        select.innerHTML = '<option value="">-- Select admin unit --</option>';
        units.forEach(u => {
            const opt = document.createElement('option');
            opt.value = u;
            opt.textContent = u;
            if (u === data.monte_carlo.admin_name) opt.selected = true;
            select.appendChild(opt);
        });
    }

    function saveMCStepData() {
        const mc = data.monte_carlo;
        mc.spatial_mode = document.querySelector('input[name="mc-spatial-mode"]:checked')?.value || 'circle';
        // Shared grid density (applies to all modes)
        mc.grid_spacing = parseFloat(document.getElementById('mc-grid-spacing').value) || 0.1;

        if (mc.spatial_mode === 'circle') {
            mc.center.lat = parseFloat(document.getElementById('mc-center-lat').value) || null;
            mc.center.lon = parseFloat(document.getElementById('mc-center-lon').value) || null;
            mc.radius_km = parseInt(document.getElementById('mc-radius').value) || 25;
            // Representative lat/lon for weather date calculation and Step 3 source list
            data.latitude = mc.center.lat;
            data.longitude = mc.center.lon;
        } else if (mc.spatial_mode === 'bbox') {
            mc.bbox.min_lat = parseFloat(document.getElementById('mc-bbox-minlat').value) || null;
            mc.bbox.max_lat = parseFloat(document.getElementById('mc-bbox-maxlat').value) || null;
            mc.bbox.min_lon = parseFloat(document.getElementById('mc-bbox-minlon').value) || null;
            mc.bbox.max_lon = parseFloat(document.getElementById('mc-bbox-maxlon').value) || null;
            // Representative point = center of bbox
            if (mc.bbox.min_lat != null && mc.bbox.max_lat != null) {
                data.latitude = (mc.bbox.min_lat + mc.bbox.max_lat) / 2;
                data.longitude = (mc.bbox.min_lon + mc.bbox.max_lon) / 2;
            }
        } else if (mc.spatial_mode === 'admin') {
            mc.admin_level = document.getElementById('mc-admin-level').value || 'admin1';
            mc.admin_name = document.getElementById('mc-admin-name').value || null;
            // data.latitude / data.longitude are filled asynchronously via
            // _ensureMCRepresentativePoint() when Step 3 opens or the admin
            // unit changes.
        }
    }

    function saveMCConfigData() {
        data.monte_carlo.nens = parseInt(document.getElementById('mc-nens').value) || 30;
        data.monte_carlo.sampling_strategy = document.getElementById('mc-sampling').value || 'random';
    }

    // =========================================================================
    // Batch Location Selection
    // =========================================================================

    let batchStatesLoaded = false;
    let batchSelectedCounties = [];  // [{name, lat, lon, checked}]

    async function loadBatchAdminStates() {
        if (batchStatesLoaded) return;
        showLoading('Loading states...');
        try {
            const result = await api('/dssat/api/admin-units/?level=admin1&parent=United States');
            const units = result.units || [];
            const select = document.getElementById('batch-state');
            select.innerHTML = '<option value="">-- Select a state --</option>';
            units.forEach(u => {
                const opt = document.createElement('option');
                opt.value = u;
                opt.textContent = u;
                select.appendChild(opt);
            });
            batchStatesLoaded = true;
        } catch (e) {
            console.error('Failed to load states:', e);
        }
        hideLoading();
    }

    async function loadBatchCounties(stateName) {
        if (!stateName) {
            document.getElementById('batch-counties-section').style.display = 'none';
            document.getElementById('batch-spatial-params').style.display = 'none';
            batchSelectedCounties = [];
            updateBatchCount();
            return;
        }
        showLoading('Loading counties...');
        try {
            const result = await api(`/dssat/api/admin-units/?level=admin2&parent=${encodeURIComponent(stateName)}`);
            const units = result.units || [];
            batchSelectedCounties = units.map(name => ({ name, checked: true }));
            renderBatchCounties();
            document.getElementById('batch-counties-section').style.display = 'block';
            document.getElementById('batch-spatial-params').style.display = 'block';
        } catch (e) {
            console.error('Failed to load counties:', e);
        }
        hideLoading();
    }

    function renderBatchCounties() {
        const container = document.getElementById('batch-county-list');
        container.innerHTML = '';
        batchSelectedCounties.forEach((county, idx) => {
            const label = document.createElement('label');
            label.className = 'batch-county-item';
            label.innerHTML = `<input type="checkbox" ${county.checked ? 'checked' : ''} onchange="wizard.toggleBatchCounty(${idx}, this.checked)"> ${county.name}`;
            container.appendChild(label);
        });
        updateBatchCount();
    }

    function toggleBatchCounty(idx, checked) {
        batchSelectedCounties[idx].checked = checked;
        updateBatchCount();
    }

    function batchSelectAll(checked) {
        batchSelectedCounties.forEach(c => c.checked = checked);
        renderBatchCounties();
    }

    function updateBatchCount() {
        const count = batchSelectedCounties.filter(c => c.checked).length;
        const total = batchSelectedCounties.length;
        const el = document.getElementById('batch-selected-count');
        if (el) el.textContent = `${count} of ${total} selected`;
    }

    function saveBatchStepData() {
        // Save batch-specific state for submit
        data._batchState = data._batchState || {};
        data._batchState.stateName = document.getElementById('batch-state').value || '';
        data._batchState.selectedCounties = batchSelectedCounties.filter(c => c.checked).map(c => c.name);
        data._batchState.radius = parseInt(document.getElementById('batch-radius')?.value) || 25;
        data._batchState.gridPoints = parseInt(document.getElementById('batch-grid-points')?.value) || 30;
    }

    // =========================================================================
    // Step 9: Sensitivity Configuration
    // =========================================================================

    function showSensitivityConfig() {
        const el = document.getElementById('sensitivity-config');
        el.style.display = 'block';

        // Build parameter dropdown — only show variables with baseline data
        const select = document.getElementById('wiz-sens-param');
        select.innerHTML = '';
        SENSITIVITY_VARIABLES.forEach(sv => {
            if (sv.requires) {
                const val = data[sv.requires];
                if (!val || (Array.isArray(val) && val.length === 0)) return;
            }
            const opt = document.createElement('option');
            opt.value = sv.key;
            opt.textContent = sv.label;
            if (sv.key === data.sensitivity.parameter) opt.selected = true;
            select.appendChild(opt);
        });

        // If the saved parameter is no longer available, select the first one
        if (select.selectedIndex === -1 && select.options.length > 0) {
            select.selectedIndex = 0;
            data.sensitivity.parameter = select.value;
            data.sensitivity.min = null;
            data.sensitivity.max = null;
        }

        // Set up labels and defaults for the selected variable
        _updateSensitivityUI();

        // Populate from saved state
        if (data.sensitivity.min !== null) document.getElementById('wiz-sens-min').value = data.sensitivity.min;
        if (data.sensitivity.max !== null) document.getElementById('wiz-sens-max').value = data.sensitivity.max;
        document.getElementById('wiz-sens-steps').value = data.sensitivity.steps || 5;
    }

    function _updateSensitivityUI() {
        const param = document.getElementById('wiz-sens-param').value;
        const sv = SENSITIVITY_VARIABLES.find(v => v.key === param);
        if (!sv) return;

        const minInput = document.getElementById('wiz-sens-min');
        const maxInput = document.getElementById('wiz-sens-max');
        const minLabel = document.getElementById('wiz-sens-min-label');
        const maxLabel = document.getElementById('wiz-sens-max-label');
        const hintEl = document.getElementById('wiz-sens-hint');

        // Update labels with units
        if (minLabel) minLabel.textContent = `Min (${sv.unit})`;
        if (maxLabel) maxLabel.textContent = `Max (${sv.unit})`;
        minInput.step = sv.step;
        maxInput.step = sv.step;

        // Reset to defaults when parameter changes
        const changed = data.sensitivity.parameter !== param;
        if (changed || !minInput.value) minInput.value = sv.min;
        if (changed || !maxInput.value) maxInput.value = sv.max;

        // Show hint
        if (hintEl) hintEl.textContent = sv.hint || '';

        // Hide preview when param changes
        if (changed) {
            const preview = document.getElementById('sensitivity-preview');
            if (preview) preview.style.display = 'none';
        }
    }

    function saveSensitivityConfig() {
        data.sensitivity = {
            parameter: document.getElementById('wiz-sens-param').value,
            min: parseFloat(document.getElementById('wiz-sens-min').value),
            max: parseFloat(document.getElementById('wiz-sens-max').value),
            steps: parseInt(document.getElementById('wiz-sens-steps').value) || 5,
        };
        if (isNaN(data.sensitivity.min)) data.sensitivity.min = null;
        if (isNaN(data.sensitivity.max)) data.sensitivity.max = null;
    }

    function previewSensitivity() {
        saveSensitivityConfig();
        const { min, max } = data.sensitivity;
        if (min === null || max === null) {
            alert('Enter min and max values.');
            return;
        }

        const previewEl = document.getElementById('sensitivity-preview');
        const treatments = generateSensitivityTreatments();

        previewEl.style.display = 'block';
        previewEl.innerHTML = treatments.map((t, i) =>
            `<div class="sensitivity-preview-item">${i + 1}. ${t.name}</div>`
        ).join('');
    }

    function generateSensitivityTreatments() {
        const { parameter, min, max, steps } = data.sensitivity;
        if (min === null || max === null || steps < 2) return [];

        const sv = SENSITIVITY_VARIABLES.find(v => v.key === parameter);
        if (!sv) return [];

        const treatments = [];
        const increment = (max - min) / (steps - 1);
        const factor = Math.pow(10, sv.decimals);

        for (let i = 0; i < steps; i++) {
            const val = Math.round((min + increment * i) * factor) / factor;

            if (sv.type === 'date_offset') {
                // Planting date: offset from baseline
                const sign = val >= 0 ? '+' : '';
                treatments.push({
                    name: `${sv.label} ${sign}${val} days`,
                    _sens_param: parameter,
                    _sens_value: val,
                    planting_date: _offsetDate(data.planting_date, val),
                });
            } else if (sv.type === 'scale') {
                // Scale: multiply baseline event amounts
                const trt = {
                    name: `${sv.label} ${val}%`,
                    _sens_param: parameter,
                    _sens_value: val,
                };
                if (parameter === 'fertilizer_scale' && data.fertilizer.length) {
                    trt.fertilizer = data.fertilizer.map(ev => ({
                        ...ev,
                        famn: ev.famn != null ? Math.round(ev.famn * val / 100 * 10) / 10 : ev.famn,
                    }));
                } else if (parameter === 'chemical_scale' && data.chemical.length) {
                    trt.chemical = data.chemical.map(ev => ({
                        ...ev,
                        chamt: ev.chamt != null ? Math.round(ev.chamt * val / 100 * 10) / 10 : ev.chamt,
                    }));
                }
                treatments.push(trt);
            } else if (parameter === 'irrigation_threshold') {
                // Auto-irrigation with varying threshold
                treatments.push({
                    name: `Auto-irrigate at ${val}% depletion`,
                    _sens_param: parameter,
                    _sens_value: val,
                    irrigation: { method: 'automatic', threshold: val, efficiency: 90 },
                });
            } else if (parameter === 'tillage_depth' && data.tillage.length) {
                treatments.push({
                    name: `Tillage depth ${val} cm`,
                    _sens_param: parameter,
                    _sens_value: val,
                    tillage: data.tillage.map(ev => ({ ...ev, tdep: val })),
                });
            } else {
                // Direct numeric override (plant_population, row_spacing)
                treatments.push({
                    name: `${sv.label} = ${val} ${sv.unit}`,
                    _sens_param: parameter,
                    _sens_value: val,
                    [parameter]: val,
                });
            }
        }
        return treatments;
    }

    function _offsetDate(dateStr, offsetDays) {
        if (!dateStr) return null;
        try {
            const d = new Date(dateStr);
            d.setDate(d.getDate() + offsetDays);
            return d.toISOString().slice(0, 10);
        } catch (e) {
            return dateStr;
        }
    }

    // =========================================================================
    // Save Current Step Data
    // =========================================================================

    function saveCurrentStepData() {
        switch (currentStep) {
            case 2:
                if (runMode === 'batch') {
                    saveBatchStepData();
                } else if (data.experiment_type === 'monte_carlo') {
                    saveMCStepData();
                } else {
                    data.latitude = parseFloat(document.getElementById('wiz-lat').value) || null;
                    data.longitude = parseFloat(document.getElementById('wiz-lon').value) || null;
                    data.elevation = parseFloat(document.getElementById('wiz-elevation').value) || null;
                }
                break;
            case 3:
                /* Site Reference Data tab. Selections live in
                   data.in_situ_selections and are applied to data.* fields directly
                   by the Apply buttons, so there's nothing to save here. */
                break;
            case 5:
                if (data.experiment_type === 'custom' && hasMultipleTreatments()) {
                    saveTreatmentData();
                } else {
                    // Single, sensitivity, management: planting at top level
                    data.planting_date = document.getElementById('wiz-pdate').value || null;
                    data.plant_population = parseFloat(document.getElementById('wiz-ppop').value) || null;
                    data.row_spacing = parseFloat(document.getElementById('wiz-plrs').value) || null;
                    data.planting_method = document.getElementById('wiz-planting-method').value || null;
                }
                break;
            case 6:
                saveICStepData();
                break;
            case 7:
                data.weather_source = document.getElementById('wiz-weather-source').value || null;
                break;
            case 8:
                if (hasMultipleTreatments()) {
                    saveTreatmentData();
                } else {
                    // Single, sensitivity: management at top level
                    data.fertilizer = getEventWidget('fertilizer')?.getRows() || [];
                    data.tillage = getEventWidget('tillage')?.getRows() || [];
                    data.chemical = getEventWidget('chemical')?.getRows() || [];
                    data.residue = getEventWidget('residue')?.getRows() || [];

                    const irrMethod = document.getElementById('wiz-irr-method').value;
                    if (irrMethod === 'automatic') {
                        data.irrigation = {
                            method: 'automatic',
                            threshold: parseFloat(document.getElementById('wiz-irr-threshold').value) || 50,
                            efficiency: parseFloat(document.getElementById('wiz-irr-efficiency').value) || 90,
                        };
                    } else if (irrMethod === 'fixed') {
                        data.irrigation = {
                            method: 'fixed',
                            events: getEventWidget('irrigation')?.getRows() || [],
                        };
                    } else {
                        data.irrigation = null;
                    }

                    data.harvest = _collectHarvestData();
                    // Initial conditions are saved on Step 6 (Soil page), not here.
                }
                break;
            case 9:
                data.simulation_controls = {
                    start_date: document.getElementById('wiz-sdate').value || null,
                    num_years: parseInt(document.getElementById('wiz-nyers').value) || 1,
                    num_reps: parseInt(document.getElementById('wiz-nreps').value) || 1,
                    water: document.getElementById('wiz-opt-water').checked ? 'Y' : 'N',
                    nitrogen: document.getElementById('wiz-opt-nitro').checked ? 'Y' : 'N',
                    co2: document.getElementById('wiz-opt-co2').checked ? 'Y' : 'N',
                };
                if (data.experiment_type === 'sensitivity') {
                    saveSensitivityConfig();
                }
                if (data.experiment_type === 'monte_carlo') {
                    saveMCConfigData();
                }
                break;
        }
    }

    // =========================================================================
    // Step 9: Review Summary
    // =========================================================================

    function _buildInSituSummaryItems() {
        const sel = data.in_situ_selections || {};
        const cache = data.in_situ_preview_cache || {};
        const items = {
            'Soil source': sel.soil ? sel.soil : null,
            'Planting date source': sel.planting_date ? sel.planting_date : null,
        };
        // In MC mode, include the coverage result if the user ran a check.
        if (data.experiment_type === 'monte_carlo') {
            if (cache.soil && cache.soil.mode === 'coverage') {
                items['Soil coverage'] = `${cache.soil.covered}/${cache.soil.total_points} points`;
            }
            if (cache.planting_date && cache.planting_date.mode === 'coverage') {
                items['Planting date coverage'] =
                    `${cache.planting_date.covered}/${cache.planting_date.total_points} points`;
            }
        }
        return items;
    }

    function _buildMCLocationItems() {
        const mc = data.monte_carlo;
        const items = { 'Mode': mc.spatial_mode };
        if (mc.spatial_mode === 'circle') {
            items['Center'] = mc.center.lat && mc.center.lon ? `${mc.center.lat}, ${mc.center.lon}` : null;
            items['Radius'] = `${mc.radius_km} km`;
        } else if (mc.spatial_mode === 'bbox') {
            items['Bounds'] = mc.bbox.min_lat ? `${mc.bbox.min_lat}-${mc.bbox.max_lat}N, ${mc.bbox.min_lon}-${mc.bbox.max_lon}E` : null;
        } else if (mc.spatial_mode === 'admin') {
            items['Admin Unit'] = mc.admin_name;
            items['Level'] = mc.admin_level;
        }
        items['Grid Spacing'] = `${mc.grid_spacing}°`;
        return items;
    }

    function _buildBatchLocationItems() {
        const bs = data._batchState || {};
        const selected = (bs.selectedCounties || []).length;
        const items = {
            'State': bs.stateName || '(not selected)',
            'Counties': `${selected} selected`,
        };
        if (selected > 0 && selected <= 5) {
            items['Counties'] = bs.selectedCounties.join(', ');
        }
        return items;
    }

    function buildReviewSummary() {
        saveCurrentStepData();

        // Auto-fill simulation start date from planting date
        const pdate = data.experiment_type === 'custom' && data.treatments.length
            ? data.treatments[0].planting_date
            : data.planting_date;
        if (pdate && !document.getElementById('wiz-sdate').value) {
            const d = new Date(pdate);
            d.setDate(d.getDate() - 30);
            document.getElementById('wiz-sdate').value = d.toISOString().slice(0, 10);
        }

        const container = document.getElementById('review-summary');
        const typeLabels = {
            single: 'Single Simulation',
            sensitivity: 'Sensitivity Analysis',
            management: 'Management Comparison',
            custom: 'Custom Ensemble',
            monte_carlo: 'Monte Carlo Spatial Analysis',
        };

        const sections = [
            { title: 'Experiment Type', step: 1, items: {
                'Type': typeLabels[data.experiment_type] || data.experiment_type,
                ...(runMode === 'batch' ? { 'Run Mode': 'Batch / Multi-Location' } : {}),
            }},
            runMode === 'batch'
                ? { title: 'Batch Locations', step: 2, items: _buildBatchLocationItems() }
                : data.experiment_type === 'monte_carlo'
                    ? { title: 'Spatial Grid', step: 2, items: _buildMCLocationItems() }
                    : { title: 'Location', step: 2, items: { 'Latitude': data.latitude, 'Longitude': data.longitude, 'Elevation': data.elevation ? `${data.elevation} m` : null } },
            { title: 'Site Reference Data', step: 3, items: _buildInSituSummaryItems() },
            { title: 'Crop & Cultivar', step: 4, items: { 'Crop': data.crop_name ? `${data.crop_name} (${data.crop_code})` : null, 'Cultivar': data.cultivar_code } },
        ];

        // Planting section — depends on type
        if (data.experiment_type === 'custom' && data.treatments.length) {
            sections.push({ title: 'Planting', step: 5, items: { 'Treatments': `${data.treatments.length} treatments (per-treatment planting)` } });
        } else {
            sections.push({ title: 'Planting', step: 5, items: {
                'Date': data.planting_date,
                'Population': data.plant_population ? `${data.plant_population} plants/m2` : null,
                'Row Spacing': data.row_spacing ? `${data.row_spacing} cm` : null,
            }});
        }

        sections.push({ title: 'Soil', step: 6, items: { 'Soil ID': data.soil_id, 'Inline Soil': data.inline_soil ? 'Custom (from texture)' : null } });

        sections.push({ title: 'Weather', step: 7, items: { 'Source': data.weather_source || 'nasa_power (default)' } });

        // Management section — depends on type
        if (hasMultipleTreatments() && data.treatments.length) {
            const mgmtItems = { 'Treatments': `${data.treatments.length} treatments` };
            // Show summary of each treatment
            data.treatments.forEach((t, i) => {
                const parts = [];
                if (t.fertilizer && t.fertilizer.length) parts.push(`${t.fertilizer.length} fert`);
                if (t.irrigation) parts.push(t.irrigation.method);
                if (t.harvest) parts.push(t.harvest.option);
                mgmtItems[t.name] = parts.length ? parts.join(', ') : 'default';
            });
            sections.push({ title: 'Management', step: 8, items: mgmtItems });
        } else {
            sections.push({ title: 'Management', step: 8, items: {
                'Fertilizer': data.fertilizer.length ? `${data.fertilizer.length} events` : null,
                'Irrigation': data.irrigation ? data.irrigation.method : null,
                'Harvest': data.harvest ? data.harvest.option : null,
                'Tillage': data.tillage.length ? `${data.tillage.length} events` : null,
            }});
        }

        // Sensitivity info
        if (data.experiment_type === 'sensitivity' && data.sensitivity.min !== null && data.sensitivity.max !== null) {
            const sv = SENSITIVITY_VARIABLES.find(v => v.key === data.sensitivity.parameter);
            sections.push({ title: 'Sensitivity', step: 9, items: {
                'Parameter': sv ? sv.label : data.sensitivity.parameter,
                'Range': `${data.sensitivity.min} to ${data.sensitivity.max} ${sv ? sv.unit : ''}`,
                'Steps': data.sensitivity.steps,
            }});
        }

        // Monte Carlo info
        if (data.experiment_type === 'monte_carlo') {
            sections.push({ title: 'Monte Carlo', step: 9, items: {
                'Samples': data.monte_carlo.nens,
                'Strategy': data.monte_carlo.sampling_strategy,
            }});
        }

        let html = '';
        for (const section of sections) {
            const status = stepStatus[section.step];
            let badge = '';
            const hasValues = Object.values(section.items).some(v => v != null);

            if (status === 'skipped' && !hasValues) {
                badge = '<span class="review-badge review-badge-skip">Skipped</span>';
            } else if (hasValues) {
                badge = '<span class="review-badge review-badge-ok">Set</span>';
            } else if (section.step <= 3) {
                badge = '<span class="review-badge review-badge-missing">Missing</span>';
            }

            html += `<div class="review-section">
                <div class="review-section-header">
                    <div class="review-section-title">${section.title}</div>
                    ${badge}
                </div>
                <div class="review-params">`;

            for (const [key, val] of Object.entries(section.items)) {
                if (val != null) {
                    html += `<div class="review-key">${key}</div><div class="review-value">${val}</div>`;
                }
            }
            if (!hasValues) {
                html += `<div class="review-key" style="grid-column:1/-1;color:var(--text-muted);font-style:italic;">Not configured</div>`;
            }

            html += '</div></div>';
        }

        container.innerHTML = html;
    }

    // =========================================================================
    // Submit
    // =========================================================================

    async function submit() {
        saveCurrentStepData();

        let payload;

        if (data.experiment_type === 'single') {
            // Flat payload (unchanged)
            payload = { ...data };
            delete payload._activeTreatmentIdx;
            delete payload.sensitivity;
            delete payload.treatments;
            // Clean nulls / empty arrays
            Object.keys(payload).forEach(k => {
                if (payload[k] === null || (Array.isArray(payload[k]) && payload[k].length === 0)) {
                    delete payload[k];
                }
            });
        } else if (data.experiment_type === 'sensitivity') {
            saveSensitivityConfig();
            // Generate treatments from sensitivity config
            const treatments = generateSensitivityTreatments();
            if (treatments.length === 0) {
                alert('Configure sensitivity analysis parameters first.');
                return;
            }
            payload = {
                experiment_type: 'ensemble',
                base: {
                    crop_code: data.crop_code,
                    crop_name: data.crop_name,
                    cultivar_code: data.cultivar_code,
                    latitude: data.latitude,
                    longitude: data.longitude,
                    elevation: data.elevation,
                    soil_id: data.soil_id,
                    inline_soil: data.inline_soil,
                    weather_source: data.weather_source,
                    planting_date: data.planting_date,
                    plant_population: data.plant_population,
                    row_spacing: data.row_spacing,
                    planting_method: data.planting_method,
                    fertilizer: data.fertilizer.length ? data.fertilizer : undefined,
                    irrigation: data.irrigation,
                    harvest: data.harvest,
                    initial_conditions: data.initial_conditions,
                    tillage: data.tillage.length ? data.tillage : undefined,
                    chemical: data.chemical.length ? data.chemical : undefined,
                    residue: data.residue.length ? data.residue : undefined,
                    simulation_controls: data.simulation_controls,
                },
                sensitivity: data.sensitivity,
                treatments: treatments,
            };
        } else if (data.experiment_type === 'management') {
            // Base = shared + planting; treatments = management per treatment
            const base = {
                crop_code: data.crop_code,
                crop_name: data.crop_name,
                cultivar_code: data.cultivar_code,
                latitude: data.latitude,
                longitude: data.longitude,
                elevation: data.elevation,
                soil_id: data.soil_id,
                inline_soil: data.inline_soil,
                weather_source: data.weather_source,
                planting_date: data.planting_date,
                plant_population: data.plant_population,
                row_spacing: data.row_spacing,
                planting_method: data.planting_method,
                initial_conditions: data.initial_conditions,
                simulation_controls: data.simulation_controls,
            };
            const treatments = data.treatments.map(t => {
                const tx = { name: t.name };
                MANAGEMENT_FIELDS.forEach(f => {
                    if (t[f] !== null && t[f] !== undefined) {
                        if (Array.isArray(t[f]) && t[f].length === 0) return;
                        tx[f] = t[f];
                    }
                });
                return tx;
            });
            payload = { experiment_type: 'ensemble', base, treatments };
        } else if (data.experiment_type === 'custom') {
            // Base = shared only; treatments = planting + management per treatment
            const base = {
                crop_code: data.crop_code,
                crop_name: data.crop_name,
                cultivar_code: data.cultivar_code,
                latitude: data.latitude,
                longitude: data.longitude,
                elevation: data.elevation,
                soil_id: data.soil_id,
                inline_soil: data.inline_soil,
                weather_source: data.weather_source,
                initial_conditions: data.initial_conditions,
                simulation_controls: data.simulation_controls,
            };
            const treatments = data.treatments.map(t => {
                const tx = { name: t.name };
                TREATMENT_FIELDS.forEach(f => {
                    if (t[f] !== null && t[f] !== undefined) {
                        if (Array.isArray(t[f]) && t[f].length === 0) return;
                        tx[f] = t[f];
                    }
                });
                return tx;
            });
            payload = { experiment_type: 'ensemble', base, treatments };
        } else if (data.experiment_type === 'monte_carlo') {
            // Monte Carlo spatial analysis — single-treatment with spatial grid
            const mc = data.monte_carlo;
            payload = {
                experiment_type: 'monte_carlo',
                spatial_mode: mc.spatial_mode,
                nens: mc.nens,
                sampling_strategy: mc.sampling_strategy,
                weather_source: data.weather_source || 'power',
                crop_code: data.crop_code,
                crop_name: data.crop_name,
                cultivar_code: data.cultivar_code,
                soil_id: data.soil_id,
                inline_soil: data.inline_soil,
                planting: {
                    pdate: data.planting_date,
                    ppop: data.plant_population,
                    plrs: data.row_spacing,
                },
                simulation_controls: data.simulation_controls,
            };
            // Grid density applies to all three spatial modes
            payload.grid_spacing = mc.grid_spacing;
            // Add mode-specific geometry
            if (mc.spatial_mode === 'circle') {
                payload.center = mc.center;
                payload.radius_km = mc.radius_km;
            } else if (mc.spatial_mode === 'bbox') {
                payload.bbox = mc.bbox;
            } else if (mc.spatial_mode === 'admin') {
                payload.admin_name = mc.admin_name;
                payload.admin_level = mc.admin_level;
            }
            // Add management if configured
            if (data.fertilizer.length) payload.fertilizer = data.fertilizer;
            if (data.irrigation) payload.irrigation = data.irrigation;
            if (data.harvest) payload.harvest = data.harvest;
            if (data.initial_conditions) payload.initial_conditions = data.initial_conditions;
            if (data.tillage.length) payload.tillage = data.tillage;
        }

        // Clean undefined values from base
        if (payload.base) {
            Object.keys(payload.base).forEach(k => {
                if (payload.base[k] === null || payload.base[k] === undefined) {
                    delete payload.base[k];
                }
            });
        }

        // Wrap in batch params if batch mode
        if (runMode === 'batch') {
            const bs = data._batchState || {};
            const selectedCounties = bs.selectedCounties || [];

            if (!bs.stateName || selectedCounties.length === 0) {
                alert('Please select a state and at least one county for batch mode.');
                return;
            }

            const locationConfig = {
                mode: 'admin_boundary',
                admin_parent: bs.stateName,
                admin_parent_level: 'admin1',
                admin_child_level: 'admin2',
                selected_units: selectedCounties,
            };

            // The sub-experiment type is whatever was selected in step 1
            const subType = payload.experiment_type || 'single';

            // Template params = the regular payload minus type overrides
            const templateParams = { ...payload };
            delete templateParams.experiment_type;
            delete templateParams.spatial_mode;
            delete templateParams.nens;
            delete templateParams.sampling_strategy;

            payload = {
                experiment_type: 'batch',
                sub_experiment_type: subType === 'ensemble' ? 'ensemble' : (subType === 'monte_carlo' ? 'monte_carlo' : 'single'),
                location_config: locationConfig,
                template_params: templateParams,
            };
        }

        const submitBtn = document.getElementById('btn-submit');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Submitting...';

        try {
            const result = await api('/dssat/api/experiment/submit/', {
                method: 'POST',
                body: JSON.stringify(payload),
            });

            if (result.redirect_url) {
                window.location.href = result.redirect_url;
            } else if (result.error) {
                alert('Error: ' + result.error);
                submitBtn.disabled = false;
                submitBtn.textContent = 'Submit Experiment';
            }
        } catch (e) {
            console.error('Submit failed:', e);
            alert('Failed to submit experiment. Please try again.');
            submitBtn.disabled = false;
            submitBtn.textContent = 'Submit Experiment';
        }
    }

    // =========================================================================
    // Step dot click navigation
    // =========================================================================

    document.querySelectorAll('.step-dot').forEach(dot => {
        dot.addEventListener('click', () => {
            const step = parseInt(dot.dataset.step);
            if (step <= currentStep || stepStatus[step]) {
                saveCurrentStepData();
                goToStep(step);
                onStepEnter(step);
            }
        });
    });

    // =========================================================================
    // Init
    // =========================================================================

    updateUI();

    // =========================================================================
    // Public API
    // =========================================================================

    return {
        nextStep,
        prevStep,
        skipStep,
        selectType,
        setRunMode,
        loadBatchCounties,
        toggleBatchCounty,
        batchSelectAll,
        selectCrop,
        changeCrop,
        selectCultivar,
        changeCultivar,
        filterCrops,
        filterCultivars,
        filterSoils,
        selectSoil,
        changeSoil,
        switchSoilTab,
        estimateSoil,
        checkWeather,
        switchMgmtTab,
        addTreatment,
        removeTreatment,
        renameTreatment,
        setActiveTreatmentIdx,
        previewSensitivity,
        onSensParamChange: _updateSensitivityUI,
        switchSpatialMode,
        loadAdminUnits,
        submit,
        // Step 3 — Site Reference Data
        onInSituSourceChange,
        previewInSituLookup,
        applyInSituLookup,
        // Per-step auto-fill escape hatches
        onSoilAutoSourceChange,
        previewSoilAuto,
        applySoilAuto,
        autoFillPlantingDate,
    };
})();
