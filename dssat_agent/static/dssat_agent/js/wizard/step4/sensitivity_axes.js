/**
 * Sensitivity-analysis axes — UI builders + Cartesian generator.
 *
 * Used by:
 *   * ``protocol_editor.js`` — to render the Sensitivity chip's body
 *     inside the Treatments accordion (chip 2, between Crop & Cultivar
 *     and Planting & Harvest).
 *   * ``step4/sensitivity.js`` — to call ``generate(...)`` at lock time
 *     and materialise N concrete treatment payloads.
 *
 * The categories and axis shapes come from the original sensitivity
 * spec:
 *
 *   planting    — date central + ± days × N steps × pop range × N steps
 *                 × row-spacing range × N steps. Up to 3 axes (Cartesian).
 *   cultivar    — multi-select cultivar list (1 axis, no Cartesian).
 *   management  — pick ONE practice; sweep amount × number of
 *                 applications × days-between-applications.
 *                 2- or 3-axis Cartesian within that one practice.
 *
 * Generator clamps DOY into [1, 366] so an aggressive sweep doesn't
 * underflow. Empty axes / missing min-max gracefully fall back to the
 * baseline's value (singleton list).
 */

import {h, clear, on} from '../dom.js';
import {catalog, refs} from '../api.js';

const AUTO_PLANTING_SOURCE = 'dssat_planting_date_lookup_v1';

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export function renderAxesFor(category, host, axes, baseline, persist, ctx = {}) {
    if (category === 'planting')   return renderPlantingAxes(host, axes, persist, ctx);
    if (category === 'cultivar')   return renderCultivarAxes(host, axes, baseline, persist);
    if (category === 'management') return renderManagementAxes(host, axes, persist);
}

/** One-line treatment-count summary for the chip header. */
export function summary(sensitivity, baseline) {
    if (!sensitivity || !sensitivity.category) return '';
    const generated = generate(sensitivity, baseline || {});
    const cat = sensitivity.category;
    return `${cat} sweep · ${generated.length} treatment${generated.length === 1 ? '' : 's'}`;
}

// ---------------------------------------------------------------------------
// Axes UI per category
// ---------------------------------------------------------------------------

