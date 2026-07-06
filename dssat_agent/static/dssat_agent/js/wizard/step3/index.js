/**
 * Step 3: Field configuration.
 *
 * Three vertical sections:
 *
 *   1. **Weather Source** — single dropdown, filtered to DSSAT-complete
 *      sources via /data/api/sources/?dssat_complete=1. The chosen source
 *      is applied to every attached Field on Lock.
 *
 *   2. **Field Details** — independent Auto / Manual toggles for Soil
 *      and Elevation. When set to Auto, on Lock the wizard runs a per-
 *      field auto-fill against the chosen source. When set to Manual,
 *      the per-field editor below exposes a picker for that section
 *      (and Lock validates that every field has a value).
 *
 *   3. **Per-field editor** (only rendered when at least one section is
 *      Manual). Treatment-selector-style tab bar with one tab per
 *      attached field; per-tab body shows just the manual section(s).
 *
 * Step 3 only updates Field records in place — it never adds/removes
 * fields. The Field set is fixed by Step 2's lock hook.
 */

import {h, clear, on, escapeHtml} from '../dom.js';
import {fields as fieldsAPI, refs, catalog} from '../api.js';
import {renderSelector} from '../treatment_selector.js';

let weatherSourcesCache = null;
let soilsCache = null;

const SOIL_AUTO_SOURCES = [
    {value: 'auto',                       label: 'Auto (in-situ → SoilGrids fallback)'},
    {value: 'dssat_soil_lookup_v1',       label: 'In-house SE-US (5 km)'},
    {value: 'soilgrids',                  label: 'SoilGrids 2.0 (global)'},
    {value: 'ssurgo',                     label: 'SSURGO (US)'},
];

// Elevation auto-fill catalog. Just SRTM today, but rendered as a dropdown
// for symmetry with the soil picker — adding a second source later means
// appending one entry here and (if it's not handled by /dssat/api/elevation/
// already) routing in the backend.
const ELEV_AUTO_SOURCES = [
    {value: 'srtm_v1', label: 'SRTM v1 (SE-US)'},
];

