const groupedRoles = JSON.parse(document.getElementById('grouped-roles-data').textContent);
let editingUserId = null;

// Tab switching
document.querySelectorAll('.mgmt-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.mgmt-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
    });
});

// Action buttons bound via event delegation (data-action attributes).
document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === 'approve') {
        const reqId = btn.dataset.reqId;
        const row = document.getElementById('req-' + reqId);
        const requestedRoles = Array.from(row.querySelectorAll('.role-tag')).map(t => t.textContent.trim());
        approveRequest(reqId, requestedRoles);
    } else if (action === 'reject') {
        rejectRequest(btn.dataset.reqId);
    } else if (action === 'edit-roles') {
        editUserRoles(btn.dataset.userId);
    } else if (action === 'toggle-active') {
        toggleActive(btn.dataset.userId);
    } else if (action === 'save-roles') {
        saveRoles();
    } else if (action === 'close-modal') {
        closeModal();
    }
});

function getCookie(name) {
    const cookies = document.cookie.split(';');
    for (let c of cookies) {
        c = c.trim();
        if (c.startsWith(name + '=')) return decodeURIComponent(c.substring(name.length + 1));
    }
    return '';
}

function approveRequest(reqId, requestedRoles) {
    fetch(`${window.SUBPATH}/accounts/api/approve/${reqId}/`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken')},
        body: JSON.stringify({roles: requestedRoles}),
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            document.getElementById('req-' + reqId).remove();
            location.reload();
        }
    });
}

function rejectRequest(reqId) {
    if (!confirm('Reject this registration request?')) return;
    fetch(`${window.SUBPATH}/accounts/api/reject/${reqId}/`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken')},
        body: JSON.stringify({}),
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            document.getElementById('req-' + reqId).remove();
        }
    });
}

function editUserRoles(userId) {
    editingUserId = userId;
    const row = document.getElementById('user-' + userId);
    const currentRoles = Array.from(row.querySelectorAll('.role-tag')).map(t => t.textContent.trim());

    const container = document.getElementById('role-checkboxes');
    container.innerHTML = '';

    groupedRoles.forEach(group => {
        const groupWrap = document.createElement('div');
        groupWrap.style.cssText = 'margin-bottom:1rem;';

        const header = document.createElement('div');
        header.style.cssText = 'font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.35rem;padding-bottom:0.3rem;border-bottom:1px solid var(--glass-border);';
        header.textContent = group.label;
        groupWrap.appendChild(header);

        group.roles.forEach(role => {
            const label = document.createElement('label');
            label.title = role.description || '';
            label.style.cssText = 'display:flex;align-items:center;gap:0.5rem;padding:0.3rem 0;cursor:pointer;font-size:0.85rem;color:var(--text-primary);';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = role.name;
            cb.checked = currentRoles.includes(role.name);
            cb.style.accentColor = 'var(--primary-color)';
            label.appendChild(cb);
            label.appendChild(document.createTextNode(role.display_name));
            groupWrap.appendChild(label);
        });

        container.appendChild(groupWrap);
    });

    document.getElementById('role-modal').style.display = 'flex';
}

function saveRoles() {
    const checkboxes = document.querySelectorAll('#role-checkboxes input[type=checkbox]:checked');
    const roles = Array.from(checkboxes).map(cb => cb.value);

    fetch(`${window.SUBPATH}/accounts/api/users/${editingUserId}/roles/`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken')},
        body: JSON.stringify({roles}),
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            closeModal();
            location.reload();
        }
    });
}

function closeModal() {
    document.getElementById('role-modal').style.display = 'none';
    editingUserId = null;
}

function toggleActive(userId) {
    fetch(`${window.SUBPATH}/accounts/api/users/${userId}/toggle-active/`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken')},
        body: JSON.stringify({}),
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) location.reload();
    });
}
