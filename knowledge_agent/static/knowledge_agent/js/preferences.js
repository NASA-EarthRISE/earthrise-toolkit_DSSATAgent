(function () {
    const form = document.getElementById('knowledge-prefs-form');
    const banner = form.querySelector('.save-banner');
    const csrfToken = (document.cookie.split(';').find(c => c.trim().startsWith('csrftoken=')) || '').split('=')[1] || '';

    form.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const selected = form.querySelector('input[name="preferred_strategy"]:checked');
        const value = selected ? selected.value : '';
        banner.style.display = 'none';

        try {
            const SUBPATH = window.SUBPATH || '';
            const resp = await fetch(SUBPATH + '/knowledge/api/config/preferences/', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken},
                body: JSON.stringify({preferred_strategy: value}),
            });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
            banner.textContent = data.reset ? 'Reset to system default.' : 'Saved.';
            banner.className = 'save-banner success';
            banner.style.display = 'block';
            setTimeout(() => { banner.style.display = 'none'; }, 4000);
        } catch (err) {
            banner.textContent = `Save failed: ${err.message}`;
            banner.className = 'save-banner error';
            banner.style.display = 'block';
        }
    });
})();
