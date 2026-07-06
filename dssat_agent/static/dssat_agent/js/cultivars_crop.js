(function() {
    const _cfg = document.getElementById('cvc-config').dataset;
    const CROP_CODE = _cfg.cropCode;
    const DSSAT_MODEL = _cfg.dssatModel;
    let currentPage = 1;
    let pageSize = 25;
    let allItems = [];
    let activeView = 'cultivars'; // 'cultivars' or 'ecotypes'

    const tbody = document.getElementById('cv-tbody');
    const tableHead = document.getElementById('table-head');
    const resultCount = document.getElementById('result-count');
    const paginationBar = document.getElementById('pagination-bar');
    const paginationInfo = document.getElementById('pagination-info');
    const pageNumbers = document.getElementById('page-numbers');
    const pageSizeSelect = document.getElementById('page-size');
    const clearBtn = document.getElementById('clear-filters');
    const filterForm = document.getElementById('filter-form');
    const searchInput = document.getElementById('filter-search');
    const cropTitle = document.getElementById('crop-title');
    const createBtn = document.getElementById('create-btn');
    const toggleSlider = document.getElementById('toggle-slider');
    const btnCultivars = document.getElementById('btn-cultivars');
    const btnEcotypes = document.getElementById('btn-ecotypes');

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function sourceBadge(s) {
        if (!s) return '';
        return `<span style="display:inline-block;padding:0.1rem 0.4rem;border-radius:0.25rem;font-size:0.65rem;font-weight:600;text-transform:uppercase;${
            s === 'dssat' ? 'background:rgba(1,112,185,0.12);color:#0170B9' :
            s === 'custom' ? 'background:rgba(34,197,94,0.12);color:#15803d' :
            'background:rgba(28,103,227,0.12);color:#1c67e3'
        }">${escapeHtml(s)}</span>`;
    }

    function buildPageNumbers(current, totalPages) {
        const pages = [];
        if (totalPages <= 7) {
            for (let i = 1; i <= totalPages; i++) pages.push(i);
        } else {
            pages.push(1);
            if (current > 3) pages.push('...');
            const start = Math.max(2, current - 1);
            const end = Math.min(totalPages - 1, current + 1);
            for (let i = start; i <= end; i++) pages.push(i);
            if (current < totalPages - 2) pages.push('...');
            pages.push(totalPages);
        }
        return pages;
    }

    function getFiltered() {
        const search = searchInput.value.trim().toLowerCase();
        if (!search) return allItems;
        return allItems.filter(item => {
            const code = (item.code || item.ecotype_code || '').toLowerCase();
            const name = (item.name || item.ecotype_name || '').toLowerCase();
            return code.includes(search) || name.includes(search);
        });
    }

    function setTableHeaders() {
        const modelParam = DSSAT_MODEL ? `?dssat_model=${encodeURIComponent(DSSAT_MODEL)}` : '';
        if (activeView === 'cultivars') {
            tableHead.innerHTML = '<tr><th>Code</th><th>Name</th><th>Ecotype</th><th>Source</th><th></th></tr>';
            createBtn.textContent = 'Create Custom Cultivar';
            createBtn.href = `/dssat/crops/${CROP_CODE}/create-cultivar/${modelParam}`;
        } else {
            tableHead.innerHTML = '<tr><th>Code</th><th>Name</th><th>Source</th><th></th></tr>';
            createBtn.textContent = 'Create Custom Ecotype';
            createBtn.href = `/dssat/crops/${CROP_CODE}/create-ecotype/${modelParam}`;
        }
    }

    function renderPage(page) {
        currentPage = page || 1;
        const filtered = getFiltered();
        const total = filtered.length;
        const label = activeView === 'cultivars' ? 'cultivar' : 'ecotype';
        const cols = activeView === 'cultivars' ? 5 : 4;

        clearBtn.classList.toggle('is-hidden', !searchInput.value.trim());
        resultCount.textContent = `${total} ${label}${total !== 1 ? 's' : ''} found`;

        if (total === 0) {
            tbody.innerHTML = `<tr class="empty-row"><td colspan="${cols}">No ${label}s found.</td></tr>`;
            paginationBar.classList.add('is-hidden');
            return;
        }

        const totalPages = Math.ceil(total / pageSize);
        if (currentPage > totalPages) currentPage = totalPages;
        const from = (currentPage - 1) * pageSize;
        const to = Math.min(from + pageSize, total);
        const slice = filtered.slice(from, to);
        const detailModelParam = DSSAT_MODEL ? `?dssat_model=${encodeURIComponent(DSSAT_MODEL)}` : '';

        if (activeView === 'cultivars') {
            tbody.innerHTML = slice.map(cv => `
                <tr class="cv-row" data-href="${window.SUBPATH}/dssat/crops/${escapeHtml(CROP_CODE)}/cultivar/${escapeHtml(cv.code)}/${detailModelParam}">
                    <td class="mono">${escapeHtml(cv.code || '\u2014')}</td>
                    <td>${escapeHtml(cv.name || '\u2014')}</td>
                    <td class="mono">${escapeHtml(cv.ecotype || '\u2014')}</td>
                    <td>${sourceBadge(cv.source)}</td>
                    <td><a href="${window.SUBPATH}/dssat/crops/${escapeHtml(CROP_CODE)}/cultivar/${escapeHtml(cv.code)}/${detailModelParam}" class="btn btn-sm">Details</a></td>
                </tr>
            `).join('');
        } else {
            tbody.innerHTML = slice.map(eco => `
                <tr class="cv-row" data-href="${window.SUBPATH}/dssat/crops/${escapeHtml(CROP_CODE)}/ecotype/${escapeHtml(eco.ecotype_code)}/${detailModelParam}">
                    <td class="mono">${escapeHtml(eco.ecotype_code || '\u2014')}</td>
                    <td>${escapeHtml(eco.ecotype_name || '\u2014')}</td>
                    <td>${sourceBadge(eco.source)}</td>
                    <td><a href="${window.SUBPATH}/dssat/crops/${escapeHtml(CROP_CODE)}/ecotype/${escapeHtml(eco.ecotype_code)}/${detailModelParam}" class="btn btn-sm">Details</a></td>
                </tr>
            `).join('');
        }

        if (totalPages <= 1) { paginationBar.classList.add('is-hidden'); return; }
        paginationBar.classList.remove('is-hidden');
        paginationInfo.textContent = `Showing ${from + 1}\u2013${to} of ${total}`;
        const pages = buildPageNumbers(currentPage, totalPages);
        let html = `<button class="page-btn" data-page="${currentPage - 1}" ${currentPage === 1 ? 'disabled' : ''}>&laquo;</button>`;
        for (const p of pages) {
            if (p === '...') { html += '<span class="page-ellipsis">\u2026</span>'; }
            else { html += `<button class="page-btn ${p === currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`; }
        }
        html += `<button class="page-btn" data-page="${currentPage + 1}" ${currentPage === totalPages ? 'disabled' : ''}>&raquo;</button>`;
        pageNumbers.innerHTML = html;
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    async function loadData() {
        const cols = activeView === 'cultivars' ? 5 : 4;
        tbody.innerHTML = `<tr class="loading-row"><td colspan="${cols}">Loading...</td></tr>`;
        paginationBar.classList.add('is-hidden');
        setTableHeaders();

        try {
            const modelParam = DSSAT_MODEL ? `?dssat_model=${encodeURIComponent(DSSAT_MODEL)}` : '';
            let url, itemsKey;
            if (activeView === 'cultivars') {
                url = `${window.SUBPATH}/dssat/api/cultivars/${CROP_CODE}/${modelParam}`;
                itemsKey = 'cultivars';
            } else {
                url = `${window.SUBPATH}/dssat/api/ecotypes/${CROP_CODE}/${modelParam}`;
                itemsKey = 'ecotypes';
            }
            const response = await fetch(url);
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Failed to load data');

            allItems = data[itemsKey] || [];
            if (data.crop_name) cropTitle.textContent = data.crop_name;
            renderPage(1);
        } catch (err) {
            resultCount.textContent = 'Error loading data';
            tbody.innerHTML = `<tr class="error-row"><td colspan="${cols}">${escapeHtml(err.message)}</td></tr>`;
        }
    }

    // Toggle handlers
    const btnCropDetails = document.getElementById('btn-crop-details');
    const viewCropDetails = document.getElementById('view-crop-details');
    const dataTableWrapper = document.querySelector('.data-table-wrapper');
    const filterControls = document.querySelector('.filter-controls');

    // Slider position per view — matches the [data-pos] CSS rules.
    const VIEW_POSITIONS = { 'cultivars': 0, 'ecotypes': 1, 'crop-details': 2 };
    const ALL_BTNS = [btnCultivars, btnEcotypes, btnCropDetails];

    function switchView(view) {
        if (view === activeView) return;
        activeView = view;
        searchInput.value = '';

        // Move the slider highlight.
        toggleSlider.dataset.pos = String(VIEW_POSITIONS[view] ?? 0);
        ALL_BTNS.forEach(b => b.classList.toggle('active', b.dataset.view === view));

        if (view === 'crop-details') {
            // Hide table + filters; show the crop-details panel.
            if (dataTableWrapper) dataTableWrapper.style.display = 'none';
            if (filterControls) filterControls.style.display = 'none';
            paginationBar.classList.add('is-hidden');
            viewCropDetails.hidden = false;
            loadCropDetails();
            return;
        }

        // Restore the table view. Update search placeholder to match the
        // active list so users always see which items they're searching.
        if (dataTableWrapper) dataTableWrapper.style.display = '';
        if (filterControls) filterControls.style.display = '';
        viewCropDetails.hidden = true;
        searchInput.placeholder = view === 'ecotypes'
            ? 'Search ecotypes...'
            : 'Search cultivars...';
        loadData();
    }

    btnCultivars.addEventListener('click', () => switchView('cultivars'));
    btnEcotypes.addEventListener('click', () => switchView('ecotypes'));
    btnCropDetails.addEventListener('click', () => switchView('crop-details'));

    // Cached CropModel response so re-selecting the tab doesn't re-fetch.
    let cropDetailsCache = null;

    async function loadCropDetails() {
        if (cropDetailsCache) {
            renderCropDetails(cropDetailsCache);
            return;
        }
        const subpath = (window.SUBPATH || '').replace(/\/$/, '');
        const modelQS = DSSAT_MODEL ? `?dssat_model=${encodeURIComponent(DSSAT_MODEL)}` : '';
        try {
            const resp = await fetch(`${subpath}/dssat/api/crop-model/${encodeURIComponent(CROP_CODE)}/${modelQS}`);
            const j = resp.ok ? await resp.json() : null;
            cropDetailsCache = (j && j.data) || {};
        } catch (err) {
            cropDetailsCache = {};
        }
        renderCropDetails(cropDetailsCache);
    }

    function _fmtRange(rng) {
        if (!rng || typeof rng !== 'object') return '—';
        const { min, max, unit = '', typical } = rng;
        const hasMin = min !== null && min !== undefined && min !== '';
        const hasMax = max !== null && max !== undefined && max !== '';
        if (!hasMin && !hasMax) return '—';
        const lo = hasMin ? String(min) : '?';
        const hi = hasMax ? String(max) : '?';
        const body = `${lo} – ${hi}${unit ? ' ' + unit : ''}`;
        return (typical !== undefined && typical !== null && typical !== '')
            ? `${body}  (typical ${typical})` : body;
    }

    function renderCropDetails(data) {
        // Simple scalar fields.
        const setField = (name, val) => {
            const el = viewCropDetails.querySelector(`[data-cd-field="${name}"]`);
            if (el) el.textContent = (val === undefined || val === null || val === '') ? '—' : String(val);
        };
        setField('display_name', data.display_name);
        setField('crop_group', data.crop_group);
        setField('default_cultivar_code', data.default_cultivar_code);
        if (!DSSAT_MODEL) setField('dssat_model', data.dssat_model || '—');

        // Range triple.
        for (const name of ['plant_population_range', 'row_spacing_range', 'planting_depth_range']) {
            const el = viewCropDetails.querySelector(`[data-cd-range="${name}"]`);
            if (el) el.textContent = _fmtRange(data[name]);
        }

        // Chip lists.
        for (const name of ['supported_harvs_modes', 'supported_management']) {
            const ul = viewCropDetails.querySelector(`[data-cd-list="${name}"]`);
            if (!ul) continue;
            const items = Array.isArray(data[name]) ? data[name] : [];
            if (!items.length) {
                ul.innerHTML = '<li class="empty-hint">—</li>';
            } else {
                ul.innerHTML = items.map(v => `<li>${escapeHtml(String(v))}</li>`).join('');
            }
        }

        // Harvest stages table.
        const stagesWrap = viewCropDetails.querySelector('[data-cd-stages-wrap]');
        const stages = Array.isArray(data.harvest_stages) ? data.harvest_stages : [];
        if (!stages.length) {
            stagesWrap.innerHTML = '<span class="empty-hint">No GRSTAGE entries seeded for this (crop, model) pair.</span>';
        } else {
            stagesWrap.innerHTML = `
                <table class="stage-table">
                    <thead><tr><th>Code</th><th>Name</th><th>Description</th></tr></thead>
                    <tbody>
                        ${stages.map(s => `
                            <tr>
                                <td class="mono">${escapeHtml(s.code || '')}</td>
                                <td>${escapeHtml(s.name || '')}</td>
                                <td>${escapeHtml(s.description || '')}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        }

        // Notes (hidden unless present).
        const notesWrap = viewCropDetails.querySelector('[data-cd-notes-wrap]');
        const notesEl = viewCropDetails.querySelector('[data-cd-notes]');
        if (data.notes && String(data.notes).trim()) {
            notesEl.textContent = data.notes;
            notesWrap.hidden = false;
        } else {
            notesWrap.hidden = true;
        }
    }

    tbody.addEventListener('click', (e) => {
        const row = e.target.closest('.cv-row');
        if (row && !e.target.closest('a')) window.location.href = row.dataset.href;
    });

    filterForm.addEventListener('submit', (e) => { e.preventDefault(); renderPage(1); });

    let debounce;
    searchInput.addEventListener('input', () => {
        clearTimeout(debounce);
        debounce = setTimeout(() => renderPage(1), 200);
    });

    pageNumbers.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-page]');
        if (btn && !btn.disabled) renderPage(parseInt(btn.dataset.page));
    });

    pageSizeSelect.addEventListener('change', () => { pageSize = parseInt(pageSizeSelect.value); renderPage(1); });

    clearBtn.addEventListener('click', (e) => { e.preventDefault(); searchInput.value = ''; renderPage(1); });

    loadData();
})();
