/**
 * Shared Leaflet helpers for the wizard.
 *
 * Behavioural rules (per spec):
 *   - All maps share a CartoCDN dark base layer + admin-boundary overlay
 *     with a level selector (country / admin1 / admin2).
 *   - Hover style: ONLY the boundary highlights — keep the polygon's fill
 *     transparent. Tooltips remain (sticky, top-anchored) so the user can
 *     identify the unit they're hovering over.
 *   - Click semantics differ by mode; the caller registers handlers via
 *     `setMapMode()` / on(...) callbacks.
 */

import {SUBPATH, refs} from './api.js';

const TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const TILE_OPTS = {
    attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; '
                 + '<a href="https://www.openstreetmap.org/copyright">OSM</a>',
    subdomains: 'abcd',
    maxZoom: 19,
};

const ADMIN_STYLE_BASE = {
    color: '#ffffff', weight: 1.0, opacity: 0.45,
    fillColor: 'transparent', fillOpacity: 0,
};
const ADMIN_STYLE_HOVER = {
    color: '#0170B9', weight: 2.5, opacity: 1,
    fillColor: 'transparent', fillOpacity: 0,
};
const ADMIN_STYLE_SELECTED = {
    color: '#f59e0b', weight: 3, opacity: 1,
    fillColor: 'transparent', fillOpacity: 0,
};

/**
 * Construct a wizard map and return a small controller object.
 *
 * @param {HTMLElement} el         container to mount into
 * @param {Object} options
 * @param {Array}  options.center  [lat, lon]
 * @param {Number} options.zoom
 * @param {String} options.adminLevel  initial admin level: 'country'|'admin1'|'admin2'
 * @param {Boolean} options.showLevelSelect  render the level selector chip
 */
