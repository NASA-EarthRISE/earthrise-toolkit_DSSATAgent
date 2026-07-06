/*
 * Page logic for DSSAT Preferences / DSSAT System Config.
 *
 * Shared across both pages — reads window.CONFIG_PAGE to know which scope
 * (user vs system) to save to. Uses the reusable `buildCropCard` (from
 * crop_card.js), `buildManagementPractices` (from management_practices.js),
 * and `EventTable` (from event_table.js) helpers.
 *
 *   Tab 1 (General) and Tab 2 (Simulation Options) are plain forms, saved
 *   via `data-config-section` submission handlers.
 *
 *   Tab 3 (Management Fallbacks) renders `buildManagementPractices` bound to
 *   the null crop (global fallback scope).
 *
 *   Tab 4 (My Crops / System Per-Crop Defaults) renders one `buildCropCard`
 *   per configured crop, each containing a `buildManagementPractices` for
 *   its per-crop management events.
 */

(function () {
    'use strict';

    const CONFIG = window.CONFIG_PAGE || {
        scope: 'user', availableCrops: [], configuredCrops: [],
    };

    const SUBPATH = window.SUBPATH || '';
    const csrfToken = getCookie('csrftoken');

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------
    function getCookie(name) {
        const match = document.cookie.split(';').find(c => c.trim().startsWith(name + '='));
        return match ? decodeURIComponent(match.split('=')[1]) : '';
    }

    function jsonFetch(url, {method = 'GET', body = null} = {}) {
        const init = {
            method,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
            },
        };
        if (body != null) init.body = JSON.stringify(body);
        const fullUrl = url.startsWith('/') ? SUBPATH + url : url;
        return fetch(fullUrl, init).then(async (resp) => {
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                const err = data.error || `HTTP ${resp.status}`;
                throw new Error(err);
            }
            return data;
        });
    }

    function scopeQS() {
        return CONFIG.scope === 'system' ? '?scope=system' : '';
    }

    function showBanner(target, message, kind = 'success') {
        let banner = target.matches('.save-banner') ? target : target.querySelector('.save-banner');
        if (!banner) {
            banner = document.createElement('div');
            banner.className = 'save-banner';
            target.appendChild(banner);
        }
        banner.textContent = message;
        banner.className = `save-banner ${kind}`;
        setTimeout(() => banner.classList.remove(kind), 4000);
    }

    // -------------------------------------------------------------------------
    // Tab switching
    // -------------------------------------------------------------------------
    document.querySelectorAll('.mgmt-tab').forEach((tab) => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.mgmt-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            const panel = document.getElementById('tab-' + tab.dataset.tab);
            if (panel) panel.classList.add('active');
        });
    });

    // -------------------------------------------------------------------------
    // Tabs 1–2: section-level save (general / sim-options)
    // -------------------------------------------------------------------------
    document.querySelectorAll('form[data-config-section]').forEach((form) => {
        form.addEventListener('submit', async (ev) => {
            ev.preventDefault();
            const section = form.dataset.configSection;
            const body = {};
            for (const el of form.querySelectorAll('input, select')) {
                if (!el.name) continue;
                body[el.name] = el.value;
            }
            try {
                await jsonFetch(`/dssat/api/config/${section}/${scopeQS()}`, {
                    method: 'POST', body,
                });
                showBanner(form, 'Saved.', 'success');
            } catch (err) {
                showBanner(form, `Save failed: ${err.message}`, 'error');
            }
        });
    });

    // -------------------------------------------------------------------------
    // Tab 3: Management Fallbacks — global fallback events per practice.
    // Stored as DSSATConfig rows with JSON values (fallback_fertilizer, etc).
    // -------------------------------------------------------------------------
    const fallbacksRoot = document.getElementById('fallbacks-root');
    const fallbacksBanner = document.getElementById('fallbacks-banner');
    const saveFallbacksBtn = document.getElementById('btn-save-fallbacks');
    let fallbacksMP = null;

    async function initFallbacksTab() {
        if (!fallbacksRoot) return;
        let initial;
        try {
            const resp = await jsonFetch(`/dssat/api/config/fallbacks/${scopeQS()}`);
            initial = resp.data || {};
        } catch {
            initial = {};
        }
        fallbacksMP = buildManagementPractices(fallbacksRoot, {
            cropCode: null,
            initialData: _unpackManagementData(initial),
        });
    }

    if (saveFallbacksBtn) {
        saveFallbacksBtn.addEventListener('click', async () => {
            if (!fallbacksMP) return;
            const payload = _packManagementData(fallbacksMP.getValue());
            try {
                await jsonFetch(`/dssat/api/config/fallbacks/${scopeQS()}`, {
                    method: 'POST', body: payload,
                });
                showBanner(fallbacksBanner, 'Saved.', 'success');
            } catch (err) {
                showBanner(fallbacksBanner, `Save failed: ${err.message}`, 'error');
            }
        });
    }

    // Re-shape for server: fallback_fertilizer / fallback_irrigation / etc.
    function _packManagementData(data) {
        return {
            fallback_fertilizer: data.fertilizer,
            fallback_irrigation: data.irrigation,
            fallback_harvest: data.harvest,
            fallback_tillage: data.tillage,
            fallback_chemical: data.chemical,
            fallback_residue: data.residue,
        };
    }

    function _unpackManagementData(data) {
        return {
            fertilizer: data.fallback_fertilizer || null,
            irrigation: data.fallback_irrigation || null,
            harvest: data.fallback_harvest || null,
            tillage: data.fallback_tillage || null,
            chemical: data.fallback_chemical || null,
            residue: data.fallback_residue || null,
        };
    }

    // -------------------------------------------------------------------------
    // Tab 4: Per-crop defaults (one crop_card per configured crop)
    // -------------------------------------------------------------------------
    const container = document.getElementById('crop-cards-container');
    const emptyMsg = document.getElementById('no-crops-message');

    async function buildCropCardForCrop(cropCode, existingData = null) {
        const crop = (CONFIG.availableCrops || []).find(c => c.code === cropCode) ||
                     {code: cropCode, name: cropCode};

        const {card, body, footer, setSaving} = buildCropCard({
            cropCode: crop.code,
            cropName: crop.name,
            initiallyExpanded: true,
            deleteLabel: 'Remove this crop',
            onDelete: async () => {
                if (!confirm(`Remove all defaults for ${crop.name}?`)) return;
                setSaving(true);
                try {
                    await jsonFetch(`/dssat/api/config/crop-default/${crop.code}/${scopeQS()}`, {
                        method: 'DELETE',
                    });
                    card.remove();
                    updateEmptyState();
                } finally {
                    setSaving(false);
                }
            },
        });

        // Body: scalar fields + management practices
        const form = document.createElement('form');
        form.className = 'mgmt-form crop-form';
        form.innerHTML = `
            <div class="form-grid">
                <div class="form-group">
                    <label>Default Cultivar Code</label>
                    <input type="text" name="cultivar_code" maxlength="6" placeholder="(auto-select)">
                </div>
                <div class="form-group">
                    <label>Plant Population (plants/m²)</label>
                    <input type="number" name="plant_population" step="0.1" placeholder="7.0">
                </div>
            </div>
            <div class="form-grid">
                <div class="form-group">
                    <label>Row Spacing (cm)</label>
                    <input type="number" name="row_spacing" step="1" placeholder="75">
                </div>
                <div class="form-group">
                    <label>Planting Method</label>
                    <input type="text" name="planting_method" maxlength="10" placeholder="(default)">
                </div>
            </div>
            <div class="crop-practices-slot"></div>
        `;
        body.appendChild(form);

        const practicesSlot = form.querySelector('.crop-practices-slot');
        const practices = buildManagementPractices(practicesSlot, {
            cropCode: crop.code,
            initialData: existingData ? _cropDataToMP(existingData) : {},
        });

        // Populate scalar fields from existingData (if provided)
        if (existingData) {
            for (const key of ['cultivar_code', 'plant_population', 'row_spacing', 'planting_method']) {
                const input = form.querySelector(`[name="${key}"]`);
                if (input && existingData[key] != null) input.value = existingData[key];
            }
        }

        // Save button in footer
        const saveBtn = document.createElement('button');
        saveBtn.type = 'button';
        saveBtn.className = 'btn-primary btn-sm';
        saveBtn.textContent = 'Save';
        saveBtn.addEventListener('click', async () => {
            const mpData = practices.getValue();
            const payload = {
                cultivar_code: form.cultivar_code.value.trim(),
                plant_population: form.plant_population.value.trim() || null,
                row_spacing: form.row_spacing.value.trim() || null,
                planting_method: form.planting_method.value.trim(),
                fertilizer: mpData.fertilizer,
                irrigation: mpData.irrigation,
                harvest: mpData.harvest,
                tillage: mpData.tillage,
                chemical: mpData.chemical,
                residue: mpData.residue,
            };
            setSaving(true);
            try {
                await jsonFetch(`/dssat/api/config/crop-default/${crop.code}/${scopeQS()}`, {
                    method: 'POST', body: payload,
                });
                showBanner(body, 'Saved.', 'success');
            } catch (err) {
                showBanner(body, `Save failed: ${err.message}`, 'error');
            } finally {
                setSaving(false);
            }
        });
        footer.insertBefore(saveBtn, footer.firstChild);

        // Lazy-load existing data if not supplied
        if (!existingData) {
            try {
                const resp = await jsonFetch(`/dssat/api/config/crop-default/${crop.code}/${scopeQS()}`);
                const data = resp.data || {};
                for (const key of ['cultivar_code', 'plant_population', 'row_spacing', 'planting_method']) {
                    const input = form.querySelector(`[name="${key}"]`);
                    if (input && data[key] != null) input.value = data[key];
                }
                await practices.setValue(_cropDataToMP(data));
            } catch {
                // No row yet — fine.
            }
        }

        return card;
    }

    // Map CropDefault server shape → buildManagementPractices initialData
    function _cropDataToMP(data) {
        const out = {
            fertilizer: Array.isArray(data.fertilizer) ? data.fertilizer : null,
            tillage: Array.isArray(data.tillage) ? data.tillage : null,
            chemical: Array.isArray(data.chemical) ? data.chemical : null,
            residue: Array.isArray(data.residue) ? data.residue : null,
            irrigation: null,
            harvest: null,
        };
        // Irrigation: accept event array, dict with {method, ...}, or null
        if (data.irrigation && !Array.isArray(data.irrigation)) {
            out.irrigation = data.irrigation;
        } else if (Array.isArray(data.irrigation)) {
            out.irrigation = {method: 'fixed', events: data.irrigation};
        }
        // Harvest: accept {option, date} or {hdate, ...}; prefer option-based shape
        if (data.harvest && !Array.isArray(data.harvest)) {
            if (data.harvest.option !== undefined) {
                out.harvest = data.harvest;
            } else if (data.harvest.hdate) {
                out.harvest = {option: 'on_date', date: data.harvest.hdate};
            }
        }
        return out;
    }

    function updateEmptyState() {
        if (!container || !emptyMsg) return;
        emptyMsg.classList.toggle('is-hidden', !!container.children.length);
    }

    async function loadInitialCrops() {
        if (!container) return;
        for (const code of CONFIG.configuredCrops || []) {
            const card = await buildCropCardForCrop(code);
            container.appendChild(card);
        }
        updateEmptyState();
    }

    // -------------------------------------------------------------------------
    // Crop picker modal (shared by add-crop buttons on Tab 4 and curated cards)
    // -------------------------------------------------------------------------
    function openCropPicker(onPick, {excludeConfigured = true} = {}) {
        const excluded = excludeConfigured
            ? new Set(
                Array.from(container ? container.querySelectorAll('.crop-card') : [])
                     .map(c => c.dataset.cropCode)
            )
            : new Set();
        const available = (CONFIG.availableCrops || []).filter(c => !excluded.has(c.code));

        const groupOptions = Array.from(new Set(
            available.map(c => c.crop_group || '').filter(Boolean)
        )).sort();

        const modal = document.createElement('div');
        modal.className = 'crop-picker-modal open';
        modal.innerHTML = `
            <div class="crop-picker-dialog">
                <h3 style="margin: 0 0 0.75rem;">Select a Crop</h3>
                <div style="display:flex; gap:0.5rem; margin-bottom:0.6rem;">
                    <input type="text" class="crop-picker-search"
                           placeholder="Search by code or name..."
                           style="flex:1;">
                    <select class="crop-picker-group-filter"
                            style="max-width:11rem; padding:0.5rem 0.7rem; background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); border-radius:0.4rem; color:var(--text-primary);">
                        <option value="">All groups</option>
                        ${groupOptions.map(g => `<option value="${g}">${g.charAt(0).toUpperCase() + g.slice(1)}</option>`).join('')}
                    </select>
                </div>
                <div class="crop-picker-list"></div>
                <div class="crop-picker-footer">
                    <button type="button" class="btn-sm crop-picker-cancel">Cancel</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        const list = modal.querySelector('.crop-picker-list');
        const search = modal.querySelector('.crop-picker-search');
        const groupFilter = modal.querySelector('.crop-picker-group-filter');

        function renderList() {
            const f = (search.value || '').toLowerCase();
            const g = groupFilter.value || '';
            list.innerHTML = '';
            const filtered = available.filter(c => {
                if (g && (c.crop_group || '') !== g) return false;
                if (!f) return true;
                return c.code.toLowerCase().includes(f)
                    || (c.name || '').toLowerCase().includes(f);
            });
            if (!filtered.length) {
                list.innerHTML = '<div style="padding:1rem; text-align:center; color: var(--text-muted); font-size:0.85rem;">No matching crops</div>';
                return;
            }
            filtered.forEach(c => {
                const item = document.createElement('div');
                item.className = 'crop-picker-item';
                item.innerHTML = `<span class="code">${c.code}</span> ${c.name || c.code}`
                    + (c.crop_group ? ` <span style="color:var(--text-muted); font-size:0.75rem; margin-left:auto;">${c.crop_group}</span>` : '');
                item.addEventListener('click', () => {
                    modal.remove();
                    onPick(c.code);
                });
                list.appendChild(item);
            });
        }

        search.addEventListener('input', renderList);
        groupFilter.addEventListener('change', renderList);
        modal.querySelector('.crop-picker-cancel').addEventListener('click', () => modal.remove());
        modal.addEventListener('click', (ev) => {
            if (ev.target === modal) modal.remove();
        });
        renderList();
        search.focus();
    }

    // Expose globally so config_system.js can reuse the picker
    window.openCropPicker = openCropPicker;

    const addCropBtn = document.getElementById('btn-add-crop');
    if (addCropBtn) {
        addCropBtn.addEventListener('click', () => {
            openCropPicker(async (cropCode) => {
                const card = await buildCropCardForCrop(cropCode);
                container.appendChild(card);
                updateEmptyState();
                card.scrollIntoView({behavior: 'smooth', block: 'center'});
            });
        });
    }

    // -------------------------------------------------------------------------
    // Kickoff
    // -------------------------------------------------------------------------
    loadInitialCrops();
    initFallbacksTab();

})();
