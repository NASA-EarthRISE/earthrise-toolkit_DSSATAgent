# Tenant + source registration

Three ways to register, all converging through the same Pydantic
validation and the same `KnowledgeTenant` / `KnowledgeSource` ORM
upsert.

**Rule: tenants must be registered before any sources reference them.**
The loader processes tenants first then sources within each input
source. Programmatic callers must do the same — `register_source(...)`
raises if the named tenant doesn't exist.

---

## Method 1 — Django settings dict (declarative, recommended)

```python
# settings.py
KNOWLEDGE_AGENT = {
    "ollama": {
        "base_url": "http://localhost:11434",
        "embedding_model": "nomic-embed-text",
        "retrieval_model": "llama3.1",
    },
    "batch_max_workers": 4,

    "tenants": {
        "dssat": {
            "display_name": "DSSAT Knowledge Base",
            "default_strategy": "hybrid",
            "domain": {
                "hint": "DSSAT crop modelling and agronomy",
                "entity_types": [
                    "Crop", "Cultivar", "CropModel", "SimulationParameter",
                    "SoilProperty", "WeatherVariable",
                ],
                "relationship_types": [
                    "requires_input", "affects", "simulated_by",
                ],
                "ontology": {
                    "namespace": "http://dssat.net/ontology#",
                    "prefix":    "dssat",
                    "relationship_typing": {
                        "requires_input": ["CropModel", "SimulationParameter"],
                        "affects":        ["SimulationParameter", "WeatherVariable"],
                    },
                    "data_properties": ["hasUnit", "hasDescription"],
                },
            },
            "sources": [
                {"path": "/data/docs/manuals", "label": "DSSAT Manual v4.7"},
                {"path": "/data/docs/papers",  "label": "Reference papers"},
            ],
        },
    },
}
```

Read once at `KnowledgeAgentConfig.ready()`. Validation errors fail
loud at `manage.py runserver` startup with a clear message.

## Method 2 — YAML or JSON file (ops-friendly, versionable)

```python
# settings.py
KNOWLEDGE_AGENT = {
    "config_file": BASE_DIR / "knowledge_tenants.yaml",
}
```

```yaml
# knowledge_tenants.yaml
tenants:
  dssat:
    display_name: DSSAT Knowledge Base
    default_strategy: hybrid
    domain:
      hint: DSSAT crop modelling and agronomy
      entity_types: [Crop, Cultivar, CropModel, SimulationParameter]
      relationship_types: [requires_input, affects]
      ontology:
        namespace: http://dssat.net/ontology#
        prefix: dssat
        relationship_typing:
          requires_input: [CropModel, SimulationParameter]
          affects:        [SimulationParameter, WeatherVariable]
    sources:
      - {path: /data/docs/manuals, label: DSSAT Manual v4.7}
      - {path: /data/docs/papers,  label: Reference papers}
```

JSON works the same way (use `.json` extension). Validated identically;
file path errors fail loud at startup.

## Method 3 — Programmatic (sister app's AppConfig.ready)

```python
# myapp/apps.py
class MyAppConfig(AppConfig):
    def ready(self):
        from knowledge_agent.registry import register_tenant, register_source

        register_tenant(
            tenant_id="acme",
            display_name="Acme Knowledge Base",
            domain={
                "hint": "legal contracts and case law",
                "entity_types": ["Contract", "Party", "Clause"],
                "relationship_types": ["governed_by", "amends"],
            },
        )
        register_source(
            tenant_id="acme",
            path="/data/acme/contracts",
            label="Contracts 2024",
        )
```

Programmatic calls run after the settings/YAML loader. Each call is
idempotent (upsert by `tenant_id` for tenants, by `(tenant, path)` for
sources).

## Conflict resolution

If the **same** `tenant_id` is defined in both `settings.KNOWLEDGE_AGENT['tenants']`
and the `config_file`, startup raises `RuntimeError` with a clear
message pointing at both sources.

Override by setting `KNOWLEDGE_AGENT['allow_override'] = True` — the
file then wins (last-write semantics, since file-based config is
typically the deploy-time authoritative source).

Programmatic `register_tenant(...)` calls have no conflict detection —
they're always upserts. Two `register_tenant` calls with the same
`tenant_id` collapse to a single row with the second call's config.

## What happens when domain config changes

When `register_tenant` is called with a different domain than the stored
hash, three granular dirty flags get set:

| Field changed | Dirty flag set |
|---|---|
| `domain.hint` | `raptor_dirty` (RAPTOR summaries use the hint) |
| `entity_types`, `relationship_types` | `graphrag_dirty` + `ontology_dirty` |
| `ontology.namespace`, `prefix`, `relationship_typing`, `data_properties` | `ontology_dirty` |

Chunks are **never** invalidated by domain changes (chunks come from the
source documents, not from the domain vocabulary). The
`ingest_knowledge` command consumes these flags and re-runs only the
affected pipelines.

See [`ingestion.md`](ingestion.md) for the workflow.

## Validation rules (cross-field)

- `tenant_id` must match `[a-z0-9_-]+`
- `ontology.prefix` must match `[a-z_][a-z0-9_]*`
- `ontology.namespace` must be a non-empty string
- `ontology.relationship_typing.keys()` ⊆ `relationship_types`
- `relationship_typing` typing values ⊆ `entity_types`
- `default_strategy` must be a registered strategy
- `sources[*].path` must exist and be a directory

All errors surface with `tenant_id` context.
