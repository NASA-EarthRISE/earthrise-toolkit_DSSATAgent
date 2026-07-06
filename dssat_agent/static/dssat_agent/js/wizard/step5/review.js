/**
 * Step 5: Review + Submit.
 *
 * Renders a styled summary of the draft so the user can sanity-check
 * before kicking off the run. Layout:
 *
 *   Top KV:  experiment type / year / treatment count
 *   Fields   styled card grid, one card per attached Field
 *   Treatments  styled card grid, one card per UNIQUE treatment_id
 *               (MC's "1 template × N fields" collapses to a single
 *               card with footer "Applied to N fields")
 *   Pairings collapsed <details> showing every (treatment, field) row
 *               from store.treatments, including the per-pair Auto-mode
 *               resolved planting date when present
 *   Weather  per-source availability check (informational)
 *
 * Submit gates on the count check; weather is informational.
 */

import {h, clear, on, escapeHtml} from '../dom.js';
import {refs, fields as fieldsAPI, treatments as treatmentsAPI} from '../api.js';
import {generate as generateSensitivityTreatments} from '../step4/sensitivity_axes.js';

const ANCHOR_YEAR_DEFAULT = new Date().getFullYear();

export async function render(root, store, controller) {
    clear(root);
    const draft = store.draft;
    const step1 = store.getStep('step1') || {};
    const step4 = store.getStep('step4') || {};
    const year = step1.year || ANCHOR_YEAR_DEFAULT;

    const panel = h('div', {class: 'wizard-step-panel'});
    panel.appendChild(h('h2', {class: 'step-title'}, 'Review'));
    panel.appendChild(h('p', {class: 'step-description'},
        'Verify your selections and submit. Submitting hands off to a '
        + 'wizard-locked chat that runs the experiment in the background.'));

    panel.appendChild(buildKVBlock('Experiment', [
        ['Type', store.experimentType],
        ['Year', year],
        ['Treatments to run', `${store.treatments.length} / 99`],
    ]));

    const count = store.treatments.length;
    panel.appendChild(h('div', {
        class: 'wizard-flash ' + (count > 0 && count <= 99
            ? 'wizard-flash-info' : 'wizard-flash-error'),
    }, count > 99
        ? `${count} treatments — exceeds 99 cap.`
        : (count === 0
            ? 'No treatments yet — go back to Step 4.'
            : `${count} treatment${count === 1 ? '' : 's'} ready.`)));

    if (store.fields.length) {
        panel.appendChild(h('h3', {class: 'review-section-title'},
            `Fields (${store.fields.length})`));
        panel.appendChild(buildFieldGrid(store.fields));
    }

    if (store.treatments.length) {
        const grouped = groupTreatmentsByTreatmentId(store.treatments);
        const protocolsByTreatmentId = collectProtocolsByTreatmentId(
            store.treatments, step4);
        panel.appendChild(h('h3', {class: 'review-section-title'},
            `Treatments (${grouped.length})`));
        panel.appendChild(buildTreatmentGrid(grouped, protocolsByTreatmentId));

        // Final Treatments — the (treatment, field) pairings, default-collapsed.
        panel.appendChild(buildPairingsBlock(
            store.treatments, store.fields,
            step4.resolved_planting_dates || {},
            protocolsByTreatmentId, year));
    }

    const weatherBlock = h('div', {class: 'wizard-form-block'},
        h('h3', {class: 'wizard-form-subhead'}, 'Weather availability'));
    panel.appendChild(weatherBlock);
    runWeatherChecks(weatherBlock, store, year);

    root.appendChild(panel);
}

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

function buildFieldGrid(fields) {
    const grid = h('div', {class: 'review-card-grid'});
    for (const f of fields) {
        grid.appendChild(buildFieldCard(f));
    }
    return grid;
}

