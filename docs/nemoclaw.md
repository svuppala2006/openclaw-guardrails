# NemoClaw: Self-Sandboxed OpenClaw

## Overview

NemoClaw is an alternative deployment pattern where the entire OpenClaw gateway runs inside a sandboxed container. Instead of delegating agent code execution to external OpenShell sandbox pods, the OpenClaw process and all agent-generated code run directly inside a single restricted pod. The container itself is the isolation boundary.

This trades per-session isolation (where each agent session gets its own sandbox) for architectural simplicity — one pod, no external sandbox infrastructure. It is appropriate for experimentation, single-user workloads, or environments where deploying the full OpenShell stack is not practical.

## Architecture Comparison

### Standard OpenShell Architecture (Per-Session Isolation)

```
  OpenClaw Namespace (<user>-claw)           OpenShell Namespace (openshell-<user>)
  ┌─────────────────────────────┐            ┌──────────────────────────────────┐
  │  OpenClaw Pod               │            │  OpenShell Gateway Pod           │
  │  ┌────────────────────┐     │   HTTP     │  ┌────────────────────────┐     │
  │  │ Gateway             │────┼───:8080────┼─>│ openshell-<user>       │     │
  │  │ + openshell-sandbox │    │            │  │ (manages sandbox pods) │     │
  │  │   plugin            │    │            │  └────────────┬───────────┘     │
  │  └────────────────────┘     │            │               │                 │
  └─────────────────────────────┘            │      ┌────────┴────────┐        │
                                             │      v                 v        │
                                             │  ┌────────┐      ┌────────┐    │
                                             │  │Sandbox │      │Sandbox │    │
                                             │  │ Pod 1  │      │ Pod 2  │    │
                                             │  │(agent  │      │(agent  │    │
                                             │  │session)│      │session)│    │
                                             │  └────────┘      └────────┘    │
                                             └──────────────────────────────────┘
```

### NemoClaw Architecture (Self-Sandboxed)

```
  OpenClaw Namespace (<user>-claw)
  ┌──────────────────────────────────────────┐
  │  NemoClaw Pod (restricted-v2 SCC)        │
  │  ┌────────────────────────────────────┐  │
  │  │ Gateway                            │  │
  │  │ + agent code executes directly     │  │
  │  │   (no external sandbox)            │  │
  │  │                                    │  │
  │  │ Restrictions:                      │  │
  │  │  • All capabilities dropped        │  │
  │  │  • Non-root (assigned UID)         │  │
  │  │  • Seccomp RuntimeDefault          │  │
  │  │  • SELinux MCS isolation           │  │
  │  │  • CPU/memory limits enforced      │  │
  │  │  • Network egress: proxy + DNS     │  │
  │  └────────────────────────────────────┘  │
  └──────────────────────────────────────────┘
```

## Security Posture

The NemoClaw pod runs under OpenShift's `restricted-v2` SCC with additional resource constraints applied via the Claw CR:

| Layer | Restriction |
|-------|------------|
| SCC | `restricted-v2` (OpenShift enforced) |
| User | Non-root (namespace-assigned UID) |
| Capabilities | All dropped, `allowPrivilegeEscalation: false` |
| Seccomp | `RuntimeDefault` filter |
| SELinux | MCS label isolation (e.g., `s0:c37,c19`) |
| CPU | Request 250m, limit 1 core |
| Memory | Request 512Mi, limit 2Gi |
| Network egress | Proxy pod + DNS only (operator-managed NetworkPolicy) |
| Network ingress | Controlled by operator-managed NetworkPolicy |
| Process isolation | `NoNewPrivs: 1`, zero effective/permitted/bounding capabilities |

### What This Protects Against

- **Resource exhaustion:** CPU and memory limits prevent runaway agent code from starving the cluster.
- **Privilege escalation:** All capabilities dropped, no new privileges allowed, seccomp filters active.
- **Network breakout:** Agent code cannot reach the internet directly — only the proxy pod (which handles API calls to the model provider) and DNS.
- **Cross-namespace access:** SELinux MCS labels and network policies isolate the pod from other namespaces.

### What This Does NOT Protect Against

- **Cross-session interference:** All agent sessions share the same process. A crash or resource exhaustion affects all sessions in the pod.
- **Gateway credential exposure:** Agent-generated code runs in the same container as the gateway process and can access its environment variables and mounted secrets.
- **Filesystem writes:** The root filesystem is writable (the claw-operator does not expose `readOnlyRootFilesystem` via the Claw CR). Agent code can write to the container filesystem.

