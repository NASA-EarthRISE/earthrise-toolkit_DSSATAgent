# Backend interfaces

Two interfaces — `EmbeddingBackend` and `LLMBackend` — abstract the
external model call so strategies and ingestion code never import
provider-specific libraries directly.

## Selection

Configured via `settings.KNOWLEDGE_AGENT`:

```python
KNOWLEDGE_AGENT = {
    "llm_backend":       "ollama",   # default
    "embedding_backend": "ollama",   # default
    "ollama": {
        "base_url":         "http://localhost:11434",
        "embedding_model":  "nomic-embed-text",
        "retrieval_model":  "llama3.1",
    },
}
```

Backend selection is a static switch-case in
`knowledge_agent/backends/__init__.py` (not dynamic class loading from
a string). v1 ships exactly one implementation of each interface:
`OllamaEmbeddingBackend`, `OllamaLLMBackend`.

## Startup validation

`KnowledgeAgentConfig.ready()` calls `validate_backends()`:

- Constructing each backend → if the backend can't be constructed,
  raise `ImproperlyConfigured` (hard startup failure)
- Backend health check (Ollama tags endpoint) → if unreachable, log
  WARN but don't raise (deployments where Ollama starts in parallel
  with Django shouldn't block startup on a transient connection refusal)

## Adding a new backend

1. Implement the interface in a new module:

   ```python
   # knowledge_agent/backends/openai.py
   from .base import LLMBackend

   class OpenAILLMBackend(LLMBackend):
       def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
           ...
       def generate(self, prompt: str, **kwargs) -> str:
           ...
       def health_check(self) -> None:
           ...
   ```

2. Add a case to the switch:

   ```python
   # knowledge_agent/backends/__init__.py
   if name == "openai":
       from .openai import OpenAILLMBackend
       openai_cfg = cfg.get("openai", {})
       _llm_backend = OpenAILLMBackend(**openai_cfg)
   ```

3. Document the new option in this file.

## Why module-wide, not per-tenant

A single backend serves every tenant. Mixing backends per tenant adds
config surface (per-tenant backend settings) without a real use case
for v1. If a future deployment needs per-tenant backends, the singleton
caching in `get_llm_backend()` / `get_embedding_backend()` is the
extension point.

## Backward compatibility

The legacy `knowledge_agent.embeddings.EmbeddingService` class is now a
thin shim around `get_embedding_backend()`. Existing imports
(`from knowledge_agent.embeddings import EmbeddingService`) keep
working. New code should use the backend directly.
