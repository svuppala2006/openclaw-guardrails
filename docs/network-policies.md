# Kubernetes NetworkPolicies: Ingress Isolation

## Overview

NetworkPolicies enforce pod-level ingress isolation in the `suhruth-test` namespace on the NERC MGHPCC OpenShift cluster. By default, OpenShift allows all pod-to-pod communication within a namespace and all external traffic to reach any exposed route. The policies documented here replace that permissive default with a deny-all baseline, then selectively allow only the traffic flows that the system requires.

These are namespace-scoped resources applied directly via `oc apply` and are managed in this repository.

## Why Ingress Isolation Matters

Without ingress isolation, every guardrails service that has an OpenShift Route is reachable from the public internet. This means anyone can call the guardrails orchestrator or NeMo Guardrails directly, bypassing the OpenClaw gateway entirely. That defeats the purpose of having an authenticated gateway in front of the AI agent stack.

Ingress isolation ensures:

- Guardrails services are only reachable through the OpenClaw gateway, which enforces authentication (OAuth proxy) and authorization.
- The attack surface is reduced to a single ingress point (the OpenClaw route).
- Lateral movement within the namespace is restricted to explicitly allowed paths.

## Traffic Flow Diagram

```
                    Internet
                       |
                       v
              +------------------+
              | OpenShift Router  |
              | (ingress namespace)|
              +------------------+
                       |
          (allow-openclaw-from-router)
          ports 18789, 8443
                       |
                       v
              +------------------+
              |    OpenClaw      |
              |  (app: openclaw) |
              +------------------+
                  |            |
 (allow-guardrails-from-openclaw)  (allow-nemo-from-openclaw)
 ports 8032, 8034, 8080            ports 80, 8000
                  |            |
                  v            v
       +----------------+  +-------------------------+
       | TrustyAI       |  | NeMo Guardrails         |
       | Guardrails     |  | (app: openclaw-nemo-     |
       | (app: openclaw-|  |  guardrails)             |
       |  guardrails)   |  +-------------------------+
       +----------------+

  X--- Internet ---> guardrails pods   (BLOCKED by default-deny-ingress)
  X--- Other pods -> guardrails pods   (BLOCKED unless app: openclaw)
```

## Policies

### 1. default-deny-ingress

Blocks all inbound traffic to every pod in the namespace. This is the foundation: every other policy is an exception carved out of this deny-all baseline.

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
  namespace: suhruth-test
spec:
  podSelector: {}
  policyTypes:
    - Ingress
```

- `podSelector: {}` matches all pods in the namespace.
- `policyTypes: [Ingress]` declares this policy governs ingress traffic.
- No `ingress` rules are defined, which means no ingress traffic is permitted.

### 2. allow-openclaw-from-router

Allows the OpenShift router (HAProxy ingress controller) to reach the OpenClaw pods. Without this, the OpenClaw Route would stop working.

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-openclaw-from-router
  namespace: suhruth-test
spec:
  podSelector:
    matchLabels:
      app: openclaw
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              network.openshift.io/policy-group: ingress
      ports:
        - port: 18789
          protocol: TCP
        - port: 8443
          protocol: TCP
  policyTypes:
    - Ingress
```

- **Target pods:** `app: openclaw`
- **Source:** Any pod in a namespace labeled `network.openshift.io/policy-group: ingress` (the OpenShift router namespace).
- **Ports:** 18789 (OpenClaw gateway) and 8443 (OAuth proxy).

### 3. allow-guardrails-from-openclaw

Allows OpenClaw pods to reach the TrustyAI guardrails orchestrator.

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-guardrails-from-openclaw
  namespace: suhruth-test
spec:
  podSelector:
    matchLabels:
      app: openclaw-guardrails
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: openclaw
      ports:
        - port: 8032
          protocol: TCP
        - port: 8034
          protocol: TCP
        - port: 8080
          protocol: TCP
  policyTypes:
    - Ingress
