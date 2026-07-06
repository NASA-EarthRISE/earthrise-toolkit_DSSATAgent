/**
 * Protocol editor — accordion of crop-gated chips. One chip per
 * section; sections collapse to a one-line summary chip and re-expand
 * via Edit / clicking the header. Crop & Cultivar gates the rest of
 * the protocol — sections 2-5 are locked until a crop is picked
 * because their lookups depend on the crop_code + dssat_model.
 *
 * Section order:
 *
 *   [1] Crop & Cultivar           always enabled
 *   [2] Planting & Harvest        crop-dependent (planting methods,
 *                                 harvest stages from /api/crop-model/)
 *   [3] Management Practices      crop-dependent (DSSAT defaults)
 *   [4] Initial Conditions        crop-independent (locked for sequencing)
 *   [5] Simulation Controls       crop-independent (locked for sequencing)
 *
 * Each section impl exports:
 *   - validate(state, ctx)  -> {ok, message?}   used to derive chip state
 *                                              and gate the Save button
 *   - summary(state, ctx)   -> string           one-line chip summary
 *   - renderBody(state, fire, ctx, ui) -> DOM   the editable body
 *
 * Public API stays the same as the previous editor:
 *
 *   const ed = mount(host, {value, onChange, options})
 *   ed.getValue()  -> deep-copied state matching the Treatment payload
 *   ed.destroy()
 *
 * `options` accepts:
 *   - year     int    Step 1's year (used for Fixed-Date mode + Auto resolution)
 *   - fields   array  store.fields (used for Coverage button + later resolution)
 *
 * State shape produced by getValue() — additive over the previous shape:
 *
 *   planting: {
 *     mode: 'auto' | 'fixed',         // NEW
 *     auto_source: 'dssat_planting_date_lookup_v1', // mode=auto only
 *     pdoy: 117,                       // mode=fixed only
 *     planting_month_day: '04-27',     // mode=fixed only
 *     plme: 'S', ppop: 7.2, plrs: 76, pldp: 5
 *   }
 *
 *   harvest: {
 *     option: 'maturity' | 'dap' | 'growth_stage' | 'on_date',
 *     dap:   120,            // option=dap
 *     hstg:  'GS005',        // option=growth_stage
 *     hdate: '2026-09-15'    // option=on_date  (year locked to step1.year)
 *   }
 */

import {h, clear, on, escapeHtml} from '../dom.js';
import {catalog, refs} from '../api.js';
import {mount as mountDapTable} from '../dap_event_table.js';
import {mountSearchablePicker} from '../searchable_picker.js';
import {
    renderAxesFor,
    summary as summariseSensitivity,
    filterCultivarsForModel,
    generate as generateSensitivityTreatments,
} from './sensitivity_axes.js';

// ---------------------------------------------------------------------------
// Module-scope caches — shared across mounts so multiple protocols (in
// ensemble / sensitivity / batch) don't re-fetch the same crop list.
// ---------------------------------------------------------------------------

let cropsCache = null;
const cultivarsCache = {};
const cropModelCache = {};   // key: `${crop_code}::${dssat_model||''}`

// Sources offered for Auto-mode planting. Today only the in-house SE-US
// raster is loaded; expand here as new providers come online.
const AUTO_PLANTING_SOURCES = [
    {value: 'dssat_planting_date_lookup_v1',
     label: 'In-house SE-US planting date (5km)'},
];

const MGMT_KEYS = ['fertilizer', 'irrigation', 'tillage', 'chemical', 'residue'];

const PLANTING_METHODS = [
    {code: 'S', label: 'Seed (S)'},
    {code: 'T', label: 'Transplant (T)'},
    {code: 'P', label: 'Pre-germinated (P)'},
    {code: 'B', label: 'Nursery seedling (B)'},
    {code: 'I', label: 'Pre-germ + nursery (I)'},
];

const PCR_OPTIONS = [
    {code: 'FA', label: 'Fallow'},
    {code: 'MZ', label: 'Maize'},
    {code: 'WH', label: 'Wheat'},
    {code: 'SB', label: 'Soybean'},
    {code: 'SG', label: 'Sorghum'},
    {code: 'PN', label: 'Peanut'},
    {code: 'CO', label: 'Cotton'},
];

// Master section list. ``visibleFor`` (when present) restricts the chip
// to a specific ``experimentType``; otherwise the chip is always shown.
// Sensitivity Analysis sits between Crop & Cultivar and Planting & Harvest
// because every axis (cultivar list, planting date sweep, management
// practice sweep) depends on the chosen crop+model.
const ALL_SECTIONS = [
    {key: 'crop',        label: 'Crop & Cultivar',      icon: '🌱', gated: false},
    {key: 'sensitivity', label: 'Sensitivity Analysis', icon: '📊', gated: true,
     visibleFor: ['sensitivity']},
    {key: 'planting',    label: 'Planting & Harvest',   icon: '📅', gated: true},
    {key: 'mgmt',        label: 'Management Practices', icon: '🚜', gated: true},
    {key: 'ic',          label: 'Initial Conditions',   icon: '🌾', gated: true},
    {key: 'sc',          label: 'Simulation Controls',  icon: '⚙️', gated: true},
];

function visibleSections(experimentType) {
    return ALL_SECTIONS.filter((s) =>
        !s.visibleFor || s.visibleFor.includes(experimentType));
}

// ---------------------------------------------------------------------------
// Public mount
// ---------------------------------------------------------------------------

