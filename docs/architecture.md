# OpenClaw Security Architecture

This document describes the defense-in-depth security architecture for OpenClaw, an AI agent gateway running on OpenShift. Multiple security layers work together to protect against prompt injection, data exfiltration, network-based attacks, and unauthorized access.

## Security Layers Overview

The architecture applies defense in depth across five active layers, with three additional layers planned for future deployment.

| Layer | Technology | Purpose | Status |
|-------|-----------|---------|--------|
| 1. Inference guardrails | NeMo Guardrails sidecar + TrustyAI GuardrailsOrchestrator | Block unsafe LLM inputs/outputs | Active |
| 2. PII detection | Presidio via LiteLLM pre/post call hooks | Detect and redact personal data | Active (superseded by built-in detector) |
| 3. Ingress isolation | Kubernetes NetworkPolicies | Restrict pod-to-pod communication | Active |
| 4. Egress control | AdminNetworkPolicy + OVN EgressFirewall | Restrict outbound traffic to known destinations | Active |
| 5. Secrets management | HashiCorp Vault | Centralized secret storage and rotation | Planned |
| 6. Agent sandboxing | NVIDIA OpenShell | Isolate agent tool execution | Planned |
| 7. Tool governance | MCP Gateway | Control which tools agents can invoke | Planned |

### Layer 1: Inference Guardrails

Two complementary guardrail systems intercept LLM traffic:

**NeMo Guardrails sidecar** runs inside the OpenClaw pod as a sidecar container. It intercepts all requests before they reach the LLM and applies `self check input` and `self check output` flows. These use the LLM itself to evaluate whether messages comply with defined safety policies. Blocked messages receive a fixed refusal response and never reach the model provider.

**TrustyAI GuardrailsOrchestrator** runs as a separate pod managed by the TrustyAI operator from Open Data Hub. It provides content safety detection through configurable detectors, including a built-in PII detector that uses text content analysis. The orchestrator exposes gRPC (8032) and HTTP (8034) endpoints, plus a detector endpoint (8080).

### Layer 2: PII Detection

Presidio Analyzer and Anonymizer were deployed as standalone pods to detect six entity types: email addresses, Social Security numbers, credit card numbers, phone numbers, person names, and physical addresses. Integration was achieved through LiteLLM's `pre_call` and `post_call` guardrail hooks.

This layer has been largely superseded by the TrustyAI built-in detector, which provides PII detection without requiring separate pods. The Presidio deployment is retained as a reference for cases where standalone, fine-grained PII detection is needed.

### Layer 3: Ingress Isolation

Kubernetes NetworkPolicies enforce a default-deny ingress posture. Only explicitly allowed traffic paths are permitted between pods. This prevents compromised or rogue pods from reaching the guardrails infrastructure or the LLM proxy directly.

### Layer 4: Egress Control

AdminNetworkPolicy and OVN EgressFirewall restrict outbound traffic from the namespace. Only known-good destinations are allowed, preventing data exfiltration to arbitrary external hosts even if an attacker gains code execution inside a pod.

### Layer 5 (Future): Secrets Management

HashiCorp Vault will provide centralized management for API keys, LiteLLM master keys, and GCP service account credentials. This replaces the current approach of storing secrets in ConfigMaps and environment variables.

### Layer 6 (Future): Agent Sandboxing

NVIDIA OpenShell will provide sandboxed execution environments for AI agent tool invocations, preventing agents from accessing the host filesystem, network, or other pods beyond their authorized scope.

### Layer 7 (Future): Tool Governance

MCP Gateway will control which Model Context Protocol tools an agent can invoke, enforcing tool-level authorization policies and audit logging.

## Architecture Diagram

```
                                    External Traffic
                                          |
                                          v
                                 ┌─────────────────┐
                                 │  OpenShift Router │
                                 │   (HAProxy/TLS)   │
                                 └────────┬──────────┘
                                          |
              ┌───────────────────────────────────────────────────────────────────┐
              │  OpenClaw Pod (4 containers)                                      │
              │                                                                   │
              │  ┌──────────────┐   ┌──────────────┐   ┌───────────────────────┐  │
              │  │  oauth-proxy │   │   gateway     │   │  nemo-guardrails      │  │
              │  │              │──>│  port 18789   │──>│  sidecar              │  │
              │  │              │   │               │   │  port 8000            │  │
              │  └──────────────┘   └──────┬───────┘   │                       │  │
              │                            |           │  1. Check input rail   │  │
              │                            |           │  2. If blocked: refuse │  │
              │                            |           │  3. If safe: forward   │  │
              │                            |           └───────────┬───────────┘  │
              │                            |                       |              │
              │                            |                       v              │
              │                            |           ┌───────────────────────┐  │
              │                            |           │  litellm              │  │
              │                            |           │  port 4000            │──┼──> Vertex AI
              │                            |           │                       │  │    (us-east5)
              │                            |           └───────────────────────┘  │    Claude
              │                            |                                      │
              └────────────────────────────┼──────────────────────────────────────┘
                                           |
                                           | content safety checks
                                           v
              ┌───────────────────────────────────────────────────────────────────┐
              │  Guardrails Orchestrator Pod (2 containers)                       │
              │                                                                   │
              │  ┌───────────────────────────┐   ┌─────────────────────────────┐  │
              │  │  openclaw-guardrails      │   │  built-in-detector          │  │
              │  │  gRPC: port 8032          │   │  port 8080                  │  │
              │  │  HTTP: port 8034          │   │  (text content analysis,    │  │
              │  │                           │   │   PII detection)            │  │
              │  └───────────────────────────┘   └─────────────────────────────┘  │
              │                                                                   │
              └───────────────────────────────────────────────────────────────────┘


              ┌───────────────────────────────────────────────────────────────────┐
              │  NeMo Guardrails Standalone Pod                                   │
              │                                                                   │
              │  ┌───────────────────────────────────────────────────────────────┐ │
              │  │  nemo-guardrails server                                      │ │
              │  │  container port: 8000                                        │ │
              │  │  service port: 80 -> 8000                                    │ │
              │  └───────────────────────────────────────────────────────────────┘ │
              │                                                                   │
              └───────────────────────────────────────────────────────────────────┘
```

