/**
 * Step 2 (Monte Carlo): center+radius / bbox / admin + grid_spacing.
 *
 * UI:
 *   - Three-mode toggle: circle / bbox / admin.
 *   - Shared sampling-strategy + density knob (grid_spacing for systematic,
 *     n_points for random) above the mode-specific panel.
 *   - Map adapts to the active mode:
 *       circle  -> click to set center, circle overlay reflects radius
 *       bbox    -> Leaflet.Draw rectangle, four bbox inputs sync both ways
 *       admin   -> admin polygons are clickable; the chosen unit is
 *                  highlighted with the selected style.
 *   - Live preview overlay: every state change debounce-fires a call to
 *     /dssat/api/mc/preview-grid/ and renders the returned points as
 *     small circle markers, so the user sees exactly what the run will
 *     sample.
 *
 * On Lock the wizard materialises the grid: one ``Field`` row per
 * generated point. Step 3 then renders one tab per field in its per-field
 * editor (or, if Auto soil/elev is chosen for everything, runs the
 * auto-fill loop over each). The MC run pipeline consumes the explicit
 * Field set rather than regenerating the grid at run time, so per-field
 * soil/elevation chosen in Step 3 actually flows through to DSSAT.
 */

import {h, clear, on} from '../dom.js';
import {fields as fieldsAPI, refs} from '../api.js';
import {createWizardMap, placeMarker, placeCircle, placeRectangle} from '../map.js';
import {reverseGeocode} from '../geocode.js';

const MAP_HEIGHT = '320px';
// Only density / mode knobs get defaults — geometry (center, bbox,
// admin_name) stays empty so the user has to click the map (or type) to
// commit a region. The preview stays blank until they do.
const DEFAULTS = {
    spatial_mode: 'circle',
    grid_spacing: 0.1,
    radius_km: 25,
    n_points: 30,
    sampling_strategy: 'systematic',
};

let mapApi = null;
let marker = null;
let circle = null;
let rect = null;
let selectedAdmin = null;
let previewLayer = null;       // L.layerGroup of preview-point markers
let previewTimer = null;       // debounce timer for preview regeneration
// Module-scope handle to render()'s closure so the buildCircle/buildBbox/
// buildAdmin save() callbacks (which sit at module scope) can refresh the
// preview when the user types into lat/lon/radius/bbox inputs.
let triggerPreview = () => {};
// Latest preview verdict so the synchronous validate() call below can gate
// Next without re-fetching. Updated on every refreshPreview success.
let lastPreviewCount = null;
let lastPreviewTruncated = false;