function buildFieldCard(f) {
    const card = h('div', {class: 'review-card review-card-field'});
    const header = h('div', {class: 'review-card-header'});
    header.appendChild(h('span', {class: 'review-card-icon'}, '📍'));
    header.appendChild(h('span', {class: 'review-card-title'}, f.name || 'Field'));
    header.appendChild(buildStatusBadge(f.status));
    card.appendChild(header);
    const meta = h('div', {class: 'review-card-meta'});
    if (f.latitude != null && f.longitude != null) {
        meta.appendChild(h('div', {class: 'review-card-row'},
            h('span', {class: 'review-card-label'}, 'Location'),
            h('span', {class: 'review-card-value'},
                `${f.latitude.toFixed(3)}, ${f.longitude.toFixed(3)}`
                + (f.elevation != null ? ` · ${f.elevation} m` : '')),
        ));
    }
    if (f.location_label) {
        meta.appendChild(h('div', {class: 'review-card-row'},
            h('span', {class: 'review-card-label'}, 'Place'),
            h('span', {class: 'review-card-value'}, f.location_label),
        ));
    }
    const soil = f.soil_id
        ? f.soil_id
        : (f.has_inline_soil ? 'inline' : '—');
    meta.appendChild(h('div', {class: 'review-card-row'},
        h('span', {class: 'review-card-label'}, 'Soil'),
        h('span', {class: 'review-card-value'}, soil),
    ));
    if (f.weather_source) {
        meta.appendChild(h('div', {class: 'review-card-row'},
            h('span', {class: 'review-card-label'}, 'Weather'),
            h('span', {class: 'review-card-value'}, f.weather_source),
        ));
    }
    card.appendChild(meta);
    card.appendChild(buildLibraryActions({
        kind: 'field',
        id: f.id,
        api: fieldsAPI,
        getStatus: () => f.status,
        setStatus: (s) => { f.status = s; },
        onChange: () => {
            // Repaint just this card so the badge + buttons reflect the new status.
            const next = buildFieldCard(f);
            card.replaceWith(next);
        },
    }));
    return card;
}

function buildTreatmentGrid(grouped, protocolsByTreatmentId) {
    const grid = h('div', {class: 'review-card-grid'});
    for (const g of grouped) {
        grid.appendChild(buildTreatmentCard(g, protocolsByTreatmentId[g.treatment_id]));
    }
    return grid;
}

function buildTreatmentCard(group, protocol) {
    const t = group.example;
    const card = h('div', {class: 'review-card review-card-treatment'});
    const header = h('div', {class: 'review-card-header'});
    header.appendChild(h('span', {class: 'review-card-icon'}, '🌱'));
    header.appendChild(h('span', {class: 'review-card-title'}, t.name || 'Treatment'));
    header.appendChild(buildStatusBadge(t.status));
    card.appendChild(header);
    const meta = h('div', {class: 'review-card-meta'});
    const cropLine = (t.crop_code || '?')
        + (t.cultivar_code ? ` · cv ${t.cultivar_code}` : ' · default cultivar');
    meta.appendChild(h('div', {class: 'review-card-row'},
        h('span', {class: 'review-card-label'}, 'Crop'),
        h('span', {class: 'review-card-value'}, cropLine),
    ));
    const planting = protocol && protocol.planting;
    if (planting) {
        let plantingStr = '';
        if (planting.mode === 'auto') {
            plantingStr = `Auto · ${planting.auto_source || '?'}`;
        } else if (planting.mode === 'fixed' && planting.pdoy != null) {
            plantingStr = planting.planting_month_day
                ? `${planting.planting_month_day} (DOY ${planting.pdoy})`
                : `DOY ${planting.pdoy}`;
        }
        if (planting.ppop != null) plantingStr += ` · ${planting.ppop} pl/m²`;
        meta.appendChild(h('div', {class: 'review-card-row'},
            h('span', {class: 'review-card-label'}, 'Planting'),
            h('span', {class: 'review-card-value'}, plantingStr || '—'),
        ));
    }
    const harvest = protocol && protocol.harvest;
    if (harvest && harvest.option) {
        let hStr = '';
        if (harvest.option === 'maturity') hStr = 'at maturity';
        else if (harvest.option === 'dap') hStr = `${harvest.dap || '?'} DAP`;
        else if (harvest.option === 'growth_stage') hStr = `stage ${harvest.hstg || '?'}`;
        else if (harvest.option === 'on_date') hStr = harvest.hdate || '—';
        meta.appendChild(h('div', {class: 'review-card-row'},
            h('span', {class: 'review-card-label'}, 'Harvest'),
            h('span', {class: 'review-card-value'}, hStr),
        ));
    }
    if (protocol) {
        const chips = managementChips(protocol);
        if (chips.length) {
            const row = h('div', {class: 'review-card-row'},
                h('span', {class: 'review-card-label'}, 'Mgmt'));
            const chipBox = h('span', {class: 'review-card-value'});
            for (const c of chips) {
                chipBox.appendChild(h('span', {class: 'review-mgmt-chip'}, c));
            }
            row.appendChild(chipBox);
            meta.appendChild(row);
        }
    }
    card.appendChild(meta);

    const fieldNoun = group.fieldCount === 1 ? 'field' : 'fields';
    card.appendChild(h('div', {class: 'review-card-footer'},
        `Applied to ${group.fieldCount} ${fieldNoun}`));
    card.appendChild(buildLibraryActions({
        kind: 'treatment',
        // The wizard-draft serializer keys the treatment id as ``id``
        // (not ``treatment_id``) on each pair row.
        id: t.id,
        api: treatmentsAPI,
        getStatus: () => t.status,
        setStatus: (s) => { t.status = s; },
        onChange: () => {
            const next = buildTreatmentCard(group, protocol);
            card.replaceWith(next);
        },
    }));
    return card;
}

