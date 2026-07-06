(function() {
    'use strict';
    const SUBPATH = window.SUBPATH || '';
    const FIELD_ID = JSON.parse(document.getElementById('field-id').textContent);
    function getCsrf() {
        const m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? m[1] : '';
    }

    document.getElementById('btn-delete').addEventListener('click', async () => {
        if (!confirm('Delete this field? Fields used by past experiments cannot be deleted.')) return;
        const r = await fetch(SUBPATH + '/dssat/api/fields/' + FIELD_ID + '/', {
            method: 'DELETE',
            credentials: 'same-origin',
            headers: {'X-CSRFToken': getCsrf()},
        });
        if (r.ok) {
            location.href = SUBPATH + '/dssat/fields/';
        } else {
            const j = await r.json().catch(() => ({}));
            alert(j.error || 'Delete failed');
        }
    });

    document.getElementById('btn-edit').addEventListener('click', async () => {
        const newName = prompt('Rename field:', document.getElementById('field-name').textContent.trim());
        if (newName == null) return;
        const r = await fetch(SUBPATH + '/dssat/api/fields/' + FIELD_ID + '/', {
            method: 'PATCH',
            credentials: 'same-origin',
            headers: {'X-CSRFToken': getCsrf(), 'Content-Type': 'application/json'},
            body: JSON.stringify({name: newName}),
        });
        if (r.ok) {
            const j = await r.json();
            document.getElementById('field-name').textContent = j.field.name;
        } else {
            alert('Rename failed');
        }
    });
})();
