# NVIDIA OpenShell: Agent Sandboxing

## Overview

OpenShell provides per-session sandboxing for OpenClaw agent code execution. When an agent runs code (tool calls, generated scripts, shell commands), that code executes in a disposable Kubernetes pod rather than inside the OpenClaw gateway pod. Each agent session gets its own sandbox with an isolated filesystem and process space. A compromised or manipulated agent can only damage its own sandbox -- it cannot access the gateway process, other sessions, or credentials stored in the gateway pod.

This is deployed on the AWS OpenShift cluster (`api.claws.bxz4.p1.openshiftapps.com:6443`) using the claw-operator, with OpenShell running in a dedicated namespace (`openshell-<user>`) alongside the OpenClaw namespace (`<user>-claw`).

## Architecture

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

The OpenClaw gateway communicates with the OpenShell gateway over HTTP on port 8080. When an agent session starts, the OpenShell gateway provisions a new sandbox pod. The sandbox pod runs agent-generated code in isolation, with the workspace synced via PVC (mirror mode) or seeded once (remote mode).

## What It Protects Against

Without sandboxing, agent-generated code runs inside the gateway pod with full access to:

- The gateway process and its memory
- All credentials mounted as environment variables or secrets
- The Kubernetes API via the pod's service account
- Other agent sessions running in the same process

With OpenShell sandboxing:

| Attack Vector | Without OpenShell | With OpenShell |
|---|---|---|
| Read gateway credentials | Agent code can read env vars and mounted secrets | Sandbox pod has no access to gateway pod filesystem or env |
| Access Kubernetes API | Agent code can reach the API server via the pod's service account | Sandbox pod's service account has no RBAC permissions beyond its namespace |
| Affect other sessions | All sessions share one process -- a crash or resource exhaustion affects everyone | Each session runs in its own pod; a crash is contained |
| Escape to host | Agent code runs as the gateway user | Sandbox pod runs as non-root (uid 65532 for execution), isolated by kernel namespaces |

## Prerequisites

- OpenShift cluster with cluster-admin access (needed for privileged SCC grants)
- claw-operator managing the OpenClaw deployment
- `oc` and `helm` CLIs
- Agent Sandbox CRDs (`sandboxes.agents.x-k8s.io`) from [kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox)

## Setup

### 1. Install Agent Sandbox CRDs

The Agent Sandbox CRDs are a cluster-wide prerequisite. They define the `Sandbox` custom resource that OpenShell uses to manage sandbox pods.

```bash
oc apply -f https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/manifests/install.yaml
```

### 2. Create the OpenShell namespace

```bash
oc new-project openshell-<YOUR_USER>
```

### 3. Grant privileged SCC to service accounts

OpenShell sandbox pods require the privileged SCC because the init containers run as root (uid 0) to set up kernel-level isolation hooks (network namespaces, filesystem mounts). The main container then drops to uid 65532 for actual code execution.

Three service accounts need the SCC:

```bash
oc adm policy add-scc-to-user privileged \
  system:serviceaccount:openshell-<YOUR_USER>:default

oc adm policy add-scc-to-user privileged \
  system:serviceaccount:openshell-<YOUR_USER>:openshell-<YOUR_USER>

oc adm policy add-scc-to-user privileged \
  system:serviceaccount:openshell-<YOUR_USER>:openshell-<YOUR_USER>-sandbox
```

**Security note:** The privileged SCC is the most permissive SCC in OpenShift. This grant is scoped to the `openshell-<YOUR_USER>` namespace only -- it does not affect any other namespace. The sandbox pods use root only during init for kernel hook setup, then drop privileges for execution.

### 4. Generate JWT signing keys

OpenShell uses Ed25519 JWT keys for authentication between the gateway and sandbox pods. If the Helm chart's `pkiInitJob` is disabled (required on OpenShift due to Job permission constraints), generate them manually:

```bash
openssl genpkey -algorithm Ed25519 -out /tmp/jwt-signing.pem
openssl pkey -in /tmp/jwt-signing.pem -pubout -out /tmp/jwt-signing.pub

oc create secret generic openshell-<YOUR_USER>-jwt-keys \
  -n openshell-<YOUR_USER> \
  --from-file=jwt-signing.pem=/tmp/jwt-signing.pem \
  --from-file=jwt-signing.pub=/tmp/jwt-signing.pub

rm /tmp/jwt-signing.pem /tmp/jwt-signing.pub
```

