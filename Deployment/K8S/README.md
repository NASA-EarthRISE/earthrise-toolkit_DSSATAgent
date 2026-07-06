# Kubernetes deployment

The tracked manifests here are **templates** with placeholder values
(`REPLACE_WITH_...`). Real, site-specific values (image registry, hostnames,
node affinity, namespace) must **not** be committed.

## Workflow

1. Copy the templates to gitignored local overlays:
   ```bash
   cp deployment.yml deployment.local.yaml
   cp ingress.yaml   ingress.local.yaml
   ```
   (`*.local.yaml` is gitignored.)
2. In the `*.local.yaml` copies, replace every `REPLACE_WITH_...`:
   - `REPLACE_WITH_YOUR_REGISTRY/dssat-agent:latest` — your image (prefer a
     pinned tag over `latest`).
   - `REPLACE_WITH_YOUR_NODE_HOSTNAME` — pin to your GPU/storage nodes, or delete
     the whole `affinity` block to schedule anywhere.
   - `REPLACE_WITH_YOUR_NAMESPACE` / `REPLACE_WITH_YOUR_HOST` (ingress).
3. Create the referenced Secrets in your namespace:
   - `earthrise-agent-secrets` — app env (from a filled-in `config.env`; **must
     NOT set `DEBUG`** so prod keeps transport security on).
   - `ssh-secret` (`dssat_key.pem`) and `earthrise-agent-tunnel-secrets` if you
     use the tunnel.
4. `kubectl apply -f deployment.local.yaml -f ingress.local.yaml -f ollama_service.yaml`

## Files
| File | Tracked | Purpose |
|------|---------|---------|
| `deployment.yml` | yes (template) | Deployment + PVC venv-builder pattern |
| `ingress.yaml` | yes (template) | HAProxy ingress |
| `ollama_service.yaml` | yes | Ollama service |
| `config.env` | **no** (gitignored) | real env/secrets |
| `*.local.yaml` | **no** (gitignored) | your filled-in manifests |

## Knowledge documents (RAG corpus)

The corpus (`dssat_chat_agent/data/documents/`, ~285 MB) is **not baked into the
image** (`.dockerignore`). Provide it at runtime on the PVC and point
`KNOWLEDGE_DOCUMENTS_DIR` at it (e.g. upload to `/earthrise/app/dssat_chat_agent/
data/documents`, or set `KNOWLEDGE_DOCUMENTS_DIR=/data/dssat-docs` and mount
there). Then run `python manage.py ingest_documents`. In local dev,
`docker-compose.yml` bind-mounts the host folder to the same in-container path,
so no override is needed.
