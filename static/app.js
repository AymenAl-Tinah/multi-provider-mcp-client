/**
 * MCP Client — Frontend Application
 * Handles WebSocket communication, UI state, config persistence
 */

// ─── State ──────────────────────────────────────────────────────────────────────

const state = {
    ws: null,
    providers: {},
    config: {},
    selectedProvider: null,
    connected: false,
    processing: false,
};

// ─── DOM ────────────────────────────────────────────────────────────────────────

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const dom = {
    providerGrid: $('#providerGrid'),
    configuredServers: $('#configuredServers'),
    serverPathInput: $('#serverPathInput'),
    connectBtn: $('#connectBtn'),
    disconnectBtn: $('#disconnectBtn'),
    connectionStatus: $('#connectionStatus'),
    toolsList: $('#toolsList'),
    toolCount: $('#toolCount'),
    messagesContainer: $('#messagesContainer'),
    welcomeScreen: $('#welcomeScreen'),
    chatInput: $('#chatInput'),
    sendBtn: $('#sendBtn'),
    clearChatBtn: $('#clearChatBtn'),
    chatTitle: $('#chatTitle'),
    chatSubtitle: $('#chatSubtitle'),
    settingsBtn: $('#settingsBtn'),
    settingsPanel: $('#settingsPanel'),
    settingsOverlay: $('#settingsOverlay'),
    closeSettingsBtn: $('#closeSettingsBtn'),
    settingsBody: $('#settingsBody'),
    saveSettingsBtn: $('#saveSettingsBtn'),
    toastContainer: $('#toastContainer'),
    mobileMenuBtn: $('#mobileMenuBtn'),
    toggleSidebarBtn: $('#toggleSidebarBtn'),
    sidebar: $('#sidebar'),
    // Config editor
    configEditorBtn: $('#configEditorBtn'),
    configEditorPanel: $('#configEditorPanel'),
    configOverlay: $('#configOverlay'),
    configTextarea: $('#configTextarea'),
    lineNumbers: $('#lineNumbers'),
    configFilePath: $('#configFilePath'),
    saveConfigJsonBtn: $('#saveConfigJsonBtn'),
    reloadConfigBtn: $('#reloadConfigBtn'),
    closeConfigBtn: $('#closeConfigBtn'),
    configEditorStatus: $('#configEditorStatus'),
    // Auth
    authOverlay: $('#authOverlay'),
    authTitle: $('#authTitle'),
    authSubtitle: $('#authSubtitle'),
    authUsername: $('#authUsername'),
    authPassword: $('#authPassword'),
    authError: $('#authError'),
    authSubmitBtn: $('#authSubmitBtn'),
    authToggleText: $('#authToggleText'),
    authToggleBtn: $('#authToggleBtn'),
    logoutBtn: $('#logoutBtn'),
};

// ─── Provider Icon Letters ──────────────────────────────────────────────────────

const PROVIDER_ICONS = {
    anthropic: 'A',
    openai: 'O',
    gemini: 'G',
    openrouter: 'R',
    deepseek: 'D',
    qwen: 'Q',
};

// ─── Auth ───────────────────────────────────────────────────────────────────────

let isLoginMode = true;

async function checkAuth() {
    try {
        const res = await fetch('/api/auth/me');
        if (res.ok) {
            const data = await res.json();
            return true;
        }
    } catch (e) { }
    return false;
}

async function submitAuth() {
    const username = dom.authUsername.value.trim();
    const password = dom.authPassword.value;
    if (!username || !password) {
        dom.authError.textContent = 'Please enter username and password.';
        return;
    }

    const endpoint = isLoginMode ? '/api/auth/login' : '/api/auth/signup';
    dom.authSubmitBtn.disabled = true;
    dom.authSubmitBtn.textContent = 'Please wait...';
    dom.authError.textContent = '';

    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();

        if (res.ok) {
            dom.authOverlay.classList.remove('active');
            startApp();
        } else {
            dom.authError.textContent = data.message || 'Authentication failed.';
        }
    } catch (e) {
        dom.authError.textContent = 'Network error. Please try again.';
    } finally {
        dom.authSubmitBtn.disabled = false;
        dom.authSubmitBtn.textContent = isLoginMode ? 'Log In' : 'Sign Up';
    }
}

