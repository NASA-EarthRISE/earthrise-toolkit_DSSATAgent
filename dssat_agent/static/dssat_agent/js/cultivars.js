(function () {
    const search = document.getElementById('crop-search');
    const groupSel = document.getElementById('crop-group-filter');
    const sections = Array.from(document.querySelectorAll('.crop-group-section'));

    function apply() {
        const q = (search.value || '').toLowerCase().trim();
        const g = groupSel.value;
        for (const sec of sections) {
            const secGroup = sec.dataset.group || '';
            const groupMatches = !g || g === secGroup;
            let visibleCount = 0;
            const cards = sec.querySelectorAll('.crop-card');
            for (const card of cards) {
                const code = (card.dataset.code || '').toLowerCase();
                const name = card.dataset.name || '';
                const textMatches = !q || code.includes(q) || name.includes(q);
                const show = groupMatches && textMatches;
                card.style.display = show ? '' : 'none';
                if (show) visibleCount++;
            }
            sec.classList.toggle('is-hidden', visibleCount === 0);
        }
    }
    search.addEventListener('input', apply);
    groupSel.addEventListener('change', apply);
})();