function renderPlantingAxes(host, axes, persist, ctx = {}) {
    axes.dateCentralDoy = axes.dateCentralDoy != null
        ? axes.dateCentralDoy
        : (axes.dateCentral ? doyFromDate(axes.dateCentral) : null);
    axes.dateOffsetDays = axes.dateOffsetDays || 0;
    axes.dateSteps     = axes.dateSteps || 1;
    axes.popMin        = axes.popMin != null ? axes.popMin : null;
    axes.popMax        = axes.popMax != null ? axes.popMax : null;
    axes.popSteps      = axes.popSteps || 1;
    axes.rsMin         = axes.rsMin != null ? axes.rsMin : null;
    axes.rsMax         = axes.rsMax != null ? axes.rsMax : null;
    axes.rsSteps       = axes.rsSteps || 1;

    function bind(el, key, parser = parseFloat) {
        on(el, 'input', () => {
            const v = parser(el.value);
            axes[key] = isNaN(v) ? null : v;
            persist();
        });
    }
    const num = (val, opts = {}) => h('input', {
        type: 'number', class: 'wizard-input',
        value: val != null ? val : '',
        ...opts,
    });

    const dateCen = h('input', {
        type: 'date', class: 'wizard-input',
        value: axes.dateCentralDoy != null ? dateFromDoy(axes.dateCentralDoy) : '',
    });
    on(dateCen, 'input', () => {
        axes.dateCentralDoy = doyFromDate(dateCen.value);
        persist();
    });
    const dateOff = num(axes.dateOffsetDays, {step: '1', min: '0'});
    bind(dateOff, 'dateOffsetDays', (v) => parseInt(v, 10));
    const dateStp = num(axes.dateSteps, {step: '1', min: '1'});
    bind(dateStp, 'dateSteps', (v) => parseInt(v, 10));

    // Auto-fill central date from the in-situ raster at the (single)
    // sensitivity field's lat/lon. The button writes ``dateCentralDoy``
    // and re-points the date input via the anchor-year mapping.
    const fields = ctx.fields || [];
    const field = fields[0];
    const year = ctx.year;
    const autoBtn = h('button', {
        type: 'button',
        class: 'btn btn-sm btn-secondary',
        style: {marginTop: '0.4rem'},
    }, 'Auto-fill from location');
    const autoStatus = h('div', {
        class: 'step-description',
        style: {marginTop: '0.3rem'},
    });
    if (!field || field.latitude == null || field.longitude == null) {
        autoBtn.disabled = true;
        autoBtn.title = 'No location configured — finish Step 2 first.';
    } else if (!year) {
        autoBtn.disabled = true;
        autoBtn.title = 'Set the experiment year on Step 1 first.';
    }
    on(autoBtn, 'click', async () => {
        if (!field || !year) return;
        autoBtn.disabled = true;
        autoStatus.textContent = 'Looking up planting date…';
        autoStatus.style.color = '';
        try {
            const r = await refs.resolvePlantingDates({
                source: AUTO_PLANTING_SOURCE,
                year,
                points: [[field.latitude, field.longitude]],
            });
            const entry = (r && r.resolved && r.resolved[0]) || null;
            if (!entry) {
                autoStatus.textContent =
                    'In-situ raster has no value at this location — '
                    + 'set the central date manually.';
                autoStatus.style.color = 'var(--text-muted)';
                return;
            }
            axes.dateCentralDoy = entry.doy;
            dateCen.value = dateFromDoy(entry.doy);
            persist();
            autoStatus.textContent =
                `Filled: ${entry.date} (DOY ${entry.doy})`;
            autoStatus.style.color = '';
        } catch (e) {
            autoStatus.textContent = 'Auto-fill failed: ' + e.message;
            autoStatus.style.color = 'var(--text-muted)';
        } finally {
            autoBtn.disabled = false;
        }
    });

    host.appendChild(h('h4', {style: {marginTop: '0.5rem'}}, 'Planting date'));
    host.appendChild(h('div', {class: 'wizard-form-row-3'},
        h('div', {class: 'wizard-form-group'},
            h('label', {}, 'Central date'), dateCen, autoBtn, autoStatus),
        h('div', {class: 'wizard-form-group'},
            h('label', {}, '± days'), dateOff),
        h('div', {class: 'wizard-form-group'},
            h('label', {}, 'Steps'), dateStp),
    ));

    const popMin = num(axes.popMin, {step: '0.1', min: '0', placeholder: 'e.g. 5.0'});
    bind(popMin, 'popMin');
    const popMax = num(axes.popMax, {step: '0.1', min: '0', placeholder: 'e.g. 10.0'});
    bind(popMax, 'popMax');
    const popStp = num(axes.popSteps, {step: '1', min: '1'});
    bind(popStp, 'popSteps', (v) => parseInt(v, 10));

    host.appendChild(h('h4', {}, 'Population (plants/m²)'));
    host.appendChild(h('div', {class: 'wizard-form-row-3'},
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Min'), popMin),
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Max'), popMax),
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Steps'), popStp),
    ));

    const rsMin = num(axes.rsMin, {step: '1', min: '0'});
    bind(rsMin, 'rsMin');
    const rsMax = num(axes.rsMax, {step: '1', min: '0'});
    bind(rsMax, 'rsMax');
    const rsStp = num(axes.rsSteps, {step: '1', min: '1'});
    bind(rsStp, 'rsSteps', (v) => parseInt(v, 10));
    host.appendChild(h('h4', {}, 'Row spacing (cm)'));
    host.appendChild(h('div', {class: 'wizard-form-row-3'},
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Min'), rsMin),
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Max'), rsMax),
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Steps'), rsStp),
    ));
    host.appendChild(h('p', {class: 'step-description'},
        'Each axis with Steps = 1 stays fixed at its baseline value. '
        + 'Treatment count = product of all three step counts.'));
}

