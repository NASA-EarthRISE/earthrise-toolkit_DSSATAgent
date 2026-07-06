/**
 * ChatAgent Explorer - Interactive functionality for data explorer pages
 * Handles soil CRUD, weather checks, tab switching, and AJAX interactions
 */

function getCsrfToken() {
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
}

// Deployment subpath prefix (e.g. "/subpath" or ""). Prepend to module-absolute URLs.
const SUBPATH = window.SUBPATH || '';

document.addEventListener('DOMContentLoaded', () => {

    // ===================================
    // Tab Switching
    // ===================================

    const tabs = document.querySelectorAll('.tab');
    if (tabs.length > 0) {
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const targetId = tab.dataset.tab;

                // Update tab buttons
                tabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');

                // Update tab content
                document.querySelectorAll('.tab-content').forEach(content => {
                    content.classList.remove('active');
                });
                const target = document.getElementById(`tab-${targetId}`);
                if (target) target.classList.add('active');
            });
        });
    }

    // ===================================
    // Soil Detail - Layer Management
    // ===================================

    const addLayerBtn = document.getElementById('add-layer-btn');
    const layerModal = document.getElementById('layer-modal');
    const cancelLayerBtn = document.getElementById('cancel-layer-btn');
    const saveLayerBtn = document.getElementById('save-layer-btn');
    const layerModalTitle = document.getElementById('layer-modal-title');

    let editingLayerIdx = null;

    if (addLayerBtn && layerModal) {
        addLayerBtn.addEventListener('click', () => {
            editingLayerIdx = null;
            layerModalTitle.textContent = 'Add Soil Layer';
            clearLayerForm();
            layerModal.classList.remove('is-hidden');
        });

        cancelLayerBtn.addEventListener('click', () => {
            layerModal.classList.add('is-hidden');
        });

        // Close modal on overlay click
        layerModal.addEventListener('click', (e) => {
            if (e.target === layerModal) {
                layerModal.classList.add('is-hidden');
            }
        });

        saveLayerBtn.addEventListener('click', async () => {
            const data = getLayerFormData();
            if (!data.sllb) {
                alert('Depth is required');
                return;
            }

            try {
                let url, method;
                if (editingLayerIdx !== null) {
                    url = `${SUBPATH}/dssat/soils/${SOIL_ID}/layer/${editingLayerIdx}/edit/`;
                } else {
                    url = `${SUBPATH}/dssat/soils/${SOIL_ID}/add-layer/`;
                }

                const response = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
                    body: JSON.stringify(data),
                });

                const result = await response.json();
                if (result.success) {
                    window.location.reload();
                } else {
                    alert('Error: ' + (result.error || 'Unknown error'));
                }
            } catch (error) {
                console.error('Error saving layer:', error);
                alert('Failed to save layer');
            }
        });
    }

    // Edit layer buttons
    document.querySelectorAll('.edit-layer-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            editingLayerIdx = parseInt(btn.dataset.idx);
            layerModalTitle.textContent = `Edit Layer ${editingLayerIdx + 1}`;

            // Pre-fill form with existing values from the table row
            const row = btn.closest('tr');
            const cells = row.querySelectorAll('td');
            const fields = ['sllb', 'slcl', 'slsi', 'sbdm', 'sloc', 'slll', 'sldul', 'slsat', 'sksat'];
            fields.forEach((field, i) => {
                const input = document.getElementById(`layer-${field}`);
                const val = cells[i + 1]?.textContent?.trim();
                if (input && val && val !== '—') {
                    input.value = val;
                }
            });

            layerModal.classList.remove('is-hidden');
        });
    });

    // Delete layer buttons
    document.querySelectorAll('.delete-layer-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const idx = btn.dataset.idx;
            if (!confirm(`Delete layer ${parseInt(idx) + 1}?`)) return;

            try {
                const response = await fetch(`${SUBPATH}/dssat/soils/${SOIL_ID}/layer/${idx}/delete/`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
                });

                const result = await response.json();
                if (result.success) {
                    window.location.reload();
                } else {
                    alert('Error: ' + (result.error || 'Unknown error'));
                }
            } catch (error) {
                console.error('Error deleting layer:', error);
                alert('Failed to delete layer');
            }
        });
    });

    // Auto-estimate from clay/silt button
    const estimateLayerBtn = document.getElementById('estimate-layer-btn');
    if (estimateLayerBtn) {
        estimateLayerBtn.addEventListener('click', async () => {
            const clay = parseFloat(document.getElementById('layer-slcl')?.value);
            const silt = parseFloat(document.getElementById('layer-slsi')?.value);

            if (isNaN(clay) || isNaN(silt)) {
                alert('Enter clay and silt percentages first');
                return;
            }

            try {
                const response = await fetch(`${SUBPATH}/dssat/api/soils/estimate/`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
                    body: JSON.stringify({
                        clay_pct: clay,
                        silt_pct: silt,
                        bulk_density: parseFloat(document.getElementById('layer-sbdm')?.value) || undefined,
                        organic_carbon: parseFloat(document.getElementById('layer-sloc')?.value) || undefined,
                    }),
                });

                const data = await response.json();
                if (data.success && data.result) {
                    const r = data.result;
                    if (r.slll != null) document.getElementById('layer-slll').value = r.slll;
                    if (r.sldul != null) document.getElementById('layer-sldul').value = r.sldul;
                    if (r.slsat != null) document.getElementById('layer-slsat').value = r.slsat;
                    if (r.sksat != null) document.getElementById('layer-sksat').value = r.sksat;
                    if (r.sbdm != null && !document.getElementById('layer-sbdm').value) {
                        document.getElementById('layer-sbdm').value = r.sbdm;
                    }
                } else {
                    alert('Estimation failed: ' + (data.error || 'Unknown error'));
                }
            } catch (error) {
                console.error('Error estimating:', error);
            }
        });
    }

    function getLayerFormData() {
        const fields = ['sllb', 'slcl', 'slsi', 'sbdm', 'sloc', 'slll', 'sldul', 'slsat', 'sksat'];
        const data = {};
        fields.forEach(field => {
            const el = document.getElementById(`layer-${field}`);
            if (el && el.value) {
                data[field] = parseFloat(el.value);
            }
        });
        return data;
    }

    function clearLayerForm() {
        const fields = ['sllb', 'slcl', 'slsi', 'sbdm', 'sloc', 'slll', 'sldul', 'slsat', 'sksat'];
        fields.forEach(field => {
            const el = document.getElementById(`layer-${field}`);
            if (el) el.value = '';
        });
    }