// ---------------------------------------------------------------------------
// Status badge + Save / Publish buttons (per card)
// ---------------------------------------------------------------------------

function buildStatusBadge(status) {
    const cls = 'review-status-badge review-status-' + (status || 'used');
    const label = status === 'published' ? 'Published'
                : status === 'saved' ? 'Saved'
                : 'Used';
    return h('span', {class: cls, style: {marginLeft: 'auto'}}, label);
}

/**
 * Per-card library-action footer with Save / Publish buttons. The
 * current status is highlighted; clicking the OTHER state PATCHes the
 * row's status and then re-renders the card via the supplied
 * ``onChange`` callback.
 *
 * Owner-only (the wizard always creates rows owned by the current user,
 * so we don't need to gate further) — published rows owned by other
 * users would never appear in the wizard's own field/treatment list.
 */
function buildLibraryActions({api, id, getStatus, setStatus, onChange}) {
    const wrap = h('div', {class: 'review-card-actions'});
    async function patchStatus(next) {
        try {
            const r = await api.patch(id, {status: next});
            const newStatus = (r && (r.field || r.treatment) || {}).status || next;
            setStatus(newStatus);
            onChange();
        } catch (e) {
            const flash = h('div', {class: 'wizard-flash wizard-flash-error',
                style: {marginTop: '0.4rem'}},
                'Status update failed: ' + e.message);
            wrap.appendChild(flash);
            setTimeout(() => { try { wrap.removeChild(flash); } catch (_e) {} },
                4000);
        }
    }
    const cur = getStatus() || 'used';
    const saveBtn = h('button', {
        type: 'button',
        class: 'btn btn-sm '
               + (cur === 'saved' ? 'btn-primary' : 'btn-secondary'),
    }, cur === 'saved' ? 'Saved ✓' : 'Save for Future Use');
    const pubBtn = h('button', {
        type: 'button',
        class: 'btn btn-sm '
               + (cur === 'published' ? 'btn-primary' : 'btn-secondary'),
    }, cur === 'published' ? 'Published ✓' : 'Publish');
    on(saveBtn, 'click', () => {
        if (cur === 'saved') return;
        patchStatus('saved');
    });
    on(pubBtn, 'click', () => {
        if (cur === 'published') return;
        patchStatus('published');
    });
    wrap.appendChild(saveBtn);
    wrap.appendChild(pubBtn);
    return wrap;
}