export function mount(host, {value = {}, onChange, options = {}} = {}) {
    const ctx = {
        year: parseInt(options.year, 10) || new Date().getFullYear(),
        fields: options.fields || [],
        experimentType: options.experimentType || 'single',
        cropModel: null,    // populated lazily after crop selection
    };
    const state = normalizeState(value, ctx.experimentType);
    const SECTIONS = visibleSections(ctx.experimentType);

    clear(host);
    const root = h('div', {class: 'protocol-accordion'});
    host.appendChild(root);

    // Track which chip is expanded; -1 means all collapsed.
    let chipExpanded = sectionValidates(0) ? -1 : 0;
    // Track which chips the user has explicitly Saved at least once,
    // so first-run auto-expand-next only fires for unsaved chips.
    const saveTouched = new Set();
    for (let i = 0; i < SECTIONS.length; i++) {
        if (sectionValidates(i)) saveTouched.add(i);
    }

    // Re-fetch crop-model metadata when an existing draft already has
    // a crop set — Planting & Harvest sub-step needs it for the harvest
    // dropdown and supported-modes gating.
    if (state.crop_code) ensureCropModel(state.crop_code, state.dssat_model, ctx).then(renderAll);

    function fire() {
        if (onChange) onChange(getValue());
    }
    function getValue() {
        return JSON.parse(JSON.stringify(state));
    }

    function isLocked(idx) {
        if (!SECTIONS[idx].gated) return false;
        return !sectionValidates(0);
    }

    function sectionValidates(idx) {
        const sec = SECTION_IMPL[SECTIONS[idx].key];
        const r = sec.validate(state, ctx);
        return r && r.ok;
    }

    function chipState(idx) {
        if (chipExpanded === idx) return 'active';
        if (isLocked(idx)) return 'locked';
        if (sectionValidates(idx)) return 'complete';
        if (saveTouched.has(idx)) return 'partial';
        return 'empty';
    }

    function expandChip(idx) {
        if (isLocked(idx)) return;
        chipExpanded = idx;
        renderAll();
    }
    function collapseChip() {
        chipExpanded = -1;
        renderAll();
    }

    function saveChip(idx) {
        const sec = SECTION_IMPL[SECTIONS[idx].key];
        const v = sec.validate(state, ctx);
        if (!v.ok) {
            sec._flashError && sec._flashError(v.message
                || 'Some required fields are missing.');
            return;
        }
        saveTouched.add(idx);
        // Auto-expand next un-saved chip (skip locked ones).
        let next = idx + 1;
        while (next < SECTIONS.length && isLocked(next)) next++;
        if (next < SECTIONS.length && !saveTouched.has(next)) {
            chipExpanded = next;
        } else {
            chipExpanded = -1;
        }
        renderAll();
    }

    function renderAll() {
        clear(root);
        for (let i = 0; i < SECTIONS.length; i++) {
            root.appendChild(renderChip(i));
        }
    }

    function renderChip(idx) {
        const def = SECTIONS[idx];
        const st = chipState(idx);
        const chip = h('div', {class: `protocol-chip protocol-chip-${st}`});
        chip.appendChild(renderChipHeader(idx, st));
        if (st === 'active') {
            chip.appendChild(renderChipBody(idx));
        }
        return chip;
    }

    function renderChipHeader(idx, st) {
        const def = SECTIONS[idx];
        const impl = SECTION_IMPL[def.key];
        const summary = impl.summary(state, ctx);
        const header = h('div', {class: 'protocol-chip-header'});
        // Index pill + icon
        header.appendChild(h('span', {class: 'protocol-chip-index'},
            String(idx + 1)));
        header.appendChild(h('span', {class: 'protocol-chip-icon'},
            def.icon || '•'));
        header.appendChild(h('span', {class: 'protocol-chip-label'},
            def.label));

        if (st === 'locked') {
            header.appendChild(h('span', {class: 'protocol-chip-meta'},
                'Complete Crop & Cultivar first'));
        } else {
            // Summary takes the remaining space; trailing button on
            // the right toggles the chip.
            const sumStr = summary
                || (st === 'empty' ? 'Click to configure'
                    : (st === 'partial' ? 'Incomplete — click to finish' : ''));
            header.appendChild(h('span', {class: 'protocol-chip-summary'}, sumStr));
            const action = h('button', {
                type: 'button',
                class: 'protocol-chip-action',
            }, st === 'active' ? 'Close' : (st === 'complete' ? 'Edit' : 'Open'));
            on(action, 'click', (e) => {
                e.stopPropagation();
                if (chipExpanded === idx) collapseChip();
                else expandChip(idx);
            });
            header.appendChild(action);
            on(header, 'click', () => {
                if (chipExpanded === idx) collapseChip();
                else expandChip(idx);
            });
        }
        return header;
    }

    function renderChipBody(idx) {
        const def = SECTIONS[idx];
        const impl = SECTION_IMPL[def.key];
        const body = h('div', {class: 'protocol-chip-body'});
        const flash = h('div', {
            class: 'wizard-flash wizard-flash-error',
            style: {display: 'none', marginBottom: '0.6rem'},
        });
        body.appendChild(flash);
        const ui = {
            flashError(msg) {
                flash.style.display = '';
                flash.textContent = msg;
            },
            clearError() {
                flash.style.display = 'none';
                flash.textContent = '';
            },
            rerenderHeader() {
                // Re-paint the chip without tearing down the body, so a
                // searchable picker that's currently in focus stays alive.
                // We swap the entire chip wholesale via renderAll for now —
                // simpler and cheap.
                renderAll();
            },
        };
        impl._flashError = ui.flashError;
        const inner = impl.renderBody(state, fire, ctx, ui);
        body.appendChild(inner);
        // Footer with Save / Cancel.
        const footer = h('div', {class: 'protocol-chip-footer'});
        const saveBtn = h('button', {
            type: 'button',
            class: 'btn btn-primary',
        }, 'Save');
        on(saveBtn, 'click', () => saveChip(idx));
        const cancelBtn = h('button', {
            type: 'button',
            class: 'btn btn-secondary',
        }, 'Close');
        on(cancelBtn, 'click', () => collapseChip());
        footer.appendChild(saveBtn);
        footer.appendChild(cancelBtn);
        body.appendChild(footer);
        return body;
    }

    renderAll();
    return {getValue, destroy: () => clear(host)};
}

// ---------------------------------------------------------------------------
// State helpers
// ---------------------------------------------------------------------------

