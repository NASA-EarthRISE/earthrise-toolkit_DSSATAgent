(function() {
    const _cfg = document.getElementById('cf-config').dataset;
    const CROP_CODE = _cfg.cropCode;
    const DSSAT_MODEL = _cfg.dssatModel;
    const EDIT_MODE = _cfg.editMode === 'true';
    const CULTIVAR_ID = _cfg.cultivarId;
    const CLONE_SOURCE_ID = _cfg.cloneSourceId;
    const SUBPATH = window.SUBPATH || '';

    let schema = null;
    let cultivarData = null;
    let codeValid = false;

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function buildFieldInput(field, value) {
        const id = 'param_' + field.name;
        let input;
        if (field.type === 'number') {
            const step = field.precision > 0 ? (1 / Math.pow(10, field.precision)).toString() : '1';
            input = `<input type="number" id="${id}" name="${field.name}" step="${step}" value="${value != null ? value : ''}" class="form-input">`;
        } else {
            const ml = field.max_length || 16;
            input = `<input type="text" id="${id}" name="${field.name}" maxlength="${ml}" value="${escapeHtml(value != null ? String(value) : '')}" class="form-input">`;
        }
        return `<div class="form-group"><label for="${id}">${escapeHtml(field.label)}</label>${input}</div>`;
    }

    function renderSchema() {
        if (!schema) return;

        // Cultivar params
        const culGrid = document.getElementById('cul-params-grid');
        culGrid.innerHTML = schema.cultivar_fields.map(f => {
            const val = cultivarData ? (cultivarData.cultivar_params || {})[f.name] : null;
            return buildFieldInput(f, val);
        }).join('');

        // Ecotype dropdown
        const ecoSelect = document.getElementById('ecotype_select');
        ecoSelect.innerHTML = '<option value="">-- None --</option>';
        for (const e of schema.available_ecotypes || []) {
            const label = `${e.ecotype_code} - ${e.ecotype_name || 'unnamed'} [${e.source}]`;
            ecoSelect.innerHTML += `<option value="${e.id}">${escapeHtml(label)}</option>`;
        }

        // Pre-select ecotype if editing/cloning
        if (cultivarData && cultivarData.ecotype_code) {
            for (const e of schema.available_ecotypes || []) {
                if (e.ecotype_code === cultivarData.ecotype_code) {
                    ecoSelect.value = e.id;
                    break;
                }
            }
        }

        // Pre-fill identity fields
        if (cultivarData) {
            const nameField = document.getElementById('cultivar_name');
            if (nameField && cultivarData.cultivar_name) nameField.value = cultivarData.cultivar_name;
            if (!EDIT_MODE && CLONE_SOURCE_ID) {
                // Clone mode: clear code so user enters a new one
                document.getElementById('cultivar_code').value = '';
                document.getElementById('cultivar_code').focus();
            }
        }
    }

    async function loadSchema() {
        try {
            const resp = await fetch(`${SUBPATH}/dssat/api/cultivars/${CROP_CODE}/schema/?dssat_model=${DSSAT_MODEL}`);
            const data = await resp.json();
            if (data.success) {
                schema = data;
                document.getElementById('page-title').textContent =
                    `${EDIT_MODE ? 'Edit' : 'Create'} ${data.crop_name || CROP_CODE} Cultivar`;
            }
        } catch (e) {
            console.error('Failed to load schema', e);
        }

        // Load existing cultivar data if editing or cloning
        const sourceId = EDIT_MODE ? CULTIVAR_ID : CLONE_SOURCE_ID;
        if (sourceId) {
            try {
                const resp = await fetch(`${SUBPATH}/dssat/api/cultivars/${CROP_CODE}/${sourceId}/`);
                const data = await resp.json();
                if (data.success) cultivarData = data;
            } catch (e) {
                console.error('Failed to load cultivar', e);
            }
        }

        renderSchema();
        document.getElementById('validate-btn').classList.remove('is-hidden');
    }

    function collectParams(fieldDefs) {
        const params = {};
        for (const f of fieldDefs) {
            const el = document.getElementById('param_' + f.name);
            if (!el) continue;
            const raw = el.value.trim();
            if (raw === '') continue;
            params[f.name] = f.type === 'number' ? parseFloat(raw) : raw;
        }
        return params;
    }

    // ── Cultivar code uniqueness check ──
    const codeInput = document.getElementById('cultivar_code');
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
                const resp = await fetch(`${SUBPATH}/dssat/api/cultivars/${CROP_CODE}/?dssat_model=${DSSAT_MODEL}`);
                const data = await resp.json();
                const exists = (data.cultivars || []).some(cv =>
                    cv.code === code && !(EDIT_MODE && cv.id === CULTIVAR_ID)
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
    // Trigger initial check for pre-filled codes (edit mode)
    if (codeInput.value.trim()) {
        codeInput.dispatchEvent(new Event('input'));
    }

    document.getElementById('cultivar-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const saveBtn = document.getElementById('save-btn');
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<span class="spinner"></span>Saving...';

        const params = collectParams(schema.cultivar_fields);
        const body = {
            cultivar_code: document.getElementById('cultivar_code').value.trim(),
            cultivar_name: document.getElementById('cultivar_name').value.trim(),
            dssat_model: DSSAT_MODEL,
            params: params,
            ecotype_id: document.getElementById('ecotype_select').value || null,
        };

        try {
            let url, method;
            if (EDIT_MODE) {
                url = `${SUBPATH}/dssat/api/cultivars/${CROP_CODE}/${CULTIVAR_ID}/`;
                method = 'PUT';
            } else if (CLONE_SOURCE_ID) {
                url = `${SUBPATH}/dssat/api/cultivars/${CROP_CODE}/${CLONE_SOURCE_ID}/clone/`;
                method = 'POST';
                body.clone_ecotype = false;
            } else {
                url = `${SUBPATH}/dssat/api/cultivars/${CROP_CODE}/create/`;
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
            saveBtn.textContent = 'Save Cultivar';
        }
    });

    document.getElementById('validate-btn').addEventListener('click', async () => {
        const vBtn = document.getElementById('validate-btn');
        const vResult = document.getElementById('validation-result');
        vBtn.disabled = true;
        vBtn.innerHTML = '<span class="spinner"></span>Validating...';
        vResult.style.display = 'none';

        // We need a saved cultivar to validate — save first if creating
        let idToValidate = CULTIVAR_ID;
        if (!idToValidate) {
            // Create a temporary cultivar for validation
            const params = collectParams(schema.cultivar_fields);
            const body = {
                cultivar_code: document.getElementById('cultivar_code').value.trim() || '_VTMP',
                cultivar_name: 'Validation temp',
                dssat_model: DSSAT_MODEL,
                params: params,
                ecotype_id: document.getElementById('ecotype_select').value || null,
                source: 'custom',
            };
            try {
                const resp = await fetch(`${SUBPATH}/dssat/api/cultivars/${CROP_CODE}/create/`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body),
                });
                const data = await resp.json();
                if (data.id) {
                    idToValidate = data.id;
                } else {
                    vResult.className = 'validation-result error';
                    vResult.style.display = '';
                    vResult.textContent = 'Save the cultivar first before validating.';
                    vBtn.disabled = false;
                    vBtn.textContent = 'Validate with DSSAT';
                    return;
                }
            } catch (err) {
                vResult.className = 'validation-result error';
                vResult.style.display = '';
                vResult.textContent = 'Failed to save for validation: ' + err.message;
                vBtn.disabled = false;
                vBtn.textContent = 'Validate with DSSAT';
                return;
            }
        }

        try {
            const resp = await fetch(`${SUBPATH}/dssat/api/cultivars/${CROP_CODE}/${idToValidate}/validate/`, {method: 'POST'});
            const data = await resp.json();
            vResult.className = 'validation-result ' + (data.status || 'error');
            vResult.style.display = '';
            vResult.textContent = data.message || JSON.stringify(data);
        } catch (err) {
            vResult.className = 'validation-result error';
            vResult.style.display = '';
            vResult.textContent = 'Validation request failed: ' + err.message;
        } finally {
            vBtn.disabled = false;
            vBtn.textContent = 'Validate with DSSAT';
        }
    });

    loadSchema();
})();
