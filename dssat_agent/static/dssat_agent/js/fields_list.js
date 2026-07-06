(function() {
    'use strict';
    const SUBPATH = window.SUBPATH || '';
    const wrap = document.getElementById('fields-table-wrap');
    const q = document.getElementById('fields-q');

    function getCsrf() {
        const m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? m[1] : '';
    }

    async function fetchFields() {
        const url = SUBPATH + '/dssat/api/fields/?q=' + encodeURIComponent(q.value || '');
        const r = await fetch(url, {credentials: 'same-origin'});
        if (!r.ok) {
            wrap.innerHTML = '<div class="empty-state small">Failed to load.</div>';
            return;
        }
        const data = await r.json();
        render(data.fields || []);
    }

    function render(fields) {
        if (!fields.length) {
            wrap.innerHTML = '<div class="empty-state"><h3>No saved fields yet</h3>'
                + '<p>The first time you complete the location step in the wizard, '
                + 'it&rsquo;ll save a Field here.</p></div>';
            return;
        }
        let html = '<div class="data-table-wrapper"><table class="data-table">'
            + '<thead><tr><th>Name</th><th>Coords</th><th>Soil</th>'
            + '<th>Weather</th><th>Updated</th><th></th></tr></thead><tbody>';
        for (const f of fields) {
            const detailUrl = SUBPATH + '/dssat/fields/' + f.id + '/';
            const soil = f.soil_id ? f.soil_id : (f.has_inline_soil ? '<em>inline</em>' : '--');
            html += '<tr>'
                + '<td><a href="' + detailUrl + '">' + escapeHtml(f.name) + '</a>'
                + (f.location_label ? '<div class="text-muted small">' + escapeHtml(f.location_label) + '</div>' : '')
                + '</td>'
                + '<td class="mono">' + f.latitude.toFixed(3) + ', ' + f.longitude.toFixed(3) + '</td>'
                + '<td>' + soil + '</td>'
                + '<td>' + escapeHtml(f.weather_source || '--') + '</td>'
                + '<td>' + new Date(f.updated_at).toLocaleString() + '</td>'
                + '<td><button class="btn btn-sm btn-danger" data-id="' + f.id + '">Delete</button></td>'
                + '</tr>';
        }
        html += '</tbody></table></div>';
        wrap.innerHTML = html;
        wrap.querySelectorAll('button[data-id]').forEach(btn =>
            btn.addEventListener('click', () => onDelete(btn.dataset.id)));
    }

    async function onDelete(id) {
        if (!confirm('Delete this field? Fields used by past experiments cannot be deleted.')) return;
        const r = await fetch(SUBPATH + '/dssat/api/fields/' + id + '/', {
            method: 'DELETE',
            credentials: 'same-origin',
            headers: {'X-CSRFToken': getCsrf()},
        });
        if (r.ok) fetchFields();
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
        qTimer = setTimeout(fetchFields, 250);
    });

    fetchFields();
})();