function managementChips(p) {
    const out = [];
    const fert = (p.fertilizer || []).length;
    if (fert) out.push(`fert ×${fert}`);
    const irr = p.irrigation && Array.isArray(p.irrigation.events)
        ? p.irrigation.events.length
        : (Array.isArray(p.irrigation) ? p.irrigation.length : 0);
    if (irr) out.push(`irr ×${irr}`);
    const till = (p.tillage || []).length;
    if (till) out.push(`till ×${till}`);
    const chem = (p.chemical || []).length;
    if (chem) out.push(`chem ×${chem}`);
    const res = (p.residue || []).length;
    if (res) out.push(`res ×${res}`);
    return out;
}

// ---------------------------------------------------------------------------
// Pairings (collapsible)
// ---------------------------------------------------------------------------

function buildPairingsBlock(pairs, fields, resolvedDates,
                              protocolsByTreatmentId, year) {
    const fieldById = {};
    for (const f of fields) fieldById[f.id] = f;
    const block = h('details', {class: 'review-pairings-block'});
    block.appendChild(h('summary', {class: 'review-pairings-summary'},
        `Treatment × Field pairings (${pairs.length})`));
    const tbl = h('table', {class: 'review-pair-table'});
    const thead = h('thead', {},
        h('tr', {},
            h('th', {}, 'Treatment'),
            h('th', {}, 'Field'),
            h('th', {}, 'Planting date'),
        ),
    );
    tbl.appendChild(thead);
    const tbody = h('tbody');
    for (const p of pairs) {
        const f = fieldById[p.field_id];
        const fieldLabel = f
            ? `${f.name} · ${f.latitude.toFixed(3)}, ${f.longitude.toFixed(3)}`
            : '(no field bound)';
        const treatmentLabel = (p.name || '—')
            + (p.crop_code ? ` · ${p.crop_code}` : '')
            + (p.cultivar_code ? `/${p.cultivar_code}` : '');
        tbody.appendChild(h('tr', {},
            h('td', {}, treatmentLabel),
            h('td', {}, fieldLabel),
            h('td', {class: 'mono'},
                pairPlantingLabel(p, resolvedDates,
                    (protocolsByTreatmentId || {})[p.id], year)),
        ));
    }
    tbl.appendChild(tbody);
    block.appendChild(tbl);
    return block;
}

/**
 * Compute the planting-date label for a single (treatment, field) row.
 * Auto-mode pairs read from the per-pair resolved-dates dict; Fixed-Date
 * pairs materialise pdoy + the experiment year. Returns "—" only when
 * neither path can produce a date (typically a draft that bypassed the
 * Step 4 resolution somehow).
 */