function renderCultivarAxes(host, axes, baseline, persist) {
    axes.cultivars = axes.cultivars || [];
    host.appendChild(h('h4', {}, 'Cultivars to sweep'));

    const cropCode = baseline?.crop_code;
    const dssatModel = baseline?.dssat_model || '';
    if (!cropCode) {
        host.appendChild(h('p', {class: 'step-description'},
            'Pick a crop first; the cultivar list will appear here.'));
        return;
    }
    const list = h('div', {class: 'wizard-multiselect'});
    host.appendChild(list);
    catalog.cultivars(cropCode).then((r) => {
        // Filter by dssat_model so the user only sees cultivars that
        // match the simulation model their crop choice resolved to
        // (e.g. Maize CERES vs Maize IXIM); otherwise the list mixes
        // models and produces invalid sweeps. Dedup by code as a
        // defensive layer (the upstream registry occasionally has the
        // same cultivar code stored under multiple model variants).
        const cvs = filterCultivarsForModel(r.cultivars || [], dssatModel);
        clear(list);
        if (!cvs.length) {
            list.appendChild(h('div', {class: 'empty-state small'},
                `No cultivars registered for ${cropCode}`
                + (dssatModel ? ` (model ${dssatModel})` : '') + '.'));
            return;
        }
        for (const cv of cvs) {
            const cb = h('input', {
                type: 'checkbox',
                value: cv.code,
                checked: axes.cultivars.includes(cv.code),
            });
            on(cb, 'change', () => {
                if (cb.checked) axes.cultivars = [...new Set([...axes.cultivars, cv.code])];
                else axes.cultivars = axes.cultivars.filter((x) => x !== cv.code);
                persist();
            });
            list.appendChild(h('label', {class: 'wizard-checkbox'},
                cb,
                h('span', {}, `${cv.code} — ${cv.name || ''}`)));
        }
    }).catch((e) => {
        list.appendChild(h('div', {class: 'wizard-flash wizard-flash-error'},
            `Cultivar load failed: ${e.message}`));
    });
}