function normalizeState(value, experimentType) {
    const v = value || {};
    const planting = {...(v.planting || {})};
    // Backfill mode for legacy drafts.
    if (!planting.mode) {
        planting.mode = (planting.pdoy != null
                         || planting.planting_month_day) ? 'fixed' : '';
    }
    if (!planting.auto_source && planting.mode === 'auto') {
        planting.auto_source = AUTO_PLANTING_SOURCES[0].value;
    }
    const harvest = v.harvest ? {...v.harvest} : null;
    if (harvest && !harvest.option) harvest.option = 'maturity';
    const out = {
        crop_code: v.crop_code || '',
        crop_name: v.crop_name || '',
        cultivar_code: v.cultivar_code || '',
        cultivar_name: v.cultivar_name || '',
        dssat_model: v.dssat_model || '',
        planting,
        harvest,
        initial_conditions: v.initial_conditions
            ? {...v.initial_conditions} : null,
        simulation_controls: {...(v.simulation_controls || {})},
        fertilizer: v.fertilizer ? v.fertilizer.slice() : null,
        irrigation: v.irrigation ? {...v.irrigation} : null,
        tillage:    v.tillage    ? v.tillage.slice()  : null,
        chemical:   v.chemical   ? v.chemical.slice() : null,
        residue:    v.residue    ? v.residue.slice()  : null,
    };
    if (experimentType === 'sensitivity') {
        const sens = v.sensitivity ? {...v.sensitivity} : {};
        sens.axes = sens.axes ? {...sens.axes} : {};
        out.sensitivity = sens;
    }
    return out;
}

async function ensureCropModel(cropCode, dssatModel, ctx) {
    if (!cropCode) {
        ctx.cropModel = null;
        return ctx.cropModel;
    }
    const key = `${cropCode}::${dssatModel || ''}`;
    if (cropModelCache[key] !== undefined) {
        ctx.cropModel = cropModelCache[key];
        return ctx.cropModel;
    }
    try {
        const r = await refs.cropModel(cropCode, dssatModel || '');
        cropModelCache[key] = r && r.data ? r.data : null;
    } catch (_e) {
        cropModelCache[key] = null;
    }
    ctx.cropModel = cropModelCache[key];
    return ctx.cropModel;
}

async function ensureCrops() {
    if (cropsCache) return cropsCache;
    try {
        const r = await catalog.crops();
        cropsCache = r.crops || [];
    } catch (_e) { cropsCache = []; }
    return cropsCache;
}

async function ensureCultivars(cropCode) {
    if (!cropCode) return [];
    if (cultivarsCache[cropCode]) return cultivarsCache[cropCode];
    try {
        const r = await catalog.cultivars(cropCode);
        cultivarsCache[cropCode] = r.cultivars || [];
    } catch (_e) { cultivarsCache[cropCode] = []; }
    return cultivarsCache[cropCode];
}

// Date <-> DOY helpers anchored on a given year.
function dateFromDoy(year, doy) {
    if (doy == null || isNaN(doy)) return '';
    const d = new Date(Date.UTC(year, 0, 1));
    d.setUTCDate(d.getUTCDate() + (parseInt(doy, 10) - 1));
    return d.toISOString().slice(0, 10);
}
function doyFromDate(iso) {
    if (!iso) return null;
    const d = new Date(iso + 'T00:00:00Z');
    if (isNaN(d.getTime())) return null;
    const start = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    return Math.floor((d - start) / 86400000) + 1;
}

// ---------------------------------------------------------------------------
// Section: Crop & Cultivar
// ---------------------------------------------------------------------------

const cropSection = {
    validate(state) {
        if (!state.crop_code) {
            return {ok: false, message: 'Pick a crop.'};
        }
        // Cultivar is optional — DSSAT picks the first cultivar by default
        // if none is set.
        return {ok: true};
    },
    summary(state) {
        if (!state.crop_code) return '';
        const crop = state.crop_name
            ? `${state.crop_name} (${state.crop_code})`
            : state.crop_code;
        if (state.cultivar_code) {
            const cv = state.cultivar_name
                ? `${state.cultivar_name} (${state.cultivar_code})`
                : state.cultivar_code;
            return `${crop} · cv ${cv}`;
        }
        return `${crop} · default cultivar`;
    },
    renderBody(state, fire, ctx, ui) {
        const block = h('div', {class: 'protocol-crop-section'});
        // Crop picker
        const cropHost = h('div', {class: 'wizard-form-group'});
        cropHost.appendChild(h('label', {}, 'Crop'));
        const cropPickerHost = h('div');
        cropHost.appendChild(cropPickerHost);
        block.appendChild(cropHost);
        // Cultivar picker (hidden until crop chosen)
        const cvHost = h('div', {
            class: 'wizard-form-group',
            style: {display: state.crop_code ? '' : 'none', marginTop: '0.75rem'},
        });
        cvHost.appendChild(h('label', {}, 'Cultivar'));
        const cvPickerHost = h('div');
        cvHost.appendChild(cvPickerHost);
        block.appendChild(cvHost);

        let cultivarPicker = null;

        async function mountCropPicker() {
            const crops = await ensureCrops();
            mountSearchablePicker(cropPickerHost, {
                items: crops,
                value: state.crop_code || null,
                placeholder: 'Search crops…',
                emptyMsg: 'No crops match.',
                getCode: (c) => c.code,
                getLabel: (c) => c.name,
                getSubtitle: (c) => c.crop_group ? `${c.code} · ${c.crop_group}` : c.code,
                onChange: async (code, item) => {
                    state.crop_code = code || '';
                    state.crop_name = item ? item.name : '';
                    state.dssat_model = item && item.dssat_model
                        ? item.dssat_model : (state.dssat_model || '');
                    state.cultivar_code = '';
                    state.cultivar_name = '';
                    cvHost.style.display = code ? '' : 'none';
                    fire();
                    if (code) {
                        await Promise.all([
                            mountCultivarPicker(),
                            ensureCropModel(code, state.dssat_model, ctx),
                        ]);
                    } else if (cultivarPicker) {
                        cultivarPicker.destroy();
                        cultivarPicker = null;
                    }
                },
            });
        }

        async function mountCultivarPicker() {
            if (!state.crop_code) return;
            const cvs = await ensureCultivars(state.crop_code);
            // Restrict the list to cultivars that match the chosen
            // crop's DSSAT model — a single crop_code (e.g. MZ) often
            // has cultivars registered against multiple model variants
            // (MZCER, MZIXIM, etc.) and the registry can return the
            // same code under more than one variant. Without filtering
            // the dropdown shows duplicates and cross-model entries
            // that would never produce a valid run.
            const filtered = filterCultivarsForModel(cvs, state.dssat_model);
            cultivarPicker = mountSearchablePicker(cvPickerHost, {
                items: filtered,
                value: state.cultivar_code || null,
                placeholder: 'Search cultivars…',
                emptyMsg: state.dssat_model
                    ? `No cultivars registered for this crop under model ${state.dssat_model}.`
                    : 'No cultivars match for this crop.',
                getCode: (c) => c.code,
                getLabel: (c) => c.name || c.code,
                getSubtitle: (c) => c.code,
                onChange: async (code, item) => {
                    state.cultivar_code = code || '';
                    state.cultivar_name = item ? (item.name || '') : '';
                    // Cultivars carry their crop-model variant; if this
                    // cultivar resolves to a different dssat_model than
                    // what was cached, the harvest dropdown's gating +
                    // stages list need a fresh fetch.
                    const nextModel = (item && item.dssat_model) || '';
                    if (nextModel && nextModel !== state.dssat_model) {
                        state.dssat_model = nextModel;
                        await ensureCropModel(state.crop_code, nextModel, ctx);
                    }
                    fire();
                },
            });
        }

        mountCropPicker();
        if (state.crop_code) mountCultivarPicker();

        return block;
    },
};

