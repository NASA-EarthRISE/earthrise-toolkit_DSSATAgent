/*
 * DSSAT System Config — admin-only additions.
 *
 * Adds Tab 5 "Curated Wizard Dropdowns" UI on top of the shared tabs served
 * by config_preferences.js. This script loads AFTER config_preferences.js
 * in system_config.html, so `window.openCropPicker` (and global helpers
 * `buildCropCard` / `EventTable` / `buildManagementPractices`) are available.
 */

(function () {
    'use strict';

    const CONFIG = window.CONFIG_PAGE || {};
    if (!CONFIG.isSystem) return;

    const SUBPATH = window.SUBPATH || '';
    const csrfToken = (document.cookie.split(';').find(c => c.trim().startsWith('csrftoken=')) || '').split('=')[1] || '';

    async function jsonFetch(url, {method = 'GET', body = null} = {}) {
        const init = {
            method,
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken},
        };
        if (body != null) init.body = JSON.stringify(body);
        const fullUrl = url.startsWith('/') ? SUBPATH + url : url;
        const resp = await fetch(fullUrl, init);
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
        return data;
    }

    // Code categories for curation — maps CuratedCropOptions field to
    // (label, codes-API-category, codes-API-context-or-null)
    const CURATED_CATEGORIES = [
        {field: 'planting_methods',        label: 'Planting Methods',        category: 'planting_method', context: null},
        {field: 'fertilizer_materials',    label: 'Fertilizer Materials',    category: 'fertilizer',      context: null},
        {field: 'fertilizer_applications', label: 'Fertilizer Applications', category: 'application_method', context: 'fertilizer'},
        {field: 'irrigation_methods',      label: 'Irrigation Methods',      category: 'irrigation',      context: null},
        {field: 'chemical_materials',      label: 'Chemical Materials',      category: 'chemical',        context: null},
        {field: 'chemical_applications',   label: 'Chemical Applications',   category: 'application_method', context: 'chemical'},
        {field: 'residue_materials',       label: 'Residue Materials',       category: 'residue',         context: null},
    ];

    const _codesCache = new Map();
    async function loadFullCodes(category, context) {
        const key = `${category}|${context || ''}`;
        if (_codesCache.has(key)) return _codesCache.get(key);
        const qs = context ? `?context=${context}` : '';
        const data = await jsonFetch(`/dssat/api/codes/${encodeURIComponent(category)}/${qs}`);
        const result = data.codes || [];
        _codesCache.set(key, result);
        return result;
    }

    const container = document.getElementById('curated-cards-container');
    const addBtn = document.getElementById('btn-add-curated-crop');
    if (!container || !addBtn) return;

    async function buildCuratedCard(cropCode) {
        const crop = (CONFIG.availableCrops || []).find(c => c.code === cropCode)
                     || {code: cropCode, name: cropCode};

        const {card, body, footer, setSaving} = buildCropCard({
            cropCode: crop.code,
            cropName: crop.name,
            initiallyExpanded: true,
            deleteLabel: 'Clear curation',
            onDelete: async () => {
                if (!confirm(`Clear curation for ${crop.name}? Dropdowns will show full code lists.`)) return;
                setSaving(true);
                try {
                    await jsonFetch(`/dssat/api/config/curated/${cropCode}/`, {method: 'DELETE'});
                    card.remove();
                } finally {
                    setSaving(false);
                }
            },
        });

        const sectionsEl = document.createElement('div');
        sectionsEl.className = 'curated-sections';
        body.appendChild(sectionsEl);

        // Fetch existing curated data (best effort)
        const existing = await jsonFetch(`/dssat/api/config/curated/${cropCode}/`)
            .then(r => r.data)
            .catch(() => ({}));

        // One collapsible section per menu type, each with checkbox grid
        for (const cat of CURATED_CATEGORIES) {
            const selected = new Set(existing[cat.field] || []);
            const fullCodes = await loadFullCodes(cat.category, cat.context);

            const section = document.createElement('details');
            section.className = 'fallback-section';
            section.dataset.field = cat.field;
            section.innerHTML = `
                <summary>
                    <h3>${cat.label} <span class="helptext curated-count" style="font-weight:normal; color: var(--text-muted); margin-left: 0.5rem;">(${selected.size} selected of ${fullCodes.length})</span></h3>
                </summary>
                <div style="padding: 0.8rem;">
                    <div style="display:flex; gap:0.5rem; margin-bottom:0.5rem;">
                        <input type="text" class="curated-search" placeholder="Filter codes..." style="flex:1; padding:0.35rem 0.5rem; background:rgba(255,255,255,0.04); border:1px solid var(--glass-border); border-radius:0.3rem; color:var(--text-primary);">
                        <button type="button" class="btn-sm curated-select-all">Select all</button>
                        <button type="button" class="btn-sm curated-clear-all">Clear</button>
                    </div>
                    <div class="curated-options" style="display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 0.3rem; max-height: 300px; overflow-y: auto; padding: 0.4rem; border: 1px solid var(--glass-border); border-radius: 0.35rem;"></div>
                </div>
            `;

            const optsEl = section.querySelector('.curated-options');
            const countEl = section.querySelector('.curated-count');
            const searchEl = section.querySelector('.curated-search');

            const updateCount = () => {
                const checked = section.querySelectorAll('input[type=checkbox]:checked').length;
                countEl.textContent = `(${checked} selected of ${fullCodes.length})`;
            };

            const renderOptions = (filter = '') => {
                const f = filter.toLowerCase();
                optsEl.innerHTML = '';
                for (const {code, description} of fullCodes) {
                    if (f && !code.toLowerCase().includes(f) && !(description || '').toLowerCase().includes(f)) continue;
                    const label = document.createElement('label');
                    label.style.cssText = 'display:flex; align-items:center; gap:0.4rem; font-size:0.8rem; color:var(--text-primary); padding:0.2rem 0.3rem; cursor:pointer;';
                    label.innerHTML = `
                        <input type="checkbox" value="${code}" ${selected.has(code) ? 'checked' : ''} style="accent-color: var(--primary-color);">
                        <span><strong style="font-family:monospace; color:var(--primary-color);">${code}</strong>${description ? ' — ' + description : ''}</span>
                    `;
                    const cb = label.querySelector('input');
                    cb.addEventListener('change', () => {
                        if (cb.checked) selected.add(code);
                        else selected.delete(code);
                        updateCount();
                    });
                    optsEl.appendChild(label);
                }
            };

            renderOptions();
            searchEl.addEventListener('input', () => renderOptions(searchEl.value));
            section.querySelector('.curated-select-all').addEventListener('click', () => {
                for (const {code} of fullCodes) selected.add(code);
                renderOptions(searchEl.value);
                updateCount();
            });
            section.querySelector('.curated-clear-all').addEventListener('click', () => {
                selected.clear();
                renderOptions(searchEl.value);
                updateCount();
            });

            sectionsEl.appendChild(section);
            section._selected = selected;
            section._field = cat.field;
        }

        // Banner inside body (below sections) for save feedback
        const banner = document.createElement('div');
        banner.className = 'save-banner';
        body.appendChild(banner);

        function showBanner(msg, kind) {
            banner.textContent = msg;
            banner.className = `save-banner ${kind}`;
            setTimeout(() => banner.classList.remove(kind), 4000);
        }

        const saveBtn = document.createElement('button');
        saveBtn.type = 'button';
        saveBtn.className = 'btn-primary btn-sm';
        saveBtn.textContent = 'Save';
        saveBtn.addEventListener('click', async () => {
            const payload = {};
            sectionsEl.querySelectorAll('.fallback-section').forEach((s) => {
                payload[s._field] = Array.from(s._selected);
            });
            setSaving(true);
            try {
                await jsonFetch(`/dssat/api/config/curated/${cropCode}/`, {
                    method: 'POST', body: payload,
                });
                showBanner('Saved.', 'success');
            } catch (err) {
                showBanner(`Save failed: ${err.message}`, 'error');
            } finally {
                setSaving(false);
            }
        });
        footer.insertBefore(saveBtn, footer.firstChild);

        return card;
    }

    // Add-crop button opens the shared picker (showing all crops, since the
    // curated editor is independent of per-crop defaults).
    addBtn.addEventListener('click', () => {
        const picker = window.openCropPicker || _localPicker;
        picker(async (cropCode) => {
            const existing = container.querySelector(`.crop-card[data-crop-code="${cropCode}"]`);
            if (existing) {
                existing.scrollIntoView({behavior: 'smooth', block: 'center'});
                return;
            }
            const card = await buildCuratedCard(cropCode);
            container.appendChild(card);
            card.scrollIntoView({behavior: 'smooth', block: 'center'});
        }, {excludeConfigured: false});
    });

    // Fallback picker (used only if config_preferences.js hasn't loaded yet)
    function _localPicker(onPick) {
        const modal = document.createElement('div');
        modal.className = 'crop-picker-modal open';
        modal.innerHTML = `
            <div class="crop-picker-dialog">
                <h3 style="margin: 0 0 0.75rem;">Select a Crop</h3>
                <input type="text" class="crop-picker-search" placeholder="Search by code or name...">
                <div class="crop-picker-list"></div>
                <div class="crop-picker-footer">
                    <button type="button" class="btn-sm crop-picker-cancel">Cancel</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        const list = modal.querySelector('.crop-picker-list');
        const search = modal.querySelector('.crop-picker-search');

        function renderList(filter = '') {
            const f = filter.toLowerCase();
            list.innerHTML = '';
            const filtered = (CONFIG.availableCrops || []).filter(c =>
                !f || c.code.toLowerCase().includes(f) || (c.name || '').toLowerCase().includes(f)
            );
            filtered.forEach(c => {
                const item = document.createElement('div');
                item.className = 'crop-picker-item';
                item.innerHTML = `<span class="code">${c.code}</span> ${c.name || c.code}`;
                item.addEventListener('click', () => {
                    modal.remove();
                    onPick(c.code);
                });
                list.appendChild(item);
            });
        }

        search.addEventListener('input', () => renderList(search.value));
        modal.querySelector('.crop-picker-cancel').addEventListener('click', () => modal.remove());
        modal.addEventListener('click', (ev) => { if (ev.target === modal) modal.remove(); });
        renderList();
        search.focus();
    }

})();
