/**
 * Step 2 (Batch): Custom Points OR Counties by State (mutually exclusive).
 *
 * A top-of-step toggle picks one of two location modes:
 *
 *   1. "Custom Points" — click the map to drop point markers. State
 *      polygons are decorative (hover tooltip only). One DSSAT field per
 *      point.
 *
 *   2. "Counties by State" — click admin1 polygons (or use the search +
 *      Add bar) to add states. On Lock, each chosen state expands into
 *      its admin2 (county) centroids — one DSSAT field per centroid.
 *      The live county count is surfaced so the user knows what they're
 *      committing to before they hit Next.
 *
 * The two modes don't combine: switching between them clears the inactive
 * mode's selections, since their lock behaviours produce very different
 * field counts and you almost never want both.
 *
 * 99-treatment cap is enforced at lock time with a confirm prompt when
 * the expansion would exceed.
 */

import {h, clear, on, escapeHtml} from '../dom.js';
import {fields as fieldsAPI, refs} from '../api.js';
import {createWizardMap} from '../map.js';
import {reverseGeocode} from '../geocode.js';

const MAP_HEIGHT = '380px';
const HIGHLIGHT_STYLE = {
    color: '#f59e0b', weight: 3, opacity: 1,
    fillColor: 'transparent', fillOpacity: 0,
};

let mapApi = null;
let pointMarkers = [];
let selectedAdminLayers = {};   // state name -> Leaflet layer
let allUsStates = [];
let countyCountCache = {};      // state name -> int (lazily filled)