**Important:** The keys must be Ed25519. EC/ECDSA keys will fail with `failed to parse Ed25519 signing key PEM: InvalidKeyFormat`.

### 5. Install OpenShell via Helm

```bash
helm install openshell-<YOUR_USER> \
  oci://ghcr.io/nvidia/openshell/helm-chart \
  --version <CHART_VERSION> \
  -n openshell-<YOUR_USER> \
  -f configs/openshell/openshell-values-openshift.yaml
```

See `configs/openshell/openshell-values-openshift.yaml` for the Helm values template.

### 6. Build and push the OpenClaw + OpenShell image

The OpenClaw image must include the OpenShell CLI binary at `/opt/openshell/bin/openshell`. The CLI version must match the Helm chart version exactly -- a mismatch causes sandbox provisioning timeouts.

Using the OpenShift internal registry:

```bash
# Expose the internal registry (if not already)
oc patch configs.imageregistry.operator.openshift.io/cluster \
  --type merge -p '{"spec":{"defaultRoute":true}}'

REGISTRY=$(oc get route default-route -n openshift-image-registry -o jsonpath='{.spec.host}')

# Log in to the registry
oc whoami -t | docker login -u $(oc whoami) --password-stdin $REGISTRY

# Build the image
docker build -t $REGISTRY/<YOUR_NAMESPACE>/openclaw-openshell:latest \
  --build-arg OPENCLAW_BASE_IMAGE=ghcr.io/openclaw/openclaw:<VERSION> \
  --build-arg OPENSHELL_CLI_VERSION=<CHART_VERSION> \
  -f Dockerfile .

docker push $REGISTRY/<YOUR_NAMESPACE>/openclaw-openshell:latest
```

**Important constraints:**
- The base image must be Debian-based (not UBI), because the claw-operator requires `tini` in the image and sets it as the entrypoint. The `:latest` tag is UBI-based and lacks `tini`.
- The OpenClaw version must support the `openshell-sandbox` plugin API version. Check the plugin's `package.json` for the required `pluginApi` range.

### 7. Configure the Claw CR

Patch the Claw custom resource to use the OpenShell-enabled image and configure the plugin:

```bash
oc patch claw <YOUR_CLAW_CR> -n <YOUR_NAMESPACE> --type merge -p "$(cat configs/openshell/claw-cr-patch.yaml)"
```

See `configs/openshell/claw-cr-patch.yaml` for the patch template. Key configuration sections:

- `spec.image` -- points to the custom image with OpenShell CLI baked in
- `spec.plugins` -- installs `@openclaw/openshell-sandbox` via init container
- `spec.network.inClusterBypass` -- allows the gateway to reach the OpenShell namespace
- `spec.network.additionalEgress` -- adds a NetworkPolicy rule allowing egress to the OpenShell gateway on port 8080
- `spec.config.raw` -- configures the sandbox backend, mode, scope, and gateway endpoint

### 8. Verify the deployment

Restart the OpenClaw pod to pick up the new image and config:

```bash
oc rollout restart deployment/<YOUR_CLAW_DEPLOYMENT> -n <YOUR_NAMESPACE>
```

Check that the OpenShell gateway is running:

```bash
oc get pods -n openshell-<YOUR_USER>
```

## Security Isolation Test

To verify that sandboxing is working and that sandbox pods are properly isolated from the gateway:

### Test: Run commands inside a sandbox

From the OpenClaw UI, send an agent prompt that triggers code execution (e.g., "run `whoami && id` in a terminal"). The agent will execute the command inside a sandbox pod.

### Expected results

| Check | Expected Result | What It Proves |
|---|---|---|
| `whoami` / `id` | Non-root UID (e.g., uid 1001480000 on OpenShift) | Sandbox runs as non-root despite init containers using root |
| `ls /home/node/.openclaw/` | Permission denied or no such directory | Sandbox has no access to gateway's data directory |
| `ps aux \| grep node` | No gateway process visible | Gateway process is in a different pod -- not visible from sandbox |
| `curl -k https://kubernetes.default.svc/api` | Connection refused or 403 Forbidden | Sandbox pod cannot reach the Kubernetes API |