export async function render(root, store, controller) {
    clear(root);
    const state = store.getStep('step3') || {};
    const fields = store.fields || [];

    if (!fields.length) {
        root.appendChild(h('div', {class: 'wizard-flash wizard-flash-error'},
            'No fields attached. Go back to Step 2 and add a location.'));
        return;
    }

    // Default modes — Auto for both, matching the legacy behaviour. We
    // only persist when changed so an unmodified draft stays clean.
    const soilMode = state.soil_mode || 'auto';
    const elevMode = state.elevation_mode || 'auto';
    if (state.soil_mode == null || state.elevation_mode == null) {
        store.setStep('step3', {
            soil_mode: soilMode,
            elevation_mode: elevMode,
        });
    }

    const panel = h('div', {class: 'wizard-step-panel'});
    panel.appendChild(h('h2', {class: 'step-title'}, 'Fields'));

    // -----------------------------------------------------------------
    // Section 1 — Weather Details (just the dropdown, no inner label)
    // -----------------------------------------------------------------
    const weatherBlock = h('div', {class: 'wizard-form-block'});
    weatherBlock.appendChild(h('h3', {class: 'wizard-form-subhead'},
        'Weather Details'));
    const weatherSel = h('select', {
        class: 'wizard-select', style: {minWidth: '20rem'},
    }, h('option', {value: ''}, 'Loading weather sources…'));
    weatherBlock.appendChild(weatherSel);
    panel.appendChild(weatherBlock);

    // -----------------------------------------------------------------
    // Section 2 — Field Details
    //   * Auto/Manual toggles for soil + elevation (independent)
    //   * When either is Manual, the per-field tab bar + editor body
    //     render directly underneath the toggles (one section, not two).
    // -----------------------------------------------------------------
    const detailsBlock = h('div', {class: 'wizard-form-block'});
    detailsBlock.appendChild(h('h3', {class: 'wizard-form-subhead'},
        'Field Details'));
    detailsBlock.appendChild(h('p', {class: 'step-description'},
        'Choose Auto for either or both — the wizard will auto-fill them '
        + 'per field on Next using the source you pick. Manual exposes a '
        + 'per-field editor below the toggles.'));

    const soilToggleHost  = h('div', {class: 'step3-toggle-host'});
    const elevToggleHost  = h('div', {class: 'step3-toggle-host'});
    const soilSourceHost  = h('div', {class: 'step3-source-host'});
    const elevSourceHost  = h('div', {class: 'step3-source-host'});

    // Stacked vertically — soil first, elevation second — so each
    // toggle's source dropdown sits directly below its label rather than
    // squeezing into a two-column row.
    detailsBlock.appendChild(h('div', {class: 'wizard-form-group'},
        h('label', {}, 'Soil'),
        soilToggleHost, soilSourceHost,
    ));
    detailsBlock.appendChild(h('div', {class: 'wizard-form-group'},
        h('label', {}, 'Elevation'),
        elevToggleHost, elevSourceHost,
    ));

    // Per-field editor lives inside the same block — toggleable visibility
    // based on whether at least one section is Manual.
    const editorWrap = h('div', {class: 'step3-editor-wrap'});
    const tabHost   = h('div', {class: 'wizard-tselect-host'});
    const fieldHost = h('div', {class: 'step3-field-editor'});
    editorWrap.appendChild(tabHost);
    editorWrap.appendChild(fieldHost);
    detailsBlock.appendChild(editorWrap);

    panel.appendChild(detailsBlock);

    root.appendChild(panel);

    // -----------------------------------------------------------------
    // Local UI state — re-read from the store on every render. Avoids
    // captured-stale-value bugs when the user navigates away and back.
    // -----------------------------------------------------------------
    let activeFieldIdx = state.active_field_idx || 0;
    if (activeFieldIdx >= fields.length) activeFieldIdx = 0;

    // Per-field flag set: contains field ids whose soil picker the user
    // has explicitly re-expanded after a previous selection. Empty by
    // default, so a field with `soil_id` already set renders as a chip.
    const expandedSoilFields = new Set();

    // -----------------------------------------------------------------
    // Reference data
    // -----------------------------------------------------------------
    if (!weatherSourcesCache) {
        try {
            // DSSAT weather inputs require these four variables. data_agent
            // lists every source with its declared variables; we filter here
            // (consumer side) so the requirement stays in the DSSAT app.
            const DSSAT_REQUIRED_VARS = ['tmax', 'tmin', 'rain', 'srad'];
            const r = await refs.weatherSources();
            weatherSourcesCache = (r.sources || []).filter((s) => {
                const kind = s.kind || s.storage_mode;
                if (kind === 'static') return false;
                if (kind === 'combined') return true;  // complete by construction
                const vars = (s.variables || []).map((v) => String(v).toLowerCase());
                return DSSAT_REQUIRED_VARS.every((v) => vars.includes(v));
            });
        } catch (e) { weatherSourcesCache = []; }
    }
    populateWeatherSelect(weatherSel, state.weather_source);
    on(weatherSel, 'change', () =>
        store.setStep('step3', {weather_source: weatherSel.value}));

    // -----------------------------------------------------------------
    // Toggles + per-section source dropdowns
    // -----------------------------------------------------------------
    renderModeToggle(soilToggleHost, {
        current: () => (store.getStep('step3') || {}).soil_mode || 'auto',
        onChange: (mode) => {
            store.setStep('step3', {soil_mode: mode});
            renderSoilSource();
            renderEditor();
        },
    });
    renderModeToggle(elevToggleHost, {
        current: () => (store.getStep('step3') || {}).elevation_mode || 'auto',
        onChange: (mode) => {
            store.setStep('step3', {elevation_mode: mode});
            renderElevSource();
            renderEditor();
        },
    });

    function renderSoilSource() {
        clear(soilSourceHost);
        const cur = (store.getStep('step3') || {});
        if (cur.soil_mode !== 'auto') return;
        const sel = h('select', {class: 'wizard-select'});
        for (const s of SOIL_AUTO_SOURCES) {
            sel.appendChild(h('option', {value: s.value}, s.label));
        }
        sel.value = cur.auto_soil_source || 'auto';
        on(sel, 'change', () =>
            store.setStep('step3', {auto_soil_source: sel.value}));
        const {row, btn, flash} = buildCoverageRow();
        on(btn, 'click', () => runCoverageCheck({
            parameter: 'soil',
            // 'auto' is a wizard alias — the actual probe needs a real
            // raster id. Use the SE-US lookup as the fallback.
            source: sel.value === 'auto'
                ? 'dssat_soil_lookup_v1' : sel.value,
            sourceLabel: sel.options[sel.selectedIndex].text,
            btn,
            flash,
        }));
        soilSourceHost.appendChild(h('div', {style: {marginTop: '0.4rem'}},
            h('label', {class: 'step-description'}, 'Source'),
            sel,
            row,
            flash));
    }

    function renderElevSource() {
        clear(elevSourceHost);
        const cur = (store.getStep('step3') || {});
        if (cur.elevation_mode !== 'auto') return;
        const sel = h('select', {class: 'wizard-select'});
        for (const s of ELEV_AUTO_SOURCES) {
            sel.appendChild(h('option', {value: s.value}, s.label));
        }
        sel.value = cur.auto_elevation_source || 'srtm_v1';
        on(sel, 'change', () =>
            store.setStep('step3', {auto_elevation_source: sel.value}));
        const {row, btn, flash} = buildCoverageRow();
        on(btn, 'click', () => runCoverageCheck({
            parameter: 'elevation',
            source: sel.value || 'srtm_v1',
            sourceLabel: sel.options[sel.selectedIndex].text,
            btn,
            flash,
        }));
        elevSourceHost.appendChild(h('div', {style: {marginTop: '0.4rem'}},
            h('label', {class: 'step-description'}, 'Source'),
            sel,
            row,
            flash));
    }

    /** Build the inline "Check Coverage" button + result flash element
     *  shared by both auto-section panels. */
    function buildCoverageRow() {
        const btn = h('button', {
            type: 'button', class: 'btn btn-sm btn-secondary',
            style: {marginTop: '0.4rem'},
        }, 'Check Coverage');
        const flash = h('div', {
            class: 'wizard-flash wizard-flash-info',
            style: {display: 'none', marginTop: '0.4rem'},
        });
        return {row: btn, btn, flash};
    }

    /** Probe the chosen source at every attached field's (lat, lon) and
     *  surface coverage stats inline. Mirrors the Step 4 MC in-situ
     *  planting Coverage button. */
    async function runCoverageCheck({parameter, source, sourceLabel, btn, flash}) {
        const fieldsList = store.fields || [];
        if (!fieldsList.length) {
            flash.style.display = '';
            flash.className = 'wizard-flash wizard-flash-error';
            flash.textContent = 'No fields attached.';
            return;
        }
        const points = fieldsList
            .filter((f) => f.latitude != null && f.longitude != null)
            .map((f) => [f.latitude, f.longitude]);
        flash.style.display = '';
        flash.className = 'wizard-flash wizard-flash-info';
        flash.textContent = 'Checking coverage…';
        btn.disabled = true;
        try {
            const r = await refs.pointsCoverage({points, parameter, source});
            const total = r.total_points || 0;
            const covered = r.covered || 0;
            const ratio = Math.round((r.coverage_ratio || 0) * 100);
            const ok = covered === total && total > 0;
            flash.className = 'wizard-flash '
                + (ok ? 'wizard-flash-info' : 'wizard-flash-error');
            const tag = sourceLabel || source;
            let msg = `${covered}/${total} field${total === 1 ? '' : 's'} `
                + `covered by ${tag} (${ratio}%)`;
            if (!ok && (r.uncovered || []).length) {
                const ex = r.uncovered[0];
                msg += ` · uncovered example: [${ex[0].toFixed(3)}, ${ex[1].toFixed(3)}]`;
                if (r.uncovered.length > 1) {
                    msg += ` (+${r.uncovered.length - 1} more)`;
                }
            }
            if (!ok) {
                msg += ' — switch to Manual or pick a different source for '
                     + 'the uncovered locations.';
            }
            flash.textContent = msg;
        } catch (e) {
            flash.className = 'wizard-flash wizard-flash-error';
            flash.textContent = 'Coverage check failed: ' + e.message;
        } finally {
            btn.disabled = false;
        }
    }

    renderSoilSource();
    renderElevSource();

    // -----------------------------------------------------------------
    // Per-field editor (tab bar + body)
    // -----------------------------------------------------------------
    function renderEditor() {
        const cur = (store.getStep('step3') || {});
        const anyManual = cur.soil_mode === 'manual'
            || cur.elevation_mode === 'manual';
        editorWrap.style.display = anyManual ? '' : 'none';
        if (!anyManual) return;
        renderTabBar();
        renderField(activeFieldIdx);
    }

    function renderTabBar() {
        const items = (store.fields || []).map((f) => ({id: f.id, name: f.name}));
        renderSelector(tabHost, {
            items,
            activeIdx: activeFieldIdx,
            label: 'Field:',
            onSelect: (i) => {
                activeFieldIdx = i;
                store.setStep('step3', {active_field_idx: i});
                // Re-render the tab bar so the highlight tracks the click,
                // and the body so the right field's data is shown.
                renderTabBar();
                renderField(i);
            },
        });
    }

    async function renderField(idx) {
        clear(fieldHost);
        const f = (store.fields || [])[idx];
        if (!f) return;
        const cur = (store.getStep('step3') || {});

        // Build the SOIL block iff Manual.
        if (cur.soil_mode === 'manual') {
            fieldHost.appendChild(await buildSoilBlock(f));
        }
        // Build the ELEVATION block iff Manual.
        if (cur.elevation_mode === 'manual') {
            fieldHost.appendChild(buildElevBlock(f));
        }
    }

    async function buildSoilBlock(f) {
        const block = h('div', {class: 'step3-field-soil'});
        block.appendChild(h('h4', {}, 'Soil profile'));

        // Lazy-load the soils catalog the first time anyone opens an
        // expanded picker; cached at module scope across fields.
        async function ensureCatalog() {
            if (!soilsCache) {
                try {
                    const r = await catalog.soils();
                    soilsCache = r.profiles || r.soils || [];
                } catch (_e) { soilsCache = []; }
            }
            return soilsCache;
        }

        const body = h('div');
        block.appendChild(body);

        function isCollapsed() {
            return f.soil_id && !expandedSoilFields.has(f.id);
        }

        async function renderInner() {
            clear(body);
            if (isCollapsed()) {
                // Compact "Selected: <soil_id>" chip with a Change button
                // — same pattern as the legacy wizard's selected-crop chip.
                const chip = h('div', {class: 'selected-chip'},
                    h('span', {class: 'selected-chip-label'}, 'Selected:'),
                    h('strong', {}, f.soil_id),
                    h('button', {
                        type: 'button',
                        class: 'selected-chip-btn',
                    }, 'Change'),
                );
                on(chip.querySelector('button'), 'click', () => {
                    expandedSoilFields.add(f.id);
                    renderInner();
                });
                body.appendChild(chip);
                return;
            }

            // Expanded: search + clickable list. Clicking a row commits
            // the selection and collapses back to a chip.
            const search = h('input', {
                type: 'search', class: 'wizard-input',
                placeholder: 'Search soil profiles…',
            });
            const list = h('div', {class: 'wizard-picker-list-inline'});
            body.appendChild(h('div', {class: 'wizard-form-row'}, search));
            body.appendChild(list);

            const cache = await ensureCatalog();

            function rerenderList(query) {
                clear(list);
                const q = (query || '').toLowerCase();
                const matches = (cache || []).filter((s) =>
                    !q || (s.soil_id || '').toLowerCase().includes(q)
                       || (s.name || '').toLowerCase().includes(q));
                if (!matches.length) {
                    list.appendChild(h('div', {class: 'empty-state small'},
                        'No matching soils.'));
                    return;
                }
                for (const s of matches.slice(0, 30)) {
                    const row = h('button', {
                        type: 'button', class: 'wizard-picker-row',
                    });
                    row.innerHTML =
                        `<span class="mono">${escapeHtml(s.soil_id)}</span>`
                        + ` <span class="text-muted">${escapeHtml(s.name || s.classification || '')}</span>`;
                    on(row, 'click', async () => {
                        try {
                            await fieldsAPI.patch(f.id,
                                {soil_id: s.soil_id, inline_soil: null});
                        } catch (e) {
                            window.alert('Save failed: ' + e.message);
                            return;
                        }
                        f.soil_id = s.soil_id;
                        // Selection commits → collapse back to chip.
                        expandedSoilFields.delete(f.id);
                        renderInner();
                    });
                    list.appendChild(row);
                }
            }
            on(search, 'input', () => rerenderList(search.value));
            rerenderList('');
        }

        renderInner();
        return block;
    }

    function buildElevBlock(f) {
        const block = h('div', {class: 'step3-field-elev'});
        block.appendChild(h('h4', {}, 'Elevation'));
        const elevInput = h('input', {
            type: 'number', step: '0.1',
            class: 'wizard-input',
            placeholder: 'meters',
            value: f.elevation != null ? f.elevation : '',
        });
        const autoBtn = h('button', {
            type: 'button', class: 'btn btn-sm btn-secondary',
        }, 'Auto-fill from SRTM');
        block.appendChild(h('div', {class: 'wizard-form-row'},
            h('div', {class: 'wizard-form-group'},
                h('label', {}, 'Elevation (m)'), elevInput),
            h('div', {class: 'wizard-form-group'},
                h('label', {}, ' '), autoBtn),
        ));
        on(elevInput, 'change', async () => {
            const v = parseFloat(elevInput.value);
            await fieldsAPI.patch(f.id,
                {elevation: isNaN(v) ? null : v});
            f.elevation = isNaN(v) ? null : v;
        });
        on(autoBtn, 'click', async () => {
            autoBtn.disabled = true;
            autoBtn.textContent = '…';
            try {
                const r = await refs.elevation(f.latitude, f.longitude);
                if (r.elevation_m != null) {
                    elevInput.value = r.elevation_m;
                    await fieldsAPI.patch(f.id,
                        {elevation: r.elevation_m});
                    f.elevation = r.elevation_m;
                } else {
                    window.alert(
                        'SRTM did not return a value here (outside coverage or NoData).');
                }
            } catch (e) {
                window.alert('Auto-fill failed: ' + e.message);
            }
            autoBtn.disabled = false;
            autoBtn.textContent = 'Auto-fill from SRTM';
        });
        return block;
    }

    renderEditor();
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function populateWeatherSelect(sel, current) {
    clear(sel);
    if (!weatherSourcesCache || !weatherSourcesCache.length) {
        sel.appendChild(h('option', {value: ''},
            'No weather sources available.'));
        return;
    }
    sel.appendChild(h('option', {value: ''}, '— Pick a source —'));
    for (const s of weatherSourcesCache) {
        sel.appendChild(h('option', {value: s.id || s.dataset_subtype || s.name},
            (s.name || s.dataset_name || s.id)
            + ' — '
            + (s.resolution || s.tds_spatial_resolution || '?')));
    }
    if (current) sel.value = current;
}

function renderModeToggle(host, {current, onChange}) {
    clear(host);
    const bar = h('div', {class: 'wizard-mode-toggle'});
    const cur = current();
    const auto = h('button', {
        type: 'button',
        class: 'wizard-mode-btn' + (cur === 'auto' ? ' active' : ''),
    }, 'Auto');
    const manual = h('button', {
        type: 'button',
        class: 'wizard-mode-btn' + (cur === 'manual' ? ' active' : ''),
    }, 'Manual');
    on(auto, 'click', () => {
        if (current() === 'auto') return;
        onChange('auto');
        renderModeToggle(host, {current, onChange});
    });
    on(manual, 'click', () => {
        if (current() === 'manual') return;
        onChange('manual');
        renderModeToggle(host, {current, onChange});
    });
    bar.appendChild(auto);
    bar.appendChild(manual);
    host.appendChild(bar);
}

// ---------------------------------------------------------------------------
// Validate + lock hook
// ---------------------------------------------------------------------------

/**
 * Loading-overlay copy. Step 3's lock hook runs sequentially per field —
 * one weather patch, one in-situ soil lookup (when Auto), and one SRTM
 * elevation lookup (when Auto). For Monte Carlo grids that's N×3 round
 * trips so a 50-point grid means ~150 backend calls.
 */
export function lockProgressLabel(store) {
    const s = store.getStep('step3') || {};
    const n = (store.fields || []).length;
    const auto = [];
    if (s.soil_mode !== 'manual') auto.push('soil');
    if (s.elevation_mode !== 'manual') auto.push('elevation');
    const fieldsCopy = `${n} field${n === 1 ? '' : 's'}`;
    if (!auto.length) {
        return {
            title: `Saving weather source for ${fieldsCopy}`,
            detail: 'Persisting per-field selections.',
        };
    }
    const tasks = auto.join(' and ');
    return {
        title: `Auto-filling ${tasks} for ${fieldsCopy}`,
        detail:
            `Each location is queried independently against the chosen `
            + `source${auto.length > 1 ? 's' : ''}. Larger field sets take `
            + `longer because every lookup is a separate request — switch `
            + `to Manual on Step 3 if you'd rather pick values yourself.`,
    };
}

export function validate(store) {
    const s = store.getStep('step3') || {};
    if (!s.weather_source) {
        return {ok: false, message: 'Pick a weather data source.'};
    }
    if (s.soil_mode === 'manual') {
        for (const f of (store.fields || [])) {
            if (!f.soil_id && !f.has_inline_soil) {
                return {
                    ok: false,
                    message: `Field "${f.name}" has no soil set. `
                        + `Pick one or switch Soil to Auto.`,
                };
            }
        }
    }
    if (s.elevation_mode === 'manual') {
        for (const f of (store.fields || [])) {
            if (f.elevation == null) {
                return {
                    ok: false,
                    message: `Field "${f.name}" has no elevation. `
                        + `Set one or switch Elevation to Auto.`,
                };
            }
        }
    }
    return {ok: true};
}

export function lockSummary(store) {
    const s = store.getStep('step3') || {};
    const w = s.weather_source ? `weather=${s.weather_source}` : 'no weather';
    const sm = s.soil_mode === 'manual' ? 'soil=manual' : 'soil=auto';
    const em = s.elevation_mode === 'manual' ? 'elev=manual' : 'elev=auto';
    return `${w} · ${sm} · ${em}`;
}

/**
 * Lock hook: apply weather to every field, plus per-section auto-fill
 * loops when their toggle is set to Auto.
 */
export async function onLockBeforeNext(store) {
    const s = store.getStep('step3') || {};
    const fieldsList = store.fields || [];

    // Weather source: apply to every field.
    for (const f of fieldsList) {
        try {
            await fieldsAPI.patch(f.id, {weather_source: s.weather_source});
        } catch (e) {
            console.warn('weather patch failed for', f.id, e);
        }
    }

    // Soil auto-fill (if Auto).
    if (s.soil_mode !== 'manual') {
        const source = s.auto_soil_source === 'auto'
            ? 'dssat_soil_lookup_v1'
            : (s.auto_soil_source || 'dssat_soil_lookup_v1');
        for (const f of fieldsList) {
            try {
                const r = await fetch(
                    (window.SUBPATH || '') + '/dssat/api/insitu/preview/',
                    {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '',
                        },
                        body: JSON.stringify({
                            lat: f.latitude, lon: f.longitude,
                            parameter: 'soil', source,
                        }),
                    });
                const j = await r.json();
                if (r.ok && j.dominant && j.dominant.soil_id) {
                    await fieldsAPI.patch(f.id, {soil_id: j.dominant.soil_id});
                }
            } catch (e) {
                console.warn('soil auto-fill failed for', f.id, e);
            }
        }
    }

    // Elevation auto-fill (if Auto).
    if (s.elevation_mode !== 'manual') {
        for (const f of fieldsList) {
            try {
                const r = await refs.elevation(f.latitude, f.longitude);
                if (r.elevation_m != null) {
                    await fieldsAPI.patch(f.id, {elevation: r.elevation_m});
                }
            } catch (e) {
                console.warn('elevation auto-fill failed for', f.id, e);
            }
        }
    }
}
