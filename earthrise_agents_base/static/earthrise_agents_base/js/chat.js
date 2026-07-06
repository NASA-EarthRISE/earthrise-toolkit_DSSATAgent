/**
 * ChatAgent - Modern SPA Chat Application
 * Handles real-time messaging, conversation management, chart display, and UI interactions
 */

function getCsrfToken() {
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
}

/**
 * AgentToggleController — wires the .agent-toggle-bar buttons to the
 * per-chat enabled_agents state. Empty set == unrestricted (backend
 * treats [] as "all agents allowed"). Changes are debounced and
 * persisted via PATCH /api/chats/<id>/agents/. Apply timing is
 * future-turns-only — the running message keeps its original scope.
 *
 * The controller owns the inner DOM of `#agent-toggle-bar`: it builds
 * either the unlocked button row or the locked indicator pill from
 * `availableAgentList` ([{label, display}, ...]). On chat-switch the
 * SPA calls `applyChatState({chatId, enabled, locked})` — no full
 * reload required to swap between locked and unlocked chats.
 */
class AgentToggleController {
    constructor({ container, chatId, basePath, enabled, available, availableList, locked }) {
        this.container = container;
        this.chatId = chatId;
        this.basePath = basePath || '';
        this.availableList = Array.isArray(availableList) ? availableList : [];
        this.available = Array.isArray(available) && available.length
            ? available
            : this.availableList.map((a) => a.label);
        this.enabled = new Set(Array.isArray(enabled) ? enabled : []);
        this.locked = !!locked;
        this._saveTimer = null;
        this._wired = false;

        if (!this.container) return;
        this._buildDOM();
        this._wire();
    }

    /**
     * Swap chat without reloading the page: rebuild the inner DOM for
     * the new chat's lock state, repoint chatId so debounced PATCHes
     * hit the right URL, and reset the enabled set from the server's
     * authoritative copy.
     */
    applyChatState({ chatId, enabled, locked }) {
        if (chatId != null) this.chatId = chatId;
        this.enabled = new Set(Array.isArray(enabled) ? enabled : []);
        this.locked = !!locked;
        this._buildDOM();
    }

    _displayFor(label) {
        const match = this.availableList.find((a) => a.label === label);
        return match ? match.display : label;
    }

    _buildDOM() {
        if (!this.container) return;
        this.container.classList.toggle('locked', this.locked);
        this.container.dataset.locked = this.locked ? 'true' : 'false';
        if (this.locked) {
            const labels = Array.from(this.enabled).map((l) => this._displayFor(l));
            const text = labels.length ? `🔒 Locked to: ${labels.join(', ')}` : '🔒 Locked';
            this.container.innerHTML = (
                '<span class="agent-toggle-lock-note" '
                + 'title="This chat\'s agent scope was fixed at creation and cannot be changed.">'
                + this._escape(text)
                + '</span>'
            );
            return;
        }
        const parts = ['<button type="button" class="agent-toggle all" data-agent="__all__">All</button>'];
        for (const agent of this.availableList) {
            parts.push(
                `<button type="button" class="agent-toggle" data-agent="${this._escape(agent.label)}">`
                + this._escape(agent.display)
                + '</button>'
            );
        }
        this.container.innerHTML = parts.join('');
        this._render();
    }

    _allMode() {
        return this.enabled.size === 0 || this.enabled.size >= this.available.length;
    }

    _render() {
        if (this.locked || !this.container) return;
        const allMode = this._allMode();
        const allBtn = this.container.querySelector('[data-agent="__all__"]');
        if (allBtn) allBtn.classList.toggle('active', allMode);
        this.container.querySelectorAll('.agent-toggle:not(.all)').forEach((btn) => {
            const label = btn.dataset.agent;
            const on = allMode || this.enabled.has(label);
            btn.classList.toggle('active', on);
        });
    }

    _wire() {
        if (this._wired || !this.container) return;
        // Event delegation: one listener on the container handles every
        // generation of buttons, including those rebuilt by `_buildDOM`.
        this.container.addEventListener('click', (e) => {
            if (this.locked) return;
            const btn = e.target.closest('.agent-toggle');
            if (!btn || btn.disabled) return;
            const label = btn.dataset.agent;
            if (label === '__all__') {
                this.enabled = new Set();
            } else {
                if (this.enabled.size === 0) {
                    this.enabled = new Set(this.available);
                }
                if (this.enabled.has(label)) {
                    this.enabled.delete(label);
                } else {
                    this.enabled.add(label);
                }
                if (this.enabled.size === this.available.length) {
                    this.enabled = new Set();
                }
            }
            this._render();
            this._save();
        });
        this._wired = true;
    }

    _save() {
        clearTimeout(this._saveTimer);
        this._saveTimer = setTimeout(() => this._patch(), 300);
    }

    async _patch() {
        if (this.locked || !this.chatId) return;
        try {
            const res = await fetch(`${this.basePath}/api/chats/${this.chatId}/agents/`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                },
                body: JSON.stringify({ enabled_agents: Array.from(this.enabled) }),
            });
            if (!res.ok) {
                console.error('[agent-toggles] PATCH failed:', res.status, await res.text());
            }
        } catch (err) {
            console.error('[agent-toggles] PATCH error:', err);
        }
    }

    getEnabled() {
        return Array.from(this.enabled);
    }

    _escape(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[c]);
    }
}