// ---------------------------------------------------------------------------
// Section: Planting & Harvest
// ---------------------------------------------------------------------------

function plantingIsSwept(state, ctx) {
    return ctx.experimentType === 'sensitivity'
        && (state.sensitivity || {}).category === 'planting';
}

const plantingSection = {
    validate(state, ctx) {
        // When the Sensitivity sweep targets planting, the per-treatment
        // generator overrides every planting field — the static section
        // below is informational, not required.
        if (plantingIsSwept(state, ctx)) return {ok: true};
        const p = state.planting || {};
        if (!p.mode) {
            return {ok: false, message: 'Pick a planting-date mode (Auto or Fixed Date).'};
        }
        if (p.mode === 'auto' && !p.auto_source) {
            return {ok: false, message: 'Pick an Auto planting-date source.'};
        }
        if (p.mode === 'fixed' && (p.pdoy == null || isNaN(p.pdoy))) {
            return {ok: false, message: 'Pick a planting date.'};
        }
        const h = state.harvest;
        if (!h || !h.option) {
            return {ok: false, message: 'Pick a harvest mode.'};
        }
        if (h.option === 'dap' && (h.dap == null || isNaN(h.dap))) {
            return {ok: false, message: 'Set a days-after-planting value for harvest.'};
        }
        if (h.option === 'growth_stage' && !h.hstg) {
            return {ok: false, message: 'Pick a harvest growth stage.'};
        }
        if (h.option === 'on_date' && !h.hdate) {
            return {ok: false, message: 'Pick a fixed harvest date.'};
        }
        return {ok: true};
    },
    summary(state, ctx) {
        if (plantingIsSwept(state, ctx)) {
            return 'Swept by Sensitivity Analysis';
        }
        const p = state.planting || {};
        let plantingStr = '';
        if (p.mode === 'auto') {
            const src = (AUTO_PLANTING_SOURCES.find((s) => s.value === p.auto_source) || {});
            plantingStr = `Auto · ${src.label || p.auto_source || '?'}`;
        } else if (p.mode === 'fixed' && p.pdoy != null) {
            const md = p.planting_month_day || '';
            plantingStr = md ? `${md} (DOY ${p.pdoy})` : `DOY ${p.pdoy}`;
        }
        const h = state.harvest || {};
        let harvestStr = '';
        if (h.option === 'maturity') harvestStr = 'harvest at maturity';
        else if (h.option === 'dap') harvestStr = h.dap != null ? `harvest ${h.dap} DAP` : 'DAP harvest';
        else if (h.option === 'growth_stage') harvestStr = h.hstg ? `harvest at ${h.hstg}` : 'stage harvest';
        else if (h.option === 'on_date') harvestStr = h.hdate ? `harvest ${h.hdate}` : 'fixed-date harvest';
        const popStr = p.ppop != null ? ` · ${p.ppop} pl/m²` : '';
        const out = [plantingStr, harvestStr].filter(Boolean).join(' · ');
        return out ? out + popStr : '';
    },
    renderBody(state, fire, ctx, ui) {
        if (plantingIsSwept(state, ctx)) {
            const block = h('div', {class: 'protocol-planting-section'});
            block.appendChild(h('div', {class: 'wizard-flash wizard-flash-info'},
                'Planting & Harvest is being swept by Sensitivity Analysis. '
                + 'Edit the sweep axes in the Sensitivity Analysis chip above '
                + 'to control planting date / population / row spacing across '
                + 'the generated treatments.'));
            return block;
        }
        if (!state.planting) state.planting = {};
        if (!state.harvest)  state.harvest  = {option: 'maturity'};
        const p = state.planting;
        const h_ = state.harvest;
        const block = h('div', {class: 'protocol-planting-section'});

        // -----------------------------------------------------------------
        // Planting date — Auto / Fixed Date toggle
        // -----------------------------------------------------------------
        const plantBlock = h('div', {class: 'wizard-form-block'});
        plantBlock.appendChild(h('h4', {}, 'Planting date'));

        const toggleHost = h('div');
        const sourceHost = h('div', {style: {marginTop: '0.5rem'}});
        const dateHost   = h('div', {style: {marginTop: '0.5rem'}});
        plantBlock.appendChild(toggleHost);
        plantBlock.appendChild(sourceHost);
        plantBlock.appendChild(dateHost);

        function renderToggle() {
            clear(toggleHost);
            const bar = h('div', {class: 'wizard-mode-toggle'});
            const auto = h('button', {
                type: 'button',
                class: 'wizard-mode-btn' + (p.mode === 'auto' ? ' active' : ''),
            }, 'Auto');
            const fixed = h('button', {
                type: 'button',
                class: 'wizard-mode-btn' + (p.mode === 'fixed' ? ' active' : ''),
            }, 'Fixed Date');
            on(auto, 'click', () => {
                if (p.mode === 'auto') return;
                p.mode = 'auto';
                if (!p.auto_source) p.auto_source = AUTO_PLANTING_SOURCES[0].value;
                // Wipe any Fixed-Date fields so they don't leak through
                // as a stale pdoy that the lock-time resolver and the
                // Step 5 review both fall back to.
                p.pdoy = null;
                p.planting_month_day = null;
                fire();
                renderToggle();
                renderSourceOrDate();
            });
            on(fixed, 'click', () => {
                if (p.mode === 'fixed') return;
                p.mode = 'fixed';
                // Wipe Auto-only fields so the source picker dropdown
                // doesn't haunt the next save.
                p.auto_source = null;
                fire();
                renderToggle();
                renderSourceOrDate();
            });
            bar.appendChild(auto);
            bar.appendChild(fixed);
            toggleHost.appendChild(bar);
        }

        function renderSourceOrDate() {
            clear(sourceHost);
            clear(dateHost);
            if (p.mode === 'auto') {
                const sel = h('select', {class: 'wizard-select'});
                for (const s of AUTO_PLANTING_SOURCES) {
                    sel.appendChild(h('option', {value: s.value}, s.label));
                }
                sel.value = p.auto_source || AUTO_PLANTING_SOURCES[0].value;
                on(sel, 'change', () => {
                    p.auto_source = sel.value;
                    fire();
                });
                const covBtn = h('button', {
                    type: 'button', class: 'btn btn-sm btn-secondary',
                    style: {marginLeft: '0.5rem'},
                }, 'Check coverage');
                const covFlash = h('div', {
                    class: 'wizard-flash wizard-flash-info',
                    style: {display: 'none', marginTop: '0.4rem'},
                });
                on(covBtn, 'click', async () => {
                    const fields = ctx.fields || [];
                    if (!fields.length) {
                        covFlash.style.display = '';
                        covFlash.className = 'wizard-flash wizard-flash-error';
                        covFlash.textContent =
                            'No fields attached — go back to Step 2.';
                        return;
                    }
                    covFlash.style.display = '';
                    covFlash.className = 'wizard-flash wizard-flash-info';
                    covFlash.textContent = 'Checking coverage…';
                    covBtn.disabled = true;
                    try {
                        const points = fields
                            .filter((f) => f.latitude != null && f.longitude != null)
                            .map((f) => [f.latitude, f.longitude]);
                        const r = await refs.pointsCoverage({
                            points, parameter: 'planting_date',
                            source: p.auto_source,
                        });
                        const total = r.total_points || 0;
                        const cov = r.covered || 0;
                        const ratio = Math.round((r.coverage_ratio || 0) * 100);
                        const ok = cov === total && total > 0;
                        covFlash.className = 'wizard-flash '
                            + (ok ? 'wizard-flash-info' : 'wizard-flash-error');
                        let msg = `${cov}/${total} field${total === 1 ? '' : 's'} covered (${ratio}%)`;
                        if (!ok && (r.uncovered || []).length) {
                            const ex = r.uncovered[0];
                            msg += ` · uncovered example: [${ex[0].toFixed(3)}, ${ex[1].toFixed(3)}]`;
                            if (r.uncovered.length > 1) {
                                msg += ` (+${r.uncovered.length - 1} more)`;
                            }
                            msg += ' — switch to Fixed Date or pick a different source.';
                        }
                        covFlash.textContent = msg;
                    } catch (e) {
                        covFlash.className = 'wizard-flash wizard-flash-error';
                        covFlash.textContent = 'Coverage check failed: ' + e.message;
                    } finally {
                        covBtn.disabled = false;
                    }
                });
                sourceHost.appendChild(h('div', {class: 'wizard-form-row'},
                    h('div', {class: 'wizard-form-group'},
                        h('label', {class: 'step-description'}, 'Source'),
                        sel),
                    h('div', {class: 'wizard-form-group'},
                        h('label', {class: 'step-description'}, ' '),
                        covBtn),
                ));
                sourceHost.appendChild(covFlash);
                sourceHost.appendChild(h('p', {class: 'step-description'},
                    'No date input — the per-field planting date will be '
                    + 'resolved from the in-situ raster on Next.'));
            } else if (p.mode === 'fixed') {
                const yearStr = String(ctx.year);
                const minStr = `${yearStr}-01-01`;
                const maxStr = `${yearStr}-12-31`;
                const dateInp = h('input', {
                    type: 'date', class: 'wizard-input',
                    min: minStr, max: maxStr,
                    value: p.pdoy != null ? dateFromDoy(ctx.year, p.pdoy) : '',
                });
                const doyDisp = h('span', {
                    class: 'step-description',
                    style: {marginLeft: '0.5rem'},
                }, p.pdoy != null ? `DOY ${p.pdoy}` : '');
                on(dateInp, 'input', () => {
                    const iso = dateInp.value;
                    p.pdoy = doyFromDate(iso);
                    p.planting_month_day = iso ? iso.slice(5) : null;
                    doyDisp.textContent = p.pdoy != null ? `DOY ${p.pdoy}` : '';
                    fire();
                });
                dateHost.appendChild(h('div', {class: 'wizard-form-row'},
                    h('div', {class: 'wizard-form-group'},
                        h('label', {class: 'step-description'}, `Date (year locked to ${yearStr})`),
                        h('div', {style: {display: 'flex', alignItems: 'center'}},
                            dateInp, doyDisp)),
                ));
            }
        }

        renderToggle();
        renderSourceOrDate();

        block.appendChild(plantBlock);

        // -----------------------------------------------------------------
        // Planting method / population / row spacing / depth
        // -----------------------------------------------------------------
        const methodSel = h('select', {class: 'wizard-select'});
        for (const m of PLANTING_METHODS) {
            methodSel.appendChild(h('option', {value: m.code}, m.label));
        }
        methodSel.value = p.plme || 'S';
        on(methodSel, 'change', () => { p.plme = methodSel.value; fire(); });

        const num = (key, label, opts = {}) => {
            const inp = h('input', {
                type: 'number', class: 'wizard-input',
                ...opts,
                value: p[key] != null ? p[key] : '',
            });
            on(inp, 'input', () => {
                const v = parseFloat(inp.value);
                p[key] = isNaN(v) ? null : v;
                fire();
            });
            return h('div', {class: 'wizard-form-group'},
                h('label', {}, label), inp);
        };

        block.appendChild(h('div', {class: 'wizard-form-row'},
            h('div', {class: 'wizard-form-group'},
                h('label', {}, 'Planting method'), methodSel),
            num('ppop',  'Population (plants/m²)', {step: '0.1', min: '0', placeholder: 'e.g. 7.2'}),
        ));
        block.appendChild(h('div', {class: 'wizard-form-row'},
            num('plrs',  'Row spacing (cm)',       {step: '1',   min: '0', placeholder: 'e.g. 76'}),
            num('pldp',  'Planting depth (cm)',    {step: '0.5', min: '0', placeholder: 'e.g. 5'}),
        ));

        // -----------------------------------------------------------------
        // Harvest mode
        // -----------------------------------------------------------------
        const harvBlock = h('div', {class: 'wizard-form-block'});
        harvBlock.appendChild(h('h4', {}, 'Harvest mode'));
        const harvSel = h('select', {class: 'wizard-select'});
        const harvBody = h('div', {style: {marginTop: '0.5rem'}});
        harvBlock.appendChild(harvSel);
        harvBlock.appendChild(harvBody);

        const allModes = [
            {value: 'maturity',     label: 'At maturity (default)'},
            {value: 'dap',          label: 'Days after planting'},
            {value: 'growth_stage', label: 'Growth stage'},
            {value: 'on_date',      label: 'Fixed Date'},
        ];

        function rebuildHarvestSelect() {
            clear(harvSel);
            // Empty / missing supported_harvs_modes → no constraint. The
            // CropModel row often doesn't have this field seeded for every
            // (crop, dssat_model) combination, in which case the API
            // returns ``[]`` — and treating that as a whitelist would
            // disable every mode.
            const raw = ctx.cropModel && ctx.cropModel.supported_harvs_modes;
            const supported = (Array.isArray(raw) && raw.length > 0)
                ? new Set(raw) : null;
            for (const m of allModes) {
                const allowed = !supported || supported.has(m.value);
                const opt = h('option', {value: m.value}, m.label
                    + (allowed ? '' : ' (not supported)'));
                if (!allowed) opt.disabled = true;
                harvSel.appendChild(opt);
            }
            harvSel.value = h_.option || 'maturity';
        }
        rebuildHarvestSelect();

        on(harvSel, 'change', () => {
            const opt = harvSel.value;
            // Reset per-mode fields when switching.
            state.harvest = {option: opt};
            fire();
            renderHarvBody();
        });

        function renderHarvBody() {
            clear(harvBody);
            const opt = state.harvest && state.harvest.option;
            if (opt === 'dap') {
                const dapInp = h('input', {
                    type: 'number', class: 'wizard-input',
                    min: '0', step: '1',
                    placeholder: 'e.g. 120',
                    value: state.harvest.dap != null ? state.harvest.dap : '',
                });
                on(dapInp, 'input', () => {
                    const v = parseInt(dapInp.value, 10);
                    state.harvest.dap = isNaN(v) ? null : v;
                    fire();
                });
                harvBody.appendChild(h('div', {class: 'wizard-form-row'},
                    h('div', {class: 'wizard-form-group'},
                        h('label', {}, 'Days after planting'), dapInp),
                ));
            } else if (opt === 'growth_stage') {
                const stageSel = h('select', {class: 'wizard-select'});
                stageSel.appendChild(h('option', {value: ''}, 'Loading stages…'));
                harvBody.appendChild(h('div', {class: 'wizard-form-row'},
                    h('div', {class: 'wizard-form-group'},
                        h('label', {}, 'Growth stage'), stageSel),
                ));
                (async () => {
                    const cm = ctx.cropModel
                        || await ensureCropModel(state.crop_code, state.dssat_model, ctx);
                    const stages = (cm && Array.isArray(cm.harvest_stages))
                        ? cm.harvest_stages : [];
                    clear(stageSel);
                    if (!stages.length) {
                        stageSel.appendChild(h('option', {value: ''},
                            '(no growth stages registered for this crop/model)'));
                        return;
                    }
                    stageSel.appendChild(h('option', {value: ''}, '— pick a stage —'));
                    for (const s of stages) {
                        const desc = s.description ? ` — ${s.description}` : '';
                        stageSel.appendChild(h('option', {value: s.code},
                            `${s.name || s.code}${desc}`));
                    }
                    stageSel.value = state.harvest.hstg || '';
                    on(stageSel, 'change', () => {
                        state.harvest.hstg = stageSel.value || null;
                        fire();
                    });
                })();
            } else if (opt === 'on_date') {
                const yearStr = String(ctx.year);
                const dateInp = h('input', {
                    type: 'date', class: 'wizard-input',
                    min: `${yearStr}-01-01`, max: `${yearStr}-12-31`,
                    value: state.harvest.hdate || '',
                });
                on(dateInp, 'input', () => {
                    state.harvest.hdate = dateInp.value || null;
                    fire();
                });
                harvBody.appendChild(h('div', {class: 'wizard-form-row'},
                    h('div', {class: 'wizard-form-group'},
                        h('label', {}, `Harvest date (year locked to ${yearStr})`),
                        dateInp),
                ));
            }
            // 'maturity' has no extra fields.
        }
        renderHarvBody();

        block.appendChild(harvBlock);
        return block;
    },
};

