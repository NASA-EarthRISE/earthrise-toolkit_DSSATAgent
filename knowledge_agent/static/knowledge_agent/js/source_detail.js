// Base-chunk card expand/collapse — toggles the `expanded` class on the
// parent card when a chunk header is clicked.
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.chunk-card-header').forEach((header) => {
        header.addEventListener('click', () => {
            header.parentElement.classList.toggle('expanded');
        });
    });
});
