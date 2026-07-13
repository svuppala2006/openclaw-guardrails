# AdminNetworkPolicy: Egress Restriction

## Overview

The AdminNetworkPolicy (ANP) `restrict-suhruth-test-egress` is a cluster-scoped resource that controls egress traffic from OpenClaw pods in the `suhruth-test` namespace. Unlike namespace-scoped NetworkPolicies, ANPs are managed by cluster administrators and cannot be overridden or modified by namespace users.

This ANP is managed via ArgoCD in the `nerc-ocp-config` repository. It is not part of this repository and cannot be applied with `oc apply` from a namespace-scoped account.

## What is AdminNetworkPolicy?

AdminNetworkPolicy is a Kubernetes resource defined by the Network Policy API (KEP-2091), supported in OpenShift 4.14+. Key differences from standard NetworkPolicy:

- **Cluster-scoped:** Not bound to a single namespace. Cluster admins define them; namespace users cannot modify or delete them.
- **Priority-ordered:** Each ANP has a numeric priority. Lower numbers are evaluated first. Rules within an ANP are evaluated in order.
- **Three actions:** Rules can `Allow`, `Deny`, or `Pass` (delegate to namespace NetworkPolicies). Standard NetworkPolicies only have allow semantics.
- **Evaluated before NetworkPolicies:** ANP rules take precedence. A `Deny` in an ANP cannot be overridden by a namespace NetworkPolicy.

## Why It Exists

Standard NetworkPolicies are namespace-scoped, meaning anyone with write access to the namespace can modify or delete them. For a shared cluster like NERC MGHPCC, the cluster administrators need a mechanism to enforce network boundaries that namespace users cannot circumvent.

The ANP ensures that even if a namespace user deletes all NetworkPolicies, the OpenClaw pods still cannot make arbitrary outbound connections.

## Subject

The ANP applies only to pods matching **both** conditions:

- **Namespace:** `suhruth-test` (matched by `kubernetes.io/metadata.name`)
- **Pod label:** `app: openclaw`

Other pods in the namespace (guardrails, NeMo, etc.) are not subject to this ANP.

## Priority

**Priority: 50**

ANPs are evaluated in ascending priority order (lower number = higher precedence). Priority 50 is in the mid-range, allowing cluster admins to insert higher-priority policies if needed.

## Rules

The ANP defines six egress rules, evaluated in order. The first matching rule determines the action.

### Rule 1: allow-dns

**Action:** Allow

Permits DNS resolution to the cluster DNS service.

| Field | Value |
|-------|-------|
| Destination | Namespace: `openshift-dns` |
| Ports | 53 (UDP), 53 (TCP), 5353 (UDP), 5353 (TCP) |

Without this rule, pods cannot resolve any DNS names, breaking all service discovery and external hostname resolution.

### Rule 2: allow-kube-api

**Action:** Allow

Permits communication with the Kubernetes API server.

| Field | Value |
|-------|-------|
| Destination | CIDR: 172.30.0.1/32, 10.30.8.23/32, 10.30.8.24/32, 10.30.8.25/32 |
| Ports | 6443 (TCP), 443 (TCP) |

These IPs are the Kubernetes API VIP (172.30.0.1) and the control plane nodes (10.30.8.23-25). This is required for pods that use the Kubernetes API (e.g., for service account token validation, leader election, or operator functionality).

### Rule 3: allow-local-llm

**Action:** Allow

Permits OpenClaw to reach the LiteLLM proxy service within the namespace.

| Field | Value |
|-------|-------|
| Destination | Pods in `suhruth-test` namespace (same namespace) |
| Ports | 8443 (TCP) |

LiteLLM is the LLM proxy that routes requests to Vertex AI. The OpenClaw gateway connects to it on port 8443 within the cluster.

### Rule 4: allow-trustyai-guardrails

**Action:** Allow

Permits OpenClaw to reach the TrustyAI guardrails orchestrator within the namespace.

