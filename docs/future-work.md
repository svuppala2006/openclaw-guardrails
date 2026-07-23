# Future Work: Planned Security Enhancements

## 1. HashiCorp Vault -- Secrets Management (High Priority)

### Problem

OpenClaw currently stores secrets as base64-encoded Kubernetes Secrets. This has several weaknesses:

- Base64 is encoding, not encryption. Anyone with namespace read access can decode them.
- No audit trail for secret access. There is no record of which pod read which secret or when.
- No automatic rotation. Secrets remain static until manually updated.
- Secrets are stored in etcd, which may or may not be encrypted at rest depending on cluster configuration.

The following 6 secrets are currently stored as plain Kubernetes Secrets:

| Secret | Purpose |
|--------|---------|
| `OPENCLAW_GATEWAY_TOKEN` | Gateway authentication token |
| `TELEGRAM_BOT_TOKEN` | Telegram bot integration |
| `SSH_IDENTITY` | SSH private key for Git operations |
| `SSH_CERTIFICATE` | SSH certificate for Git operations |
| `SSH_KNOWN_HOSTS` | SSH known hosts for Git operations |
| `LITELLM_MASTER_KEY` | LiteLLM proxy admin key |

### Ideal Architecture

Deploy Vault in HA mode within the cluster:

- **3 Vault server nodes** with Raft integrated storage in a dedicated namespace (e.g., `vault`).
- **Vault Secrets Operator (VSO)** running as a controller that watches `VaultStaticSecret` and `VaultDynamicSecret` CRs, syncing their values into Kubernetes Secrets automatically.
- **Kubernetes auth method** for pod authentication. Pods present their service account JWT to Vault, which validates it against the Kubernetes API. No static credentials are distributed to pods.

### OpenClaw Integration

OpenClaw's credential management supports an exec provider pattern. The integration would use:

```
vault kv get -field=value secret/openclaw/<secret-name>
```

This would be configured as the exec provider command, allowing OpenClaw to fetch secrets directly from Vault at runtime rather than reading them from environment variables populated by Kubernetes Secrets.

### Blockers

Vault's Helm chart requires capabilities that are restricted on the NERC cluster:

- **anyuid SCC:** Vault containers need to run as a specific non-root UID, which requires the `anyuid` Security Context Constraint.
- **ClusterRoleBindings:** The Kubernetes auth method requires Vault to call the TokenReview API, which needs a ClusterRoleBinding. This requires cluster-admin permissions that namespace users do not have.

These constraints make an in-cluster Vault deployment difficult without cluster administrator assistance.

### Alternative: External Secrets Operator (ESO)

The External Secrets Operator (ESO) is already installed cluster-wide on the NERC cluster. This provides a viable alternative path:

1. Connect to an **external Vault instance** (hosted outside the cluster, avoiding SCC and ClusterRoleBinding issues).
2. Create a `SecretStore` CR in the namespace pointing to the external Vault.
3. Create `ExternalSecret` CRs for each secret. ESO will fetch values from Vault and create/update the corresponding Kubernetes Secrets automatically.

This approach gives most of the benefits of Vault (encrypted storage, audit logging, rotation) without requiring any elevated cluster permissions. The external Vault instance can be managed independently.

## 2. ~~NVIDIA OpenShell -- Sandboxed Agent Runtime~~ (Deployed)

OpenShell agent sandboxing has been deployed on the AWS OpenShift cluster. See [openshell.md](openshell.md) for full documentation including setup steps, security isolation test results, and troubleshooting notes.

## 3. MCP Gateway -- Tool Governance (Medium Priority)

### Problem

The Model Context Protocol (MCP) defines how agents discover and invoke tools. Without a governance layer, any agent can invoke any available MCP tool. There is no mechanism to restrict which tools a specific agent or user can call, or to enforce authorization policies on tool invocations.

### What MCP Gateway Provides

MCP Gateway is an Envoy-based proxy that sits between agents and MCP tool servers. It provides:

- **Identity-based tool filtering:** Tool availability is determined by claims in the caller's authentication token. Different users or agents see different tool sets.
- **OAuth2 token exchange:** The gateway exchanges the caller's token for a scoped, per-backend token before forwarding the request to the tool server. This ensures tool servers receive only the minimum required credentials.
- **Authorization via Kuadrant AuthPolicy:** Policy decisions are made by Authorino (for authentication) and OPA (for fine-grained authorization rules), integrated through Kuadrant's AuthPolicy CRD.

### Current Status

MCP Gateway is currently in developer preview. It is not yet production-ready, but the architecture aligns well with OpenClaw's needs:

- OpenClaw already uses MCP for tool integration.
- Kuadrant is available on OpenShift as a supported operator.
- Envoy is a proven proxy with extensive OpenShift deployment experience.

### Integration Path

When MCP Gateway reaches general availability:

1. Deploy the Envoy-based gateway as a sidecar or standalone service.
2. Configure AuthPolicy CRs to define per-tool authorization rules.
3. Update OpenClaw's MCP client configuration to route through the gateway instead of connecting directly to tool servers.

## 4. Observability -- End-to-End Tracing (Important)

### Problem

The current deployment has limited visibility into the request lifecycle. When a request flows through the gateway, guardrails, LLM proxy, and back, there is no unified trace that connects these steps. This makes it difficult to:

- Diagnose latency issues (which component is slow?).
- Detect prompt injection attempts that guardrails miss (what exactly was sent to the LLM after guardrails processing?).
- Audit agent behavior (which tools were called, what decisions were made, what the LLM returned).

### Proposed Architecture

Deploy end-to-end OpenTelemetry (OTEL) tracing with MLflow as the trace backend:

- **Trace every request** through the full pipeline: gateway entry, guardrail input scan, LLM call, guardrail output scan, response to client.
- **Trace tool calls** with their arguments and results.
- **Trace guardrail decisions** including which rules triggered, confidence scores, and allow/deny outcomes.
- **Trace LLM interactions** including the full prompt (after guardrails processing), model response, token counts, and latency.

### Current State

The guardrails orchestrator already has partial OTEL configuration:

```
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
```

This indicates the orchestrator is prepared to export traces via gRPC, but a collector and backend have not yet been deployed.

### Why This Matters for Security

Observability is a security control, not just an operational convenience. Guardrails are probabilistic -- they catch most prompt injection attempts but not all. End-to-end tracing provides:

- **Detection of guardrail bypasses:** If a prompt injection passes the input guardrail, the trace will show the malicious content being sent to the LLM. Automated analysis of traces can flag these cases.
- **Forensic capability:** After an incident, traces provide a complete record of what happened, in what order, and with what data.
- **Guardrail tuning data:** Traces of false positives and false negatives provide the data needed to improve guardrail rules and thresholds.

### Implementation Steps

1. Deploy an OpenTelemetry Collector in the namespace.
2. Deploy MLflow as the trace backend (or connect to an existing instance).
3. Configure each component (gateway, guardrails orchestrator, LiteLLM) to export traces to the collector.
4. Set up trace-based alerts for anomalous patterns (e.g., guardrail overrides, unusual tool call sequences).