// ---------------------------------------------------------------------------
// Section: Management Practices (preserved from previous editor)
// ---------------------------------------------------------------------------

function extractIrrigationRows(irr) {
    if (!irr) return [];
    if (Array.isArray(irr)) return irr;
    if (irr.events) return irr.events;
    return [];
}

function sweptManagementPractice(state, ctx) {
    if (ctx.experimentType !== 'sensitivity') return null;
    const sens = state.sensitivity || {};
    if (sens.category !== 'management') return null;
    return (sens.axes || {}).practice || null;
}

const mgmtSection = {
    validate() { return {ok: true}; },
    summary(state, ctx) {
        const counts = [];
        for (const k of MGMT_KEYS) {
            let n = 0;
            const v = state[k];
            if (Array.isArray(v)) n = v.length;
            else if (v && Array.isArray(v.events)) n = v.events.length;
            if (n) counts.push(`${k.slice(0, 3)} ×${n}`);
        }
        const swept = sweptManagementPractice(state, ctx);
        const sweptTag = swept ? ` · ${swept} swept` : '';
        return (counts.length ? counts.join(' · ') : 'No events (auto-management defaults)')
            + sweptTag;
    },
    renderBody(state, fire, ctx, ui) {
        const block = h('div', {class: 'protocol-mgmt-section'});
        block.appendChild(h('p', {class: 'step-description'},
            'Add events for any practices you want to model. Each event is '
            + 'anchored to the planting date as days-after-planting (DAP) — '
            + 'negative values mean before planting. Empty blocks fall back to '
            + 'DSSAT auto-management based on simulation-controls flags.'));
        const swept = sweptManagementPractice(state, ctx);
        for (const key of MGMT_KEYS) {
            const sub = h('div', {class: 'protocol-mgmt-block'});
            sub.appendChild(h('h4', {}, key.charAt(0).toUpperCase() + key.slice(1)));
            const tbl = h('div', {class: 'protocol-mgmt-table'});
            sub.appendChild(tbl);
            block.appendChild(sub);
            if (swept === key) {
                // The Sensitivity sweep is varying this practice — disable
                // edits here so the user doesn't accidentally hand-write
                // events that the generator will overwrite anyway.
                tbl.appendChild(h('div', {class: 'wizard-flash wizard-flash-info'},
                    `${key.charAt(0).toUpperCase()}${key.slice(1)} is being `
                    + `swept by Sensitivity Analysis. Edit the sweep axes `
                    + `in the Sensitivity Analysis chip above to control `
                    + `${key} across the generated treatments.`));
                continue;
            }
            try {
                const initialRows = key === 'irrigation'
                    ? extractIrrigationRows(state.irrigation)
                    : (state[key] || []);
                mountDapTable(tbl, key, initialRows, (rows) => {
                    if (key === 'irrigation') {
                        state.irrigation = rows.length
                            ? {method: 'fixed', events: rows}
                            : null;
                    } else {
                        state[key] = rows.slice();
                    }
                    fire();
                });
            } catch (e) {
                tbl.appendChild(h('div', {class: 'wizard-flash wizard-flash-error'},
                    `Couldn't init ${key} widget: ${e.message}`));
            }
        }
        return block;
    },
};

