/**
 * Auto-mode planting-date resolver — invoked at the Step 4 → Step 5
 * lock for every (treatment, field) pair where the treatment carries
 * `planting.mode === 'auto'`. Hits the new
 * /dssat/api/planting-dates/resolve/ endpoint to look up each pair's
 * planting DOY from the chosen in-situ raster, then persists the
 * resolved absolute dates into ``step_state.step4.resolved_planting_dates``
 * keyed by ``"<treatment_id>:<field_id>"``.
 *
 * Strict-no-fallback semantic (per user direction):
 *
 *   * Any failed resolution (point outside coverage, raster nodata,
 *     network error) throws a descriptive Error from this helper. The
 *     caller — Step 4's onLockBeforeNext — is expected to surface the
 *     error in the wizard's flash bar and abort the transition. The
 *     user stays on Step 4 to fix the geometry, switch to Fixed Date,
 *     or pick a different source.
 *   * On any failure, no partial resolved-dates are written — the
 *     existing dict in step_state is left untouched.
 *
 * The MC service (and downstream ensemble/single/batch) reads the
 * resolved dates at submit time rather than re-doing the in-situ
 * lookup, so what the user saw at Next time is exactly what runs.
 *
 * Public API:
 *
 *   await resolveAutoPlantingDatesForPairs(store, pairs)
 *     pairs: Array<{
 *       treatment_id:        string,    // committed Treatment row id
 *       field_id:            string,    // committed Field row id
 *       treatment_payload:   Object,    // the editor's value() — needs
 *                                       //   .planting.mode, .auto_source
 *       field:               Object,    // store.fields[i] — needs
 *                                       //   .latitude, .longitude
 *     }>
 */

import {refs} from './api.js';

/**
 * Bucket pairs by source so we batch one HTTP call per source. The
 * resolver endpoint accepts a single source per call (each in-situ
 * raster table is a separate query).
 */
function bucketBySource(pairs) {
    const out = new Map();
    for (const p of pairs) {
        const planting = (p.treatment_payload && p.treatment_payload.planting) || {};
        if (planting.mode !== 'auto') continue;
        const source = planting.auto_source;
        if (!source) {
            throw new Error(
                `Treatment "${p.treatment_payload.crop_code || '?'}" is in `
                + `Auto planting mode but no source is set. Pick a source `
                + `in Planting & Harvest, or switch to Fixed Date.`);
        }
        if (!out.has(source)) out.set(source, []);
        out.get(source).push(p);
    }
    return out;
}

/**
 * Resolve auto-mode planting dates for the given (treatment, field)
 * pairs. Throws on any failure (no partial commit). On success, writes
 * `step_state.step4.resolved_planting_dates` and returns the resolution
 * map (keyed by `${treatment_id}:${field_id}`) for the caller to
 * surface in UI if useful.
 */
export async function resolveAutoPlantingDatesForPairs(store, pairs) {
    const buckets = bucketBySource(pairs);
    if (!buckets.size) {
        // Nothing to do — every pair is Fixed-Date mode. Leave the
        // existing resolved-dates dict untouched.
        return {};
    }

    const step1 = store.getStep('step1') || {};
    const year = parseInt(step1.year, 10);
    if (!year || isNaN(year)) {
        throw new Error(
            'Auto planting needs a year — set the experiment year on Step 1.');
    }

    // Run each source-bucket in parallel; failures from any one bubble
    // up via Promise.all and abort the whole resolution.
    const allResolved = {};
    const failures = [];

    await Promise.all(Array.from(buckets.entries()).map(async ([source, pairsForSrc]) => {
        const points = pairsForSrc.map((p) => [p.field.latitude, p.field.longitude]);
        let response;
        try {
            response = await refs.resolvePlantingDates({source, year, points});
        } catch (e) {
            failures.push(`source ${source}: ${e.message}`);
            return;
        }
        const resolved = (response && response.resolved) || [];
        if (resolved.length !== pairsForSrc.length) {
            failures.push(
                `source ${source}: backend returned ${resolved.length} `
                + `rows for ${pairsForSrc.length} points`);
            return;
        }
        for (let i = 0; i < pairsForSrc.length; i++) {
            const pair = pairsForSrc[i];
            const r = resolved[i];
            const fieldName = pair.field.name
                || `${pair.field.latitude}, ${pair.field.longitude}`;
            const trtName = (pair.treatment_payload && pair.treatment_payload.crop_code)
                || pair.treatment_id;
            if (!r || r.date == null) {
                failures.push(
                    `Treatment "${trtName}" at field "${fieldName}" `
                    + `is uncovered by source "${source}". Switch to `
                    + `Fixed Date or pick a different source.`);
                continue;
            }
            const key = `${pair.treatment_id}:${pair.field_id}`;
            allResolved[key] = {
                date: r.date,         // 'YYYY-MM-DD'
                doy: r.doy,           // int 1-366
                source,
                lat: pair.field.latitude,
                lon: pair.field.longitude,
            };
        }
    }));

    if (failures.length) {
        // Don't half-commit — the caller will block the transition and
        // the user fixes whatever is wrong before retrying.
        throw new Error(failures.join('  ·  '));
    }

    // Persist into step_state. Merge with any prior dict so re-running
    // the same lock on a partially-edited draft preserves prior pairs
    // that aren't currently in scope (defensive — typical lock flow
    // resolves all pairs anyway).
    const cur = (store.getStep('step4') || {}).resolved_planting_dates || {};
    const next = {...cur, ...allResolved};
    store.setStep('step4', {resolved_planting_dates: next});
    // ``setStep`` queues a debounced PATCH. The caller's next move is
    // typically ``store.setTreatments(rows)`` which fires a synchronous
    // PATCH and replaces ``store.draft`` with the server's response —
    // racing the still-pending resolved_planting_dates patch and wiping
    // it. Force a flush here so the server has the resolved dates before
    // setTreatments runs.
    if (typeof store.flushPending === 'function') {
        await store.flushPending();
    }
    return allResolved;
}

/**
 * Helper for the caller to scrub stale entries from
 * resolved_planting_dates that no longer correspond to a live
 * (treatment, field) pair. Call this AFTER store.setTreatments(...)
 * commits so the stored set matches the freshly-attached pair list.
 *
 * @param {Object} store
 * @param {Array<{treatment_id, field_id}>} keepPairs
 */
export function pruneResolvedPlantingDates(store, keepPairs) {
    const keep = new Set(keepPairs.map((p) => `${p.treatment_id}:${p.field_id}`));
    const cur = (store.getStep('step4') || {}).resolved_planting_dates || {};
    const next = {};
    for (const [k, v] of Object.entries(cur)) {
        if (keep.has(k)) next[k] = v;
    }
    store.setStep('step4', {resolved_planting_dates: next});
}