| Field | Value |
|-------|-------|
| Destination | Pods in `suhruth-test` namespace (same namespace) |
| Ports | 8032 (TCP), 8034 (TCP), 8080 (TCP) |

These are the guardrails orchestrator service ports for input scanning (8032), output scanning (8034), and health checks (8080).

### Rule 5: allow-https-egress

**Action:** Allow

Permits outbound HTTPS to any external IP address.

| Field | Value |
|-------|-------|
| Destination | CIDR: 0.0.0.0/0 |
| Ports | 443 (TCP) |

This allows the pod to reach external HTTPS services such as Vertex AI, Google OAuth, and GitHub. The broad CIDR (0.0.0.0/0) is intentional at the ANP layer -- the EgressFirewall further restricts which specific domains can be reached on port 443.

### Rule 6: deny-all-other-egress

**Action:** Deny

Blocks all remaining egress traffic.

| Field | Value |
|-------|-------|
| Destination | CIDR: 0.0.0.0/0 and ::/0 |
| Ports | All |

This is the catch-all deny rule. Any egress traffic that did not match rules 1-5 is dropped. This includes:

- Non-HTTPS traffic to external hosts (e.g., HTTP on port 80, SSH on port 22).
- Traffic to internal pods on ports not explicitly allowed.
- All IPv6 traffic.

## Interaction with Other Network Controls

### Layering Model

The three network controls operate at different layers and complement each other:

```
  Egress path from an OpenClaw pod:

  1. AdminNetworkPolicy (ANP)
     - Cluster-scoped, admin-controlled
     - Controls WHICH PORTS and DESTINATIONS the pod can reach
     - Evaluated first (priority-based)
     |
     v
  2. EgressFirewall
     - Namespace-scoped, admin-controlled (ArgoCD)
     - Controls WHICH DOMAINS can be reached on allowed ports
     - Applies to all pods in the namespace
     |
     v
  3. NetworkPolicy (ingress only in our config)
     - Namespace-scoped, user-controlled
     - Controls WHO CAN REACH each pod (ingress isolation)
     - Does not affect egress in our configuration
```

### ANP vs. EgressFirewall

These two controls work together for egress:

- The **ANP** allows HTTPS (port 443) to 0.0.0.0/0 for openclaw pods. This is a broad port-level allow.
- The **EgressFirewall** restricts which domains can be reached on that port. Only `oauth2.googleapis.com`, `us-east5-aiplatform.googleapis.com`, `accounts.google.com`, `github.com`, and `api.github.com` are permitted.

The result: OpenClaw pods can make HTTPS calls, but only to a specific allowlist of domains.

### ANP vs. NetworkPolicies

- The **ANP** controls egress from openclaw pods.
- The **NetworkPolicies** control ingress to all pods.
- They operate on different traffic directions and do not conflict.
- Because ANPs are evaluated before NetworkPolicies, a `Deny` in the ANP cannot be overridden by a namespace-scoped NetworkPolicy.

## Notable Implications

**No port 80/8000 egress.** The ANP does not allow egress on ports 80 or 8000 to any destination. This means the OpenClaw gateway cannot reach the standalone NeMo Guardrails pod via the pod network on those ports. This is a non-issue because the gateway uses a NeMo sidecar container at `localhost:8000`, which does not traverse the pod network.

**Only openclaw pods are restricted.** The ANP targets `app: openclaw` pods specifically. Other pods in the namespace (guardrails orchestrator, NeMo, LiteLLM) are not subject to these egress restrictions. They are still subject to the namespace-wide EgressFirewall.

**Managed externally.** Changes to this ANP must be made in the `nerc-ocp-config` repository and deployed via ArgoCD. To request changes, open a PR in that repository.

## Viewing the ANP

Since AdminNetworkPolicies are cluster-scoped, they require cluster-level permissions to view:

```bash
oc get adminnetworkpolicies
oc describe adminnetworkpolicy restrict-suhruth-test-egress
```

If you do not have cluster-admin access, ask a NERC cluster administrator to retrieve the current configuration.