export async function render(root, store) {
    clear(root);
    // Persist any missing defaults so a user who clicks Next immediately
    // doesn't get blocked by a "pick a spatial mode" validator on
    // already-defaulted-to-visible-values. Anything the user later edits
    // overwrites these.
    const cur = store.getStep('step2') || {};
    const seedPatch = {};
    for (const [key, val] of Object.entries(DEFAULTS)) {
        if (cur[key] == null) seedPatch[key] = val;
    }
    if (Object.keys(seedPatch).length) {
        store.setStep('step2', seedPatch);
    }
    const state = store.getStep('step2') || {};
    const mode = state.spatial_mode || DEFAULTS.spatial_mode;

    const panel = h('div', {class: 'wizard-step-panel'},
        h('h2', {class: 'step-title'}, 'Monte Carlo Spatial Grid'),
        h('p', {class: 'step-description'},
          'Pick the geometry, then watch the map preview the points the '
          + 'run will sample. The preview regenerates as you adjust '
          + 'controls; switching modes clears the previous overlay.'),
    );

    // Mode toggle (button bar — same shape as the Custom Points / Counties
    // by State toggle on batch).
    const modeBar = h('div', {class: 'wizard-mode-toggle'});
    const modeButtons = {};
    const MODES = [
        {key: 'circle', label: 'Center + Radius'},
        {key: 'bbox',   label: 'Bounding Box'},
        {key: 'admin',  label: 'Admin Boundary'},
    ];
    for (const m of MODES) {
        const btn = h('button', {
            type: 'button',
            class: 'wizard-mode-btn' + (mode === m.key ? ' active' : ''),
            dataset: {mode: m.key},
        }, m.label);
        modeButtons[m.key] = btn;
        on(btn, 'click', () => switchMode(m.key));
        modeBar.appendChild(btn);
    }
    panel.appendChild(h('div', {class: 'wizard-form-group'},
        h('label', {}, 'Spatial mode'),
        modeBar,
    ));

    // Sampling strategy gates which density knob the user sees:
    //   systematic -> grid_spacing (deterministic lattice; N falls out of
    //                 the geometry; capped at 99 by submit-time validation)
    //   random     -> n_points (uniform random samples drawn directly
    //                 inside the geometry; capped at 99 by the input itself)
    const sampling = h('select', {class: 'wizard-select', style: {maxWidth: '12rem'}},
        h('option', {value: 'systematic'}, 'Systematic (grid lattice)'),
        h('option', {value: 'random'}, 'Random (uniform sampling)'),
    );
    sampling.value = state.sampling_strategy || 'systematic';

    const gridSpacing = h('input', {
        type: 'number', step: '0.01', min: '0.01', max: '1.0',
        class: 'wizard-input',
        value: state.grid_spacing != null ? state.grid_spacing : 0.1,
        style: {maxWidth: '8rem'},
    });
    const nPoints = h('input', {
        type: 'number', step: '1', min: '1', max: '99',
        class: 'wizard-input',
        value: state.n_points != null
            ? state.n_points
            : (state.nens != null ? state.nens : 30),
        style: {maxWidth: '8rem'},
    });

    const gridSpacingGroup = h('div', {class: 'wizard-form-group'},
        h('label', {}, 'Grid spacing (degrees)'),
        gridSpacing,
        h('div', {class: 'step-description', style: {marginTop: '0.25rem'}},
          'Deterministic lattice density. Total points = whatever the '
          + 'geometry contains; capped at 99.'));
    const nPointsGroup = h('div', {class: 'wizard-form-group'},
        h('label', {}, 'Number of points (≤ 99)'),
        nPoints,
        h('div', {class: 'step-description', style: {marginTop: '0.25rem'}},
          'Uniform random samples drawn inside the geometry. Each draw '
          + 'becomes one DSSAT treatment.'));
    const samplingGroup = h('div', {class: 'wizard-form-group'},
        h('label', {}, 'Sampling strategy'), sampling);

    const sampleRow = h('div', {class: 'wizard-form-row-3'},
        samplingGroup, gridSpacingGroup, nPointsGroup);
    panel.appendChild(sampleRow);

    function applyStrategyVisibility() {
        const strat = sampling.value;
        gridSpacingGroup.style.display = strat === 'systematic' ? '' : 'none';
        nPointsGroup.style.display = strat === 'random' ? '' : 'none';
    }
    applyStrategyVisibility();

    // Mode-specific config containers
    const circleEl = h('div', {class: 'wizard-form-block', style: {display: mode === 'circle' ? '' : 'none'}});
    const bboxEl = h('div', {class: 'wizard-form-block', style: {display: mode === 'bbox' ? '' : 'none'}});
    const adminEl = h('div', {class: 'wizard-form-block', style: {display: mode === 'admin' ? '' : 'none'}});
    panel.appendChild(circleEl);
    panel.appendChild(bboxEl);
    panel.appendChild(adminEl);

    // Map
    const mapHost = h('div', {id: 'wizard-step2-mc-map'});
    panel.appendChild(mapHost);
    const previewStatus = h('div', {
        class: 'wizard-flash wizard-flash-info',
        style: {marginTop: '0.5rem', display: 'none'},
    });
    panel.appendChild(previewStatus);
    root.appendChild(panel);

    // Build sub-panel inputs
    buildCircle(circleEl, store);
    buildBbox(bboxEl, store);
    buildAdmin(adminEl, store);

    // Bindings: each control change persists + triggers a debounced
    // preview regeneration so the user sees the effect on the map.
    on(gridSpacing, 'input', () => {
        store.setStep('step2', {grid_spacing: parseFloat(gridSpacing.value) || 0.1});
        schedulePreview();
    });
    on(nPoints, 'input', () => {
        store.setStep('step2', {n_points: parseInt(nPoints.value, 10) || 30});
        schedulePreview();
    });
    on(sampling, 'change', () => {
        store.setStep('step2', {sampling_strategy: sampling.value});
        applyStrategyVisibility();
        schedulePreview();
    });

    function switchMode(next) {
        const cur = store.getStep('step2') || {};
        const prev = cur.spatial_mode;
        if (prev === next) return;

        // Clear the previous mode's geometry from the store. Density knobs
        // (grid_spacing, n_points, sampling_strategy) and per-mode
        // preferences (radius_km, admin_level) survive intentionally —
        // those track the user's preferences across mode switches.
        const update = {spatial_mode: next};
        if (prev === 'circle') update.center = null;
        else if (prev === 'bbox') update.bbox = null;
        else if (prev === 'admin') {
            update.admin_name = null;
            update.admin_id = null;
        }
        store.setStep('step2', update);

        // Reset the previous mode's input fields to match the cleared store.
        if (prev === 'circle') {
            const inputs = circleEl.querySelectorAll('input.mc-circle');
            if (inputs.length >= 2) { inputs[0].value = ''; inputs[1].value = ''; }
        } else if (prev === 'bbox') {
            for (const inp of bboxEl.querySelectorAll('input.mc-bbox')) {
                inp.value = '';
            }
        } else if (prev === 'admin') {
            const label = adminEl.querySelector('.admin-selected-label');
            if (label) {
                label.textContent =
                    'Click an admin polygon on the map to select it.';
            }
        }

        // Update toggle button highlights.
        for (const [key, btn] of Object.entries(modeButtons)) {
            btn.classList.toggle('active', key === next);
        }
        circleEl.style.display = next === 'circle' ? '' : 'none';
        bboxEl.style.display = next === 'bbox' ? '' : 'none';
        adminEl.style.display = next === 'admin' ? '' : 'none';
        // Strip overlays + preview points from the previous mode so the
        // user doesn't see stale highlights bleeding through.
        clearAllOverlays();

        // On entering admin mode, sync the map's vector layer to the
        // panel's admin_level dropdown — without this the two can drift
        // (e.g. a previously-loaded admin1 layer left over from earlier
        // while the dropdown still says admin2).
        if (next === 'admin' && mapApi) {
            const wanted = (store.getStep('step2') || {}).admin_level
                || 'admin1';
            if (mapApi.currentAdminLevel !== wanted) {
                mapApi.loadAdmins(wanted);
            }
        }

        switchMapMode(next);
        schedulePreview();
    }

    // Initialise map
    mapApi = createWizardMap(mapHost, {
        height: MAP_HEIGHT,
        center: [state.center?.lat || 33.0, state.center?.lon || -86.5],
        zoom: 5,
        adminLevel: 'admin1',
    });
    marker = circle = rect = selectedAdmin = null;

    mapApi.on('click', (latlng) => {
        const cur = store.getStep('step2') || {};
        if (cur.spatial_mode === 'circle' || !cur.spatial_mode) {
            const center = {lat: +latlng.lat.toFixed(4), lon: +latlng.lng.toFixed(4)};
            store.setStep('step2', {center});
            applyCircleOverlay(center, cur.radius_km || state.radius_km || 25);
            const inputs = circleEl.querySelectorAll('input.mc-circle');
            inputs[0].value = center.lat;
            inputs[1].value = center.lon;
            schedulePreview();
        }
    });

    mapApi.on('rectangle', (bounds) => {
        const v = {
            min_lat: +bounds.getSouth().toFixed(4),
            max_lat: +bounds.getNorth().toFixed(4),
            min_lon: +bounds.getWest().toFixed(4),
            max_lon: +bounds.getEast().toFixed(4),
        };
        store.setStep('step2', {bbox: v});
        const fields = bboxEl.querySelectorAll('input.mc-bbox');
        fields[0].value = v.min_lat; fields[1].value = v.max_lat;
        fields[2].value = v.min_lon; fields[3].value = v.max_lon;
        rect = placeRectangle(mapApi.map, rect, [[v.min_lat, v.min_lon], [v.max_lat, v.max_lon]]);
        schedulePreview();
    });

    mapApi.on('adminClick', ({feature, layer}) => {
        const cur = store.getStep('step2') || {};
        if (cur.spatial_mode !== 'admin') return;
        mapApi.setSelectedAdmin(layer);
        selectedAdmin = layer;
        const props = feature.properties || {};
        const level = mapApi.currentAdminLevel;
        const name = props[level] || props.admin1 || props.country || '';
        // Capture parent context so the backend can disambiguate names
        // that aren't unique on their own (e.g. "Jefferson" county exists
        // in 25+ US states).
        store.setStep('step2', {
            admin_level: level,
            admin_name: name,
            admin_country: props.country || null,
            admin_parent_admin1: level === 'admin2'
                ? (props.admin1 || null) : null,
        });
        adminEl.querySelector('.admin-selected-label').textContent =
            'Selected: ' + [props.country, props.admin1, props.admin2].filter(Boolean).join(' > ');
        schedulePreview();
    });

    switchMapMode(mode);
    mapApi.invalidateSize();
    // No geometry seeded → preview will bail until the user clicks the
    // map (or types lat/lon / bbox values directly).
    schedulePreview();

    // Live preview helpers --------------------------------------------------

    function schedulePreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(refreshPreview, 250);
    }
    triggerPreview = schedulePreview;

    async function refreshPreview() {
        const cur = store.getStep('step2') || {};
        if (!cur.spatial_mode) return;
        // Skip preview when the geometry isn't ready yet (avoids the
        // backend yelling about missing center / bbox / admin_name).
        if (cur.spatial_mode === 'circle'
            && (!cur.center || cur.center.lat == null || cur.center.lon == null)) return;
        if (cur.spatial_mode === 'bbox') {
            const b = cur.bbox || {};
            if ([b.min_lat, b.max_lat, b.min_lon, b.max_lon].some((v) => v == null)) return;
        }
        if (cur.spatial_mode === 'admin' && !cur.admin_name) return;
        try {
            const r = await refs.mcPreviewGrid({
                spatial_mode: cur.spatial_mode,
                center: cur.center,
                radius_km: cur.radius_km,
                bbox: cur.bbox,
                admin_name: cur.admin_name,
                admin_level: cur.admin_level,
                admin_country: cur.admin_country,
                admin_parent_admin1: cur.admin_parent_admin1,
                grid_spacing: cur.grid_spacing,
                n_points: cur.n_points,
                sampling_strategy: cur.sampling_strategy,
            });
            renderPreviewMarkers(r.points || []);
            updatePreviewStatus(r);
            lastPreviewCount = (r.points || []).length;
            lastPreviewTruncated = !!r.truncated;
        } catch (e) {
            console.warn('MC preview failed:', e.message);
            renderPreviewMarkers([]);
            updatePreviewStatus({points: [], error: e.message});
            lastPreviewCount = null;
            lastPreviewTruncated = false;
        }
    }

    function renderPreviewMarkers(points) {
        if (!mapApi) return;
        if (previewLayer) {
            mapApi.map.removeLayer(previewLayer);
            previewLayer = null;
        }
        if (!points.length) return;
        const layers = points.map(([lat, lon]) =>
            L.circleMarker([lat, lon], {
                radius: 4, color: '#0170B9', weight: 1,
                fillColor: '#0170B9', fillOpacity: 0.6,
            }));
        previewLayer = L.layerGroup(layers).addTo(mapApi.map);
    }

    function updatePreviewStatus(r) {
        if (r.error) {
            previewStatus.style.display = '';
            previewStatus.className = 'wizard-flash wizard-flash-error';
            previewStatus.textContent = 'Preview error: ' + r.error;
            return;
        }
        const count = (r.points || []).length;
        if (!count) {
            previewStatus.style.display = 'none';
            return;
        }
        const overCap = count > 99 || r.truncated;
        previewStatus.style.display = '';
        previewStatus.className = 'wizard-flash '
            + (overCap ? 'wizard-flash-error' : 'wizard-flash-info');
        previewStatus.textContent = r.truncated
            ? `Preview shows ${count} of >500 points — exceeds the 99-cap. `
              + `Loosen density and try again.`
            : `${count} grid point${count === 1 ? '' : 's'} `
              + (overCap
                 ? `— exceeds the 99-treatment cap. Loosen density.`
                 : `· each becomes one Field on Next.`);
    }

    function clearAllOverlays() {
        if (!mapApi) return;
        if (marker) { mapApi.map.removeLayer(marker); marker = null; }
        if (circle) { mapApi.map.removeLayer(circle); circle = null; }
        if (rect)   { mapApi.map.removeLayer(rect); rect = null; }
        if (previewLayer) {
            mapApi.map.removeLayer(previewLayer);
            previewLayer = null;
        }
        if (selectedAdmin && mapApi.adminLayer) {
            mapApi.adminLayer.resetStyle(selectedAdmin);
        }
        selectedAdmin = null;
        mapApi.setSelectedAdmin(null);
        // Hide the count chip + reset the cached verdict so validate()
        // doesn't keep gating Next on a stale point count.
        previewStatus.style.display = 'none';
        previewStatus.textContent = '';
        lastPreviewCount = null;
        lastPreviewTruncated = false;
    }
}