export async function render(root, store) {
    clear(root);
    const state = store.getStep('step2') || {};
    if (!state.batch_mode) {
        // Default mode persisted on first render so subsequent reads see it.
        store.setStep('step2', {batch_mode: 'points'});
    }
    const mode = (store.getStep('step2') || {}).batch_mode || 'points';

    const panel = h('div', {class: 'wizard-step-panel'},
        h('h2', {class: 'step-title'}, 'Batch Locations'),
        h('p', {class: 'step-description'},
          'Pick one location-input mode for the batch. Switching modes '
          + 'clears the other one\'s selections.'),
    );

    // ---- Mode toggle ----
    const toggleBar = h('div', {class: 'wizard-mode-toggle'});
    const btnPoints = h('button', {
        type: 'button',
        class: 'wizard-mode-btn' + (mode === 'points' ? ' active' : ''),
        dataset: {mode: 'points'},
    }, 'Custom Points');
    const btnStates = h('button', {
        type: 'button',
        class: 'wizard-mode-btn' + (mode === 'states' ? ' active' : ''),
        dataset: {mode: 'states'},
    }, 'Counties by State');
    toggleBar.appendChild(btnPoints);
    toggleBar.appendChild(btnStates);
    panel.appendChild(toggleBar);

    // ---- States block (search + Add + chips + live count) ----
    const statesBlock = h('div', {
        class: 'wizard-form-block',
        style: {display: mode === 'states' ? '' : 'none'},
    });
    const stateInput = h('input', {
        type: 'text',
        class: 'wizard-input',
        placeholder: 'Type a state name…',
        autocomplete: 'off',
    });
    const addBtn = h('button', {
        type: 'button', class: 'btn btn-sm btn-secondary',
    }, 'Add');
    const stateRowWrap = h('div', {class: 'wizard-state-search-wrap'},
        h('div', {class: 'wizard-form-row'},
            h('div', {class: 'wizard-form-group', style: {flex: '1'}},
              stateInput),
            h('div', {class: 'wizard-form-group'}, addBtn),
        ),
    );
    const dropdown = h('div', {
        class: 'wizard-state-dropdown',
        style: {display: 'none'},
    });
    stateRowWrap.appendChild(dropdown);
    statesBlock.appendChild(stateRowWrap);

    const stateChips = h('div', {class: 'wizard-state-chips'});
    statesBlock.appendChild(stateChips);

    const countNote = h('div', {class: 'wizard-state-count step-description'});
    statesBlock.appendChild(countNote);

    panel.appendChild(statesBlock);

    // ---- Map ----
    const mapHost = h('div', {id: 'wizard-step2-batch-map'});
    panel.appendChild(mapHost);

    // ---- Points block ----
    const pointsBlock = h('div', {
        class: 'wizard-form-block',
        style: {display: mode === 'points' ? '' : 'none'},
    });
    const pointsHeader = h('h3', {class: 'wizard-form-subhead'}, 'Points');
    pointsBlock.appendChild(pointsHeader);
    pointsBlock.appendChild(h('p', {class: 'step-description'},
        'Click anywhere on the map to drop a point. Click a row below or '
        + 'its marker to remove.'));
    const pointsList = h('div', {class: 'wizard-points-list'});
    pointsBlock.appendChild(pointsList);
    panel.appendChild(pointsBlock);

    root.appendChild(panel);

    // ---- Map init ----
    mapApi = createWizardMap(mapHost, {
        height: MAP_HEIGHT,
        center: [38.0, -97.0],
        zoom: 4,
        adminLevel: 'admin1',
        showLevelSelect: false,
    });
    pointMarkers = [];
    selectedAdminLayers = {};
    for (const p of (state.points || [])) addPointMarker(p);

    // The map fires both adminClick and click for any click that lands on
    // a polygon. We gate by mode: in points mode, clicks always drop a
    // point (admin polygons are decorative); in states mode, clicks on a
    // polygon toggle the state and the map's own click handler is a
    // no-op. Stops the two from fighting.
    let suppressNextMapClick = false;

    mapApi.on('click', (latlng) => {
        const cur = (store.getStep('step2') || {});
        if ((cur.batch_mode || 'points') !== 'points') return;
        if (suppressNextMapClick) {
            suppressNextMapClick = false;
            return;
        }
        const points = cur.points || [];
        const idx = points.length;
        const newPoint = {
            lat: +latlng.lat.toFixed(4),
            lon: +latlng.lng.toFixed(4),
            label: `Point ${idx + 1}`,  // placeholder; geocode replaces below
        };
        const next = [...points, newPoint];
        store.setStep('step2', {points: next});
        addPointMarker(newPoint);
        rerenderPoints();

        // Async-rename via Photon reverse geocode. Cached, so re-clicking
        // a nearby spot doesn't fire a second request.
        reverseGeocode(newPoint.lat, newPoint.lon).then((label) => {
            if (!label) return;
            const list = (store.getStep('step2') || {}).points || [];
            // Re-locate the row by coords (idx may have shifted if the
            // user added/removed points while geocoding was in flight).
            const i = list.findIndex((p) =>
                Math.abs(p.lat - newPoint.lat) < 1e-6
                && Math.abs(p.lon - newPoint.lon) < 1e-6);
            if (i < 0) return;
            // Don't clobber a user-edited label.
            if (list[i].label && list[i].label !== `Point ${i + 1}`) return;
            const updated = list.slice();
            updated[i] = {...updated[i], label};
            store.setStep('step2', {points: updated});
            rerenderPoints();
        });
    });

    mapApi.on('adminClick', ({feature, layer, level}) => {
        const cur = (store.getStep('step2') || {});
        if ((cur.batch_mode || 'points') !== 'states') return;
        if (level !== 'admin1') return;
        const props = feature.properties || {};
        const name = props.admin1;
        if (!name) return;
        suppressNextMapClick = true;
        setTimeout(() => { suppressNextMapClick = false; }, 50);
        toggleState(name, layer);
    });

    // Re-apply highlight to any pre-selected states once the overlay
    // streams in (resume case).
    const reapplyTimer = setInterval(() => {
        if (!mapApi || !mapApi.adminLayer) return;
        const s = (store.getStep('step2') || {}).states || [];
        if (!s.length) return;
        let allFound = true;
        for (const stateName of s) {
            if (!selectedAdminLayers[stateName]) {
                const lyr = findStateLayer(stateName);
                if (lyr) {
                    lyr.setStyle(HIGHLIGHT_STYLE);
                    selectedAdminLayers[stateName] = lyr;
                } else {
                    allFound = false;
                }
            }
        }
        if (allFound) clearInterval(reapplyTimer);
    }, 400);

    mapApi.invalidateSize();

    // ---- Pre-load the US admin1 catalog (for typeahead + Add) ----
    refs.adminUnits('admin1', 'United States')
        .then((r) => { allUsStates = r.units || []; })
        .catch(() => { allUsStates = []; });

    // ---- Wire mode toggle ----
    on(btnPoints, 'click', () => switchMode('points'));
    on(btnStates, 'click', () => switchMode('states'));

    function switchMode(next) {
        const cur = (store.getStep('step2') || {});
        if ((cur.batch_mode || 'points') === next) return;
        // Confirm the implicit clear when switching modes if the *other*
        // mode's selections are non-trivial — otherwise users lose data
        // they didn't realise was tied to the mode.
        const willLose = next === 'points'
            ? (cur.states && cur.states.length > 0)
            : (cur.points && cur.points.length > 0);
        if (willLose && !window.confirm(
            'Switching modes clears the other mode\'s selections. Continue?'
        )) return;
        // Wipe the inactive mode's data + UI artifacts.
        const patch = {batch_mode: next};
        if (next === 'points') {
            patch.states = [];
            // Reset any highlighted polygons.
            for (const name of Object.keys(selectedAdminLayers)) {
                const lyr = selectedAdminLayers[name];
                if (lyr && mapApi && mapApi.adminLayer) {
                    mapApi.adminLayer.resetStyle(lyr);
                }
            }
            selectedAdminLayers = {};
        } else {
            patch.points = [];
            for (const m of pointMarkers) mapApi.map.removeLayer(m);
            pointMarkers = [];
        }
        store.setStep('step2', patch);

        // Update toggle button state + section visibility.
        btnPoints.classList.toggle('active', next === 'points');
        btnStates.classList.toggle('active', next === 'states');
        pointsBlock.style.display = next === 'points' ? '' : 'none';
        statesBlock.style.display = next === 'states' ? '' : 'none';
        rerenderPoints();
        rerenderChips();
        renderCountNote();
    }

    // ---- Wire Add button + Enter ----
    on(addBtn, 'click', () => {
        if (addTypedState(stateInput.value)) {
            stateInput.value = '';
            hideDropdown();
        }
    });
    on(stateInput, 'keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            if (addTypedState(stateInput.value)) {
                stateInput.value = '';
                hideDropdown();
            }
        } else if (e.key === 'Escape') {
            hideDropdown();
        }
    });
    on(stateInput, 'input', () => renderDropdown(stateInput.value));
    on(stateInput, 'focus', () => renderDropdown(stateInput.value));
    on(document, 'click', (e) => {
        // Close the dropdown when clicking anywhere outside the search row.
        if (!stateRowWrap.contains(e.target)) hideDropdown();
    });

    // ---- Initial UI state ----
    rerenderPoints();
    rerenderChips();
    renderCountNote();

    // -----------------------------------------------------------------
    // Closures — read current store state on each call rather than
    // capturing `state` / `mode` from the outer render so they react to
    // mode-switches without a full re-render.
    // -----------------------------------------------------------------

    function rerenderPoints() {
        const cur = (store.getStep('step2') || {}).points || [];
        pointsHeader.textContent = `Points (${cur.length})`;
        clear(pointsList);
        if (!cur.length) {
            pointsList.appendChild(h('div', {class: 'empty-state small'},
                'No points yet. Click the map to add one.'));
            return;
        }
        cur.forEach((p, idx) => {
            const row = h('div', {class: 'wizard-points-row'},
                h('span', {class: 'mono'},
                    `${p.lat.toFixed(3)}, ${p.lon.toFixed(3)}`),
                h('span', {style: {flex: 1}}, p.label || ''),
                h('button', {
                    type: 'button',
                    class: 'btn btn-sm btn-danger',
                }, 'Remove'),
            );
            on(row.querySelector('button'), 'click', () => {
                const list = (store.getStep('step2') || {}).points || [];
                const next = list.filter((_, i) => i !== idx);
                store.setStep('step2', {points: next});
                refreshAllMarkers(next);
                rerenderPoints();
            });
            pointsList.appendChild(row);
        });
    }

    function rerenderChips() {
        clear(stateChips);
        const cur = (store.getStep('step2') || {}).states || [];
        if (!cur.length) {
            stateChips.appendChild(h('span', {class: 'step-description'},
                'No states selected yet — click them on the map or use Add.'));
            return;
        }
        for (const name of cur) {
            const chip = h('span', {class: 'wizard-state-chip'},
                escapeHtml(name),
                h('button', {
                    type: 'button', class: 'wizard-state-chip-x',
                    title: 'Remove',
                }, '×'),
            );
            on(chip.querySelector('button'), 'click', () => {
                toggleState(name, selectedAdminLayers[name]);
            });
            stateChips.appendChild(chip);
        }
    }

    async function renderCountNote() {
        const cur = (store.getStep('step2') || {});
        if ((cur.batch_mode || 'points') !== 'states') {
            countNote.textContent = '';
            return;
        }
        const states = cur.states || [];
        if (!states.length) {
            countNote.textContent =
                'Each selected state generates one DSSAT field per county '
                + 'centroid. The 99-field cap is enforced before submit.';
            return;
        }
        countNote.textContent = `Counting counties for ${states.length} `
            + `state${states.length === 1 ? '' : 's'}…`;
        try {
            let total = 0;
            for (const sn of states) {
                if (countyCountCache[sn] == null) {
                    const r = await refs.adminUnits('admin2', sn);
                    countyCountCache[sn] = (r.units || []).length;
                }
                total += countyCountCache[sn];
            }
            const overCap = total > 99;
            countNote.textContent =
                `Counties by State will generate ~${total} DSSAT field`
                + `${total === 1 ? '' : 's'} (one per county centroid)`
                + (overCap ? ` — exceeds the 99-field cap; only the first `
                    + `99 will be kept.` : '.');
            countNote.className = 'wizard-state-count step-description'
                + (overCap ? ' wizard-flash-error' : '');
        } catch (e) {
            countNote.textContent =
                `Counted partially: ${e.message || 'admin2 lookup failed'}.`;
        }
    }

    function toggleState(name, layer) {
        const curSet = new Set((store.getStep('step2') || {}).states || []);
        if (curSet.has(name)) {
            curSet.delete(name);
            const lyr = layer || selectedAdminLayers[name];
            if (lyr && mapApi.adminLayer) {
                mapApi.adminLayer.resetStyle(lyr);
            }
            delete selectedAdminLayers[name];
        } else {
            curSet.add(name);
            if (layer) {
                layer.setStyle(HIGHLIGHT_STYLE);
                layer.bringToFront();
                selectedAdminLayers[name] = layer;
            } else {
                const auto = findStateLayer(name);
                if (auto) {
                    auto.setStyle(HIGHLIGHT_STYLE);
                    auto.bringToFront();
                    selectedAdminLayers[name] = auto;
                }
            }
        }
        store.setStep('step2', {states: Array.from(curSet)});
        rerenderChips();
        renderCountNote();
    }

    function addTypedState(raw) {
        raw = (raw || '').trim();
        if (!raw) return false;
        const lower = raw.toLowerCase();
        let match = allUsStates.find((s) => s.toLowerCase() === lower);
        if (!match) match = allUsStates.find(
            (s) => s.toLowerCase().startsWith(lower));
        if (!match) {
            window.alert(`No US state named "${raw}".`);
            return false;
        }
        const cur = new Set((store.getStep('step2') || {}).states || []);
        if (!cur.has(match)) toggleState(match, null);
        return true;
    }

    function renderDropdown(query) {
        clear(dropdown);
        const q = (query || '').trim().toLowerCase();
        if (!q || !allUsStates.length) {
            hideDropdown();
            return;
        }
        // Prefix matches first, then substring.
        const prefix = allUsStates.filter(
            (s) => s.toLowerCase().startsWith(q));
        const subset = allUsStates.filter(
            (s) => !s.toLowerCase().startsWith(q)
                && s.toLowerCase().includes(q));
        const matches = prefix.concat(subset).slice(0, 8);
        const selectedSet = new Set((store.getStep('step2') || {}).states || []);
        if (!matches.length) {
            hideDropdown();
            return;
        }
        for (const m of matches) {
            const row = h('button', {
                type: 'button',
                class: 'wizard-state-dropdown-row'
                       + (selectedSet.has(m) ? ' selected' : ''),
            }, escapeHtml(m),
               selectedSet.has(m)
                   ? h('span', {class: 'wizard-state-dropdown-tag'}, '✓ added')
                   : null,
            );
            on(row, 'click', () => {
                addTypedState(m);
                stateInput.value = '';
                hideDropdown();
                stateInput.focus();
            });
            dropdown.appendChild(row);
        }
        dropdown.style.display = '';
    }

    function hideDropdown() {
        dropdown.style.display = 'none';
        clear(dropdown);
    }

    function findStateLayer(name) {
        if (!mapApi || !mapApi.adminLayer) return null;
        let found = null;
        mapApi.adminLayer.eachLayer((lyr) => {
            const props = lyr.feature && lyr.feature.properties;
            if (props && props.admin1 === name) found = lyr;
        });
        return found;
    }

    function addPointMarker(p) {
        const m = L.marker([p.lat, p.lon]).addTo(mapApi.map);
        m.on('click', () => {
            const cur = (store.getStep('step2') || {}).points || [];
            const idx = cur.findIndex((x) =>
                Math.abs(x.lat - p.lat) < 1e-6 && Math.abs(x.lon - p.lon) < 1e-6);
            if (idx >= 0) {
                const next = cur.filter((_, i) => i !== idx);
                store.setStep('step2', {points: next});
                refreshAllMarkers(next);
                rerenderPoints();
            }
        });
        pointMarkers.push(m);
    }
    function refreshAllMarkers(points) {
        for (const m of pointMarkers) mapApi.map.removeLayer(m);
        pointMarkers = [];
        for (const p of points) addPointMarker(p);
    }
}