function renderManagementAxes(host, axes, persist) {
    axes.practice    = axes.practice || 'fertilizer';
    axes.amountMin   = axes.amountMin != null ? axes.amountMin : null;
    axes.amountMax   = axes.amountMax != null ? axes.amountMax : null;
    axes.amountSteps = axes.amountSteps || 1;
    axes.appsMin     = axes.appsMin || 1;
    axes.appsMax     = axes.appsMax || 1;
    axes.gapDaysMin  = axes.gapDaysMin || 1;
    axes.gapDaysMax  = axes.gapDaysMax || 1;
    axes.gapSteps    = axes.gapSteps || 1;

    const practiceSel = h('select', {class: 'wizard-select'},
        h('option', {value: 'fertilizer'}, 'Fertilizer'),
        h('option', {value: 'irrigation'}, 'Irrigation'),
        h('option', {value: 'tillage'}, 'Tillage'),
        h('option', {value: 'chemical'}, 'Chemical'),
        h('option', {value: 'residue'}, 'Residue'),
    );
    practiceSel.value = axes.practice;
    on(practiceSel, 'change', () => { axes.practice = practiceSel.value; persist(); });
    host.appendChild(h('div', {class: 'wizard-form-group'},
        h('label', {}, 'Practice to vary'), practiceSel));

    const num = (val, opts = {}) => {
        const el = h('input', {
            type: 'number', class: 'wizard-input',
            value: val != null ? val : '',
            ...opts,
        });
        return el;
    };
    const bind = (el, key, parser = parseFloat) => {
        on(el, 'input', () => {
            const v = parser(el.value);
            axes[key] = isNaN(v) ? null : v;
            persist();
        });
    };
    const amtMin = num(axes.amountMin, {step: 'any'}); bind(amtMin, 'amountMin');
    const amtMax = num(axes.amountMax, {step: 'any'}); bind(amtMax, 'amountMax');
    const amtStp = num(axes.amountSteps, {step: '1', min: '1'});
    bind(amtStp, 'amountSteps', (v) => parseInt(v, 10));
    host.appendChild(h('h4', {}, 'Amount per application'));
    host.appendChild(h('div', {class: 'wizard-form-row-3'},
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Min'), amtMin),
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Max'), amtMax),
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Steps'), amtStp),
    ));

    const appsMin = num(axes.appsMin, {step: '1', min: '1'});
    bind(appsMin, 'appsMin', (v) => parseInt(v, 10));
    const appsMax = num(axes.appsMax, {step: '1', min: '1'});
    bind(appsMax, 'appsMax', (v) => parseInt(v, 10));
    host.appendChild(h('h4', {}, 'Number of applications'));
    host.appendChild(h('div', {class: 'wizard-form-row'},
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Min'), appsMin),
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Max'), appsMax),
    ));

    const gapMin = num(axes.gapDaysMin, {step: '1', min: '1'});
    bind(gapMin, 'gapDaysMin', (v) => parseInt(v, 10));
    const gapMax = num(axes.gapDaysMax, {step: '1', min: '1'});
    bind(gapMax, 'gapDaysMax', (v) => parseInt(v, 10));
    const gapStp = num(axes.gapSteps, {step: '1', min: '1'});
    bind(gapStp, 'gapSteps', (v) => parseInt(v, 10));
    host.appendChild(h('h4', {}, 'Days between applications'));
    host.appendChild(h('div', {class: 'wizard-form-row-3'},
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Min'), gapMin),
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Max'), gapMax),
        h('div', {class: 'wizard-form-group'}, h('label', {}, 'Steps'), gapStp),
    ));
}

// ---------------------------------------------------------------------------
// Cultivar filtering — keep cultivars whose dssat_model matches (or is
// blank) and dedupe by code.
// ---------------------------------------------------------------------------

export function filterCultivarsForModel(cvs, dssatModel) {
    const model = (dssatModel || '').toUpperCase();
    const keep = (cv) => {
        if (!model) return true;
        const cm = (cv.dssat_model || '').toUpperCase();
        // Matching model wins; cultivars with no recorded model are
        // permitted as a fallback so user-uploaded cultivars without a
        // model annotation still appear.
        return !cm || cm === model;
    };
    const seen = new Set();
    const out = [];
    for (const cv of (cvs || [])) {
        if (!keep(cv)) continue;
        const code = cv.code;
        if (!code || seen.has(code)) continue;
        seen.add(code);
        out.push(cv);
    }
    return out;
}

// ---------------------------------------------------------------------------
// Treatment generator (Cartesian within category)
// ---------------------------------------------------------------------------

function range(min, max, steps) {
    if (min == null || max == null || !steps || steps < 1) return [];
    if (steps === 1) return [(min + max) / 2];
    const step = (max - min) / (steps - 1);
    return Array.from({length: steps}, (_, i) => +(min + i * step).toFixed(4));
}

function doyRange(centralDoy, offsetDays, steps) {
    if (centralDoy == null || !steps || steps < 1)
        return centralDoy != null ? [centralDoy] : [];
    if (steps === 1) return [centralDoy];
    const out = [];
    const total = (offsetDays || 0) * 2;
    for (let i = 0; i < steps; i++) {
        const offset = -offsetDays + (i * total) / (steps - 1);
        const v = Math.max(1, Math.min(366, Math.round(centralDoy + offset)));
        out.push(v);
    }
    return out;
}

