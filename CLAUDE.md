# OpenClaw Guardrails

This repo documents the security stack for OpenClaw (AI agent gateway) on OpenShift at the NERC MGHPCC cluster.

## Project context

- **Cluster:** api.ocp-test.nerc.mghpcc.org:6443, namespace: `suhruth-test`
- **User:** svuppala2006
- **OpenClaw pod:** 4 sidecar containers — oauth-proxy (8443), gateway (18789), litellm (4000), nemo-guardrails (8000)
- **Model provider:** Claude on Vertex AI (us-east5, GCP project itpc-gcp-octo-eng-claude) via LiteLLM proxy
- **Cluster-scoped resources** (AdminNetworkPolicy, EgressFirewall, TrustyAI CRs) are managed in [nerc-ocp-config](https://github.com/OCP-on-NERC/nerc-ocp-config) via ArgoCD — not this repo

## Security layers (current state)

| Layer | Status | Notes |
|-------|--------|-------|
| NeMo Guardrails | Deployed | Sidecar proxy, self-check input/output via Colang |
| TrustyAI GuardrailsOrchestrator | Deployed | Built-in PII detector on ports 8032/8034/8080 |
| Ingress NetworkPolicies | Deployed | 4 policies: default-deny + 3 allow rules |
| AdminNetworkPolicy | Deployed | 6 egress rules, targets app=openclaw only |
| EgressFirewall | Deployed | DNS allowlist, wildcard fix merged (PR #963) |
| OTEL Tracing (MLflow) | Deployed | LiteLLM → OTEL Collector (gRPC :8080) → MLflow; traces visible in UI |
| Secrets management (ESO/Vault) | Planned | Next priority |
| OpenShell agent sandboxing | Planned | Per-session sandbox model, needs privileged SCC |
| MCP Gateway | Planned | Awaiting GA release |

## Repo structure

- `docs/` — documentation for each security layer
- `configs/` — template YAMLs with `<PLACEHOLDER>` values (no real credentials)
- `proxy/` — NeMo Guardrails OpenAI-compatible proxy
- Live cluster configs are in nerc-ocp-config, not here

## Key constraints

- No cluster-admin access — cannot install operators, create ClusterRoleBindings, or grant SCCs
- External Secrets Operator (ESO) is already installed cluster-wide
- OVN-Kubernetes does NOT support wildcard DNS in EgressFirewall rules
- AdminNetworkPolicy blocks egress from openclaw pods on all ports except 443, 8443, 8032, 8034, 8080, 53, 5353, 6443

## Working with this repo

- Template configs use placeholders like `<YOUR_NAMESPACE>`, `<CLUSTER_DNS_IP>`, `<VERTEX_AI_REGION>` — never commit real credentials
- When documenting new security work, update both the relevant `docs/` file and the security layers table in `README.md`
- This repo also documents failed experiments and things that didn't work out — ask the user if new attempts should be added
- Use `oc` CLI to verify live cluster state before documenting; don't assume configs match what's deployed