/**
 * Loading-overlay copy shown while the lock hook runs. Batch lock can
 * be very slow because it expands states to per-county centroids
 * (one admin lookup + one centroid lookup per county) and then creates
 * a Field row per result.
 */
export function lockProgressLabel(store) {
    const s = store.getStep('step2') || {};
    const mode = s.batch_mode || 'points';
    if (mode === 'points') {
        const n = (s.points || []).length;
        return {
            title: `Saving ${n} batch location${n === 1 ? '' : 's'}`,
            detail:
                `Creating one Field record per point. Each row gets `
                + `weather + soil + elevation defaults that you'll refine `
                + `on the next step.`,
        };
    }
    const states = (s.states || []).length;
    return {
        title: `Expanding ${states} state${states === 1 ? '' : 's'} to counties`,
        detail:
            `Querying admin boundaries to fetch every admin2 county inside `
            + `each selected state, then resolving each county's centroid `
            + `and creating one Field record per centroid. This is the `
            + `slowest path — counts can run into the hundreds.`,
    };
}

export function validate(store) {
    const s = store.getStep('step2') || {};
    const mode = s.batch_mode || 'points';
    if (mode === 'points') {
        if (!(s.points || []).length) {
            return {ok: false, message: 'Add at least one point.'};
        }
    } else {
        if (!(s.states || []).length) {
            return {ok: false, message: 'Pick at least one state.'};
        }
    }
    return {ok: true};
}