// ---------------------------------------------------------------------------
// Section: Initial Conditions
// ---------------------------------------------------------------------------

const icSection = {
    validate() { return {ok: true}; },
    summary(state) {
        const ic = state.initial_conditions;
        if (!ic) return 'DSSAT defaults';
        const parts = [];
        if (ic.pcr) parts.push(`prev ${ic.pcr}`);
        if (ic.icres != null) parts.push(`residue ${ic.icres} kg/ha`);
        if (ic.icren != null) parts.push(`N ${ic.icren}%`);
        return parts.length ? `Custom · ${parts.join(' · ')}` : 'Custom (defaults)';
    },
    renderBody(state, fire, ctx, ui) {
        const block = h('div', {class: 'protocol-ic-section'});
        const enabled = h('input', {
            type: 'checkbox', id: 'protocol-ic-on',
            checked: state.initial_conditions != null,
        });
        block.appendChild(h('label', {class: 'wizard-checkbox', for: 'protocol-ic-on'},
            enabled, h('span', {}, 'Override DSSAT defaults with custom IC')));

        const inner = h('div', {style: {display: state.initial_conditions ? '' : 'none'}});
        block.appendChild(inner);

        function render() {
            clear(inner);
            const ic = state.initial_conditions || {};
            const pcrSel = h('select', {class: 'wizard-select'});
            for (const o of PCR_OPTIONS) {
                pcrSel.appendChild(h('option', {value: o.code}, o.label));
            }
            pcrSel.value = ic.pcr || 'FA';
            on(pcrSel, 'change', () => {
                const cur = state.initial_conditions || {};
                cur.pcr = pcrSel.value;
                state.initial_conditions = cur;
                fire();
            });
            const num = (key, label, opts = {}) => {
                const inp = h('input', {
                    type: 'number', class: 'wizard-input',
                    ...opts,
                    value: ic[key] != null ? ic[key] : '',
                });
                on(inp, 'input', () => {
                    const v = parseFloat(inp.value);
                    const cur = state.initial_conditions || {};
                    cur[key] = isNaN(v) ? null : v;
                    state.initial_conditions = cur;
                    fire();
                });
                return h('div', {class: 'wizard-form-group'},
                    h('label', {}, label), inp);
            };
            inner.appendChild(h('div', {class: 'wizard-form-row-3'},
                h('div', {class: 'wizard-form-group'},
                    h('label', {}, 'Previous crop'), pcrSel),
                num('icres', 'Surface residue (kg/ha)', {step: '10', min: '0'}),
                num('icren', 'Residue N (%)', {step: '0.1', min: '0'}),
            ));
            inner.appendChild(h('p', {class: 'step-description'},
                'Per-layer SH2O / SNH4 / SNO3 are auto-derived from the chosen soil '
                + 'profile at run time; advanced overrides land in a follow-up.'));
        }
        on(enabled, 'change', () => {
            state.initial_conditions = enabled.checked ? {pcr: 'FA'} : null;
            inner.style.display = enabled.checked ? '' : 'none';
            render();
            fire();
        });
        render();
        return block;
    },
};