function buildCircle(host, store) {
    const state = store.getStep('step2') || {};
    const lat = h('input', {
        type: 'number', step: '0.0001',
        class: 'wizard-input mc-circle',
        value: state.center?.lat != null ? state.center.lat : '',
        placeholder: 'e.g. 32.6',
    });
    const lon = h('input', {
        type: 'number', step: '0.0001',
        class: 'wizard-input mc-circle',
        value: state.center?.lon != null ? state.center.lon : '',
        placeholder: 'e.g. -86.7',
    });
    const radius = h('input', {
        type: 'number', step: '1', min: '1', max: '500',
        class: 'wizard-input mc-circle',
        value: state.radius_km != null ? state.radius_km : 25,
    });
    host.appendChild(h('div', {class: 'wizard-form-row-3'},
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Center latitude'), lat),
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Center longitude'), lon),
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Radius (km)'), radius),
    ));
    on(lat, 'input', () => save());
    on(lon, 'input', () => save());
    on(radius, 'input', () => save());
    function save() {
        const center = {
            lat: parseFloat(lat.value) || null,
            lon: parseFloat(lon.value) || null,
        };
        store.setStep('step2', {center, radius_km: parseInt(radius.value, 10) || 25});
        if (mapApi && center.lat != null && center.lon != null) {
            applyCircleOverlay(center, parseInt(radius.value, 10) || 25);
        }
        triggerPreview();
    }
}

