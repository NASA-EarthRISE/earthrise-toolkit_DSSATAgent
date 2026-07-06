/* Simple tab filtering for categories */
document.addEventListener('DOMContentLoaded', () => {
    const tabs = document.querySelectorAll('#category-tabs .tab');
    const sections = document.querySelectorAll('.category-section');
    if (!tabs.length) return;

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            const key = tab.dataset.tab;
            sections.forEach(s => {
                s.style.display = (key === 'all' || s.dataset.category === key) ? '' : 'none';
            });
        });
    });
});
