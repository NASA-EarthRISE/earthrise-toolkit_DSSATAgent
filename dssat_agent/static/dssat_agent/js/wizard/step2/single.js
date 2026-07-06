/**
 * Step 2 (single / ensemble / sensitivity): single-point location picker.
 *
 * UI:
 *   - Leaflet map with admin-vector overlay (level selector chip).
 *     Boundary-only hover (no fill highlight) per the spec.
 *   - Click on the map drops/moves a marker; lat/lon inputs stay synced.
 *   - "Save & continue" creates a Field record on Next via the validate
 *     hook, attaching it to the draft as the only field. Existing field
 *     attachments are replaced.
 */

import {h, clear, on} from '../dom.js';
import {fields as fieldsAPI} from '../api.js';
import {createWizardMap, placeMarker} from '../map.js';
import {reverseGeocode} from '../geocode.js';

const MAP_HEIGHT = '320px';

let mapApi = null;
let marker = null;

export async function render(root, store, controller) {
    clear(root);
    const state = store.getStep('step2') || {};
    const panel = h('div', {class: 'wizard-step-panel'},
        h('h2', {class: 'step-title'}, 'Location'),
        h('p', {class: 'step-description'},
          'Click the map to drop a point, or type lat/lon. Toggle the admin '
          + 'layer to see country / state / county outlines for reference; '
          + 'hovering shows the unit name without filling the polygon.'),
    );

    const mapHost = h('div', {id: 'wizard-step2-map'});
    panel.appendChild(mapHost);

    const latInput = h('input', {
        type: 'number', step: '0.0001', min: '-90', max: '90',
        class: 'wizard-input',
        placeholder: 'e.g. 32.6',
        value: state.latitude != null ? state.latitude : '',
    });
    const lonInput = h('input', {
        type: 'number', step: '0.0001', min: '-180', max: '180',
        class: 'wizard-input',
        placeholder: 'e.g. -86.7',
        value: state.longitude != null ? state.longitude : '',
    });
    const labelInput = h('input', {
        type: 'text', class: 'wizard-input',
        placeholder: 'Optional name, e.g. Auburn, AL',
        value: state.location_label || '',
    });

    const row = h('div', {class: 'wizard-form-row'},
        h('div', {class: 'wizard-form-group'},
            h('label', {}, 'Latitude'), latInput),
        h('div', {class: 'wizard-form-group'},
            h('label', {}, 'Longitude'), lonInput),
    );
    const labelRow = h('div', {class: 'wizard-form-group'},
        h('label', {}, 'Location label (optional)'), labelInput);

    panel.appendChild(row);
    panel.appendChild(labelRow);
    root.appendChild(panel);

    mapApi = createWizardMap(mapHost, {
        height: MAP_HEIGHT,
        center: [state.latitude || 33.0, state.longitude || -86.5],
        zoom: state.latitude ? 8 : 5,
        adminLevel: 'admin1',
    });
    marker = null;
    if (state.latitude != null && state.longitude != null) {
        marker = placeMarker(mapApi.map, marker, [state.latitude, state.longitude]);
    }

    // Track where the current label value came from so Photon can
    // overwrite a coarse admin-polygon stamp but never a value the user
    // typed by hand. 'empty' before the first set; 'admin' after the
    // adminClick fallback fills it; 'photon' after the geocoder resolves;
    // 'manual' after the user types in the input.
    let labelSource = labelInput.value.trim() ? 'manual' : 'empty';

    mapApi.on('click', (latlng) => {
        latInput.value = latlng.lat.toFixed(4);
        lonInput.value = latlng.lng.toFixed(4);
        marker = placeMarker(mapApi.map, marker, [latlng.lat, latlng.lng]);
        store.setStep('step2', {
            latitude: parseFloat(latInput.value),
            longitude: parseFloat(lonInput.value),
        });
        scheduleAutoLabel(latlng.lat, latlng.lng);
    });

    mapApi.on('adminClick', ({feature}) => {
        // Coarse fallback. Stamps a label only when the user hasn't typed
        // one yet, and tags the source as 'admin' so the Photon callback
        // (firing a few hundred ms later) can overwrite with the finer
        // city-level label.
        if (labelSource === 'manual') return;
        const props = feature.properties || {};
        const lab = [props.country, props.admin1, props.admin2]
            .filter(Boolean).join(' > ');
        if (lab) {
            labelInput.value = lab;
            store.setStep('step2', {location_label: lab});
            labelSource = 'admin';
        }
    });

    on(latInput, 'input', () => {
        const v = parseFloat(latInput.value);
        if (!isNaN(v)) syncMarkerFromInputs();
        store.setStep('step2', {latitude: isNaN(v) ? null : v});
        const lon = parseFloat(lonInput.value);
        if (!isNaN(v) && !isNaN(lon)) scheduleAutoLabel(v, lon);
    });
    on(lonInput, 'input', () => {
        const v = parseFloat(lonInput.value);
        if (!isNaN(v)) syncMarkerFromInputs();
        store.setStep('step2', {longitude: isNaN(v) ? null : v});
        const lat = parseFloat(latInput.value);
        if (!isNaN(lat) && !isNaN(v)) scheduleAutoLabel(lat, v);
    });
    on(labelInput, 'input', () => {
        store.setStep('step2', {location_label: labelInput.value || ''});
        labelSource = labelInput.value.trim() ? 'manual' : 'empty';
    });

    // Reverse-geocode the current point and fill `location_label`. Wins
    // over the admin-polygon fallback (stamps when source is 'empty' or
    // 'admin') but always defers to the user's manual edit.
    let labelTimer = null;
    function scheduleAutoLabel(lat, lon) {
        clearTimeout(labelTimer);
        labelTimer = setTimeout(() => {
            if (labelSource === 'manual') return;
            reverseGeocode(lat, lon).then((label) => {
                if (!label || labelSource === 'manual') return;
                labelInput.value = label;
                store.setStep('step2', {location_label: label});
                labelSource = 'photon';
            });
        }, 350);
    }

    function syncMarkerFromInputs() {
        const lat = parseFloat(latInput.value);
        const lon = parseFloat(lonInput.value);
        if (isNaN(lat) || isNaN(lon)) return;
        marker = placeMarker(mapApi.map, marker, [lat, lon]);
        mapApi.map.setView([lat, lon], Math.max(mapApi.map.getZoom(), 8));
    }

    mapApi.invalidateSize();
}