const ANCHOR_YEAR = 2000;
function dateFromDoy(doy) {
    if (doy == null || isNaN(doy)) return '';
    const d = new Date(Date.UTC(ANCHOR_YEAR, 0, 1));
    d.setUTCDate(d.getUTCDate() + (parseInt(doy, 10) - 1));
    return d.toISOString().slice(0, 10);
}
function doyFromDate(iso) {
    if (!iso) return null;
    const d = new Date(iso + 'T00:00:00Z');
    const start = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    return Math.floor((d - start) / 86400000) + 1;
}

export function generate(sens, baseline) {
    const base = baseline || {};
    const cat = sens.category;
    const a = sens.axes || {};

    if (cat === 'planting') {
        const doys = doyRange(a.dateCentralDoy, a.dateOffsetDays, a.dateSteps || 1);
        const pops = range(a.popMin, a.popMax, a.popSteps || 1);
        const rss = range(a.rsMin, a.rsMax, a.rsSteps || 1);
        const out = [];
        const dlist = doys.length ? doys : [base.planting?.pdoy || null];
        const plist = pops.length ? pops : [base.planting?.ppop || null];
        const rlist = rss.length ? rss : [base.planting?.plrs || null];
        let i = 0;
        for (const d of dlist) for (const p of plist) for (const r of rlist) {
            const t = JSON.parse(JSON.stringify(base));
            // Sweep override: Fixed Date with pdoy is what the run
            // pipeline materialises into a real planting date. The
            // baseline's planting.mode might have been 'auto' — sweeping
            // forces 'fixed' since we're varying the date axis.
            t.planting = {
                ...(t.planting || {}),
                mode: 'fixed',
                pdoy: d,
                ppop: p,
                plrs: r,
                auto_source: null,
            };
            t.name = `T${++i}: DOY=${d} pop=${p} rs=${r}`;
            out.push(t);
        }
        return out;
    }
    if (cat === 'cultivar') {
        const list = a.cultivars || [];
        return list.map((cv, i) => {
            const t = JSON.parse(JSON.stringify(base));
            t.cultivar_code = cv;
            t.name = `T${i + 1}: ${cv}`;
            return t;
        });
    }
    if (cat === 'management') {
        const amts = range(a.amountMin, a.amountMax, a.amountSteps || 1);
        const apps = (a.appsMin && a.appsMax)
            ? Array.from({length: a.appsMax - a.appsMin + 1}, (_, i) => a.appsMin + i)
            : [a.appsMin || 1];
        const gaps = range(a.gapDaysMin, a.gapDaysMax, a.gapSteps || 1);
        const out = [];
        let i = 0;
        for (const amt of (amts.length ? amts : [null])) {
            for (const napps of apps) {
                for (const gap of (gaps.length ? gaps : [null])) {
                    const t = JSON.parse(JSON.stringify(base));
                    const events = buildEvents(a.practice, amt, napps, gap);
                    if (a.practice === 'irrigation') {
                        t.irrigation = {method: 'fixed', events};
                    } else {
                        t[a.practice] = events;
                    }
                    t.name = `T${++i}: ${a.practice} amt=${amt} n=${napps} gap=${gap}`;
                    out.push(t);
                }
            }
        }
        return out;
    }
    return [];
}

function buildEvents(practice, amount, n, gap) {
    const events = [];
    const dapKey = {
        fertilizer: 'fdap', irrigation: 'idap', tillage: 'tdap',
        chemical: 'cdap', residue: 'rdap',
    }[practice];
    const amtKey = {
        fertilizer: 'famn', irrigation: 'irval', tillage: 'tdep',
        chemical: 'chamt', residue: 'ramt',
    }[practice];
    if (!dapKey || !amtKey) return events;
    for (let i = 0; i < n; i++) {
        const ev = {};
        ev[dapKey] = (gap || 0) * i;
        if (amount != null) ev[amtKey] = amount;
        events.push(ev);
    }
    return events;
}