function buildBbox(host, store) {
    const state = store.getStep('step2') || {};
    const b = state.bbox || {};
    const inp = (key, label, ph) => {
        const el = h('input', {
            type: 'number', step: '0.01',
            class: 'wizard-input mc-bbox',
            value: b[key] != null ? b[key] : '',
            placeholder: ph,
        });
        on(el, 'input', () => save());
        return h('div', {class: 'wizard-form-group'}, h('label', {}, label), el);
    };
    const minLat = inp('min_lat', 'Min latitude', 'e.g. 32.0');
    const maxLat = inp('max_lat', 'Max latitude', 'e.g. 33.0');
    const minLon = inp('min_lon', 'Min longitude', 'e.g. -87.0');
    const maxLon = inp('max_lon', 'Max longitude', 'e.g. -86.0');
    host.appendChild(h('div', {class: 'wizard-form-row'}, minLat, maxLat));
    host.appendChild(h('div', {class: 'wizard-form-row'}, minLon, maxLon));
    host.appendChild(h('p', {class: 'step-description'},
        'Drag a rectangle on the map to fill these, or type values directly.'));

    function save() {
        const fields = host.querySelectorAll('input.mc-bbox');
        const v = {
            min_lat: parseFloat(fields[0].value),
            max_lat: parseFloat(fields[1].value),
            min_lon: parseFloat(fields[2].value),
            max_lon: parseFloat(fields[3].value),
        };
        for (const k of Object.keys(v)) if (isNaN(v[k])) v[k] = null;
        store.setStep('step2', {bbox: v});
        if (mapApi && Object.values(v).every((x) => x != null)) {
            rect = placeRectangle(mapApi.map, rect,
                [[v.min_lat, v.min_lon], [v.max_lat, v.max_lon]]);
        }
        triggerPreview();
    }
}

