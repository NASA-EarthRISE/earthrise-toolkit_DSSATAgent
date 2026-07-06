/**
 * Sitewide feedback widget.
 *
 * Wires the floating "Feedback" button and every `[data-open-feedback]`
 * element (including the nav-bar "Send Feedback" link) to the modal partial
 * defined in chat/partials/feedback_widget.html.
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

    function getCsrfToken() {
        return getCookie('csrftoken');
    }

    function init() {
        const modal = document.querySelector('[data-feedback-modal]');
        const form = document.querySelector('[data-feedback-form]');
        if (!modal || !form) {
            return;
        }

        const statusEl = form.querySelector('[data-feedback-status]');
        const submitBtn = form.querySelector('[data-feedback-submit]');

        function setStatus(text, kind) {
            if (!statusEl) return;
            statusEl.textContent = text || '';
            statusEl.classList.remove('error', 'success');
            if (kind) statusEl.classList.add(kind);
        }

        function openModal() {
            modal.hidden = false;
            setStatus('');
            const firstField = form.querySelector('select, textarea, input');
            if (firstField) firstField.focus();
        }

        function closeModal() {
            modal.hidden = true;
        }

        document.querySelectorAll('[data-open-feedback]').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                openModal();
            });
        });

        modal.querySelectorAll('[data-close-feedback]').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                closeModal();
            });
        });

        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeModal();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.hidden) closeModal();
        });

        form.addEventListener('submit', async (e) => {
            e.preventDefault();

            const data = new FormData(form);
            const payload = {
                category: (data.get('category') || '').toString(),
                content: (data.get('content') || '').toString().trim(),
                page_url: window.location.href,
            };
            const anonymousEmail = (data.get('anonymous_email') || '').toString().trim();
            if (anonymousEmail) {
                payload.anonymous_email = anonymousEmail;
            }

            if (!payload.category) {
                setStatus('Please choose a category.', 'error');
                return;
            }
            if (!payload.content) {
                setStatus('Please tell us a little more.', 'error');
                return;
            }

            submitBtn.disabled = true;
            setStatus('Sending…');

            try {
                const response = await fetch(`${BASE}/api/feedback/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                    },
                    body: JSON.stringify(payload),
                });
                const result = await response.json().catch(() => ({}));

                if (!response.ok || !result.success) {
                    const msg = (result && result.error) || 'Unable to send feedback.';
                    setStatus(msg, 'error');
                    submitBtn.disabled = false;
                    return;
                }

                setStatus('Thanks — feedback sent.', 'success');
                form.reset();
                setTimeout(() => {
                    closeModal();
                    setStatus('');
                    submitBtn.disabled = false;
                }, 900);
            } catch (err) {
                setStatus('Network error. Please try again.', 'error');
                submitBtn.disabled = false;
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