export function createWizardMap(el, options = {}) {
    if (typeof L === 'undefined') {
        el.innerHTML = '<div class="wizard-flash wizard-flash-error">'
            + 'Leaflet failed to load.</div>';
        return null;
    }
    const center = options.center || [33.0, -86.5];
    const zoom = options.zoom || 5;
    const adminLevel = options.adminLevel || 'admin1';
    const showLevelSelect = options.showLevelSelect !== false;

    el.classList.add('wizard-map-host');
    el.innerHTML = '';
    const mapEl = document.createElement('div');
    mapEl.className = 'wizard-map';
    mapEl.style.height = options.height || '320px';
    el.appendChild(mapEl);

    const map = L.map(mapEl, {center, zoom, zoomControl: true});
    L.tileLayer(TILE_URL, TILE_OPTS).addTo(map);

    let adminLayer = null;
    let selectedAdminLayer = null;
    let currentLevel = adminLevel;

    const handlers = {
        click: null,            // (latlng) => void  — only fires when no admin polygon was clicked
        adminClick: null,       // ({feature, layer}) => void
        adminLevelChange: null, // (level) => void
        rectangle: null,        // (bounds) => void  — leaflet.draw rectangle created
    };

    function loadAdmins(level) {
        currentLevel = level;
        if (adminLayer) { map.removeLayer(adminLayer); adminLayer = null; }
        selectedAdminLayer = null;
        if (!level) return;

        adminLayer = L.geoJSON(null, {
            style: ADMIN_STYLE_BASE,
            onEachFeature: (feature, layer) => {
                const props = feature.properties || {};
                const label = [props.country, props.admin1, props.admin2]
                    .filter(Boolean).join(' > ');
                layer.bindTooltip(label, {
                    sticky: true, direction: 'top', opacity: 0.9,
                });
                layer.on('mouseover', () => {
                    if (layer === selectedAdminLayer) return;
                    layer.setStyle(ADMIN_STYLE_HOVER);
                    layer.bringToFront();
                });
                layer.on('mouseout', () => {
                    if (layer !== selectedAdminLayer && adminLayer) {
                        adminLayer.resetStyle(layer);
                    }
                });
                layer.on('click', (e) => {
                    if (handlers.adminClick) {
                        handlers.adminClick({
                            feature, layer, level,
                            latlng: e.latlng,
                        });
                    }
                    // Deliberately no stopPropagation: callers that want
                    // *both* admin selection AND a point drop (single /
                    // ensemble / sensitivity / batch) get the map's click
                    // event for free; callers that only want admin
                    // selection (MC admin mode) already gate on
                    // spatial_mode inside their own click handler.
                });
            },
        }).addTo(map);

        const url = SUBPATH + '/data/api/map/admin/?level='
            + encodeURIComponent(level) + '&stream=1';
        fetch(url, {headers: {'Accept': 'application/x-ndjson'}, credentials: 'same-origin'})
            .then((r) => {
                const reader = r.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                function pump(result) {
                    if (result.done) {
                        if (buffer.trim() && adminLayer) {
                            try { adminLayer.addData(JSON.parse(buffer.trim())); }
                            catch (_e) { /* ignore */ }
                        }
                        return;
                    }
                    buffer += decoder.decode(result.value, {stream: true});
                    const lines = buffer.split('\n');
                    buffer = lines.pop();
                    for (const line of lines) {
                        if (!line.trim() || !adminLayer) continue;
                        try { adminLayer.addData(JSON.parse(line)); }
                        catch (_e) { /* skip bad line */ }
                    }
                    return reader.read().then(pump);
                }
                return reader.read().then(pump);
            })
            .catch((err) => console.warn('Admin overlay load failed:', err));
    }

    if (showLevelSelect) {
        const sel = document.createElement('div');
        sel.className = 'wizard-map-level-select';
        sel.innerHTML = '<label>Admin layer</label>'
            + '<select>'
            + '<option value="">None</option>'
            + '<option value="country">Country</option>'
            + '<option value="admin1">States</option>'
            + '<option value="admin2">Counties</option>'
            + '</select>';
        const select = sel.querySelector('select');
        select.value = adminLevel;
        select.addEventListener('change', () => {
            loadAdmins(select.value);
            if (handlers.adminLevelChange) handlers.adminLevelChange(select.value);
        });
        el.appendChild(sel);
    }

    map.on('click', (e) => {
        if (handlers.click) handlers.click(e.latlng);
    });

    if (adminLevel) loadAdmins(adminLevel);

    // Optional: Leaflet.Draw rectangle (used by MC bbox mode).
    let drawHandler = null;
    function enableRectangle() {
        if (typeof L.Draw === 'undefined') return;
        if (!drawHandler) {
            drawHandler = new L.Draw.Rectangle(map, {
                shapeOptions: {color: '#0170B9', weight: 2, fillOpacity: 0.1},
            });
            map.on(L.Draw.Event.CREATED, (e) => {
                if (handlers.rectangle) handlers.rectangle(e.layer.getBounds(), e.layer);
                setTimeout(() => drawHandler && drawHandler.enable(), 0);
            });
        }
        drawHandler.enable();
    }
    function disableRectangle() {
        if (drawHandler) drawHandler.disable();
    }

    function on(event, fn) { handlers[event] = fn; }

    function setSelectedAdmin(layer) {
        if (selectedAdminLayer && adminLayer) {
            adminLayer.resetStyle(selectedAdminLayer);
        }
        selectedAdminLayer = layer || null;
        if (selectedAdminLayer) {
            selectedAdminLayer.setStyle(ADMIN_STYLE_SELECTED);
            selectedAdminLayer.bringToFront();
        }
    }

    return {
        map,
        on,
        loadAdmins,
        setSelectedAdmin,
        enableRectangle,
        disableRectangle,
        invalidateSize: () => setTimeout(() => map.invalidateSize(), 60),
        get adminLayer() { return adminLayer; },
        get currentAdminLevel() { return currentLevel; },
    };
}

/**
 * Drop-or-update a single marker. Returns the marker so callers can keep
 * a ref and remove/replace it.
 */
export function placeMarker(map, marker, latlng, opts = {}) {
    if (marker) {
        marker.setLatLng(latlng);
        return marker;
    }
    return L.marker(latlng, opts).addTo(map);
}

export function placeCircle(map, circle, latlng, radiusKm) {
    if (circle) map.removeLayer(circle);
    return L.circle(latlng, {
        radius: radiusKm * 1000,
        color: '#0170B9', weight: 2, fillOpacity: 0.1,
    }).addTo(map);
}

export function placeRectangle(map, rect, bounds) {
    if (rect) map.removeLayer(rect);
    return L.rectangle(bounds, {
        color: '#0170B9', weight: 2, fillOpacity: 0.1,
    }).addTo(map);
}
