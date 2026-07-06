/*
 * Reusable expandable crop card.
 *
 * Used by DSSAT Preferences (My Crops), DSSAT System Config (System Per-Crop
 * Defaults, Curated Dropdowns), and any future page that needs a per-crop
 * card with a clearly labeled header, expandable body, and delete action.
 *
 * Usage:
 *
 *   const {card, body, setSaving} = buildCropCard({
 *       cropCode: 'MZ',
 *       cropName: 'Maize (Corn)',
 *       initiallyExpanded: true,
 *       deleteLabel: 'Remove this crop',
 *       onDelete: async () => { ... },
 *   });
 *   // Populate the body with your own content:
 *   body.appendChild(myFormEl);
 *   // Attach the card to the DOM:
 *   container.appendChild(card);
 */

(function (global) {
    'use strict';

    function buildCropCard(config) {
        const {
            cropCode,
            cropName,
            initiallyExpanded = true,
            deleteLabel = 'Remove',
            onDelete = null,
        } = config;

        const card = document.createElement('div');
        card.className = 'crop-card';
        card.dataset.cropCode = cropCode;

        const header = document.createElement('div');
        header.className = 'crop-card-header';
        header.innerHTML = `
            <h3 class="crop-card-title">
                <span class="crop-code-pill">${_escape(cropCode)}</span>
                <span class="crop-name">${_escape(cropName || cropCode)}</span>
            </h3>
            <span class="toggle-icon" aria-hidden="true">${initiallyExpanded ? '▾' : '▸'}</span>
        `;

        const body = document.createElement('div');
        body.className = 'crop-card-body';
        if (!initiallyExpanded) body.classList.add('collapsed');

        const footer = document.createElement('div');
        footer.className = 'crop-card-footer';
        if (onDelete) {
            const del = document.createElement('button');
            del.type = 'button';
            del.className = 'btn-sm btn-reject crop-card-delete';
            del.textContent = deleteLabel;
            del.addEventListener('click', async (ev) => {
                ev.stopPropagation();
                try {
                    await onDelete(cropCode);
                } catch (err) {
                    alert(`Failed: ${err.message || err}`);
                }
            });
            footer.appendChild(del);
        }

        // Expand/collapse on header click (but not when clicking interactive
        // elements inside the body)
        header.addEventListener('click', () => {
            body.classList.toggle('collapsed');
            footer.classList.toggle('collapsed');
            const icon = header.querySelector('.toggle-icon');
            if (icon) icon.textContent = body.classList.contains('collapsed') ? '▸' : '▾';
        });

        card.appendChild(header);
        card.appendChild(body);
        card.appendChild(footer);

        function setSaving(saving) {
            card.classList.toggle('is-saving', !!saving);
        }

        return {card, body, footer, header, setSaving};
    }

    function _escape(s) {
        const d = document.createElement('div');
        d.textContent = String(s == null ? '' : s);
        return d.innerHTML;
    }

    global.buildCropCard = buildCropCard;
})(window);
