/**
 * ChatAgent Soil Builder - Interactive soil profile creation
 * Handles texture-based creation with live property estimation
 */

function getCsrfToken() {
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
}

document.addEventListener('DOMContentLoaded', () => {

    // ===================================
    // Tab Switching
    // ===================================

    const tabs = document.querySelectorAll('.tab');
    if (tabs.length > 0) {
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const targetId = tab.dataset.tab;
                tabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                const target = document.getElementById(`tab-${targetId}`);
                if (target) target.classList.add('active');
            });
        });
    }

    // ===================================
    // Manual Form Submission
    // ===================================

    const manualForm = document.getElementById('manual-form');
    if (manualForm) {
        manualForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(manualForm);

            try {
                const response = await fetch(window.location.pathname, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': getCsrfToken() },
                    body: formData,
                });

                const data = await response.json();
                showResult(data);
            } catch (error) {
                console.error('Error creating soil:', error);
                showResult({ error: 'Failed to create soil profile' });
            }
        });
    }

    // ===================================
    // Texture Form - Layer Management
    // ===================================

    let layerCount = 1;
    const textureLayers = document.getElementById('texture-layers');
    const addTextureLayerBtn = document.getElementById('add-texture-layer');

    if (addTextureLayerBtn && textureLayers) {
        addTextureLayerBtn.addEventListener('click', () => {
            layerCount++;
            const layerDiv = document.createElement('div');
            layerDiv.className = 'texture-layer';
            layerDiv.dataset.index = layerCount - 1;
            layerDiv.innerHTML = `
                <button type="button" class="remove-layer" onclick="this.closest('.texture-layer').remove()">&times;</button>
                <div class="form-grid cols-4">
                    <div class="form-group">
                        <label>Depth (cm)</label>
                        <input type="number" name="depth" value="${30 + layerCount * 30}" step="1" class="form-input" required>
                    </div>
                    <div class="form-group">
                        <label>Clay %</label>
                        <input type="number" name="clay_pct" value="20" step="0.1" class="form-input clay-input" required>
                    </div>
                    <div class="form-group">
                        <label>Silt %</label>
                        <input type="number" name="silt_pct" value="30" step="0.1" class="form-input silt-input" required>
                    </div>
                    <div class="form-group">
                        <label>Org. Carbon %</label>
                        <input type="number" name="organic_carbon" value="0.5" step="0.01" class="form-input">
                    </div>
                </div>
                <div class="estimate-preview" data-index="${layerCount - 1}"></div>
            `;
            textureLayers.appendChild(layerDiv);
            setupEstimateListeners(layerDiv);
        });
    }

    // ===================================
    // Live Estimation
    // ===================================

    function setupEstimateListeners(layerDiv) {
        const clayInput = layerDiv.querySelector('.clay-input');
        const siltInput = layerDiv.querySelector('.silt-input');
        const previewDiv = layerDiv.querySelector('.estimate-preview');

        let debounceTimer;

        const onInput = () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => estimateProperties(layerDiv), 500);
        };

        if (clayInput) clayInput.addEventListener('input', onInput);
        if (siltInput) siltInput.addEventListener('input', onInput);
    }

    async function estimateProperties(layerDiv) {
        const clay = parseFloat(layerDiv.querySelector('.clay-input')?.value);
        const silt = parseFloat(layerDiv.querySelector('.silt-input')?.value);
        const previewDiv = layerDiv.querySelector('.estimate-preview');

        if (isNaN(clay) || isNaN(silt) || !previewDiv) return;

        try {
            const basePath = window.location.pathname.replace(/explorer\/soils\/create[^/]*\/?$/, '');
            const response = await fetch(`${basePath}api/soils/estimate/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
                body: JSON.stringify({ clay_pct: clay, silt_pct: silt }),
            });

            const data = await response.json();
            if (data.success && data.result) {
                const r = data.result;
                previewDiv.innerHTML = `
                    <strong>Estimated properties:</strong>
                    LL: ${r.slll?.toFixed(3) || '—'} |
                    DUL: ${r.sldul?.toFixed(3) || '—'} |
                    SAT: ${r.slsat?.toFixed(3) || '—'} |
                    KSAT: ${r.sksat?.toFixed(1) || '—'} |
                    BD: ${r.sbdm?.toFixed(2) || '—'}
                `;
                previewDiv.classList.add('visible');
            }
        } catch (error) {
            console.error('Estimate error:', error);
        }
    }

    // Initialize estimation for existing layers
    document.querySelectorAll('.texture-layer').forEach(layer => {
        setupEstimateListeners(layer);
    });

    // ===================================
    // Texture Form Submission
    // ===================================

    const textureForm = document.getElementById('texture-form');
    if (textureForm) {
        textureForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const soilId = document.getElementById('tex_soil_id')?.value || '';
            const name = document.getElementById('tex_name')?.value || '';

            // Collect layers
            const layers = [];
            document.querySelectorAll('.texture-layer').forEach(layerDiv => {
                const depth = parseFloat(layerDiv.querySelector('[name="depth"]')?.value);
                const clay = parseFloat(layerDiv.querySelector('[name="clay_pct"]')?.value);
                const silt = parseFloat(layerDiv.querySelector('[name="silt_pct"]')?.value);
                const oc = parseFloat(layerDiv.querySelector('[name="organic_carbon"]')?.value);

                if (!isNaN(depth) && !isNaN(clay) && !isNaN(silt)) {
                    const layer = { depth, clay_pct: clay, silt_pct: silt };
                    if (!isNaN(oc)) layer.organic_carbon = oc;
                    layers.push(layer);
                }
            });

            if (layers.length === 0) {
                showResult({ error: 'Add at least one layer' });
                return;
            }

            try {
                const basePath = window.location.pathname.replace(/create[^/]*\/?$/, '');
                const response = await fetch(`${basePath}create-from-texture/`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
                    body: JSON.stringify({ soil_id: soilId, name, layers }),
                });

                const data = await response.json();
                showResult(data);
            } catch (error) {
                console.error('Error creating soil from texture:', error);
                showResult({ error: 'Failed to create soil profile' });
            }
        });
    }

    // ===================================
    // Result Display
    // ===================================

    function showResult(data) {
        const resultPanel = document.getElementById('create-result');
        if (!resultPanel) return;

        resultPanel.classList.remove('is-hidden');

        if (data.error) {
            resultPanel.className = 'result-panel error';
            resultPanel.innerHTML = `<strong>Error:</strong> ${escapeHtml(data.error)}`;
        } else if (data.success) {
            resultPanel.className = 'result-panel';
            const soilId = data.result?.soil_id || data.result?.profile?.soil_id || '';
            resultPanel.innerHTML = `
                <strong>Success!</strong> Soil profile created.
                ${soilId ? `<br><a href="../${encodeURIComponent(soilId)}/" style="color: var(--primary-color);">View profile ${escapeHtml(soilId)} &rarr;</a>` : ''}
            `;
        }

        resultPanel.scrollIntoView({ behavior: 'smooth' });
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Make body scrollable
    document.body.classList.add('explorer-page');
});