function pairPlantingLabel(pair, resolvedDates, protocol, year) {
    // Pair rows expose the treatment's id as ``pair.id`` (per the
    // wizard-draft serializer); the resolver keyed entries by
    // ``${treatment_id}:${field_id}`` using the same id, so we read it
    // back the same way here.
    const resolved = resolvedDates[`${pair.id}:${pair.field_id}`];
    if (resolved && resolved.date) {
        return `${resolved.date} · Auto (DOY ${resolved.doy})`;
    }
    const planting = protocol && protocol.planting;
    if (planting && planting.mode === 'fixed' && planting.pdoy != null && year) {
        const d = doyToDate(year, planting.pdoy);
        const iso = d.toISOString().slice(0, 10);
        return `${iso} · Fixed (DOY ${planting.pdoy})`;
    }
    if (planting && planting.pdoy != null && year) {
        // Legacy / mode-less shape — still a Fixed-Date semantic.
        const d = doyToDate(year, planting.pdoy);
        return `${d.toISOString().slice(0, 10)} (DOY ${planting.pdoy})`;
    }
    return '—';
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function groupTreatmentsByTreatmentId(treatments) {
    // The wizard-draft serializer returns each WizardDraftTreatment row
    // with the *Treatment's* id under ``id`` (not ``treatment_id``):
    //   _serialize_treatment_brief: {'id', 'name', 'crop_code', 'cultivar_code'}
    //   _serialize_draft.treatments: {'ordering', 'field_id', **brief}
    // So the canonical treatment-id key on these rows is ``t.id``.
    const byId = new Map();
    for (const t of treatments) {
        const id = t.id;
        if (!byId.has(id)) {
            byId.set(id, {treatment_id: id, example: t, fieldCount: 0});
        }
        byId.get(id).fieldCount += 1;
    }
    return Array.from(byId.values());
}

/**
 * Look up the per-treatment protocol payload from step4's state. Different
 * experiment types stash protocols in different keys; we just walk them
 * and pull the matching crop/planting bundle so the review card can
 * surface the planting/harvest summary.
 */
function collectProtocolsByTreatmentId(treatments, step4) {
    const out = {};
    const protocols = collectProtocols(step4);
    if (!protocols.length) return out;
    // Best-effort: match by name first, then fall back to first protocol
    // when there's only one (single / mc). The serializer keys the
    // treatment id as ``t.id``.
    for (const t of treatments) {
        const tid = t.id;
        if (out[tid]) continue;
        let match = null;
        if (protocols.length === 1) {
            match = protocols[0];
        } else if (t.name) {
            match = protocols.find((p) => (p.name || '').toLowerCase()
                                          === (t.name || '').toLowerCase());
        }
        if (!match) {
            match = protocols.find((p) => p.crop_code === t.crop_code
                && (p.cultivar_code || '') === (t.cultivar_code || ''));
        }
        if (!match) match = protocols[0];
        out[tid] = match;
    }
    return out;
}

function collectProtocols(step4) {
    if (Array.isArray(step4.protocols)) return step4.protocols;
    if (step4.protocol) return [step4.protocol];
    if (step4.template) return [step4.template];
    // Sensitivity: expand the sweep into the same N protocols that the
    // lock hook materialised, so per-treatment cards and the pairings
    // table can read each row's actual planting / cultivar / management
    // block (matching by name). Without this, every sensitivity row
    // shows the same baseline planting and the swept axis disappears
    // from the review.
    if (step4 && step4.sensitivity && step4.sensitivity.category
        && step4.baseline) {
        try {
            const generated = generateSensitivityTreatments(
                step4.sensitivity, step4.baseline);
            if (generated && generated.length) return generated;
        } catch (_e) { /* fall through to baseline */ }
    }
    if (step4.baseline) return [step4.baseline];
    return [];
}

function buildKVBlock(title, rows) {
    const block = h('div', {class: 'review-section'},
        h('div', {class: 'review-section-title'}, title));
    for (const [label, value] of rows) {
        block.appendChild(h('div', {class: 'review-row'},
            h('span', {class: 'review-label'}, label),
            h('span', {class: 'review-value'},
                value != null && value !== '' ? String(value) : '--'),
        ));
    }
    return block;
}

// ---------------------------------------------------------------------------
// Weather availability (informational)
// ---------------------------------------------------------------------------

async function runWeatherChecks(host, store, year) {
    const sources = uniqueWeatherSources(store);
    if (!sources.length) {
        host.appendChild(h('p', {class: 'step-description'},
            'No weather source picked yet — set one on Step 3.'));
        return;
    }
    const range = computeDateRange(store, year);
    if (!range) {
        host.appendChild(h('p', {class: 'step-description'},
            'Could not derive a date range — Step 4 needs a planting date.'));
        return;
    }
    host.appendChild(h('p', {class: 'step-description'},
        `Checking ${range.start_date} → ${range.end_date}…`));
    const list = h('ul', {class: 'review-list'});
    host.appendChild(list);
    for (const src of sources) {
        const row = h('li', {}, `${src}: checking…`);
        list.appendChild(row);
        try {
            const r = await refs.weatherAvailability(src, range.start_date, range.end_date);
            const result = (r.results && r.results[0]) || {};
            const cov = result.coverage || 'unknown';
            const days = result.days_available != null
                ? `${result.days_available}/${result.days_requested || '?'} days`
                : '';
            const cls = cov === 'all' ? 'wizard-pill'
                       : cov === 'partial' ? 'wizard-pill wizard-pill-warning'
                       : 'wizard-pill wizard-pill-error';
            row.innerHTML = '';
            row.appendChild(h('strong', {}, src));
            row.appendChild(h('span', {class: cls, style: {marginLeft: '0.5rem'}}, cov));
            if (days) row.appendChild(h('span',
                {style: {marginLeft: '0.5rem', color: 'var(--text-muted)'}}, days));
        } catch (e) {
            row.innerHTML = '';
            row.appendChild(h('strong', {}, src));
            row.appendChild(h('span', {class: 'wizard-pill wizard-pill-error',
                style: {marginLeft: '0.5rem'}}, 'check failed'));
            row.appendChild(h('span',
                {style: {marginLeft: '0.5rem', color: 'var(--text-muted)'}},
                escapeHtml(e.message)));
        }
    }
}

function uniqueWeatherSources(store) {
    const set = new Set();
    for (const f of store.fields) {
        if (f.weather_source) set.add(f.weather_source);
    }
    const step3 = store.getStep('step3') || {};
    if (step3.weather_source) set.add(step3.weather_source);
    return Array.from(set);
}

/**
 * Derive the experiment's overall date range from year + the earliest
 * planting DOY (with start_offset_days) → latest planting DOY + num_years.
 * Auto-mode pairs use the resolved DOY; Fixed-mode uses the planting.pdoy.
 */
function computeDateRange(store, year) {
    const step4 = store.getStep('step4') || {};
    const protos = collectProtocols(step4);
    if (!protos.length) return null;
    const resolved = step4.resolved_planting_dates || {};
    let earliestStart = null;
    let latestEnd = null;
    function consider(doy, sc) {
        const start = doyToDate(year, doy + (sc?.start_offset_days || -30));
        const numYears = parseInt(sc?.num_years || 1, 10);
        const end = addDays(doyToDate(year, doy), numYears * 365);
        if (!earliestStart || start < earliestStart) earliestStart = start;
        if (!latestEnd || end > latestEnd) latestEnd = end;
    }
    // Resolved Auto-mode dates trump any protocol DOY when present.
    const resolvedDoys = Object.values(resolved).map((r) => r && r.doy).filter((d) => d != null);
    for (const doy of resolvedDoys) {
        // Match each resolved doy against any protocol's sc; with multiple
        // protocols this is approximate but conservative for the range.
        consider(doy, protos[0].simulation_controls);
    }
    for (const p of protos) {
        const planting = p.planting || {};
        if (planting.mode === 'auto' && resolvedDoys.length) continue;
        const pdoy = planting.pdoy;
        if (pdoy == null) continue;
        consider(pdoy, p.simulation_controls);
    }
    if (!earliestStart || !latestEnd) return null;
    return {
        start_date: earliestStart.toISOString().slice(0, 10),
        end_date: latestEnd.toISOString().slice(0, 10),
    };
}

function doyToDate(year, doy) {
    const d = new Date(Date.UTC(year, 0, 1));
    d.setUTCDate(d.getUTCDate() + (parseInt(doy, 10) - 1));
    return d;
}

function addDays(d, n) {
    const next = new Date(d.getTime());
    next.setUTCDate(next.getUTCDate() + n);
    return next;
}

export function validate(store) {
    if (!store.treatments.length) {
        return {ok: false, message: 'No treatments to run — finish Step 4 first.'};
    }
    if (store.treatments.length > 99) {
        return {ok: false, message: `${store.treatments.length} exceeds the 99-treatment cap.`};
    }
    return {ok: true};
}
