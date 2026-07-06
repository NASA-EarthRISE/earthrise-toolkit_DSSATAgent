# Settings reference

Every Django setting the framework reads, with default and purpose.
All settings live in the project's `settings.py`
(currently `dssat_chat_project/settings.py`).

## Framework-level

> **Note (accuracy):** There is **no `EARTHRISE_AGENTS_BASE` settings dict** in the
> current codebase — nothing reads it (verify with
> `grep -rn EARTHRISE_AGENTS_BASE earthrise_agents_base/`). Likewise there is no
> `chat_agent_class` pluggable-class mechanism, no `streaming_transport` /
> `progress_channel_prefix` setting, and no `allowed_skills` product hook — the
> orchestrator class is instantiated directly as `ChatAgent(...)` in
> `earthrise_agents_base/agent/tasks.py` and `chat_agent.py::create_chat_agent`.
> The only tunable in that family that exists is the ReAct iteration cap, read as
> an **environment variable**:

### `REACT_MAX_ITERATIONS` (env var)

Hard cap on ReAct loop iterations before the graph drops to the respond node
(guards against runaway LLM tool loops). Read by
`earthrise_agents_base/agent/chat_agent.py::load_config` from the environment
(not from a settings dict). Defaults to the value hardcoded in `load_config`.

### `CUSTOM_AGENT_NAME`

Display name shown in page titles, nav brand, emails, etc.

```python
CUSTOM_AGENT_NAME = "Acme Chat Agent"
```

Injected into every template via the `agent_name` context processor.
Falls back to `"ChatAgent"` if unset.

### `OLLAMA_URL`, `MODEL_NAME`, `EMBEDDING_MODEL`

LLM backend selection.

```python
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL_NAME = os.environ.get("MODEL_NAME", "llama3.1")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
```

`OLLAMA_URL` and `MODEL_NAME` are read by
`earthrise_agents_base.agent.chat_agent::load_config`. `EMBEDDING_MODEL` is **not**
consumed by `ChatAgent` — it is used by the embedding/knowledge layer
(`knowledge_agent`), which reads it when constructing the embedding backend.

## Sub-agent client mode

### `AGENT_CLIENTS`

Selects embedded vs. remote deployment mode per sub-agent.

Keys are the **full app labels** (`agent_label`, which equals the app label), not
short names — `get_client()` and `dssat_chat_project/urls.py` look the client up by
`agent_label`. There is no `"simulation"` agent; the DSSAT simulation agent's label
is `dssat_agent`.

```python
AGENT_CLIENTS = {
    "data_agent": {"mode": "embedded", "url": "http://localhost:5000"},
    "knowledge_agent": {"mode": "embedded", "url": "http://localhost:9100"},
    "dssat_agent": {"mode": "embedded", "url": "http://localhost:9200"},
}
```

- `mode="embedded"` — sub-agent runs in the same process; skill
  dispatch is direct Python calls
- `mode="remote"` — sub-agent lives at a separate HTTP endpoint; skill
  dispatch goes through the A2A JSON-RPC client

The framework's `EmbeddedClient` and `RemoteClient` in
`earthrise_agents_base.agent.clients` pick up which mode to use per
agent from this dict.

## Sub-agent settings

Each sub-agent may add its own settings keys. Framework settings never
depend on them. Documented in each sub-agent's
`<agent>/documentation/configuration.md`.

Common examples:

- `KNOWLEDGE_AGENT["default_tenant_id"]` — the tenant id used when a
  retrieval call arrives with no explicit tenant
- `DATAAGENT_SCHEMA` — the PostGIS schema name for weather tables
- `KNOWLEDGE_DOCUMENTS_DIR` — override the document directory
  (typically a Docker volume mount)

## Environment variables the framework reads

Direct `os.environ` reads (not passed through Django settings):

| Var | Consumer | Purpose |
|---|---|---|
| `SUBPATH` | `context_processors.subpath_prefix` | Deployment subpath for reverse-proxy setups (e.g., `www.example.com/api`) |
| `CELERY_BROKER_URL` | Celery client, SSE Redis fallback | Task queue + progress channel URL |
| `DJANGO_SETTINGS_MODULE` | Django | Must be `dssat_chat_project.settings` |
| `OLLAMA_URL` | LLM backend | Ollama server URL |
| `MODEL_NAME` | LLM backend | LLM model name |

## Restarting workers

Framework settings and env vars are read at process/request time — restart the web
and celery workers to pick up changes to `config.env` / settings.