// ---------------------------------------------------------------------------
// Section: Simulation Controls
// ---------------------------------------------------------------------------

const scSection = {
    validate() { return {ok: true}; },
    summary(state) {
        const sc = state.simulation_controls || {};
        const parts = [];
        const ny = sc.num_years != null ? sc.num_years : 1;
        parts.push(`${ny}yr`);
        if (sc.water === 'Y' || sc.water === true) parts.push('water');
        if (sc.nitrogen === 'Y' || sc.nitrogen === true) parts.push('N');
        if (sc.co2 === 'Y' || sc.co2 === true) parts.push('elev CO₂');
        const off = sc.start_offset_days;
        if (off != null) parts.push(`start ${off >= 0 ? '+' : ''}${off}d`);
        return parts.join(' · ');
    },
    renderBody(state, fire, ctx, ui) {
        const block = h('div', {class: 'protocol-sc-section'});
        const sc = state.simulation_controls || (state.simulation_controls = {});
        if (sc.start_offset_days == null) sc.start_offset_days = -30;
        const num = (key, label, opts = {}) => {
            const inp = h('input', {
                type: 'number', class: 'wizard-input',
                ...opts,
                value: sc[key] != null ? sc[key] : '',
            });
            on(inp, 'input', () => {
                const v = parseFloat(inp.value);
                sc[key] = isNaN(v) ? null : v;
                fire();
            });
            return h('div', {class: 'wizard-form-group'},
                h('label', {}, label), inp);
        };
        block.appendChild(h('div', {class: 'wizard-form-row-3'},
            num('start_offset_days', 'Start offset from planting (days)',
                {step: '1', placeholder: 'default -30'}),
            num('num_years', 'Number of years', {step: '1', min: '1'}),
            num('num_reps',  'Replicates',      {step: '1', min: '1'}),
        ));
        const opt = (key, label) => {
            const cb = h('input', {
                type: 'checkbox',
                id: 'protocol-opt-' + key,
                checked: (sc[key] === 'Y' || sc[key] === true),
            });
            on(cb, 'change', () => {
                sc[key] = cb.checked ? 'Y' : 'N';
                fire();
            });
            return h('label', {class: 'wizard-checkbox',
                for: 'protocol-opt-' + key}, cb,
                h('span', {}, label));
        };
        block.appendChild(h('div', {class: 'wizard-radio-row'},
            opt('water', 'Water balance'),
            opt('nitrogen', 'Nitrogen balance'),
            opt('co2', 'Elevated CO₂'),
        ));
        return block;
    },
};

