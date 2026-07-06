(function() {
    'use strict';
    const SUBPATH = window.SUBPATH || '';
    const wrap = document.getElementById('treatments-table-wrap');
    const q = document.getElementById('treatments-q');
    const cropSel = document.getElementById('treatments-crop');

    function getCsrf() {
        const m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? m[1] : '';
    }

    async function fetchTreatments() {
        let url = SUBPATH + '/dssat/api/treatments/?q=' + encodeURIComponent(q.value || '');
        if (cropSel.value) url += '&crop_code=' + encodeURIComponent(cropSel.value);
        const r = await fetch(url, {credentials: 'same-origin'});
        if (!r.ok) {
            wrap.innerHTML = '<div class="empty-state small">Failed to load.</div>';
            return;
        }
        const data = await r.json();
        render(data.treatments || []);
        populateCropFilter(data.treatments || []);
    }

    function populateCropFilter(items) {
        const seen = new Set([''].concat(items.map(t => t.crop_code).filter(Boolean)));
        const current = cropSel.value;
        cropSel.innerHTML = '';
        Array.from(seen).sort().forEach(c => {
            const opt = document.createElement('option');
            opt.value = c;
            opt.textContent = c || 'All crops';
            cropSel.appendChild(opt);
        });
        if (Array.from(cropSel.options).some(o => o.value === current)) {
            cropSel.value = current;
        }
    }

    function render(items) {
        if (!items.length) {
            wrap.innerHTML = '<div class="empty-state"><h3>No saved treatments yet</h3>'
                + '<p>The wizard saves a Treatment for each protocol you build. '
                + 'They&rsquo;ll show up here once you submit an experiment.</p></div>';
            return;
        }
        let html = '<div class="data-table-wrapper"><table class="data-table">'
            + '<thead><tr><th>Name</th><th>Crop</th><th>Cultivar</th>'
            + '<th>Mgmt blocks</th><th>Updated</th><th></th></tr></thead><tbody>';
        for (const t of items) {
            const detailUrl = SUBPATH + '/dssat/treatments/' + t.id + '/';
            const blocks = ['fertilizer', 'irrigation', 'tillage', 'chemical', 'residue']
                .filter(k => t[k]).join(', ') || '--';
            html += '<tr>'
                + '<td><a href="' + detailUrl + '">' + escapeHtml(t.name) + '</a></td>'
                + '<td><span class="mono">' + escapeHtml(t.crop_code) + '</span></td>'
                + '<td><span class="mono">' + escapeHtml(t.cultivar_code || '--') + '</span></td>'
                + '<td>' + escapeHtml(blocks) + '</td>'
                + '<td>' + new Date(t.updated_at).toLocaleString() + '</td>'
                + '<td><button class="btn btn-sm btn-danger" data-id="' + t.id + '">Delete</button></td>'
                + '</tr>';
        }
        html += '</tbody></table></div>';
        wrap.innerHTML = html;
        wrap.querySelectorAll('button[data-id]').forEach(btn =>
            btn.addEventListener('click', () => onDelete(btn.dataset.id)));
    }

    async function onDelete(id) {
        if (!confirm('Delete this treatment? Treatments referenced by past experiments cannot be deleted.')) return;
        const r = await fetch(SUBPATH + '/dssat/api/treatments/' + id + '/', {
            method: 'DELETE',
            credentials: 'same-origin',
            headers: {'X-CSRFToken': getCsrf()},
        });
        if (r.ok) fetchTreatments();
        else {
            const j = await r.json().catch(() => ({}));
            alert(j.error || 'Delete failed');
        }
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    let qTimer = null;
    q.addEventListener('input', () => {
        clearTimeout(qTimer);
        qTimer = setTimeout(fetchTreatments, 250);
    });
    cropSel.addEventListener('change', fetchTreatments);

    fetchTreatments();
})();
