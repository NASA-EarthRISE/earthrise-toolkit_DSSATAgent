(function() {
    const _cfg = document.getElementById('ef-config').dataset;
    const CROP_CODE = _cfg.cropCode;
    const DSSAT_MODEL = _cfg.dssatModel;
    const EDIT_MODE = _cfg.editMode === 'true';
    const ECOTYPE_ID = _cfg.ecotypeId;
    const SUBPATH = window.SUBPATH || '';

    let schema = null;
    let ecotypeData = null;
    let codeValid = false;

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function buildFieldInput(field, value) {
        const id = 'param_' + field.name;
        const step = field.precision > 0 ? (1 / Math.pow(10, field.precision)).toString() : '1';
        const input = field.type === 'number'
            ? `<input type="number" id="${id}" name="${field.name}" step="${step}" value="${value != null ? value : ''}">`
            : `<input type="text" id="${id}" name="${field.name}" maxlength="${field.max_length || 16}" value="${escapeHtml(value != null ? String(value) : '')}">`;
        return `<div class="form-group"><label for="${id}">${escapeHtml(field.label)}</label>${input}</div>`;
    }

    async function loadSchema() {
        try {
            const resp = await fetch(`${SUBPATH}/dssat/api/cultivars/${CROP_CODE}/schema/?dssat_model=${DSSAT_MODEL}`);
            const data = await resp.json();
            if (data.success) schema = data;
        } catch (e) { console.error('Failed to load schema', e); }

        if (EDIT_MODE && ECOTYPE_ID) {
            try {
                const resp = await fetch(`${SUBPATH}/dssat/api/ecotypes/${CROP_CODE}/${ECOTYPE_ID}/`);
                const data = await resp.json();
                if (data.success) ecotypeData = data;
            } catch (e) { console.error('Failed to load ecotype', e); }
        }

        if (schema) {
            const grid = document.getElementById('eco-params-grid');
            grid.innerHTML = (schema.ecotype_fields || []).map(f => {
                const val = ecotypeData ? (ecotypeData.params || {})[f.name] : null;
                return buildFieldInput(f, val);
            }).join('');
        }

        if (ecotypeData) {
            document.getElementById('ecotype_name').value = ecotypeData.ecotype_name || '';
            if (EDIT_MODE) {
                document.getElementById('ecotype_code').value = ecotypeData.ecotype_code || '';
            }
        }

        // Trigger code check
        document.getElementById('ecotype_code').dispatchEvent(new Event('input'));
    }

    // Code uniqueness check
    const codeInput = document.getElementById('ecotype_code');
    const saveBtn = document.getElementById('save-btn');
    const feedback = document.getElementById('code-feedback');
    let codeCheckTimeout;

    function updateSaveBtn() {
        saveBtn.disabled = !codeValid;
    }

    codeInput.addEventListener('input', () => {
        clearTimeout(codeCheckTimeout);
        const code = codeInput.value.trim();
        if (!code) {
            codeInput.style.borderColor = '';
            feedback.textContent = '';
            feedback.className = 'code-feedback';
            codeValid = false;
            updateSaveBtn();
            return;
        }
        codeCheckTimeout = setTimeout(async () => {
            try {
                const resp = await fetch(`${SUBPATH}/dssat/api/ecotypes/${CROP_CODE}/?dssat_model=${DSSAT_MODEL}`);
                const data = await resp.json();
                const exists = (data.ecotypes || []).some(e =>
                    e.ecotype_code === code && !(EDIT_MODE && e.id === ECOTYPE_ID)
                );
                codeValid = !exists;
                codeInput.style.borderColor = exists ? '#f64137' : '#15803d';
                feedback.className = 'code-feedback ' + (exists ? 'invalid' : 'valid');
                feedback.textContent = exists ? 'Code already in use' : 'Valid code';
                updateSaveBtn();
            } catch (e) {
                codeValid = true;
                updateSaveBtn();
            }
        }, 300);
    });

    document.getElementById('ecotype-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<span class="spinner"></span>Saving...';

        const params = {};
        for (const f of (schema ? schema.ecotype_fields : [])) {
            const el = document.getElementById('param_' + f.name);
            if (!el) continue;
            const raw = el.value.trim();
            if (raw === '') continue;
            params[f.name] = f.type === 'number' ? parseFloat(raw) : raw;
        }

        const body = {
            ecotype_code: codeInput.value.trim(),
            ecotype_name: document.getElementById('ecotype_name').value.trim(),
            dssat_model: DSSAT_MODEL,
            params: params,
        };

        try {
            let url, method;
            if (EDIT_MODE) {
                url = `${SUBPATH}/dssat/api/ecotypes/${CROP_CODE}/${ECOTYPE_ID}/`;
                method = 'PUT';
            } else {
                url = `${SUBPATH}/dssat/api/ecotypes/${CROP_CODE}/create/`;
                method = 'POST';
                body.source = 'custom';
            }
            const resp = await fetch(url, {
                method: method,
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (data.success || data.id) {
                window.location.href = `${SUBPATH}/dssat/crops/${CROP_CODE}/?dssat_model=${DSSAT_MODEL}`;
            } else {
                alert('Error: ' + (data.error || 'Unknown error'));
            }
        } catch (err) {
            alert('Error: ' + err.message);
        } finally {
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save Ecotype';
        }
    });

    loadSchema();
})();