class ChatApp {
    constructor() {
        // Get template data
        const templateData = JSON.parse(document.getElementById('template-data').textContent);
        this.currentChatId = templateData.chatId;
        this.isProcessing = templateData.isProcessing;
        this.basePath = templateData.basePath || '';
        this.enabledAgents = Array.isArray(templateData.enabledAgents) ? templateData.enabledAgents : [];
        this.agentsLocked = !!templateData.agentsLocked;
        this.availableAgentLabels = Array.isArray(templateData.availableAgentLabels)
            ? templateData.availableAgentLabels : [];
        this.availableAgentList = Array.isArray(templateData.availableAgentList)
            ? templateData.availableAgentList : [];

        // DOM elements - Sidebar
        this.sidebar = document.getElementById('sidebar');
        // Sidebar overlay removed — the sidebar is now part of the flex
        // layout, not an overlay, so no backdrop element exists.
        this.conversationsList = document.getElementById('conversations-list');
        this.newChatBtn = document.getElementById('new-chat-btn');
        this.menuToggle = document.getElementById('menu-toggle');

        // DOM elements - Chat
        this.chatTitle = document.getElementById('chat-title');
        this.messagesWrapper = document.getElementById('messages-wrapper');
        this.messagesContainer = document.getElementById('messages-container');
        this.messageForm = document.getElementById('message-form');
        this.messageInput = document.getElementById('message-input');
        this.sendButton = document.getElementById('send-button');
        this.statusBadge = document.getElementById('status-badge');

        // DOM elements - Charts
        this.chartSection = document.getElementById('chart-section');
        this.chartGallery = document.getElementById('chart-gallery');
        this.closeChartsBtn = document.getElementById('close-charts-btn');
        // GHOST-SCROLL: proxy scrollbar elements — see setupGhostScrollbar().
        this.chartGalleryGhost = document.getElementById('chart-gallery-ghost');
        this.chartGalleryGhostInner = document.getElementById('chart-gallery-ghost-inner');

        // State
        this.conversations = [];
        this.pendingMessages = new Set();
        this.chartInstances = new Map();
        this.pollInProgress = false;

        // Initialize
        this.init();
    }

    init() {
        this.setupEventListeners();
        this.setupOverlayScrollbars();
        this.setupGhostScrollbar();
        this.loadConversations();
        this.setupAgentToggleController();

        // If we have a current chat, initialize it
        if (this.currentChatId) {
            // Render messages FIRST so the DOM is populated before we scan for pending ones
            const templateData = JSON.parse(document.getElementById('template-data').textContent);
            if (templateData.messages && templateData.messages.length > 0) {
                this.renderMessages(templateData.messages);
            }

            this.initializeCurrentChat();
        }

        this.scrollToBottom();
    }

    setupAgentToggleController() {
        // Controller is created once at page load and survives SPA chat
        // switches. The toggle bar's inner DOM is rebuilt by the controller
        // itself when `applyChatState` is called from `loadChat`.
        const toggleBar = document.getElementById('agent-toggle-bar');
        if (!toggleBar) return;
        this.agentToggleController = new AgentToggleController({
            container: toggleBar,
            chatId: this.currentChatId,
            basePath: this.basePath,
            enabled: this.enabledAgents,
            available: this.availableAgentLabels,
            availableList: this.availableAgentList,
            locked: this.agentsLocked,
        });
    }

    setupOverlayScrollbars() {
        // Only applied to the messages wrapper. Initialising on the chat
        // history list or chart gallery wraps their children in an
        // `.os-content` div whose sizing rules disrupt full-width items
        // (conversation rows render too narrow, charts stack vertically
        // instead of filling the panel). Those panels keep native scroll;
        // the chart gallery uses the ghost-scrollbar proxy instead (see
        // setupGhostScrollbar).
        if (typeof OverlayScrollbarsGlobal === 'undefined') return;
        if (!(this.messagesWrapper instanceof HTMLElement)) return;
        const { OverlayScrollbars } = OverlayScrollbarsGlobal;
        OverlayScrollbars(this.messagesWrapper, {
            scrollbars: {
                theme: 'os-theme-chatagent',
                autoHide: 'leave',
                autoHideDelay: 800,
            },
        });
    }