export function lockSummary(store) {
    const s = store.getStep('step2') || {};
    const mode = s.batch_mode || 'points';
    if (mode === 'points') {
        const n = (s.points || []).length;
        return n ? `${n} custom point${n === 1 ? '' : 's'}` : 'No points.';
    }
    const n = (s.states || []).length;
    return n
        ? `Counties by State: ${n} state${n === 1 ? '' : 's'} → '
          + 'expanded to centroids on Lock`
        : 'No states.';
}

/**
 * Lock hook: materialise the active mode's selections into Field rows.
 *
 *   - points  -> one Field per dropped point.
 *   - states  -> one Field per admin2 centroid in every chosen state,
 *                capped at 99 with a confirm.
 */
export async function onLockBeforeNext(store) {
    const s = store.getStep('step2') || {};
    const mode = s.batch_mode || 'points';

    let allFields = [];
    if (mode === 'points') {
        allFields = (s.points || []).map((p, i) => ({
            name: p.label || `Point ${i + 1}`,
            latitude: p.lat,
            longitude: p.lon,
            location_label: p.label || '',
            weather_source: 'nasa_power',
        }));
    } else {
        for (const sn of (s.states || [])) {
            try {
                const r = await refs.adminUnits('admin2', sn);
                const counties = r.units || [];
                for (const county of counties) {
                    const c = await refs.adminCentroid('admin2', county);
                    allFields.push({
                        name: `${county}, ${sn}`,
                        latitude: c.lat,
                        longitude: c.lon,
                        location_label: `${county}, ${sn}`,
                        is_admin_centroid: true,
                        admin_unit: county,
                        weather_source: 'nasa_power',
                    });
                }
            } catch (e) {
                console.warn(`State expansion failed for ${sn}:`, e);
            }
        }
    }

    if (allFields.length > 99) {
        if (!window.confirm(
            `Your selection produces ${allFields.length} fields. Only the `
            + `first 99 will be kept (DSSAT batch limit). Continue?`)) {
            throw new Error('Cancelled — narrow the selection.');
        }
    }
    const trimmed = allFields.slice(0, 99);

    const created = [];
    const failures = [];
    for (let i = 0; i < trimmed.length; i++) {
        try {
            const r = await fieldsAPI.create(trimmed[i]);
            created.push({field_id: r.field.id, ordering: i});
        } catch (e) {
            failures.push(`${trimmed[i].name}: ${e.message || e}`);
            console.warn(`Field create failed for ${trimmed[i].name}:`, e);
        }
    }
    if (!created.length) {
        // Don't silently advance with zero fields — the user would land
        // on Step 3 with a misleading "No fields attached" message.
        throw new Error(
            `All ${trimmed.length} field creates failed. First error: `
            + (failures[0] || 'unknown')
        );
    }
    if (failures.length) {
        // Best-effort: tell the user some made it, some didn't. We still
        // advance because partial coverage is usable.
        console.warn(`${failures.length} of ${trimmed.length} failed:`, failures);
    }
    await store.setFields(created);
}
