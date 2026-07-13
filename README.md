# OpenClaw Guardrails

Security stack for OpenClaw on OpenShift, implementing defense in depth across inference guardrails, PII detection, network isolation, and egress control.

## Architecture

```
                          External Users
                               |
                          OpenShift Router
                               |
                               v
  ┌────────────────────────────────────────────────────────────┐
  │  OpenClaw Pod                                              │
  │  ┌────────────┐  ┌─────────────────┐  ┌────────────────┐  │
  │  │ OAuth      │  │ Gateway         │  │ NeMo           │  │
  │  │ Proxy      │─>│ (port 18789)    │─>│ Guardrails     │  │
  │  │ (port 8443)│  │                 │  │ Sidecar        │  │
  │  └────────────┘  │                 │  │ (port 8000)    │  │
  │                  │                 │  └───────┬────────┘  │
  │                  │                 │          │ if safe    │
  │                  │                 │          v            │
  │                  │                 │  ┌────────────────┐  │
  │                  │                 │  │ LiteLLM        │──┼──> Vertex AI (Claude)
  │                  │                 │  │ (port 4000)    │  │
  │                  └────────┬────────┘  └────────────────┘  │
  └───────────────────────────┼────────────────────────────────┘
                              │
               ┌──────────────┼──────────────┐
               v                             v
  ┌──────────────────────┐      ┌──────────────────────┐
  │ TrustyAI Guardrails  │      │ NeMo Guardrails      │
  │ Orchestrator         │      │ (standalone)          │
  │ (ports 8032/8034)    │      │ (port 80 -> 8000)    │
  │ + Built-in Detector  │      └──────────────────────┘
  │   (port 8080)        │
  └──────────────────────┘
```

## Security Layers

| Layer | Component | What It Does | Status |
|-------|-----------|-------------|--------|
| Inference guardrails | [NeMo Guardrails](docs/nemo-guardrails.md) | Blocks jailbreaks, prompt injection, abusive language, PII in inputs/outputs | Deployed |
| Content safety | [TrustyAI Orchestrator](docs/trustyai-orchestrator.md) | Built-in PII detector, content safety classification | Deployed |
| PII detection | [Presidio](docs/presidio-pii-detection.md) | Standalone PII detection via LiteLLM hooks (reference) | Reference |
| Ingress isolation | [NetworkPolicies](docs/network-policies.md) | Default-deny ingress, pod-level traffic segmentation | Deployed |
| Egress control | [AdminNetworkPolicy](docs/admin-network-policy.md) | Pod-level egress restrictions (DNS, K8s API, HTTPS only) | Deployed |
| Domain filtering | [EgressFirewall](docs/egress-firewall.md) | DNS-based allowlist for external destinations | Deployed |
| Secrets management | [HashiCorp Vault](docs/future-work.md#hashicorp-vault) | Encrypted, audited, auto-rotating credentials | Planned |
| Agent sandboxing | [NVIDIA OpenShell](docs/future-work.md#nvidia-openshell) | Kernel-level isolation for agent code execution | Planned |
| Tool governance | [MCP Gateway](docs/future-work.md#mcp-gateway) | Identity-based tool filtering for MCP servers | Planned |

## Documentation

- [Architecture Overview](docs/architecture.md) -- full security stack design and traffic flows
- [NeMo Guardrails](docs/nemo-guardrails.md) -- LLM input/output guardrails via sidecar proxy
- [TrustyAI Orchestrator](docs/trustyai-orchestrator.md) -- content safety orchestration with built-in detectors
- [Presidio PII Detection](docs/presidio-pii-detection.md) -- standalone PII detection reference
- [Network Policies](docs/network-policies.md) -- Kubernetes ingress isolation
- [Admin Network Policy](docs/admin-network-policy.md) -- cluster-level egress control
- [Egress Firewall](docs/egress-firewall.md) -- DNS-based domain filtering
- [Future Work](docs/future-work.md) -- Vault, OpenShell, MCP Gateway plans

## Repository Structure

```
openclaw-guardrails/
├── README.md
├── docs/                            # Documentation for each security layer
│   ├── architecture.md
│   ├── nemo-guardrails.md
│   ├── trustyai-orchestrator.md
│   ├── presidio-pii-detection.md
│   ├── network-policies.md
│   ├── admin-network-policy.md
│   ├── egress-firewall.md
│   └── future-work.md
├── configs/                         # Template configs (use placeholders, not live values)
│   ├── nemo-guardrails/             # NeMo Guardrails + LiteLLM + proxy configs
│   ├── trustyai/                    # GuardrailsOrchestrator CR + config
│   ├── network-policies/            # Ingress NetworkPolicy templates
│   └── egress/                      # AdminNetworkPolicy + EgressFirewall templates
└── proxy/
    └── proxy.py                     # OpenAI-compatible NeMo Guardrails proxy
```

## Prerequisites

- OpenShift cluster with:
  - TrustyAI operator installed (via Open Data Hub / Red Hat OpenShift AI)
  - OVN-Kubernetes network plugin (for EgressFirewall support)
- An OpenClaw deployment with LiteLLM sidecar
- A model provider (e.g., Claude on Vertex AI via GCP)
- `oc` CLI authenticated to the cluster

## Quick Start

1. Deploy NeMo Guardrails as a sidecar proxy -- see [NeMo Guardrails](docs/nemo-guardrails.md)
2. Deploy TrustyAI GuardrailsOrchestrator -- see [TrustyAI Orchestrator](docs/trustyai-orchestrator.md)
3. Apply ingress NetworkPolicies -- see [Network Policies](docs/network-policies.md)
4. Request egress controls from cluster admin -- see [Admin Network Policy](docs/admin-network-policy.md) and [Egress Firewall](docs/egress-firewall.md)

## Related Repositories

- [nerc-ocp-config](https://github.com/OCP-on-NERC/nerc-ocp-config) -- cluster-scoped configs (AdminNetworkPolicy, EgressFirewall, TrustyAI CRs) managed via ArgoCD
- [OpenClaw](https://docs.openclaw.ai) -- the AI agent gateway

## References

- [NVIDIA NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/latest/index.html)
- [TrustyAI / Red Hat OpenShift AI](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.3/html/enabling_ai_safety_with_guardrails/)
- [LiteLLM Proxy](https://docs.litellm.ai/docs/simple_proxy)
- [Kubernetes Network Policies](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [OVN EgressFirewall](https://docs.openshift.com/container-platform/latest/networking/ovn_kubernetes_network_provider/configuring-egress-firewall-ovn.html)
- [AdminNetworkPolicy (KEP-2091)](https://network-policy-api.sigs.k8s.io/api-overview/)
