# Two-tier error formatting

Per Q9: when a retrieval call fails (strategy unavailable, ontology
not built, embedding backend down), admins should see actionable error
detail (which tenant, which pipeline to run, the underlying exception)
while regular users should see a safe minimal message that doesn't leak
infrastructure context.

## Admin tier

Defined as `user.is_staff` OR `user.is_superuser`. Returns:

```python
{
    "status": "error",
    "error": "<the actual error message>",
    "details": {
        "strategy": "<requested strategy>",
        "query": "<original query>",
        # ... other context provided by the caller
    },
}
```

## User tier

Anonymous users, `AnonymousUser`, or any authenticated user without
`is_staff`/`is_superuser`. Returns:

```python
{
    "status": "error",
    "error": "This retrieval strategy is currently unavailable. Please try again or pick a different strategy.",
}
```

No infrastructure detail, no tenant name, no exception text.

## Implementation

`knowledge_agent.errors.format_strategy_error(*, user, error_message, admin_detail=None)`
is the only entry point (the arguments are keyword-only). It's called from `tool_wrapper.knowledge_retrieve_tool`
whenever the underlying `services.retrieve()` returns an error envelope.

## Example failure modes

| Trigger | Admin sees | User sees |
|---|---|---|
| `strategy="ontology"` but no `KnowledgeOntology` row exists for the tenant | "No ontology found for tenant 'dssat'. Run `manage.py ingest_knowledge --tenant=dssat`" | Generic fallback |
| `strategy="hyde"` but Ollama is unreachable | "OllamaLLMBackend connection refused: ..." | Generic fallback |
| Unknown strategy name | "Unknown strategy: 'foo'. Available: [...]" | Generic fallback |
| Filter compile error (bad operator) | "unknown operator '$xeq' under field 'year'" | Generic fallback |