For workloads where these risks matter, use the [standard OpenShell architecture](openshell.md) instead.

## Deployment

### Prerequisites

- An existing Claw namespace with the claw-operator managing it
- A credential Secret for the model provider (can reuse an existing one)
- The OpenClaw image available in the namespace's image registry

### Step 1: Create the Credential Secret

If you already have a Claw instance in the same namespace, copy its credential secret with a new name:

```bash
oc get secret openclaw-<EXISTING_INSTANCE>-anthropic-vertex-gcp \
  -n <YOUR_NAMESPACE> -o json | \
  python3 -c "
import json, sys
s = json.load(sys.stdin)
s['metadata'] = {
    'name': 'openclaw-nemoclaw-anthropic-vertex-gcp',
    'namespace': '<YOUR_NAMESPACE>',
    'labels': {
        'app.kubernetes.io/managed-by': 'openclaw-deployer',
        'openclaw-deployer.redhat.com/instance': 'nemoclaw',
        'openclaw-deployer.redhat.com/provider': 'anthropic-vertex'
    }
}
json.dump(s, sys.stdout)
" | oc apply -f -
```

### Step 2: Create the Claw CR

```bash
oc apply -f configs/openshell/nemoclaw-cr.yaml
```

See [configs/openshell/nemoclaw-cr.yaml](../configs/openshell/nemoclaw-cr.yaml) for the template.

### Step 3: Verify

Wait for the pods to come up and check status:

```bash
# Check CR status
oc get claw nemoclaw -n <YOUR_NAMESPACE> \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status}{"\n"}{end}'

# Expected:
# CredentialsResolved=True
# ProxyConfigured=True
# Ready=True

# Check pods
oc get pods -n <YOUR_NAMESPACE> | grep nemoclaw

# Expected:
# nemoclaw-<hash>         1/1     Running
# nemoclaw-proxy-<hash>   1/1     Running
```

### Step 4: Test

Send a test prompt to verify the agent can execute code directly:

```bash
oc exec deploy/nemoclaw -n <YOUR_NAMESPACE> -c gateway -- \
  openclaw agent --agent default \
  --message "Run echo hello and tell me the result" \
  --json --timeout 30
```

The response should include `"sandbox": {"mode": "off", "sandboxed": false}`, confirming code runs directly in the pod (not delegated to OpenShell).

### Step 5: Verify Security Posture

```bash
# Check SCC
oc get pod -l app=claw,claw.sandbox.redhat.com/instance=nemoclaw \
  -n <YOUR_NAMESPACE> \
  -o jsonpath='{.items[0].metadata.annotations.openshift\.io/scc}'
# Expected: restricted-v2

# Check capabilities
oc exec deploy/nemoclaw -n <YOUR_NAMESPACE> -c gateway -- \
  cat /proc/1/status | grep -E '^(Cap|Seccomp|NoNewPrivs)'
# Expected: all Cap fields 0000000000000000, NoNewPrivs 1, Seccomp 2

# Check resource limits
oc get pod -l app=claw,claw.sandbox.redhat.com/instance=nemoclaw \
  -n <YOUR_NAMESPACE> \
  -o jsonpath='{.items[0].spec.containers[0].resources}'
```

## Coexistence with OpenShell

A NemoClaw instance can run in the same namespace as a standard OpenShell-backed Claw instance. The two are independent:

- The NemoClaw CR does not include the `@openclaw/openshell-sandbox` plugin and has no network egress rules to the OpenShell namespace.
- The existing Claw instance continues to use OpenShell for per-session sandboxing as before.
- Both share the same namespace and image registry but have separate pods, routes, and credentials.

## Limitations

- **No `readOnlyRootFilesystem`:** The claw-operator does not expose this setting via the Claw CR. The gateway container needs writable paths for Node.js and AI tool execution. Adding this would require an operator change.
- **No kernel-level isolation:** Unlike OpenShell sandbox pods (which use init containers to set up kernel hooks and run with privileged SCC), NemoClaw pods run under `restricted-v2` SCC. The isolation is process-level (Linux capabilities, seccomp, SELinux) rather than kernel-level (namespaces, cgroups, gVisor/kata).
- **Single blast radius:** A compromised agent session affects the entire pod — all sessions, the gateway process, and all mounted credentials.