// ---------------------------------------------------------------------------
// Section dispatch table
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Section: Sensitivity Analysis (only shown when experimentType==='sensitivity')
// ---------------------------------------------------------------------------

const SENSITIVITY_CATEGORIES = [
    {key: 'planting',   label: 'Planting (date / population / row spacing)'},
    {key: 'cultivar',   label: 'Cultivar (multi-select)'},
    {key: 'management', label: 'Management (single practice)'},
];

const sensitivitySection = {
    validate(state) {
        const sens = state.sensitivity || {};
        if (!sens.category) {
            return {ok: false, message: 'Pick a sweep category.'};
        }
        const generated = generateSensitivityTreatments(sens, state);
        if (!generated.length) {
            return {ok: false, message: 'Sweep produces zero treatments — fill in the axes.'};
        }
        if (generated.length > 99) {
            return {ok: false, message: `Sweep produces ${generated.length} treatments; max 99.`};
        }
        return {ok: true};
    },
    summary(state) {
        const sens = state.sensitivity || {};
        if (!sens.category) return '';
        return summariseSensitivity(sens, state) || `${sens.category} sweep`;
    },
    renderBody(state, fire, ctx, ui) {
        if (!state.sensitivity) state.sensitivity = {};
        if (!state.sensitivity.axes) state.sensitivity.axes = {};
        const sens = state.sensitivity;

        const block = h('div', {class: 'protocol-sensitivity-section'});

        // Category selector
        const catSel = h('select', {class: 'wizard-select'});
        for (const c of SENSITIVITY_CATEGORIES) {
            catSel.appendChild(h('option', {value: c.key}, c.label));
        }
        catSel.value = sens.category || '';
        if (!sens.category) {
            // Insert a placeholder so the user must explicitly pick.
            catSel.insertBefore(
                h('option', {value: ''}, '— pick a sweep axis —'),
                catSel.firstChild,
            );
            catSel.value = '';
        }
        block.appendChild(h('div', {class: 'wizard-form-group'},
            h('label', {}, 'Sweep category'), catSel));

        const axesHost = h('div', {class: 'wizard-form-block'});
        const previewEl = h('div', {class: 'wizard-form-block'});
        block.appendChild(axesHost);
        block.appendChild(previewEl);

        function persist() {
            fire();
            renderPreview();
        }

        function renderAxes() {
            clear(axesHost);
            if (!sens.category) return;
            renderAxesFor(
                sens.category, axesHost, sens.axes, state, persist,
                {fields: ctx.fields, year: ctx.year},
            );
        }

        function renderPreview() {
            clear(previewEl);
            if (!sens.category) return;
            const generated = generateSensitivityTreatments(sens, state);
            const exceeds = generated.length > 99;
            const cls = exceeds
                ? 'wizard-flash wizard-flash-error'
                : 'wizard-flash wizard-flash-info';
            previewEl.appendChild(h('div', {class: cls},
                `Generates ${generated.length} treatment`
                + `${generated.length === 1 ? '' : 's'}`
                + (exceeds ? ' — exceeds the 99 cap, narrow the sweep.' : '.')));
            if (generated.length && generated.length <= 99) {
                const ul = h('ul', {class: 'review-list',
                    style: {maxHeight: '12rem', overflow: 'auto'}});
                for (const t of generated.slice(0, 50)) {
                    ul.appendChild(h('li', {}, t.name));
                }
                if (generated.length > 50) {
                    ul.appendChild(h('li',
                        {style: {color: 'var(--text-muted)'}},
                        `(${generated.length - 50} more…)`));
                }
                previewEl.appendChild(ul);
            }
        }

        on(catSel, 'change', () => {
            sens.category = catSel.value || null;
            sens.axes = {};
            renderAxes();
            persist();
            // Re-paint the chip bar so downstream chip summaries
            // reflect "Swept by Sensitivity" gating.
            if (ui && ui.rerenderHeader) ui.rerenderHeader();
        });

        renderAxes();
        renderPreview();
        return block;
    },
};

const SECTION_IMPL = {
    crop:        cropSection,
    sensitivity: sensitivitySection,
    planting:    plantingSection,
    mgmt:        mgmtSection,
    ic:          icSection,
    sc:          scSection,
};