```

- **Target pods:** `app: openclaw-guardrails`
- **Source:** Pods with `app: openclaw` in the same namespace.
- **Ports:** 8032, 8034 (guardrails orchestrator endpoints), 8080 (health/metrics).

### 4. allow-nemo-from-openclaw

Allows OpenClaw pods to reach the NeMo Guardrails service.

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-nemo-from-openclaw
  namespace: suhruth-test
spec:
  podSelector:
    matchLabels:
      app: openclaw-nemo-guardrails
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: openclaw
      ports:
        - port: 80
          protocol: TCP
        - port: 8000
          protocol: TCP
  policyTypes:
    - Ingress
```

- **Target pods:** `app: openclaw-nemo-guardrails`
- **Source:** Pods with `app: openclaw` in the same namespace.
- **Ports:** 80, 8000 (NeMo Guardrails HTTP endpoints).

## Key Effects

**Guardrails routes are blocked from external access.** The Routes for `openclaw-guardrails-*` and `openclaw-nemo-guardrails` still exist in OpenShift, but the NetworkPolicies prevent the router from delivering traffic to those pods. Only the OpenClaw gateway (via its pod-to-pod connections) can reach them.

**Kubelet health probes still work.** The kubelet runs on the node's host network, not through the pod network. Kubernetes NetworkPolicies only filter pod-to-pod traffic via the CNI, so liveness and readiness probes from the kubelet bypass these policies entirely.

**NeMo sidecar architecture is unaffected.** The OpenClaw gateway communicates with NeMo Guardrails via its localhost sidecar container (`NEMO_GUARDRAILS_URL=http://localhost:8000`). Localhost traffic within a pod does not traverse the CNI and is not subject to NetworkPolicies. The standalone NeMo pod's ingress allowance (policy 4) exists for completeness but is not exercised in the current sidecar-based architecture. Note that even if the gateway attempted to reach the standalone NeMo pod via the pod network, the AdminNetworkPolicy egress rules would block it (no port 80 or 8000 egress is allowed for the openclaw pods).

## Verification

### List all policies in the namespace

```bash
oc get networkpolicies -n suhruth-test
```

Expected output:

```
NAME                            POD-SELECTOR                    AGE
default-deny-ingress            <none>                          ...
allow-openclaw-from-router      app=openclaw                    ...
allow-guardrails-from-openclaw  app=openclaw-guardrails         ...
allow-nemo-from-openclaw        app=openclaw-nemo-guardrails    ...
```

### Inspect a specific policy

```bash
oc describe networkpolicy allow-openclaw-from-router -n suhruth-test
```

### Verify guardrails routes are blocked

From outside the cluster, attempt to reach a guardrails route:

```bash
curl -v https://openclaw-guardrails-suhruth-test.apps.shift.nerc.mghpcc.org/
```

This should time out or return a connection error, confirming the NetworkPolicy is blocking router-to-guardrails traffic.

### Verify OpenClaw route still works

```bash
curl -v https://openclaw-suhruth-test.apps.shift.nerc.mghpcc.org/
```

This should return a response (redirect to OAuth or a gateway response), confirming the router-to-openclaw path is open.

### Verify pod-to-pod connectivity

From within the OpenClaw pod, test connectivity to the guardrails service:

```bash
oc exec -n suhruth-test deploy/openclaw -- curl -s http://openclaw-guardrails:8080/health
```

This should succeed, confirming the allow-guardrails-from-openclaw policy is working.

## Layering with Other Security Controls

NetworkPolicies handle **ingress** isolation within the namespace. They work alongside two other controls that handle **egress**:

- **AdminNetworkPolicy** (`restrict-suhruth-test-egress`): Controls which egress traffic the OpenClaw pods can initiate. See [admin-network-policy.md](admin-network-policy.md).
- **EgressFirewall**: Controls which external domains pods in the namespace can reach. See [egress-firewall.md](egress-firewall.md).

Together, these three layers enforce a least-privilege network posture: only the minimum required ingress and egress paths are open.