    // GHOST-SCROLL: proxy scrollbar for the chart gallery.
    // Strategy: the real #chart-gallery keeps native overflow with the
    // native scrollbar hidden (CSS). A sibling #chart-gallery-ghost
    // element gets OverlayScrollbars; its inner placeholder is sized
    // in JS to match the gallery's scrollHeight. Scroll events on either
    // side are forwarded to the other with a one-shot ignore flag so the
    // two never feedback-loop. ResizeObserver + MutationObserver keep
    // the placeholder height in sync as charts are added/removed.
    //
    // Revert path: delete this method, its call in init(), the three DOM
    // refs above, and the matching CSS + HTML blocks tagged GHOST-SCROLL.
    setupGhostScrollbar() {
        if (typeof OverlayScrollbarsGlobal === 'undefined') return;
        if (!(this.chartGallery instanceof HTMLElement)) return;
        if (!(this.chartGalleryGhost instanceof HTMLElement)) return;
        if (!(this.chartGalleryGhostInner instanceof HTMLElement)) return;

        const { OverlayScrollbars } = OverlayScrollbarsGlobal;

        const ghostInstance = OverlayScrollbars(this.chartGalleryGhost, {
            overflow: { x: 'hidden', y: 'scroll' },
            scrollbars: {
                theme: 'os-theme-chatagent',
                autoHide: 'never',
                visibility: 'auto',
            },
        });

        const elements = ghostInstance.elements();
        const ghostViewport = elements.viewport;
        if (!(ghostViewport instanceof HTMLElement)) return;

        // One-shot ignore guards. Setting scrollTop programmatically fires
        // a `scroll` event *after* the current stack unwinds, so we flip
        // the flag, let that one event be ignored, and flip it back.
        let ignoreReal = false;
        let ignoreGhost = false;

        this.chartGallery.addEventListener('scroll', () => {
            if (ignoreReal) { ignoreReal = false; return; }
            ignoreGhost = true;
            ghostViewport.scrollTop = this.chartGallery.scrollTop;
        }, { passive: true });

        ghostViewport.addEventListener('scroll', () => {
            if (ignoreGhost) { ignoreGhost = false; return; }
            ignoreReal = true;
            this.chartGallery.scrollTop = ghostViewport.scrollTop;
        }, { passive: true });

        const updateGhostHeight = () => {
            const h = this.chartGallery.scrollHeight;
            if (this.chartGalleryGhostInner.style.height !== h + 'px') {
                this.chartGalleryGhostInner.style.height = h + 'px';
            }
        };
        updateGhostHeight();

        if (typeof ResizeObserver !== 'undefined') {
            const ro = new ResizeObserver(updateGhostHeight);
            ro.observe(this.chartGallery);
        }

        const mo = new MutationObserver(() => {
            // Run on next frame so Chart.js has finished sizing its canvases.
            requestAnimationFrame(updateGhostHeight);
        });
        mo.observe(this.chartGallery, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['style', 'class'],
        });
    }

    setupEventListeners() {
        // New chat button
        this.newChatBtn.addEventListener('click', () => this.createNewChat());

        // Mobile menu toggle
        if (this.menuToggle) {
            this.menuToggle.addEventListener('click', () => this.toggleMobileSidebar());
        }

        // Message form submission
        this.messageForm.addEventListener('submit', (e) => this.handleSubmit(e));

        // Textarea auto-resize and Enter key handling
        this.messageInput.addEventListener('input', () => this.autoResizeTextarea());
        this.messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.messageForm.requestSubmit();
            }
        });

        // Close charts button
        this.closeChartsBtn.addEventListener('click', () => this.closeCharts());
    }

    // ===================================
    // Conversation Management
    // ===================================

    async loadConversations() {
        try {
            const response = await fetch(`${this.basePath}/api/chats/`);
            if (!response.ok) throw new Error('Failed to load conversations');

            const data = await response.json();
            this.conversations = data.chats || [];
            this.renderConversationsList();
        } catch (error) {
            console.error('Error loading conversations:', error);
            this.conversationsList.innerHTML = `
                <div style="padding: 1rem; text-align: center; color: var(--text-muted);">
                    Failed to load conversations
                </div>
            `;
        }
    }

    renderConversationsList() {
        if (this.conversations.length === 0) {
            this.conversationsList.innerHTML = `
                <div style="padding: 1rem; text-align: center; color: var(--text-muted); font-size: 0.9rem;">
                    No conversations yet.<br>Click "New Chat" to start!
                </div>
            `;
            return;
        }

        this.conversationsList.innerHTML = this.conversations
            .map(chat => {
                return `
                    <div class="conversation-item ${chat.id === this.currentChatId ? 'active' : ''}"
                         data-chat-id="${chat.id}"
                         onclick="window.chatApp.loadChat('${chat.id}')">
                        <div class="conversation-title">${this.escapeHtml(chat.title || 'Untitled Chat')}</div>
                        <div class="conversation-preview">${this.escapeHtml(chat.preview || 'No messages yet')}</div>
                        <div class="conversation-date">${this.formatDate(chat.updated_at)}</div>
                    </div>
                `;
            })
            .join('');
    }

    async createNewChat() {
        try {
            const response = await fetch(`${this.basePath}/api/chats/new/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                },
                body: JSON.stringify({}),
            });

            if (!response.ok) throw new Error('Failed to create chat');

            const data = await response.json();
            if (data.success) {
                await this.loadConversations();
                await this.loadChat(data.chat_id);
            }
        } catch (error) {
            console.error('Error creating chat:', error);
            this.showError('Failed to create new chat');
        }
    }

    async loadChat(chatId) {
        if (chatId === this.currentChatId) return;

        this.currentChatId = chatId;
        this.clearCurrentChat();

        try {
            // Fetch chat data
            const response = await fetch(`${this.basePath}/api/chats/${chatId}/`);
            if (!response.ok) throw new Error('Failed to load chat');

            const data = await response.json();

            // Update UI
            this.chatTitle.textContent = data.chat.title || 'Untitled Chat';
            this.isProcessing = data.is_processing;
            this.updateProcessingState(this.isProcessing);

            // Sync toggle-bar state for the newly-loaded chat. The
            // controller rebuilds its inner DOM (locked pill vs button
            // row) so we don't need a full page reload to swap modes.
            this.enabledAgents = Array.isArray(data.chat.enabled_agents)
                ? data.chat.enabled_agents : [];
            this.agentsLocked = !!data.chat.agents_locked;
            if (this.agentToggleController) {
                this.agentToggleController.applyChatState({
                    chatId,
                    enabled: this.enabledAgents,
                    locked: this.agentsLocked,
                });
            }

            // Update URL without page reload
            window.history.pushState({ chatId }, '', `${this.basePath}/chats/${chatId}/`);

            // Render messages
            this.renderMessages(data.messages);

            // Update sidebar active state
            this.updateSidebarActiveState(chatId);

            // Enable input
            this.messageInput.disabled = false;
            this.sendButton.disabled = false;
            this.messageInput.focus();

            // Close mobile sidebar if open
            this.closeMobileSidebar();

            // Poll for any pending messages
            this.checkForPendingMessages(data.messages);
        } catch (error) {
            console.error('Error loading chat:', error);
            this.showError('Failed to load chat');
        }
    }

    clearCurrentChat() {
        // Empty the inner messages container *without* touching the wrapper —
        // `messagesWrapper.innerHTML = ''` would wipe OverlayScrollbars'
        // injected DOM (.os-host / .os-viewport / scrollbar elements) and
        // leave the instance detached, so the scrollbar would never show
        // again for subsequent chats that need to scroll.
        if (this.messagesContainer && this.messagesContainer.isConnected) {
            this.messagesContainer.innerHTML = '';
        } else {
            const newContainer = document.createElement('div');
            newContainer.id = 'messages-container';
            newContainer.className = 'messages-container';
            // Find the OverlayScrollbars content host if present, otherwise
            // fall back to appending directly to the wrapper.
            const osContent = this.messagesWrapper.querySelector('.os-content');
            (osContent || this.messagesWrapper).appendChild(newContainer);
            this.messagesContainer = newContainer;
        }

        // Clear charts
        this.chartGallery.innerHTML = '';
        this.chartInstances.clear();
        this.chartSection.classList.remove('active');

        // Clear pending messages
        this.pendingMessages.clear();
    }

    renderMessages(messages) {
        messages.forEach(message => {
            this.addMessage(
                message.type,
                message.content,
                message.id,
                message.status,
                message.created_at,
                message.artifacts || message.charts || [],
                message.feedback || {}
            );
        });

        this.scrollToBottom();
        this.scrollToHashTarget();
    }

    scrollToHashTarget() {
        const hash = window.location.hash;
        if (!hash || !hash.startsWith('#message-')) return;
        const target = document.getElementById(hash.slice(1));
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'center' });
            target.classList.add('highlighted');
            setTimeout(() => target.classList.remove('highlighted'), 2200);
        }
    }

    updateSidebarActiveState(chatId) {
        const items = this.conversationsList.querySelectorAll('.conversation-item');
        items.forEach(item => {
            if (item.dataset.chatId === chatId) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });
    }

    checkForPendingMessages(messages) {
        messages.forEach(message => {
            if (message.status === 'pending') {
                this.pendingMessages.add(message.id);
                this.updateProcessingState(true);
                this.streamMessageStatus(message.id);
            }
        });
    }

    initializeCurrentChat() {
        // Resume streaming for any pending messages (e.g. wizard redirect)
        const initialMessages = this.messagesContainer?.querySelectorAll('.message');
        if (initialMessages) {
            initialMessages.forEach(msgDiv => {
                if (msgDiv.dataset.status === 'pending') {
                    const messageId = msgDiv.dataset.messageId;
                    this.pendingMessages.add(messageId);
                    this.updateProcessingState(true);
                    this.streamMessageStatus(messageId);
                }
            });
        }

        this.messageInput.focus();
    }

    // ===================================
    // Message Handling
    // ===================================

    addMessage(type, content, messageId = null, status = 'completed', timestamp = null, artifacts = [], feedback = {}) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${type}`;
        if (messageId) {
            messageDiv.setAttribute('data-message-id', messageId);
            // Anchor id so the admin Feedback tab can link to #message-<id>.
            messageDiv.id = `message-${messageId}`;
        }
        if (status) messageDiv.setAttribute('data-status', status);

        let messageContent;
        if (status === 'pending') {
            messageContent = `
                <div class="typing-indicator">
                    <span>Thinking</span>
                    <div class="typing-dots">
                        <span></span>
                        <span></span>
                        <span></span>
                    </div>
                </div>
            `;
            messageDiv.classList.add('pending');
        } else {
            messageContent = this.formatMessageContent(content, type);
        }

        const timeStr = timestamp ? this.formatTimestamp(new Date(timestamp)) : this.formatTimestamp(new Date());

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        contentDiv.innerHTML = messageContent;

        const metaDiv = document.createElement('div');
        metaDiv.className = 'message-meta';
        metaDiv.textContent = timeStr;

        messageDiv.appendChild(contentDiv);
        messageDiv.appendChild(metaDiv);

        this.messagesContainer.appendChild(messageDiv);

        // Process artifacts after contentDiv is in the DOM
        if (status !== 'pending' && artifacts && artifacts.length > 0) {
            this.processArtifacts(artifacts, messageDiv);
        }

        // Feedback UI on completed assistant messages only.
        if (type === 'assistant' && status !== 'pending' && messageId) {
            this.renderFeedbackUI(messageDiv, messageId, feedback || {});
        }

        // AI-content disclaimer footer on completed assistant messages.
        if (type === 'assistant' && status !== 'pending') {
            this._appendAiDisclaimer(messageDiv);
        }

        this.scrollToBottom();

        return messageDiv;
    }

    _appendAiDisclaimer(messageDiv) {
        // Footer note on assistant messages flagging AI-generated content.
        // Assistant-only, and idempotent so streaming finalization + re-render
        // don't stack duplicates.
        if (!messageDiv || !messageDiv.classList.contains('assistant')) return;
        if (messageDiv.querySelector('.ai-disclaimer')) return;
        const note = document.createElement('div');
        note.className = 'ai-disclaimer';
        note.textContent =
            'This message contains AI-generated content. AI agents can make '
            + 'mistakes — please review all outputs.';
        messageDiv.appendChild(note);
    }

    renderFeedbackUI(messageDiv, messageId, feedback) {
        // Avoid duplicate insertion if called twice on the same message.
        const existing = messageDiv.querySelector('.message-feedback');
        if (existing) existing.remove();
        const existingOthers = messageDiv.querySelector('.message-feedback-others');
        if (existingOthers) existingOthers.remove();

        const own = (feedback && feedback.own) || null;

        const wrap = document.createElement('div');
        wrap.className = 'message-feedback';
        if (own) wrap.classList.add('has-rating');

        const upBtn = document.createElement('button');
        upBtn.type = 'button';
        upBtn.className = 'message-feedback-btn';
        upBtn.setAttribute('data-rating', 'up');
        upBtn.setAttribute('aria-label', 'Rate this reply good');
        upBtn.textContent = '👍';

        const downBtn = document.createElement('button');
        downBtn.type = 'button';
        downBtn.className = 'message-feedback-btn';
        downBtn.setAttribute('data-rating', 'down');
        downBtn.setAttribute('aria-label', 'Rate this reply poor');
        downBtn.textContent = '👎';

        if (own && own.rating === 'up') upBtn.classList.add('selected');
        if (own && own.rating === 'down') downBtn.classList.add('selected');

        const commentWrap = document.createElement('div');
        commentWrap.className = 'message-feedback-comment';
        const commentInput = document.createElement('textarea');
        commentInput.rows = 1;
        commentInput.placeholder = 'Why? (optional)';
        commentInput.value = (own && own.comment) || '';
        const commentSave = document.createElement('button');
        commentSave.type = 'button';
        commentSave.textContent = 'Save';
        commentWrap.appendChild(commentInput);
        commentWrap.appendChild(commentSave);

        if (own && own.comment) commentWrap.classList.add('visible');
        if (own && own.rating === 'down') {
            commentWrap.classList.add('visible');
            wrap.classList.add('expanded');
        }

        const setSelected = (rating) => {
            upBtn.classList.toggle('selected', rating === 'up');
            downBtn.classList.toggle('selected', rating === 'down');
            wrap.classList.toggle('has-rating', rating === 'up' || rating === 'down');
        };

        const sendRating = async (rating) => {
            try {
                const resp = await fetch(
                    `${this.basePath}/api/chats/${this.currentChatId}/messages/${messageId}/feedback/`,
                    {
                        method: 'PUT',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': getCsrfToken(),
                        },
                        body: JSON.stringify({
                            rating,
                            comment: commentInput.value.trim(),
                        }),
                    }
                );
                if (!resp.ok) throw new Error('Feedback save failed');
                await resp.json().catch(() => ({}));
                setSelected(rating);
            } catch (err) {
                console.error('Feedback save error:', err);
            }
        };

        const clearRating = async () => {
            try {
                await fetch(
                    `${this.basePath}/api/chats/${this.currentChatId}/messages/${messageId}/feedback/`,
                    {
                        method: 'DELETE',
                        headers: { 'X-CSRFToken': getCsrfToken() },
                    }
                );
                setSelected(null);
                commentWrap.classList.remove('visible');
                wrap.classList.remove('expanded');
                commentInput.value = '';
            } catch (err) {
                console.error('Feedback clear error:', err);
            }
        };

        upBtn.addEventListener('click', () => {
            if (upBtn.classList.contains('selected')) {
                clearRating();
            } else {
                sendRating('up');
                commentWrap.classList.remove('visible');
                wrap.classList.remove('expanded');
            }
        });
        downBtn.addEventListener('click', () => {
            if (downBtn.classList.contains('selected') && commentWrap.classList.contains('visible')) {
                clearRating();
            } else {
                sendRating('down');
                commentWrap.classList.add('visible');
                wrap.classList.add('expanded');
                commentInput.focus();
            }
        });
        commentSave.addEventListener('click', () => {
            const rating = upBtn.classList.contains('selected')
                ? 'up'
                : (downBtn.classList.contains('selected') ? 'down' : 'down');
            sendRating(rating);
        });

        wrap.appendChild(upBtn);
        wrap.appendChild(downBtn);
        messageDiv.appendChild(wrap);
        messageDiv.appendChild(commentWrap);

        // Admin view: render other users' feedback alongside.
        const others = (feedback && feedback.others) || [];
        if (others.length) {
            const othersWrap = document.createElement('div');
            othersWrap.className = 'message-feedback-others';
            const title = document.createElement('div');
            title.className = 'message-feedback-others-title';
            title.textContent = `Other raters (${others.length})`;
            othersWrap.appendChild(title);
            others.forEach((o) => {
                const item = document.createElement('div');
                item.className = 'message-feedback-others-item';
                const who = o.user.full_name || o.user.username || 'user';
                const icon = o.rating === 'up' ? '👍' : '👎';
                const cmt = o.comment ? ` — ${o.comment}` : '';
                item.textContent = `${icon} ${who}${cmt}`;
                othersWrap.appendChild(item);
            });
            messageDiv.appendChild(othersWrap);
        }
    }

    updateMessage(messageId, content, artifacts = [], status = 'completed') {
        const messageDiv = document.querySelector(`[data-message-id="${messageId}"]`);
        if (!messageDiv) return;

        if (status === 'pending') {
            // Update pending message with current node
            const messageContent = `
                <div class="typing-indicator">
                    <span>${this.escapeHtml(content)}</span>
                    <div class="typing-dots">
                        <span></span>
                        <span></span>
                        <span></span>
                    </div>
                </div>
            `;
            messageDiv.classList.add('pending');
            messageDiv.querySelector('.message-content').innerHTML = messageContent;
        } else {
            // Complete the message
            messageDiv.classList.remove('pending');
            messageDiv.setAttribute('data-status', 'completed');

            const contentDiv = messageDiv.querySelector('.message-content');
            contentDiv.innerHTML = this.formatMessageContent(content, 'assistant');

            // Process artifacts (charts, tables, text)
            if (artifacts && artifacts.length > 0) {
                this.processArtifacts(artifacts, messageDiv);
            }

            // Attach feedback buttons once the assistant reply is finalised.
            if (messageDiv.classList.contains('assistant')) {
                this.renderFeedbackUI(messageDiv, messageId, {});
                this._appendAiDisclaimer(messageDiv);
            }
        }

        this.scrollToBottom();
    }

    async handleSubmit(e) {
        e.preventDefault();
        e.stopPropagation();

        if (this.isProcessing || !this.messageInput.value.trim() || !this.currentChatId) {
            return;
        }

        const content = this.messageInput.value.trim();
        this.messageInput.value = '';
        this.autoResizeTextarea();

        // Add user message
        this.addMessage('user', content);

        // Carry the current toggle-bar state in the send payload so the
        // backend uses the authoritative UI state (no race with the
        // debounced PATCH). Omit on locked chats — the backend ignores
        // the field in that case anyway.
        const payload = { content };
        if (this.agentToggleController && !this.agentToggleController.locked) {
            payload.enabled_agents = this.agentToggleController.getEnabled();
        }

        try {
            const response = await fetch(`${this.basePath}/api/chats/${this.currentChatId}/messages/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                },
                body: JSON.stringify(payload),
            });

            if (!response.ok) throw new Error('Failed to send message');

            const data = await response.json();
            if (data.success) {
                this.addMessage('assistant', '', data.assistant_message_id, 'pending');
                this.pendingMessages.add(data.assistant_message_id);
                this.updateProcessingState(true);
                this.streamMessageStatus(data.assistant_message_id);
            } else {
                this.showError('Failed to send message: ' + (data.error || 'Unknown error'));
            }
        } catch (error) {
            console.error('Error sending message:', error);
            this.showError('Error sending message. Please try again.');
        }
    }

    streamMessageStatus(messageId) {
        const url = `${this.basePath}/api/chats/${this.currentChatId}/messages/${messageId}/stream/`;
        const source = new EventSource(url);

        source.onmessage = (event) => {
            let data;
            try {
                data = JSON.parse(event.data);
            } catch (e) {
                return;
            }

            if (data.type === 'thinking') {
                this.updateThinkingMessage(messageId, data.title, data.description);
            } else if (data.type === 'plan_update') {
                this.renderPlanProgress(messageId, data);
            } else if (data.type === 'tool_call') {
                this.appendToolTraceRow(messageId, data);
            } else if (data.type === 'tool_result') {
                this.updateToolTraceRow(messageId, data);
            } else if (data.type === 'tool_progress') {
                this.appendToolProgressSubstep(messageId, data);
            } else if (data.type === 'content') {
                this.appendToMessage(messageId, data.token);
            } else if (data.type === 'done') {
                source.close();
                this.updateMessage(messageId, data.content, data.artifacts || data.charts || [], 'completed');
                this.updateProcessingState(false);
                this.pendingMessages.delete(messageId);
                this.loadConversations();
            } else if (data.type === 'error') {
                source.close();
                this.updateMessage(messageId, data.message || 'An error occurred.', [], 'completed');
                this.updateProcessingState(false);
                this.pendingMessages.delete(messageId);
            }
        };

        source.onerror = () => {
            source.close();
            // Fallback to polling if SSE fails
            this.pollMessageStatus(messageId);
        };
    }

    updateThinkingMessage(messageId, title, description) {
        const messageDiv = document.querySelector(`[data-message-id="${messageId}"]`);
        if (!messageDiv) return;

        messageDiv.classList.add('pending');
        const contentDiv = messageDiv.querySelector('.message-content');
        contentDiv.innerHTML = `
            <div class="typing-indicator thinking-state">
                <div class="thinking-title">${this.escapeHtml(title)}</div>
                <div class="thinking-description">${this.escapeHtml(description || '')}</div>
                <div class="typing-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
        `;
        this.scrollToBottom();
    }

    _ensureToolTraceContainer(messageId) {
        const messageDiv = document.querySelector(`[data-message-id="${messageId}"]`);
        if (!messageDiv) return null;
        messageDiv.classList.add('pending');
        const contentDiv = messageDiv.querySelector('.message-content');
        let trace = contentDiv.querySelector('.tool-trace');
        if (!trace) {
            // If the content is still a plain thinking indicator, swap it
            // out for the trace container.
            contentDiv.innerHTML = `
                <div class="tool-trace">
                    <div class="tool-trace-header">Working…</div>
                    <div class="tool-trace-rows"></div>
                    <div class="typing-dots" style="margin-top: 0.5rem;">
                        <span></span><span></span><span></span>
                    </div>
                </div>
            `;
            trace = contentDiv.querySelector('.tool-trace');
        }
        return trace;
    }

    appendToolTraceRow(messageId, data) {
        const trace = this._ensureToolTraceContainer(messageId);
        if (!trace) return;
        const rows = trace.querySelector('.tool-trace-rows');
        const tcId = data.tool_call_id || `r-${rows.children.length}`;
        if (rows.querySelector(`[data-tc-id="${CSS.escape(tcId)}"]`)) return;

        // Per-agent accent color + icon come from window.AGENT_VISUALS, which
        // the platform assembles from each agent's AppConfig (agent_color /
        // agent_icon), keyed by agent_label — exactly what owner_agent carries.
        const visuals = (window.AGENT_VISUALS || {})[data.owner_agent] || {};
        const color = visuals.color || '#58585b';
        const icon = visuals.icon || '';

        const row = document.createElement('div');
        row.className = 'tool-trace-row running';
        row.setAttribute('data-tc-id', tcId);
        row.innerHTML = `
            <span class="tool-trace-icon">🔄</span>
            <span class="tool-trace-name">${this.escapeHtml(data.name || 'tool')}</span>
            <span class="tool-trace-agent" style="background: ${color}22; color: ${color}; border: 1px solid ${color}44;">${icon ? this.escapeHtml(icon) + ' ' : ''}${this.escapeHtml(data.owner_agent || '')}</span>
        `;
        rows.appendChild(row);
        this.scrollToBottom();
    }

    appendToolProgressSubstep(messageId, data) {
        // Tool-internal progress event: nest a substep line under the
        // matching tool-trace row (or the most recent running row when
        // no tool name is supplied). Consecutive duplicate stages are
        // ignored — keeps the trace readable when a tool re-emits.
        const trace = this._ensureToolTraceContainer(messageId);
        if (!trace) return;
        const rows = trace.querySelector('.tool-trace-rows');
        if (!rows) return;

        let row = null;
        if (data.tool) {
            const running = rows.querySelectorAll('.tool-trace-row.running');
            for (let i = running.length - 1; i >= 0; i--) {
                const nameEl = running[i].querySelector('.tool-trace-name');
                if (nameEl && nameEl.textContent.trim() === data.tool) {
                    row = running[i];
                    break;
                }
            }
        }
        if (!row) {
            const running = rows.querySelectorAll('.tool-trace-row.running');
            row = running[running.length - 1] || null;
        }
        if (!row) return;

        let substeps = row.querySelector('.tool-trace-substeps');
        if (!substeps) {
            substeps = document.createElement('div');
            substeps.className = 'tool-trace-substeps';
            row.appendChild(substeps);
        }
        const stage = data.stage || '';
        const last = substeps.lastElementChild;
        if (last && last.dataset.stage === stage) return;

        const step = document.createElement('div');
        step.className = 'tool-trace-substep';
        if (stage) step.dataset.stage = stage;
        step.innerHTML = `
            <span class="tool-trace-substep-icon">›</span>
            <span class="tool-trace-substep-title">${this.escapeHtml(data.title || stage || 'Step')}</span>
            ${data.description ? `<span class="tool-trace-substep-desc">${this.escapeHtml(data.description)}</span>` : ''}
        `;
        substeps.appendChild(step);
        this.scrollToBottom();
    }

    updateToolTraceRow(messageId, data) {
        const trace = this._ensureToolTraceContainer(messageId);
        if (!trace) return;
        const tcId = data.tool_call_id;
        if (!tcId) return;
        const row = trace.querySelector(`[data-tc-id="${CSS.escape(tcId)}"]`);
        if (!row) return;

        const iconMap = {
            completed: '✅',
            ok: '✅',
            skill_loaded: '✅',
            needs_input: '✏️',
            validation_error: '⚠️',
            error: '❌',
            failed: '❌',
            cancelled: '⏹️',
        };
        const icon = iconMap[data.status] || '✅';
        row.classList.remove('running');
        row.classList.add(data.status || 'completed');
        const iconSpan = row.querySelector('.tool-trace-icon');
        if (iconSpan) iconSpan.textContent = icon;
    }

    renderPlanProgress(messageId, planData) {
        const messageDiv = document.querySelector(`[data-message-id="${messageId}"]`);
        if (!messageDiv) return;

        messageDiv.classList.add('pending');
        const contentDiv = messageDiv.querySelector('.message-content');

        const statusIcons = {
            pending: '\u23F3',
            running: '\uD83D\uDD04',
            completed: '\u2705',
            failed: '\u274C',
            skipped: '\u23ED\uFE0F',
        };

        const tasks = planData.tasks || [];
        const tasksHtml = tasks.map(t => {
            const icon = statusIcons[t.status] || '\u23F3';
            const agentColor = ((window.AGENT_VISUALS || {})[t.agent] || {}).color || '#58585b';
            const statusClass = t.status || 'pending';
            return `
                <div class="plan-task-item ${statusClass}">
                    <span class="plan-task-icon">${icon}</span>
                    <span class="plan-task-name">${this.escapeHtml(t.display_name)}</span>
                    <span class="plan-task-agent" style="background: ${agentColor}22; color: ${agentColor}; border: 1px solid ${agentColor}44;">${this.escapeHtml(t.agent)}</span>
                </div>
            `;
        }).join('');

        contentDiv.innerHTML = `
            <div class="plan-progress">
                <div class="plan-summary">${this.escapeHtml(planData.plan_summary || 'Executing plan...')}</div>
                <div class="plan-tasks">${tasksHtml}</div>
                <div class="typing-dots" style="margin-top: 0.5rem;">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
        `;

        this.scrollToBottom();
    }

    appendToMessage(messageId, token) {
        const messageDiv = document.querySelector(`[data-message-id="${messageId}"]`);
        if (!messageDiv) return;

        // If still in pending state, switch to content mode
        if (messageDiv.classList.contains('pending')) {
            messageDiv.classList.remove('pending');
            messageDiv.setAttribute('data-status', 'streaming');
            const contentDiv = messageDiv.querySelector('.message-content');
            contentDiv.innerHTML = '';
            contentDiv.dataset.streamBuffer = '';
        }

        const contentDiv = messageDiv.querySelector('.message-content');
        const buffer = (contentDiv.dataset.streamBuffer || '') + token;
        contentDiv.dataset.streamBuffer = buffer;
        contentDiv.innerHTML = this.formatMessageContent(buffer, 'assistant');
        this.scrollToBottom();
    }

    pollMessageStatus(messageId) {
        const pollInterval = setInterval(async () => {
            if (this.pollInProgress) return;
            this.pollInProgress = true;

            try {
                const response = await fetch(`${this.basePath}/api/chats/${this.currentChatId}/messages/${messageId}/status/`);

                if (!response.ok) {
                    throw new Error('Failed to poll message status');
                }

                const data = await response.json();

                if (data.status === 'completed' || data.status === 'failed') {
                    clearInterval(pollInterval);
                    this.pendingMessages.delete(messageId);
                    this.updateMessage(messageId, data.content || data.error, data.artifacts || data.charts || [], 'completed');
                    this.updateProcessingState(false);

                    // Reload conversations to update preview
                    this.loadConversations();
                } else if (data.status === 'interrupted') {
                    // Interrupted: show the content and re-enable input
                    clearInterval(pollInterval);
                    this.pendingMessages.delete(messageId);
                    this.updateMessage(messageId, data.content || 'Waiting for your input...', data.artifacts || data.charts || [], 'completed');
                    this.updateProcessingState(false);

                    // Reload conversations to update preview
                    this.loadConversations();
                } else if (data.status === 'pending') {
                    const node = data.current_node || 'Waiting...';
                    this.updateMessage(messageId, `Processing: ${node}`, [], 'pending');
                }

                this.pollInProgress = false;
            } catch (error) {
                console.error('Error polling message status:', error);
                clearInterval(pollInterval);
                this.pollInProgress = false;
                this.pendingMessages.delete(messageId);
                this.updateProcessingState(false);
                this.showError('Connection error. Please refresh the page.');
            }
        }, 1000);
    }

    // ===================================
    // Chart Management
    // ===================================

    processArtifacts(artifacts, messageDiv) {
        const contentDiv = messageDiv.querySelector('.message-content');
        let hasCharts = false;

        artifacts.forEach((artifact, idx) => {
            if (artifact.type === 'chart') {
                // Chart artifact → render in chart gallery
                // Deterministic ID based on title to avoid duplicates on re-render
                const messageId = messageDiv.dataset.messageId || 'msg';
                const chartId = artifact.id || `chart-${messageId}-${idx}`;
                this.addChartToGallery({
                    id: chartId,
                    title: artifact.title || 'Chart',
                    type: artifact.chart_type || 'bar',
                    data: artifact.data,
                    options: artifact.options,
                });
                hasCharts = true;
            } else if (artifact.type === 'table') {
                // Table artifact → render inline below the message text
                const tableHtml = this.renderTableArtifact(artifact);
                contentDiv.insertAdjacentHTML('beforeend', tableHtml);
            } else if (artifact.type === 'text') {
                // Text artifact → render inline as a styled block
                const textHtml = this.renderTextArtifact(artifact);
                contentDiv.insertAdjacentHTML('beforeend', textHtml);
            }
        });

        if (hasCharts) {
            this.showChartSection();
        }
    }

    renderCollapsible(title, innerHtml, hint = '') {
        const id = `collapsible-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
        const hintHtml = hint ? `<span class="collapsible-hint">${this.escapeHtml(hint)}</span>` : '';
        return `
            <div class="artifact-collapsible">
                <div class="collapsible-toggle" onclick="document.getElementById('${id}').classList.toggle('open')">
                    <span class="collapsible-icon">&#9662;</span>
                    ${this.escapeHtml(title)}
                    ${hintHtml}
                </div>
                <div class="collapsible-body" id="${id}">
                    ${innerHtml}
                </div>
            </div>
        `;
    }

    renderTableArtifact(artifact) {
        const data = artifact.data || {};
        const columns = data.columns || [];
        const rows = data.rows || [];

        if (!columns.length || !rows.length) return '';

        const headerCells = columns.map(c => `<th>${this.escapeHtml(String(c))}</th>`).join('');
        const bodyRows = rows.map(row => {
            const cells = row.map(cell => `<td>${this.escapeHtml(String(cell ?? ''))}</td>`).join('');
            return `<tr>${cells}</tr>`;
        }).join('');

        const tableHtml = `
            <table>
                <thead><tr>${headerCells}</tr></thead>
                <tbody>${bodyRows}</tbody>
            </table>
        `;
        return this.renderCollapsible(artifact.title || 'Table', tableHtml, artifact.hint || '');
    }

    renderTextArtifact(artifact) {
        const content = (artifact.data && artifact.data.content) || '';
        if (!content) return '';

        const preHtml = `<pre>${this.escapeHtml(content)}</pre>`;
        return this.renderCollapsible(artifact.title || 'Details', preHtml, artifact.hint || '');
    }

    addChartToGallery(chartData) {
        // Skip if chart already exists
        const chartId = chartData.id || `chart-${Date.now()}`;
        if (this.chartInstances.has(chartId)) {
            return;
        }

        // Create chart container
        const chartItem = document.createElement('div');
        chartItem.className = 'chart-item';
        chartItem.innerHTML = `
            <div class="chart-title">${this.escapeHtml(chartData.title)}</div>
            <div class="chart-wrapper">
                <canvas class="chart-canvas" id="${chartId}"></canvas>
            </div>
            <div class="ai-disclaimer">This chart contains AI-generated data. AI agents can make mistakes — please review all outputs.</div>
        `;

        this.chartGallery.appendChild(chartItem);

        // Get canvas context
        const ctx = document.getElementById(chartId).getContext('2d');

        // Create Chart.js chart config, merging custom options with defaults
        const defaultOptions = { responsive: true, maintainAspectRatio: false };
        const mergedOptions = chartData.options
            ? { ...defaultOptions, ...chartData.options }
            : defaultOptions;

        // If chart has annotations, force one re-render after initial animation to fix positioning
        if (mergedOptions.plugins && mergedOptions.plugins.annotation) {
            let annotationFixed = false;
            mergedOptions.animation = {
                ...mergedOptions.animation,
                onComplete: function(anim) {
                    if (!annotationFixed) {
                        annotationFixed = true;
                        setTimeout(() => anim.chart.update('none'), 0);
                    }
                }
            };
        }

        const chartConfig = {
            type: chartData.type || 'bar',
            data: chartData.data,
            options: mergedOptions
        };

        const chartInstance = new Chart(ctx, chartConfig);
        this.chartInstances.set(chartId, chartInstance);
    }

    clearChartGallery() {
        // Destroy existing chart instances and clear the gallery
        this.chartInstances.forEach(instance => instance.destroy());
        this.chartInstances.clear();
        this.chartGallery.innerHTML = '';
    }

    showChartSection() {
        this.chartSection.classList.add('active');
    }

    closeCharts() {
        this.chartSection.classList.remove('active');
    }

    // ===================================
    // UI State Management
    // ===================================

    updateProcessingState(processing) {
        this.isProcessing = processing;
        this.messageInput.disabled = processing;
        this.sendButton.disabled = processing;

        this.statusBadge.className = `status-badge ${processing ? 'processing' : 'ready'}`;
        this.statusBadge.innerHTML = `
            <span class="status-dot"></span>
            <span>${processing ? 'Processing' : 'Ready'}</span>
        `;
    }

    toggleMobileSidebar() {
        this.sidebar.classList.toggle('collapsed');
    }

    closeMobileSidebar() {
        this.sidebar.classList.add('collapsed');
    }

    autoResizeTextarea() {
        this.messageInput.style.height = 'auto';
        this.messageInput.style.height = Math.min(this.messageInput.scrollHeight, 200) + 'px';
    }

    // ===================================
    // Utility Functions
    // ===================================

    formatMessageContent(content, type = 'assistant') {
        if (type === 'user') {
            return this.escapeHtml(content).replace(/\n/g, '<br>');
        }
        // Assistant messages: render markdown
        return marked.parse(content || '');
    }

    formatTimestamp(date) {
        return date.toLocaleString('en-US', {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        });
    }

    formatDate(dateStr) {
        const date = new Date(dateStr);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);

        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffHours < 24) return `${diffHours}h ago`;
        if (diffDays < 7) return `${diffDays}d ago`;

        return date.toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined
        });
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    scrollToBottom() {
        if (this.messagesWrapper) {
            this.messagesWrapper.scrollTop = this.messagesWrapper.scrollHeight;
        }
    }

    showError(message) {
        const errorDiv = document.createElement('div');
        errorDiv.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: var(--color-surface);
            border: 1px solid var(--color-hover);
            color: var(--color-hover);
            padding: 1rem 1.25rem;
            border-radius: var(--radius);
            z-index: 1000;
            max-width: 320px;
            box-shadow: var(--shadow-menu);
            font-size: 0.9rem;
        `;
        errorDiv.textContent = message;

        document.body.appendChild(errorDiv);

        setTimeout(() => {
            if (errorDiv.parentNode) {
                errorDiv.style.opacity = '0';
                errorDiv.style.transition = 'opacity 0.3s ease';
                setTimeout(() => errorDiv.remove(), 300);
            }
        }, 5000);
    }
}

// Initialize the chat application when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Horizon: apply the body font to all Chart.js charts rendered in the gallery.
    if (window.Chart) {
        Chart.defaults.font.family = "'Public Sans', sans-serif";
    }
    window.chatApp = new ChatApp();
});

// Handle browser back/forward buttons
window.addEventListener('popstate', (event) => {
    if (event.state && event.state.chatId) {
        window.chatApp.loadChat(event.state.chatId);
    }
});

// Handle page visibility changes
document.addEventListener('visibilitychange', () => {
    if (!document.hidden && window.chatApp) {
        window.chatApp.messageInput.focus();
    }
});

// Warn about pending messages before leaving
window.addEventListener('beforeunload', (e) => {
    if (window.chatApp && window.chatApp.pendingMessages.size > 0) {
        e.preventDefault();
        e.returnValue = 'You have messages being processed. Are you sure you want to leave?';
        return e.returnValue;
    }
});