### What this proves

1. **Process isolation:** The sandbox pod runs in a separate kernel namespace. The gateway process is invisible.
2. **Filesystem isolation:** The sandbox has its own filesystem. Gateway credentials, config, and data are not mounted.
3. **Network isolation:** The sandbox pod's service account has no RBAC permissions, so even if it reaches the API server, it cannot perform any actions.
4. **Privilege separation:** Despite requiring root for init (kernel hook setup), the execution context drops to a non-root UID.

## Sandbox Modes

| Mode | Workspace Ownership | Sync Behavior | Use Case |
|---|---|---|---|
| `mirror` | PVC is canonical | Bidirectional sync between PVC and sandbox | Default. Agent reads/writes files that persist across sandbox restarts |
| `remote` | Sandbox owns workspace | One-time seed from PVC, then sandbox is independent | Throwaway execution where workspace changes are disposable |

The mode is set in `spec.config.raw.plugins.entries.openshell.config.mode`.

## Troubleshooting

### Sandbox provisioning timeout (DependenciesNotReady)

```
sandbox provisioning timed out after 300s. Last reported status:
DependenciesNotReady: Pod exists with phase: Pending
```

**Cause:** Usually an SCC issue. The sandbox pod is stuck in Pending because it cannot validate against any SCC.

**Fix:** Verify that all three service accounts in the OpenShell namespace have the privileged SCC:

```bash
oc get scc privileged -o jsonpath='{.users}' | tr ',' '\n' | grep openshell
```

### Ed25519 key format error

```
failed to parse Ed25519 signing key PEM: InvalidKeyFormat
```

**Cause:** The JWT keys were generated with the wrong algorithm (e.g., EC/ECDSA instead of Ed25519).

**Fix:** Regenerate with `openssl genpkey -algorithm Ed25519` and recreate the secret.

### tini not found

```
executable file 'tini' not found in $PATH
```

**Cause:** The base image is UBI-based (e.g., `ghcr.io/openclaw/openclaw:latest`) which does not include `tini`. The claw-operator sets `tini` as the entrypoint.

**Fix:** Use a Debian-based versioned image as the base (e.g., `ghcr.io/openclaw/openclaw:2026.7.2-beta.1`).

### Plugin API version mismatch

```
pluginApi >=2026.7.1 required
```

**Cause:** The OpenClaw base image version is too old for the `@openclaw/openshell-sandbox` plugin.

**Fix:** Use a newer base image that satisfies the plugin's `pluginApi` requirement.

### CLI/gateway version mismatch

If sandboxes provision but then timeout or behave erratically, check that the OpenShell CLI version baked into the image matches the Helm chart version exactly:

```bash
# Check CLI version
oc exec -n <YOUR_NAMESPACE> deploy/<YOUR_DEPLOYMENT> -- /opt/openshell/bin/openshell --version

# Check Helm chart version
helm list -n openshell-<YOUR_USER>
```

These must match. A CLI at v0.0.44 talking to a gateway at v0.0.83 will fail.

## NetworkPolicy Considerations

The claw-operator creates a default NetworkPolicy that allows egress only to the LiteLLM proxy and DNS. To allow the gateway to reach the OpenShell gateway, an additional egress rule is needed.

The `spec.network.additionalEgress` field in the Claw CR adds this rule. It is scoped to:

- **Destination:** Only the `openshell-<user>` namespace (selected by `kubernetes.io/metadata.name` label)
- **Port:** Only TCP port 8080
- **Direction:** Egress only -- it does not allow any inbound traffic to the OpenClaw pod

This is the minimum-privilege network path: the OpenClaw gateway can reach the OpenShell gateway on exactly one port, and nothing else.

## References

- [OpenShell GitHub](https://github.com/nvidia/openshell)
- [Agent Sandbox CRDs](https://github.com/kubernetes-sigs/agent-sandbox)
- [OpenClaw Sandboxing Docs](https://docs.openclaw.ai/gateway/sandboxing)
- [claw-operator CRD Reference](https://github.com/openclaw/claw-operator)