function buildAdmin(host, store) {
    const state = store.getStep('step2') || {};
    const sel = h('select', {class: 'wizard-select'},
        h('option', {value: 'country'}, 'Country'),
        h('option', {value: 'admin1'}, 'State / Province'),
        h('option', {value: 'admin2'}, 'County / District'),
    );
    sel.value = state.admin_level || 'admin1';
    on(sel, 'change', () => {
        store.setStep('step2', {admin_level: sel.value, admin_name: null});
        if (mapApi) mapApi.loadAdmins(sel.value);
        triggerPreview();
    });
    host.appendChild(h('div', {class: 'wizard-form-group'},
        h('label', {}, 'Admin level'), sel));
    host.appendChild(h('p', {class: 'admin-selected-label step-description'},
        state.admin_name ? 'Selected: ' + state.admin_name : 'Click an admin polygon on the map to select it.'));
}

function switchMapMode(mode) {
    if (!mapApi) return;
    if (mode === 'bbox') {
        mapApi.enableRectangle();
    } else {
        mapApi.disableRectangle();
    }
}

function applyCircleOverlay(center, radiusKm) {
    if (!mapApi) return;
    marker = placeMarker(mapApi.map, marker, [center.lat, center.lon]);
    circle = placeCircle(mapApi.map, circle, [center.lat, center.lon], radiusKm);
}

