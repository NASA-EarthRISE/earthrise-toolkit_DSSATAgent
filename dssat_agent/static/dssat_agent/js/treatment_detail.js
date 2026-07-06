(function() {
    'use strict';
    const SUBPATH = window.SUBPATH || '';
    const TREATMENT_ID = JSON.parse(document.getElementById('treatment-id').textContent);
    const treatment = JSON.parse(document.getElementById('treatment-data').textContent);

    function getCsrf() {
        const m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? m[1] : '';
    }

    const sectionLabels = [
        ['planting', 'Planting & Harvest', ['planting', 'harvest']],
        ['initial_conditions', 'Initial Conditions', ['initial_conditions']],
        ['simulation_controls', 'Simulation Controls', ['simulation_controls']],
        ['fertilizer', 'Fertilizer (MF)', ['fertilizer']],
        ['irrigation', 'Irrigation (MI)', ['irrigation']],
        ['residue', 'Residue (MR)', ['residue']],
        ['chemical', 'Chemical (MC)', ['chemical']],
        ['tillage', 'Tillage (MT)', ['tillage']],
        ['mow', 'Mow (forage)', ['mow']],
    ];

    const out = document.getElementById('sections');
    for (const [key, label, fields] of sectionLabels) {
        const merged = {};
        let any = false;
        for (const f of fields) {
            if (treatment[f] != null
                && !(Array.isArray(treatment[f]) && treatment[f].length === 0)
                && !(typeof treatment[f] === 'object' && Object.keys(treatment[f]).length === 0)) {
                merged[f] = treatment[f];
                any = true;
            }
        }
        if (!any) continue;
        const div = document.createElement('div');
        div.className = 'treatment-section';
        div.innerHTML = '<h4>' + label + '</h4>'
            + '<pre>' + escapeHtml(JSON.stringify(merged, null, 2)) + '</pre>';
        out.appendChild(div);
    }

    document.getElementById('btn-delete').addEventListener('click', async () => {
        if (!confirm('Delete this treatment?')) return;
        const r = await fetch(SUBPATH + '/dssat/api/treatments/' + TREATMENT_ID + '/', {
            method: 'DELETE',
            credentials: 'same-origin',
            headers: {'X-CSRFToken': getCsrf()},
        });
        if (r.ok) location.href = SUBPATH + '/dssat/treatments/';
        else {
            const j = await r.json().catch(() => ({}));
            alert(j.error || 'Delete failed');
        }
    });

    document.getElementById('btn-rename').addEventListener('click', async () => {
        const name = prompt('Rename treatment:',
                            document.getElementById('treatment-name').textContent.trim());
        if (name == null) return;
        const r = await fetch(SUBPATH + '/dssat/api/treatments/' + TREATMENT_ID + '/', {
            method: 'PATCH',
            credentials: 'same-origin',
            headers: {'X-CSRFToken': getCsrf(), 'Content-Type': 'application/json'},
            body: JSON.stringify({name}),
        });
        if (r.ok) {
            const j = await r.json();
            document.getElementById('treatment-name').textContent = j.treatment.name;
        } else {
            alert('Rename failed');
        }
    });

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
})();
