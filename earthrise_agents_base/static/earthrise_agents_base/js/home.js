// Wrap each module row with OverlayScrollbars for a consistent styled
// horizontal scrollbar (matches the html-level one). Also toggles
// data-overflows so the CSS right-edge fade mask only appears when
// content actually overflows.
document.addEventListener('DOMContentLoaded', function () {
    if (typeof OverlayScrollbarsGlobal === 'undefined') return;
    const { OverlayScrollbars } = OverlayScrollbarsGlobal;

    function syncOverflow(row, inst) {
        let overflows;
        try {
            // v2 API: state() exposes hasOverflow.{x,y}.
            overflows = !!inst.state().hasOverflow.x;
        } catch (_e) {
            // Fallback: measure directly on the host element.
            overflows = row.scrollWidth > row.clientWidth + 1;
        }
        row.dataset.overflows = overflows ? 'true' : 'false';
    }

    document.querySelectorAll('[data-home-row]').forEach(function (row) {
        const inst = OverlayScrollbars(row, {
            scrollbars: {
                theme: 'os-theme-chatagent',
                autoHide: 'leave',
                autoHideDelay: 800,
            },
            overflow: { x: 'scroll', y: 'hidden' },
        });
        syncOverflow(row, inst);
        // OverlayScrollbars fires 'updated' on resize / content change.
        inst.on('updated', function () { syncOverflow(row, inst); });
    });
});