export function validate(store) {
    const s = store.getStep('step2') || {};
    if (!s.spatial_mode) return {ok: false, message: 'Pick a spatial mode.'};
    const strategy = s.sampling_strategy || 'systematic';
    if (strategy === 'systematic' && !s.grid_spacing) {
        return {ok: false, message: 'Set a grid spacing.'};
    }
    if (strategy === 'random' && (!s.n_points || s.n_points < 1)) {
        return {ok: false, message: 'Set the number of random points.'};
    }
    if (strategy === 'random' && s.n_points > 99) {
        return {ok: false, message: 'Random sampling is capped at 99 points.'};
    }
    if (s.spatial_mode === 'circle') {
        if (!s.center || s.center.lat == null || s.center.lon == null)
            return {ok: false, message: 'Click the map (or type) to set a center.'};
        if (!s.radius_km) return {ok: false, message: 'Set a radius.'};
    } else if (s.spatial_mode === 'bbox') {
        const b = s.bbox || {};
        if (b.min_lat == null || b.max_lat == null
            || b.min_lon == null || b.max_lon == null)
            return {ok: false, message: 'Draw a rectangle (or type the four bbox values).'};
    } else if (s.spatial_mode === 'admin') {
        if (!s.admin_name) return {ok: false, message: 'Click an admin polygon on the map.'};
    }
    if (lastPreviewTruncated || (lastPreviewCount != null && lastPreviewCount > 99)) {
        return {
            ok: false,
            message: `Geometry produces ${lastPreviewTruncated ? '>500' : lastPreviewCount} `
                + 'grid points — exceeds the 99-treatment cap. Loosen the '
                + 'density (grid spacing or n_points) before continuing.',
        };
    }
    return {ok: true};
}

export function lockSummary(store) {
    const s = store.getStep('step2') || {};
    const mode = s.spatial_mode || '?';
    const strategy = s.sampling_strategy || 'systematic';
    const sampleTag = strategy === 'random'
        ? `${strategy}, n=${s.n_points || '?'}`
        : `${strategy}, Δ=${s.grid_spacing || '?'}°`;
    if (mode === 'circle' && s.center) {
        return `circle @ ${s.center.lat}, ${s.center.lon} · `
            + `r=${s.radius_km}km · ${sampleTag}`;
    }
    if (mode === 'bbox' && s.bbox) {
        const b = s.bbox;
        return `bbox ${b.min_lat}–${b.max_lat}N / `
            + `${b.min_lon}–${b.max_lon}E · ${sampleTag}`;
    }
    if (mode === 'admin') {
        return `admin: ${s.admin_name || '?'} `
            + `(${s.admin_level || '?'}) · ${sampleTag}`;
    }
    return `MC mode ${mode}`;
}