export function validate(store) {
    const s = store.getStep('step2') || {};
    if (s.latitude == null || s.longitude == null) {
        return {ok: false, message: 'Click the map (or type lat/lon) to set the location.'};
    }
    if (Math.abs(s.latitude) > 90 || Math.abs(s.longitude) > 180) {
        return {ok: false, message: 'Latitude and longitude are out of range.'};
    }
    return {ok: true};
}

export function lockSummary(store) {
    const s = store.getStep('step2') || {};
    if (s.latitude == null) return 'No location set.';
    const label = s.location_label ? ` (${s.location_label})` : '';
    return `${s.latitude.toFixed(3)}, ${s.longitude.toFixed(3)}${label}`;
}

/**
 * Lock hook: persist this single point as a Field record and attach to draft.
 *
 * Behaviour:
 *   - Reuse an existing field if one was already attached to the draft and
 *     coords haven't changed.
 *   - Otherwise create a fresh Field with a default name based on the label
 *     or coordinates, then PATCH the draft's `fields` to that single id.
 */
export async function onLockBeforeNext(store) {
    const s = store.getStep('step2') || {};
    const existing = (store.fields || [])[0];
    const name = (s.location_label && s.location_label.trim())
        || `Point ${s.latitude.toFixed(3)}, ${s.longitude.toFixed(3)}`;

    let fieldId;
    if (existing
        && Math.abs(existing.latitude - s.latitude) < 1e-6
        && Math.abs(existing.longitude - s.longitude) < 1e-6) {
        // Already attached and unchanged — keep it.
        fieldId = existing.id;
    } else {
        // Step 3 will set soil + weather + elevation; create the Field with
        // a placeholder weather_source that Step 3's Next will overwrite.
        const r = await fieldsAPI.create({
            name,
            latitude: s.latitude,
            longitude: s.longitude,
            location_label: s.location_label || '',
            weather_source: 'nasa_power',
        });
        fieldId = r.field.id;
    }
    await store.setFields([{field_id: fieldId, ordering: 0}]);
}