## Services

| Service | Ports | Target |
|---------|-------|--------|
| `openclaw` | 18789, 8443 | OpenClaw gateway and oauth-proxy |
| `openclaw-guardrails-service` | 8032, 8034, 8080 | GuardrailsOrchestrator gRPC, HTTP, and built-in detector |
| `openclaw-nemo-guardrails` | 80 (target 8000) | NeMo Guardrails standalone server |
| `openclaw-trustyai-service-tls` | 443 | TrustyAI service with TLS termination |

## Traffic Flow

### Normal Request (safe content)

```
User Browser
  --> OpenShift Router (TLS termination)
    --> oauth-proxy (authentication)
      --> OpenClaw gateway (port 18789)
        --> NeMo Guardrails sidecar (port 8000, input rail check)
          --> [PASS] LiteLLM (port 4000)
            --> Vertex AI us-east5 (Claude)
              <-- response
            <-- NeMo Guardrails sidecar (output rail check)
          <-- [PASS] response returned to gateway
        <-- response returned to user
```

### Blocked Request (unsafe content)

```
User Browser
  --> OpenShift Router
    --> oauth-proxy
      --> OpenClaw gateway (port 18789)
        --> NeMo Guardrails sidecar (port 8000, input rail check)
          --> [BLOCKED] returns "I'm sorry, I can't respond to that."
        <-- refusal returned to user (LLM never called)
```

### Content Safety Check (TrustyAI)

```
OpenClaw gateway (port 18789)
  --> GuardrailsOrchestrator (port 8032 gRPC or 8034 HTTP)
    --> built-in-detector (port 8080, PII/content analysis)
      <-- detection result with confidence score
    <-- orchestrated response (pass/block with detector findings)
  <-- action taken based on orchestrator response
```

## Network Security

### Default-Deny Ingress

A default-deny NetworkPolicy blocks all ingress traffic to pods in the namespace unless explicitly allowed by a more specific policy.

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
spec:
  podSelector: {}
  policyTypes:
    - Ingress
```

### Ingress Allow Rules

Specific NetworkPolicies permit only the required traffic paths:

| Source | Destination | Ports | Purpose |
|--------|------------|-------|---------|
| OpenShift Router (ingress namespace) | OpenClaw pod | 8443 | External user access via oauth-proxy |
| OpenClaw pod | GuardrailsOrchestrator pod | 8032, 8034, 8080 | Content safety checks |
| OpenClaw pod | NeMo Guardrails standalone pod | 8000 | Guardrails evaluation |

All other pod-to-pod ingress traffic within the namespace is denied by the default-deny policy.

### Egress Control: AdminNetworkPolicy

AdminNetworkPolicy rules restrict outbound traffic from the namespace. These are cluster-scoped policies enforced before namespace-scoped NetworkPolicies.

| Rule | Destination | Ports | Action |
|------|------------|-------|--------|
| Allow DNS | kube-system DNS pods | 53 (TCP/UDP) | Allow |
| Allow Kubernetes API | API server | 6443 | Allow |
| Allow local LLM | TrustyAI service | 8443 | Allow |
| Allow guardrails | GuardrailsOrchestrator | 8032, 8034, 8080 | Allow |
| Allow HTTPS | Any | 443 | Allow |
| Deny all else | Any | Any | Deny |

### Egress Control: OVN EgressFirewall

The OVN EgressFirewall provides DNS-based egress filtering, restricting which external hostnames pods can reach. This is the last line of defense against data exfiltration.

| Rule | Destination | Action |
|------|------------|--------|
| Allow | `*.googleapis.com` (OAuth and Vertex AI us-east5) | Allow |
| Allow | `github.com` | Allow |
| Deny | All other external traffic | Deny |

The combination of AdminNetworkPolicy (port-level) and EgressFirewall (DNS-level) ensures that even if an attacker gains code execution inside a pod, they cannot exfiltrate data to arbitrary external hosts. Only traffic to Google APIs (required for Vertex AI model access and OAuth) and GitHub (required for repository access) is permitted.

## References

- [NeMo Guardrails Documentation](https://docs.nvidia.com/nemo/guardrails/latest/index.html)
- [TrustyAI / Open Data Hub](https://opendatahub.io/)
- [Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [OVN-Kubernetes EgressFirewall](https://docs.openshift.com/container-platform/latest/networking/ovn_kubernetes_network_provider/configuring-egress-firewall-ovn.html)
- [AdminNetworkPolicy](https://network-policy-api.sigs.k8s.io/reference/spec/#policy.networking.k8s.io/v1alpha1.AdminNetworkPolicy)
