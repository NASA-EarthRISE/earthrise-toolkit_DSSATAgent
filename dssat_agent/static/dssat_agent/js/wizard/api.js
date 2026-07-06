/**
 * Wizard API client — wraps every backend call the SPA makes so the
 * components don't repeat URL strings or CSRF/credentials boilerplate.
 *
 * All paths are relative to ``window.SUBPATH`` so the same code works both
 * locally (no subpath) and behind a /subpath/ deployment.
 */

const SUBPATH = window.SUBPATH || '';

function csrfToken() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
}

async function call(url, options = {}) {
    const fullUrl = url.startsWith('/') ? SUBPATH + url : url;
    const opts = {
        credentials: 'same-origin',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken(),
            ...(options.headers || {}),
        },
        ...options,
    };
    if (opts.body && typeof opts.body !== 'string') {
        opts.body = JSON.stringify(opts.body);
    }
    const r = await fetch(fullUrl, opts);
    let body = null;
    try { body = await r.json(); } catch (_e) { /* non-JSON */ }
    if (!r.ok) {
        const err = (body && body.error) || `HTTP ${r.status}`;
        throw new Error(err);
    }
    return body || {};
}

// ---------------------------------------------------------------------------
// Wizard drafts
// ---------------------------------------------------------------------------

export const drafts = {
    create: (payload) =>
        call('/dssat/api/wizard-drafts/', {method: 'POST', body: payload}),
    active: (chatId) => {
        const q = chatId ? `?chat_id=${encodeURIComponent(chatId)}` : '';
        return call('/dssat/api/wizard-drafts/active/' + q);
    },
    get: (id) => call(`/dssat/api/wizard-drafts/${id}/`),
    patch: (id, payload) =>
        call(`/dssat/api/wizard-drafts/${id}/`,
             {method: 'PATCH', body: payload}),
    delete: (id) =>
        call(`/dssat/api/wizard-drafts/${id}/`, {method: 'DELETE'}),
    lock: (id, stepPath) =>
        call(`/dssat/api/wizard-drafts/${id}/lock/`,
             {method: 'POST', body: {step_path: stepPath}}),
    edit: (id, stepPath) =>
        call(`/dssat/api/wizard-drafts/${id}/edit/`,
             {method: 'POST', body: {step_path: stepPath}}),
    submit: (id) =>
        call(`/dssat/api/wizard-drafts/${id}/submit/`, {method: 'POST'}),
};

// ---------------------------------------------------------------------------
// Field + Treatment libraries
// ---------------------------------------------------------------------------

export const fields = {
    list: (q = '') => {
        const params = q ? `?q=${encodeURIComponent(q)}` : '';
        return call('/dssat/api/fields/' + params);
    },
    create: (payload) =>
        call('/dssat/api/fields/', {method: 'POST', body: payload}),
    get: (id) => call(`/dssat/api/fields/${id}/`),
    patch: (id, payload) =>
        call(`/dssat/api/fields/${id}/`, {method: 'PATCH', body: payload}),
    delete: (id) =>
        call(`/dssat/api/fields/${id}/`, {method: 'DELETE'}),
};

export const treatments = {
    list: (q = '', cropCode = '') => {
        const params = new URLSearchParams();
        if (q) params.set('q', q);
        if (cropCode) params.set('crop_code', cropCode);
        const qs = params.toString();
        return call('/dssat/api/treatments/' + (qs ? '?' + qs : ''));
    },
    create: (payload) =>
        call('/dssat/api/treatments/', {method: 'POST', body: payload}),
    get: (id) => call(`/dssat/api/treatments/${id}/`),
    patch: (id, payload) =>
        call(`/dssat/api/treatments/${id}/`, {method: 'PATCH', body: payload}),
    delete: (id) =>
        call(`/dssat/api/treatments/${id}/`, {method: 'DELETE'}),
};

// ---------------------------------------------------------------------------
// Reference data + lookups
// ---------------------------------------------------------------------------

export const refs = {
    // Lists all data-agent sources (each with its declared variables). The
    // DSSAT-specific "must have tmax/tmin/rain/srad" filter is applied on the
    // consumer side in wizard/step3 — data_agent stays domain-agnostic.
    weatherSources: () =>
        call('/data/api/sources/'),
    weatherAvailability: (source, startDate, endDate) =>
        call('/data/api/check/', {
            method: 'POST',
            body: {source, start_date: startDate, end_date: endDate},
        }),
    elevation: (lat, lon) =>
        call(`/dssat/api/elevation/?lat=${lat}&lon=${lon}`),
    adminCentroid: (level, name) =>
        call(`/dssat/api/admin-units/centroid/?level=${level}&name=${encodeURIComponent(name)}`),
    adminUnits: (level, parent) => {
        const q = parent
            ? `?level=${level}&parent=${encodeURIComponent(parent)}`
            : `?level=${level}`;
        return call('/dssat/api/admin-units/' + q);
    },
    inSituSources: (lat, lon) =>
        call(`/dssat/api/insitu/sources/?lat=${lat}&lon=${lon}`),
    inSituCoverage: (payload) =>
        call('/dssat/api/insitu/coverage/', {method: 'POST', body: payload}),
    /** Coverage check for an explicit (lat, lon) list — used by Step 3 to
     *  probe the chosen source at every attached field's location. */
    pointsCoverage: ({points, parameter, source}) =>
        call('/dssat/api/insitu/coverage/', {
            method: 'POST',
            body: {points, parameter, source},
        }),
    /** Per-point planting-date resolution against an in-situ raster.
     *  Used by Step 4's resolve-at-lock pass for Auto-mode treatments.
     *  Returns ``{resolved: [{lat, lon, doy, date} | null, ...]}`` —
     *  one entry per input point, ``null`` for uncovered. */
    resolvePlantingDates: ({source, year, points}) =>
        call('/dssat/api/planting-dates/resolve/', {
            method: 'POST',
            body: {source, year, points},
        }),
    /** Crop-model metadata (planting methods, harvest stages,
     *  supported_harvs_modes, plant population range). Cached upstream by
     *  the protocol editor on (crop_code, dssat_model). */
    cropModel: (cropCode, dssatModel) => {
        const q = dssatModel
            ? `?dssat_model=${encodeURIComponent(dssatModel)}` : '';
        return call(`/dssat/api/crop-model/${encodeURIComponent(cropCode)}/${q}`);
    },
    mcPreviewGrid: (payload) =>
        call('/dssat/api/mc/preview-grid/', {method: 'POST', body: payload}),
};

// ---------------------------------------------------------------------------
// Crops + cultivars + soils (existing endpoints)
// ---------------------------------------------------------------------------

export const catalog = {
    crops: () => call('/dssat/api/crops/'),
    cultivars: (cropCode) =>
        call(`/dssat/api/cultivars/${cropCode}/`),
    soils: (q = '') => {
        const params = q ? `?q=${encodeURIComponent(q)}` : '';
        return call('/dssat/api/soils/' + params);
    },
};

export {SUBPATH};