const MC_FIELD_CAP = 99;

/**
 * Loading-overlay copy shown while the lock hook runs. The MC lock can
 * take several seconds for large grids because it reverse-geocodes every
 * sampled point and creates one Field row per point.
 */
export function lockProgressLabel(store) {
    const s = store.getStep('step2') || {};
    const mode = s.spatial_mode || 'circle';
    const tag = mode === 'admin' ? `admin polygon (${s.admin_name || '?'})`
        : mode === 'bbox' ? 'bounding box'
        : 'center + radius';
    const count = lastPreviewCount != null ? `${lastPreviewCount}` : 'every';
    return {
        title: 'Materializing Monte Carlo grid',
        detail:
            `Sampling ${count} grid point${lastPreviewCount === 1 ? '' : 's'} `
            + `from the ${tag} geometry, reverse-geocoding nearest cities, `
            + `and creating one Field record per point. Larger grids take `
            + `longer because each lookup is a separate request.`,
    };
}

/**
 * Lock hook: materialise the grid into one Field row per generated point.
 *
 * The same /dssat/api/mc/preview-grid/ endpoint the live preview uses also
 * acts as the source of truth at lock time so what the user saw on the
 * map is exactly what gets persisted. We cap at 99 (DSSATBatch limit) and
 * surface the count for confirm before creating dozens of records.
 *
 * Step 3's per-field editor + auto-fill loop already iterates `store.fields`,
 * so handing it a list of N fields just works. The submit path picks up the
 * explicit set instead of regenerating the grid at run time, which is what
 * lets per-field soil/elevation choices flow through to DSSAT.
 */
export async function onLockBeforeNext(store) {
    const s = store.getStep('step2') || {};
    const r = await refs.mcPreviewGrid({
        spatial_mode: s.spatial_mode,
        center: s.center,
        radius_km: s.radius_km,
        bbox: s.bbox,
        admin_name: s.admin_name,
        admin_level: s.admin_level,
        admin_country: s.admin_country,
        admin_parent_admin1: s.admin_parent_admin1,
        grid_spacing: s.grid_spacing,
        n_points: s.n_points,
        sampling_strategy: s.sampling_strategy,
    });
    const points = r.points || [];
    if (!points.length) {
        throw new Error(
            'No grid points generated for this geometry. '
            + 'Adjust the radius / bbox / admin selection or the density '
            + 'controls and try again.');
    }
    if (r.truncated || points.length > MC_FIELD_CAP) {
        throw new Error(
            `Geometry produces ${r.truncated ? '>500' : points.length} grid `
            + `points, exceeds the DSSAT cap of ${MC_FIELD_CAP}. Increase `
            + `grid spacing (systematic) or reduce n_points (random) and `
            + `try again.`);
    }

    const modeTag = s.spatial_mode === 'admin'
        ? (s.admin_name || 'MC admin')
        : (s.spatial_mode === 'bbox' ? 'MC bbox' : 'MC circle');

    // Reverse-geocode every point in parallel. Photon failures resolve to
    // null (the helper never throws) so a flaky network just falls back to
    // a lat/lon label per row.
    const labels = await Promise.all(
        points.map(([lat, lon]) => reverseGeocode(lat, lon))
    );
    // Create fields in parallel — sequential awaits made Next take ages
    // for >10 points. The fields API is concurrent-safe.
    const created = await Promise.all(
        points.map(([lat, lon], i) => {
            const geoLabel = labels[i];
            const fallback = `${lat.toFixed(3)}, ${lon.toFixed(3)}`;
            const locationLabel = geoLabel || fallback;
            const name = `${modeTag} #${i + 1} · ${locationLabel}`;
            return fieldsAPI.create({
                name,
                latitude: lat,
                longitude: lon,
                location_label: locationLabel,
                weather_source: 'nasa_power',
            });
        })
    );
    const items = created.map((c, i) => ({field_id: c.field.id, ordering: i}));
    if (!items.length) {
        throw new Error(
            'Field creation failed for every grid point — none were attached.');
    }
    await store.setFields(items);
}
