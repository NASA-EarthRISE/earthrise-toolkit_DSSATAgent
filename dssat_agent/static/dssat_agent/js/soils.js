(function() {
    const SUBPATH = window.SUBPATH || '';
    let currentPage = 1;
    let pageSize = 25;
    let totalSoils = 0;

    const tbody = document.getElementById('soils-tbody');
    const resultCount = document.getElementById('result-count');
    const paginationBar = document.getElementById('pagination-bar');
    const paginationInfo = document.getElementById('pagination-info');
    const pageNumbers = document.getElementById('page-numbers');
    const pageSizeSelect = document.getElementById('page-size');
    const clearBtn = document.getElementById('clear-filters');
    const filterForm = document.getElementById('filter-form');
    const searchInput = document.getElementById('filter-search');
    const sourceInput = document.getElementById('filter-source');
    const countryInput = document.getElementById('filter-country');

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
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

    function renderPagination(total) {
        const totalPages = Math.ceil(total / pageSize);
        if (totalPages <= 1) {
            paginationBar.classList.add('is-hidden');
            return;
        }
        paginationBar.classList.remove('is-hidden');

        const from = (currentPage - 1) * pageSize + 1;
        const to = Math.min(currentPage * pageSize, total);
        paginationInfo.textContent = `Showing ${from}–${to} of ${total}`;

        const pages = buildPageNumbers(currentPage, totalPages);
        let html = `<button class="page-btn" data-page="${currentPage - 1}" ${currentPage === 1 ? 'disabled' : ''}>&laquo;</button>`;
        for (const p of pages) {
            if (p === '...') {
                html += '<span class="page-ellipsis">…</span>';
            } else {
                html += `<button class="page-btn ${p === currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`;
            }
        }
        html += `<button class="page-btn" data-page="${currentPage + 1}" ${currentPage === totalPages ? 'disabled' : ''}>&raquo;</button>`;
        pageNumbers.innerHTML = html;
    }

    async function loadSoils(page) {
        currentPage = page || 1;
        const params = new URLSearchParams();
        params.set('page', currentPage);
        params.set('page_size', pageSize);

        const search = searchInput.value.trim();
        const source = sourceInput.value.trim();
        const country = countryInput.value.trim();

        if (search) params.set('search', search);
        if (source) params.set('source', source);
        if (country) params.set('country', country);

        clearBtn.classList.toggle('is-hidden', !(search || source || country));

        tbody.innerHTML = '<tr class="loading-row"><td colspan="7">Loading...</td></tr>';
        paginationBar.classList.add('is-hidden');

        try {
            const response = await fetch(`${SUBPATH}/dssat/api/soils/?${params}`);
            const data = await response.json();

            if (!response.ok || !data.success) {
                throw new Error(data.error || 'Failed to load soils');
            }

            const soils = data.profiles || data.soils || [];
            totalSoils = data.total || 0;

            resultCount.textContent = `${totalSoils} soil${totalSoils !== 1 ? 's' : ''} found`;

            if (soils.length === 0) {
                tbody.innerHTML = '<tr class="empty-row"><td colspan="7">No soil profiles found. Try adjusting your filters.</td></tr>';
                paginationBar.classList.add('is-hidden');
                return;
            }

            tbody.innerHTML = soils.map(soil => `
                <tr class="soil-row" data-href="${SUBPATH}/dssat/soils/${escapeHtml(soil.soil_id)}/">
                    <td class="mono">${escapeHtml(soil.soil_id || '—')}</td>
                    <td>${escapeHtml(soil.name || '—')}</td>
                    <td>${escapeHtml(soil.source || '—')}</td>
                    <td>${escapeHtml(soil.country || '—')}</td>
                    <td>${escapeHtml(soil.texture || '—')}</td>
                    <td>${soil.num_layers != null ? soil.num_layers : '—'}</td>
                    <td><a href="${SUBPATH}/dssat/soils/${escapeHtml(soil.soil_id)}/" class="btn btn-sm">View</a></td>
                </tr>
            `).join('');

            renderPagination(totalSoils);
            window.scrollTo({ top: 0, behavior: 'smooth' });

        } catch (err) {
            resultCount.textContent = 'Error loading soils';
            tbody.innerHTML = `<tr class="error-row"><td colspan="7">${escapeHtml(err.message)}</td></tr>`;
            paginationBar.classList.add('is-hidden');
        }
    }

    tbody.addEventListener('click', (e) => {
        const row = e.target.closest('.soil-row');
        if (row && !e.target.closest('a')) {
            window.location.href = row.dataset.href;
        }
    });

    filterForm.addEventListener('submit', (e) => {
        e.preventDefault();
        loadSoils(1);
    });

    pageNumbers.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-page]');
        if (btn && !btn.disabled) loadSoils(parseInt(btn.dataset.page));
    });

    pageSizeSelect.addEventListener('change', () => {
        pageSize = parseInt(pageSizeSelect.value);
        loadSoils(1);
    });

    clearBtn.addEventListener('click', (e) => {
        e.preventDefault();
        searchInput.value = '';
        sourceInput.value = '';
        countryInput.value = '';
        loadSoils(1);
    });

    loadSoils(1);
})();