function setupAuthListeners() {
    dom.authSubmitBtn.addEventListener('click', submitAuth);
    dom.authPassword.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') submitAuth();
    });

    dom.authToggleBtn.addEventListener('click', () => {
        isLoginMode = !isLoginMode;
        dom.authTitle.textContent = isLoginMode ? 'Welcome Back' : 'Create Account';
        dom.authSubtitle.textContent = isLoginMode ? 'Log in to your private workspace' : 'Sign up for a new workspace';
        dom.authSubmitBtn.textContent = isLoginMode ? 'Log In' : 'Sign Up';
        dom.authToggleText.textContent = isLoginMode ? "Don't have an account?" : "Already have an account?";
        dom.authToggleBtn.textContent = isLoginMode ? 'Sign Up' : 'Log In';
        dom.authError.textContent = '';
    });
}

// ─── Init ───────────────────────────────────────────────────────────────────────

async function init() {
    const isAuthenticated = await checkAuth();
    if (isAuthenticated) {
        dom.authOverlay.classList.remove('active');
        startApp();
    } else {
        setupAuthListeners();
    }
}

async function startApp() {
    await loadProviders();
    await loadConfig();
    setupWebSocket();
    setupEventListeners();
    renderProviders();
    renderSettings();
    renderConfiguredServers();
    applyConfig();
}

// ─── API ────────────────────────────────────────────────────────────────────────

async function loadProviders() {
    try {
        const res = await fetch('/api/providers');
        state.providers = await res.json();
    } catch (e) {
        showToast('Failed to load providers', 'error');
    }
}

async function loadConfig() {
    try {
        const res = await fetch('/api/config');
        state.config = await res.json();
    } catch (e) {
        showToast('Failed to load config', 'error');
    }
}

async function saveConfig(data) {
    try {
        await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        await loadConfig();
        showToast('Settings saved!', 'success');
    } catch (e) {
        showToast('Failed to save settings', 'error');
    }
}

// ─── WebSocket ──────────────────────────────────────────────────────────────────

function setupWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    state.ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

    state.ws.onopen = () => {
        console.log('[WS] Connected');
        // If we have a configured provider, set it
        const pid = state.config.selected_provider;
        if (pid && state.config.providers?.[pid]?.api_key) {
            setProviderOnServer(pid);
        }
    };

    state.ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleWSMessage(data);
    };

    state.ws.onerror = (err) => {
        console.error('[WS] Error:', err);
    };

    state.ws.onclose = () => {
        console.log('[WS] Disconnected, reconnecting in 3s...');
        setTimeout(setupWebSocket, 3000);
    };
}

function wsSend(data) {
    if (state.ws?.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify(data));
    } else {
        showToast('WebSocket not connected. Retrying...', 'error');
    }
}

function handleWSMessage(data) {
    switch (data.type) {
        case 'connected':
            onServerConnected(data);
            break;
        case 'disconnected':
            onServerDisconnected();
            break;
        case 'provider_set':
            onProviderSet(data);
            break;
        case 'status':
            addStatusMessage(data.message);
            break;
        case 'tool_call':
            addToolCallMessage(data);
            break;
        case 'tool_result':
            updateToolResult(data);
            break;
        case 'response':
            onResponse(data);
            break;
        case 'error':
            addErrorMessage(data.message);
            state.processing = false;
            updateInputState();
            break;
        case 'cleared':
            clearMessages();
            break;
    }
}

// ─── WS Event Handlers ─────────────────────────────────────────────────────────

