"""
Pydantic models that validate tenant registration input.

These are the single source of truth for the shape of a tenant config,
regardless of where it comes from (settings dict, YAML file, programmatic
call). Cross-field validators enforce the rules we documented:

  - `ontology.relationship_typing.keys()` ⊆ `relationship_types`
  - typing values (domain/range entity names) ⊆ `entity_types`
  - `default_strategy` must be a registered strategy
  - Source paths must exist and be directories

The `entity_types` / `relationship_types` lists are the canonical
single-source-of-truth — `ontology.classes` and `ontology.object_properties`
are NOT redefined separately. The ontology block only adds SPARQL-specific
structure (namespace, prefix, relationship_typing, data_properties).
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_TENANT_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_PREFIX_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


class OntologyConfig(BaseModel):
    """SPARQL-specific extras for a tenant that wants the `ontology`
    retrieval strategy. Optional — tenants without this block can still
    use GraphRAG (which only needs entity_types + relationship_types
    from DomainConfig)."""

    model_config = ConfigDict(extra="forbid")

    namespace: str = Field(
        ...,
        min_length=1,
        description="OWL/RDF namespace URI, e.g. 'http://example.org/ontology#'.",
    )
    prefix: str = Field(
        ...,
        max_length=32,
        description="Short prefix used in SPARQL templates, e.g. '<tenant>'. "
                    "Lowercase letters, digits, underscore.",
    )
    relationship_typing: Dict[str, Any] = Field(
        default_factory=dict,
        description="Maps relationship name → (domain_class, range_class) "
                    "tuple. Keys must be a subset of DomainConfig.relationship_types; "
                    "values must reference DomainConfig.entity_types.",
    )
    data_properties: List[str] = Field(
        default_factory=list,
        description="Names of data properties (literal-valued, e.g. "
                    "'hasUnit', 'hasDescription'). Used by the 'with_data_property' "
                    "SPARQL template.",
    )

    @field_validator("prefix")
    @classmethod
    def _prefix_pattern(cls, v: str) -> str:
        if not _PREFIX_RE.match(v):
            raise ValueError(
                f"prefix {v!r} must match [a-z_][a-z0-9_]*"
            )
        return v


class DomainConfig(BaseModel):
    """The per-tenant LLM-vocabulary configuration.

    `entity_types` and `relationship_types` are the single source of
    truth — used by both the GraphRAG extraction prompt and the ontology
    builder. The `ontology` block (optional) layers SPARQL-specific
    structure on top.

    Cross-field validation:
      - `ontology.relationship_typing` keys ⊆ `relationship_types`
      - `ontology.relationship_typing` typing values ⊆ `entity_types`
    """

    model_config = ConfigDict(extra="forbid")

    hint: str = Field(
        default="the indexed corpus",
        min_length=1,
        description="Short domain description used by HyDE and RAPTOR "
                    "prompts to write in the corpus's voice.",
    )
    entity_types: List[str] = Field(
        default_factory=list,
        description="Names of entity/concept classes the GraphRAG and "
                    "ontology extraction prompts should look for.",
    )
    relationship_types: List[str] = Field(
        default_factory=list,
        description="Names of relationships connecting entity types.",
    )
    ontology: Optional[OntologyConfig] = None

    @model_validator(mode="after")
    def _validate_ontology_cross_refs(self) -> "DomainConfig":
        if self.ontology is None:
            return self

        entities = set(self.entity_types)
        relations = set(self.relationship_types)
        for rel_name, typing in self.ontology.relationship_typing.items():
            if rel_name not in relations:
                raise ValueError(
                    f"ontology.relationship_typing references unknown "
                    f"relationship {rel_name!r} — declare it in "
                    f"relationship_types first"
                )
            # Accept either tuple/list (domain, range) or dict
            # {"domain": ..., "range": ...} for ergonomic YAML.
            if isinstance(typing, dict):
                domain_cls = typing.get("domain")
                range_cls = typing.get("range")
            elif isinstance(typing, (list, tuple)) and len(typing) == 2:
                domain_cls, range_cls = typing
            else:
                raise ValueError(
                    f"ontology.relationship_typing[{rel_name!r}] must be "
                    f"a 2-element [domain, range] list or "
                    f"{{'domain': ..., 'range': ...}} dict"
                )
            for cls_name, role in [(domain_cls, "domain"), (range_cls, "range")]:
                if cls_name not in entities:
                    raise ValueError(
                        f"ontology.relationship_typing[{rel_name!r}] "
                        f"{role}={cls_name!r} not declared in entity_types"
                    )
        return self


class SourceConfig(BaseModel):
    """A registered document directory under a tenant."""

    model_config = ConfigDict(extra="forbid")

    path: str
    label: str = ""
    category: str = ""
    agent_label: str = "default"

    @field_validator("path")
    @classmethod
    def _path_must_exist(cls, v: str) -> str:
        if not os.path.isdir(v):
            raise ValueError(
                f"source path {v!r} does not exist or is not a directory"
            )
        return v


class TenantRegistrationInput(BaseModel):
    """The top-level validated shape for `register_tenant(...)`.

    Settings dict and YAML/JSON file both deserialize into this. Sources
    can be inlined under `sources: [...]` (the loader splits them out
    and routes through `register_source()` after the tenant exists).
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(..., max_length=64)
    display_name: str = ""
    default_strategy: str = "hybrid"
    domain: DomainConfig = Field(default_factory=DomainConfig)
    sources: List[SourceConfig] = Field(default_factory=list)

    @field_validator("tenant_id")
    @classmethod
    def _tenant_id_pattern(cls, v: str) -> str:
        if not _TENANT_ID_RE.match(v):
            raise ValueError(
                f"tenant_id {v!r} must match [a-z0-9_-]+"
            )
        return v

    @field_validator("default_strategy")
    @classmethod
    def _strategy_exists(cls, v: str) -> str:
        # Lazy import so this Pydantic module can be loaded before the
        # strategy registry is populated. The registry is filled at
        # AppConfig.ready() time; this validator runs during the same
        # ready() so by then the registry exists.
        from knowledge_agent.strategies import STRATEGY_REGISTRY
        if v not in STRATEGY_REGISTRY:
            raise ValueError(
                f"default_strategy {v!r} is not registered. "
                f"Available: {sorted(STRATEGY_REGISTRY)}"
            )
        return v

    @field_validator("domain", mode="before")
    @classmethod
    def _coerce_domain_dict(cls, v):
        # Allow `domain={}` and `domain=None` to mean "default empty
        # config" without requiring the user to spell out `DomainConfig()`.
        if v is None or v == {}:
            return DomainConfig()
        return v
