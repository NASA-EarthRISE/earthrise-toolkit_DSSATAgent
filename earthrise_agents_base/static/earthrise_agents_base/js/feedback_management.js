/**
 * Feedback Management admin page interactions.
 *  - Tab switching
 *  - Message Feedback tab: fetches rows via the admin API, supports rating
 *    filter and pagination, renders "Open chat" links with `#message-<id>`.
 *  - Site Feedback tab: client-side filter on category + status, per-row
 *    save for status/admin_notes via POST /management/feedback/<id>/update/.
 */
(function () {
    'use strict';

    const BASE = (window.SUBPATH || '').replace(/\/$/, '');

    function getCookie(name) {
        const prefix = name + '=';
        const cookies = document.cookie ? document.cookie.split(';') : [];
        for (let i = 0; i < cookies.length; i += 1) {
            const c = cookies[i].trim();
            if (c.startsWith(prefix)) {
                return decodeURIComponent(c.substring(prefix.length));
            }
        }
        return '';
    }

    function csrfToken() {
        return getCookie('csrftoken');
    }

    function escapeHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function formatDate(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return iso;
        return d.toLocaleString(undefined, {
            month: 'short', day: 'numeric', year: 'numeric',
            hour: '2-digit', minute: '2-digit',
        });
    }

    // ---- Tabs -----------------------------------------------------------
    function initTabs() {
        const tabs = document.querySelectorAll('.mgmt-tab');
        const panels = document.querySelectorAll('.tab-panel');
        tabs.forEach((tab) => {
            tab.addEventListener('click', () => {
                const name = tab.dataset.tab;
                tabs.forEach((t) => t.classList.toggle('active', t === tab));
                panels.forEach((p) => {
                    p.classList.toggle('active', p.id === `tab-${name}`);
                });
            });
        });
    }

    // ---- Message Feedback tab ------------------------------------------
    const msgState = { page: 1, rating: 'all', hasNext: false };

    async function loadMessageFeedback() {
        const tbody = document.querySelector('[data-messages-tbody]');
        const prevBtn = document.querySelector('[data-messages-prev]');
        const nextBtn = document.querySelector('[data-messages-next]');
        const info = document.querySelector('[data-messages-page-info]');
        if (!tbody) return;

        tbody.innerHTML = '<tr><td colspan="8" class="mgmt-empty">Loading…</td></tr>';

        const params = new URLSearchParams({
            page: String(msgState.page),
            rating: msgState.rating,
        });

        try {
            const resp = await fetch(`${BASE}/management/feedback/messages/?${params.toString()}`);
            const data = await resp.json();

            if (!resp.ok || !data.success) {
                tbody.innerHTML = `<tr><td colspan="8" class="mgmt-empty">Failed to load: ${escapeHtml(data.error || 'unknown error')}</td></tr>`;
                return;
            }

            if (!data.feedback.length) {
                tbody.innerHTML = '<tr><td colspan="8" class="mgmt-empty">No feedback yet for this filter.</td></tr>';
            } else {
                tbody.innerHTML = data.feedback.map((fb) => {
                    const ratingIcon = fb.rating === 'up'
                        ? '<span class="feedback-rating-icon up" title="Thumbs up">▲</span>'
                        : '<span class="feedback-rating-icon down" title="Thumbs down">▼</span>';
                    const chatUrl = `${BASE}/chats/${fb.chat.id}/#message-${fb.message.id}`;
                    const prior = fb.prior_user_message
                        ? escapeHtml(fb.prior_user_message.snippet || '')
                        : '<span class="muted">—</span>';
                    const rater = escapeHtml(fb.user.full_name || fb.user.username || 'anonymous');
                    return `
                        <tr>
                            <td class="nowrap">${escapeHtml(formatDate(fb.created_at))}</td>
                            <td>${rater}</td>
                            <td>${escapeHtml(fb.chat.title)}</td>
                            <td>${ratingIcon}</td>
                            <td class="feedback-content-cell">${prior}</td>
                            <td class="feedback-content-cell">${escapeHtml(fb.message.snippet || '')}</td>
                            <td class="feedback-content-cell">${escapeHtml(fb.comment || '')}</td>
                            <td><a href="${chatUrl}" target="_blank" rel="noopener">Open chat</a></td>
                        </tr>`;
                }).join('');
            }

            msgState.hasNext = !!data.has_next;
            if (info) {
                const start = (data.page - 1) * data.page_size + 1;
                const end = Math.min(data.page * data.page_size, data.total);
                info.textContent = data.total
                    ? `Showing ${start}–${end} of ${data.total}`
                    : '';
            }
            if (prevBtn) prevBtn.disabled = msgState.page <= 1;
            if (nextBtn) nextBtn.disabled = !msgState.hasNext;
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="8" class="mgmt-empty">Network error loading feedback.</td></tr>`;
        }
    }

    function initMessagesTab() {
        const ratingFilter = document.querySelector('[data-messages-filter-rating]');
        const prevBtn = document.querySelector('[data-messages-prev]');
        const nextBtn = document.querySelector('[data-messages-next]');

        if (ratingFilter) {
            ratingFilter.addEventListener('change', () => {
                msgState.rating = ratingFilter.value || 'all';
                msgState.page = 1;
                loadMessageFeedback();
            });
        }
        if (prevBtn) {
            prevBtn.addEventListener('click', () => {
                if (msgState.page > 1) {
                    msgState.page -= 1;
                    loadMessageFeedback();
                }
            });
        }
        if (nextBtn) {
            nextBtn.addEventListener('click', () => {
                if (msgState.hasNext) {
                    msgState.page += 1;
                    loadMessageFeedback();
                }
            });
        }

        loadMessageFeedback();
    }

    // ---- Site Feedback tab ----------------------------------------------
    function applySiteFilters() {
        const catFilter = document.querySelector('[data-site-filter-category]');
        const statFilter = document.querySelector('[data-site-filter-status]');
        const cat = catFilter ? catFilter.value : 'all';
        const stat = statFilter ? statFilter.value : 'all';

        document.querySelectorAll('[data-site-row]').forEach((row) => {
            const rowCat = row.dataset.category;
            const rowStat = row.dataset.status;
            const matches = (cat === 'all' || rowCat === cat)
                && (stat === 'all' || rowStat === stat);
            row.style.display = matches ? '' : 'none';
        });
    }

    async function saveSiteRow(row) {
        const id = row.dataset.feedbackId;
        const statusSel = row.querySelector('[data-site-status]');
        const notesEl = row.querySelector('[data-site-notes]');
        const statusMsg = row.querySelector('[data-site-row-status]');
        if (!id || !statusSel || !notesEl) return;

        const payload = {
            status: statusSel.value,
            admin_notes: notesEl.value,
        };

        if (statusMsg) {
            statusMsg.textContent = 'Saving…';
            statusMsg.classList.remove('success', 'error');
        }

        try {
            const resp = await fetch(`${BASE}/management/feedback/${id}/update/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken(),
                },
                body: JSON.stringify(payload),
            });
            const result = await resp.json().catch(() => ({}));
            if (!resp.ok || !result.success) {
                if (statusMsg) {
                    statusMsg.textContent = (result && result.error) || 'Save failed';
                    statusMsg.classList.add('error');
                }
                return;
            }
            row.dataset.status = result.feedback.status;
            applySiteFilters();
            if (statusMsg) {
                statusMsg.textContent = 'Saved';
                statusMsg.classList.add('success');
                setTimeout(() => {
                    statusMsg.textContent = '';
                    statusMsg.classList.remove('success');
                }, 1500);
            }
        } catch (err) {
            if (statusMsg) {
                statusMsg.textContent = 'Network error';
                statusMsg.classList.add('error');
            }
        }
    }

    function initSiteTab() {
        document.querySelectorAll('[data-site-filter-category]').forEach((sel) => {
            sel.addEventListener('change', applySiteFilters);
        });
        document.querySelectorAll('[data-site-filter-status]').forEach((sel) => {
            sel.addEventListener('change', applySiteFilters);
        });
        document.querySelectorAll('[data-site-save]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const row = btn.closest('[data-site-row]');
                if (row) saveSiteRow(row);
            });
        });
    }

    function init() {
        initTabs();
        initMessagesTab();
        initSiteTab();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