function onServerConnected(data) {
    state.connected = true;
    updateConnectionStatus('online', `Connected — ${data.tools.length} tools`);
    renderTools(data.tools);
    dom.connectBtn.disabled = false;
    dom.disconnectBtn.disabled = false;
    dom.serverPathInput.value = data.server_path || dom.serverPathInput.value;
    updateInputState();
    removeStatusMessages();
    showToast(`Connected! ${data.tools.length} tools available`, 'success');

    // Hide welcome, enable chat
    if (dom.welcomeScreen) {
        dom.welcomeScreen.style.display = 'none';
    }
}

function onServerDisconnected() {
    state.connected = false;
    updateConnectionStatus('offline', 'Disconnected');
    renderTools([]);
    dom.connectBtn.disabled = false;
    dom.disconnectBtn.disabled = true;
    updateInputState();
    showToast('Disconnected from server', 'info');
}

function onProviderSet(data) {
    dom.chatSubtitle.textContent = `${state.providers[data.provider]?.name || data.provider} — ${data.model}`;
    updateInputState();
}

function onResponse(data) {
    // Remove any typing indicator
    removeStatusMessages();
    addAssistantMessage(data.content);
    state.processing = false;
    updateInputState();
}

// ─── UI Rendering ───────────────────────────────────────────────────────────────

function renderProviders() {
    let html = '';
    for (const [pid, info] of Object.entries(state.providers)) {
        const hasKey = state.config.providers?.[pid]?.has_key;
        const isActive = state.config.selected_provider === pid;
        html += `
            <div class="provider-card ${isActive ? 'active' : ''} ${!hasKey ? 'no-key' : ''}"
                 data-provider="${pid}" title="${info.name}${!hasKey ? ' (no API key)' : ''}">
                <div class="provider-icon ${pid}">${PROVIDER_ICONS[pid]}</div>
                <span>${info.name}</span>
            </div>
        `;
    }
    dom.providerGrid.innerHTML = html;

    // Click handlers
    dom.providerGrid.querySelectorAll('.provider-card').forEach(card => {
        card.addEventListener('click', () => {
            const pid = card.dataset.provider;
            selectProvider(pid);
        });
    });
}

function selectProvider(pid) {
    state.selectedProvider = pid;
    state.config.selected_provider = pid;

    // Update active state
    dom.providerGrid.querySelectorAll('.provider-card').forEach(c => c.classList.remove('active'));
    dom.providerGrid.querySelector(`[data-provider="${pid}"]`)?.classList.add('active');

    // Update model options
    updateModelOptions(pid);

    // Save selection
    saveConfig({ selected_provider: pid });

    // Set provider on server
    setProviderOnServer(pid);
}

function updateModelOptions(pid) {
    // Note: sidebar model options removed. Each provider has its own model in Settings.
}

function setProviderOnServer(pid) {
    const apiKey = state.config.providers?.[pid]?.api_key;
    const model = state.config.providers?.[pid]?.selected_model || state.providers[pid]?.default_model;

    if (!apiKey) {
        showToast(`No API key for ${state.providers[pid]?.name}. Open Settings to configure.`, 'error');
        return;
    }

    wsSend({
        type: 'set_provider',
        provider: pid,
        model: model,
        api_key: apiKey,
    });
}

function renderTools(tools) {
    dom.toolCount.textContent = tools.length;
    if (tools.length === 0) {
        dom.toolsList.innerHTML = '<div class="tools-empty">No tools connected</div>';
        return;
    }

    let html = '';
    for (const tool of tools) {
        html += `
            <div class="tool-badge" title="${escHtml(tool.description)}">
                <div class="tool-badge-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
                    </svg>
                </div>
                <div class="tool-badge-info">
                    <div class="tool-badge-name">${escHtml(tool.name)}</div>
                    ${tool.description ? `<div class="tool-badge-desc">${escHtml(tool.description)}</div>` : ''}
                </div>
            </div>
        `;
    }
    dom.toolsList.innerHTML = html;
}

