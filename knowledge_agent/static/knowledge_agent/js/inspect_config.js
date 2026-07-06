// Bootstrap: hydrate the server-rendered config globals that inspect.js
// consumes, reading them from JSON data islands emitted by the template.
// Must run before inspect.js. Each page emits at most one island.
(function () {
    const agentEl = document.getElementById('knowledge-agent-config');
    if (agentEl) {
        window.KNOWLEDGE_AGENT_CONFIG = JSON.parse(agentEl.textContent);
    }
    const inspectEl = document.getElementById('knowledge-inspect-config');
    if (inspectEl) {
        window.KNOWLEDGE_INSPECT_CONFIG = JSON.parse(inspectEl.textContent);
    }
})();