function updateConnectionStatus(status, text) {
    dom.connectionStatus.innerHTML = `
        <span class="status-dot ${status}"></span>
        <span>${text}</span>
    `;
}

function updateInputState() {
    const canChat = state.connected && !state.processing && state.config.selected_provider;
    dom.chatInput.disabled = !canChat;
    dom.sendBtn.disabled = !canChat;
    dom.chatInput.placeholder = !state.connected
        ? 'Connect to an MCP server first...'
        : state.processing
            ? 'Processing...'
            : 'Type your message...';
}

// ─── Settings ───────────────────────────────────────────────────────────────────

function renderSettings() {
    let html = '';
    for (const [pid, info] of Object.entries(state.providers)) {
        const savedKey = state.config.providers?.[pid]?.api_key || '';
        const savedModel = state.config.providers?.[pid]?.selected_model || info.default_model;
        const hasKey = !!savedKey;

        html += `
            <div class="settings-provider-card">
                <div class="settings-provider-header">
                    <div class="provider-icon ${pid}">${PROVIDER_ICONS[pid]}</div>
                    <span class="settings-provider-name">${info.name}</span>
                    <span class="settings-provider-badge ${hasKey ? 'configured' : 'not-configured'}">
                        ${hasKey ? '✓ Configured' : 'Not configured'}
                    </span>
                </div>
                <div class="settings-field">
                    <label class="settings-label">API Key</label>
                    <input type="password" class="settings-input" data-provider="${pid}" data-field="api_key"
                           value="${escHtml(savedKey)}" placeholder="Enter API key...">
                </div>
                <div class="settings-field">
                    <label class="settings-label">Default Model</label>
                    <select class="settings-select" data-provider="${pid}" data-field="selected_model">
                        ${info.models.map(m => `<option value="${m}" ${m === savedModel ? 'selected' : ''}>${m}</option>`).join('')}
                    </select>
                </div>
            </div>
        `;
    }
    dom.settingsBody.innerHTML = html;
}

function openSettings() {
    dom.settingsPanel.classList.add('active');
    dom.settingsOverlay.classList.add('active');
    renderSettings();
}

function closeSettings() {
    dom.settingsPanel.classList.remove('active');
    dom.settingsOverlay.classList.remove('active');
}

function saveCurrentSettings() {
    const data = { providers: {} };
    dom.settingsBody.querySelectorAll('.settings-input, .settings-select').forEach(input => {
        const pid = input.dataset.provider;
        const field = input.dataset.field;
        if (!data.providers[pid]) data.providers[pid] = {};
        data.providers[pid][field] = input.value;
    });
    saveConfig(data).then(() => {
        closeSettings();
        renderProviders();
        renderConfiguredServers();
        // Re-set provider on server if active
        const pid = state.config.selected_provider;
        if (pid) setProviderOnServer(pid);
    });
}

// ─── Config Editor ──────────────────────────────────────────────────────────────

async function openConfigEditor() {
    dom.configEditorPanel.classList.add('active');
    dom.configOverlay.classList.add('active');
    await loadRawConfig();
}

function closeConfigEditor() {
    dom.configEditorPanel.classList.remove('active');
    dom.configOverlay.classList.remove('active');
}

async function loadRawConfig() {
    try {
        const res = await fetch('/api/config/raw');
        const data = await res.json();
        dom.configTextarea.value = data.content;
        dom.configFilePath.textContent = data.path;
        updateLineNumbers();
        setConfigStatus('Ready', '');
    } catch (e) {
        setConfigStatus('Failed to load config', 'error');
    }
}

async function saveRawConfig() {
    const content = dom.configTextarea.value;
    // Validate JSON first
    try {
        JSON.parse(content);
    } catch (e) {
        setConfigStatus(`Invalid JSON: ${e.message}`, 'error');
        return;
    }

    try {
        const res = await fetch('/api/config/raw', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        const result = await res.json();
        if (result.status === 'saved') {
            setConfigStatus('Saved successfully!', 'success');
            showToast('Config saved!', 'success');
            // Reload app config
            await loadConfig();
            renderProviders();
            renderSettings();
            renderConfiguredServers();
            applyConfig();
            // Re-set provider on server
            const pid = state.config.selected_provider;
            if (pid && state.config.providers?.[pid]?.api_key) {
                setProviderOnServer(pid);
            }
        } else {
            setConfigStatus(result.message || 'Save failed', 'error');
        }
    } catch (e) {
        setConfigStatus('Failed to save config', 'error');
    }
}

function updateLineNumbers() {
    const lines = dom.configTextarea.value.split('\n');
    dom.lineNumbers.innerHTML = lines.map((_, i) => `<div>${i + 1}</div>`).join('');
}

function setConfigStatus(text, type) {
    dom.configEditorStatus.textContent = text;
    dom.configEditorStatus.className = 'config-editor-status' + (type ? ` ${type}` : '');
}

// ─── Configured Servers ─────────────────────────────────────────────────────────

function renderConfiguredServers() {
    const servers = state.config.mcpServers || {};
    const entries = Object.entries(servers);

    if (entries.length === 0) {
        dom.configuredServers.innerHTML = '<div class="tools-empty">No servers in config.json</div>';
        return;
    }

    let html = '';
    for (const [name, srv] of entries) {
        const cmd = srv.command || '';
        const args = (srv.args || []).join(' ');
        const displayCmd = `${cmd} ${args}`.trim();
        html += `
            <div class="server-card" data-server-name="${escHtml(name)}" title="${escHtml(displayCmd)}">
                <div class="server-card-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="2" y="2" width="20" height="8" rx="2"/>
                        <rect x="2" y="14" width="20" height="8" rx="2"/>
                        <circle cx="6" cy="6" r="1"/>
                        <circle cx="6" cy="18" r="1"/>
                    </svg>
                </div>
                <div class="server-card-info">
                    <div class="server-card-name">${escHtml(name)}</div>
                    <div class="server-card-cmd">${escHtml(displayCmd)}</div>
                </div>
            </div>
        `;
    }
    dom.configuredServers.innerHTML = html;

    // Click to connect
    dom.configuredServers.querySelectorAll('.server-card').forEach(card => {
        card.addEventListener('click', () => {
            const serverName = card.dataset.serverName;
            const srv = servers[serverName];
            if (srv && srv.command) {
                dom.connectBtn.disabled = true;
                updateConnectionStatus('connecting', `Connecting to ${serverName}...`);
                wsSend({
                    type: 'connect_config',
                    name: serverName,
                    command: srv.command,
                    args: srv.args || [],
                    env: srv.env || null,
                });
            } else {
                showToast(`No command defined for server "${serverName}". Check config.json.`, 'error');
            }
        });
    });
}

// ─── Messages ───────────────────────────────────────────────────────────────────

function addUserMessage(text) {
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const el = document.createElement('div');
    el.className = 'message user';
    el.innerHTML = `
        <div class="message-avatar">U</div>
        <div class="message-body">
            <div class="message-content">${escHtml(text)}</div>
            <div class="message-time">${time}</div>
        </div>
    `;
    dom.messagesContainer.appendChild(el);
    scrollToBottom();
}

function addAssistantMessage(text) {
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const el = document.createElement('div');
    el.className = 'message assistant';
    el.innerHTML = `
        <div class="message-avatar">✦</div>
        <div class="message-body">
            <div class="message-content">${formatContent(text)}</div>
            <div class="message-time">${time}</div>
        </div>
    `;
    dom.messagesContainer.appendChild(el);
    scrollToBottom();
}

function addStatusMessage(text) {
    const el = document.createElement('div');
    el.className = 'status-message';
    el.setAttribute('data-status', 'true');
    el.innerHTML = `
        <div class="typing-dots"><span></span><span></span><span></span></div>
        <span>${escHtml(text)}</span>
    `;
    dom.messagesContainer.appendChild(el);
    scrollToBottom();
}

function removeStatusMessages() {
    dom.messagesContainer.querySelectorAll('[data-status="true"]').forEach(el => el.remove());
}

function addToolCallMessage(data) {
    removeStatusMessages();
    const id = `tool-${Date.now()}`;
    const el = document.createElement('div');
    el.className = 'tool-call-card';
    el.id = id;
    el.setAttribute('data-tool-name', data.name);
    el.innerHTML = `
        <div class="tool-call-header" onclick="this.parentElement.classList.toggle('expanded')">
            <div class="tool-call-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
                </svg>
            </div>
            <span class="tool-call-name">${escHtml(data.name)}</span>
            <svg class="tool-call-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
        </div>
        <div class="tool-call-body">
            <div class="tool-call-section">
                <div class="tool-call-label">Arguments</div>
                <div class="tool-call-code">${escHtml(JSON.stringify(data.arguments, null, 2))}</div>
            </div>
            <div class="tool-call-section tool-result-placeholder">
                <div class="tool-call-label">Result</div>
                <div class="tool-call-code">
                    <div class="typing-dots"><span></span><span></span><span></span></div>
                </div>
            </div>
        </div>
    `;
    dom.messagesContainer.appendChild(el);
    scrollToBottom();
}

function updateToolResult(data) {
    // Find the most recent tool card with this name that still has a placeholder
    const cards = dom.messagesContainer.querySelectorAll(`.tool-call-card[data-tool-name="${data.name}"]`);
    const card = cards[cards.length - 1];
    if (!card) return;

    const placeholder = card.querySelector('.tool-result-placeholder');
    if (placeholder) {
        const isError = data.result.toLowerCase().includes('error');
        placeholder.classList.remove('tool-result-placeholder');
        placeholder.innerHTML = `
            <div class="tool-call-label">Result</div>
            <div class="tool-call-code ${isError ? 'tool-result-error' : 'tool-result-success'}">${escHtml(data.result)}</div>
        `;
    }

    // Auto-expand
    card.classList.add('expanded');
    scrollToBottom();
}

function addErrorMessage(text) {
    removeStatusMessages();
    const el = document.createElement('div');
    el.className = 'error-message';
    el.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
        <span>${escHtml(text)}</span>
    `;
    dom.messagesContainer.appendChild(el);
    scrollToBottom();

    // Auto-hide after 6 seconds
    setTimeout(() => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(10px)';
        el.style.transition = 'all 0.5s ease';
        setTimeout(() => el.remove(), 500);
    }, 6000);
}

function clearMessages() {
    dom.messagesContainer.innerHTML = '';
    if (dom.welcomeScreen) {
        dom.messagesContainer.appendChild(dom.welcomeScreen);
        dom.welcomeScreen.style.display = state.connected ? 'none' : '';
    }
}

function scrollToBottom() {
    requestAnimationFrame(() => {
        dom.messagesContainer.scrollTop = dom.messagesContainer.scrollHeight;
    });
}

// ─── Helpers ────────────────────────────────────────────────────────────────────

function escHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function formatContent(text) {
    if (!text) return '';
    // Basic formatting: bold, italic, code blocks, inline code
    let html = escHtml(text);
    // Code blocks
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code style="background:var(--bg-elevated);padding:2px 6px;border-radius:4px;font-family:var(--font-mono);font-size:12px;">$1</code>');
    // Bold
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Italic
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    return html;
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    dom.toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('exit');
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// ─── Event Listeners ────────────────────────────────────────────────────────────

function setupEventListeners() {
    // Send message
    dom.sendBtn.addEventListener('click', sendMessage);
    dom.chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Auto-resize textarea
    dom.chatInput.addEventListener('input', () => {
        dom.chatInput.style.height = 'auto';
        dom.chatInput.style.height = Math.min(dom.chatInput.scrollHeight, 120) + 'px';
    });


    // Server connect/disconnect
    dom.connectBtn.addEventListener('click', () => {
        const path = dom.serverPathInput.value.trim();
        if (!path) {
            showToast('Enter a server script path', 'error');
            return;
        }
        dom.connectBtn.disabled = true;
        updateConnectionStatus('connecting', 'Connecting...');
        wsSend({ type: 'connect', server_path: path });
    });

    dom.disconnectBtn.addEventListener('click', () => {
        wsSend({ type: 'disconnect' });
    });

    // Clear chat
    dom.clearChatBtn.addEventListener('click', () => {
        wsSend({ type: 'clear' });
        clearMessages();
    });

    // Settings
    dom.settingsBtn.addEventListener('click', openSettings);
    dom.closeSettingsBtn.addEventListener('click', closeSettings);
    dom.settingsOverlay.addEventListener('click', closeSettings);
    dom.saveSettingsBtn.addEventListener('click', saveCurrentSettings);

    // Logout
    if (dom.logoutBtn) {
        dom.logoutBtn.addEventListener('click', async () => {
            try {
                await fetch('/api/auth/logout', { method: 'POST' });
            } catch (e) { }
            window.location.reload();
        });
    }

    // Config editor
    dom.configEditorBtn.addEventListener('click', openConfigEditor);
    dom.closeConfigBtn.addEventListener('click', closeConfigEditor);
    dom.configOverlay.addEventListener('click', closeConfigEditor);
    dom.saveConfigJsonBtn.addEventListener('click', saveRawConfig);
    dom.reloadConfigBtn.addEventListener('click', loadRawConfig);
    dom.configTextarea.addEventListener('input', () => {
        updateLineNumbers();
        // Live JSON validation
        try {
            JSON.parse(dom.configTextarea.value);
            setConfigStatus('Valid JSON', 'success');
        } catch (e) {
            setConfigStatus(`JSON Error: ${e.message}`, 'error');
        }
    });
    dom.configTextarea.addEventListener('scroll', () => {
        dom.lineNumbers.style.transform = `translateY(-${dom.configTextarea.scrollTop}px)`;
    });
    // Handle Tab key in textarea
    dom.configTextarea.addEventListener('keydown', (e) => {
        if (e.key === 'Tab') {
            e.preventDefault();
            const start = dom.configTextarea.selectionStart;
            const end = dom.configTextarea.selectionEnd;
            dom.configTextarea.value = dom.configTextarea.value.substring(0, start) + '  ' + dom.configTextarea.value.substring(end);
            dom.configTextarea.selectionStart = dom.configTextarea.selectionEnd = start + 2;
            updateLineNumbers();
        }
    });

    // Mobile menu
    dom.mobileMenuBtn.addEventListener('click', () => {
        dom.sidebar.classList.toggle('mobile-open');
    });

    dom.toggleSidebarBtn.addEventListener('click', () => {
        dom.sidebar.classList.toggle('collapsed');
    });
}

function sendMessage() {
    const text = dom.chatInput.value.trim();
    if (!text || state.processing) return;

    addUserMessage(text);
    dom.chatInput.value = '';
    dom.chatInput.style.height = 'auto';
    state.processing = true;
    updateInputState();

    wsSend({ type: 'chat', message: text });
}

// ─── Apply Saved Config ─────────────────────────────────────────────────────────

function applyConfig() {
    const pid = state.config.selected_provider;
    if (pid) {
        state.selectedProvider = pid;
        updateModelOptions(pid);
    }

    if (state.config.mcp_server_path) {
        dom.serverPathInput.value = state.config.mcp_server_path;
    }

    renderConfiguredServers();
}

// ─── Boot ───────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', init);
