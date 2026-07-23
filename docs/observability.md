# Observability: MLflow Trace Capture for OpenClaw

LLM trace capture for OpenClaw using MLflow, with an in-namespace OTEL Collector bridging gRPC traces from LiteLLM to MLflow's HTTP OTLP endpoint.

## Status

**Working end-to-end.** Every chat message through OpenClaw generates traces in MLflow with token usage, cost breakdown, and latency.

## Architecture

```
OpenClaw Pod (app=openclaw)
  ┌──────────────────────────────────────────────────┐
  │  Gateway ──► NeMo Guardrails ──► LiteLLM         │
  │              (port 8000)         (port 4000)      │
  │                                     │             │
  │                                     │ OTEL gRPC   │
  └─────────────────────────────────────┼─────────────┘
                                        │ port 8080
                                        ▼
                              OTEL Collector (in-namespace)
                                        │
                                        │ HTTP POST
                                        ▼
                              MLflow /v1/traces (port 5000)
                                        │
                                        ▼
                              MLflow UI (Route, HTTPS)
```

Additionally, the TrustyAI GuardrailsOrchestrator also exports traces through the same collector:

```
GuardrailsOrchestrator ──► OTEL Collector ──► MLflow
  (gRPC :8080)               (HTTP :5000)
```

## Components

| Component | Image / CR | Service | Ports |
|-----------|-----------|---------|-------|
| MLflow | `quay.io/opendatahub/mlflow:latest` (v3.14.0) | `mlflow.<NAMESPACE>.svc` | 5000 (HTTP) |
| OTEL Collector | OpenTelemetryCollector CR `otel` | `otel-collector.<NAMESPACE>.svc` | 8080 (gRPC), 4318 (HTTP) |
| LiteLLM | Sidecar in openclaw pod | localhost:4000 | OTEL export via env vars |
| GuardrailsOrchestrator | TrustyAI operator-managed | `openclaw-guardrails.<NAMESPACE>.svc` | OTEL export via env vars |

## How It Works

### LiteLLM OTEL Integration

LiteLLM v1.82.3+ has full OpenTelemetry SDK support. Enabling it requires two things:

1. **Config callback**: Add `callbacks: ["otel"]` to `litellm_settings` in the LiteLLM ConfigMap
2. **Env vars on the litellm container**:
   ```
   OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.<NAMESPACE>.svc:8080
   OTEL_EXPORTER_OTLP_PROTOCOL=grpc
   OTEL_SERVICE_NAME=litellm
   ```

Each LLM call generates spans with:
- Token usage (input, output, total)
- Cost breakdown (input cost, output cost, total cost)
- Model name and provider
- Request/response latency

### OTEL Collector Bridge

MLflow supports OTLP trace ingestion via HTTP at `/v1/traces`, but NOT gRPC. LiteLLM's OTEL SDK exports via gRPC. The OTEL Collector bridges this gap:

- **Receiver**: OTLP gRPC on port 8080
- **Exporter**: OTLP HTTP to `http://mlflow.<NAMESPACE>.svc:5000/v1/traces`
- **Header**: `x-mlflow-experiment-id: 0` (routes traces to the Default experiment)

### MLflow Tracking Server

MLflow 3.14.0 runs as a single-replica deployment with:
- SQLite backend store on a 5Gi PVC
- CORS origin configured to match the OpenShift Route
- `--allowed-hosts=*` for connections from within the cluster
- Memory limit of 2Gi (OOMKilled at 1Gi under trace ingestion load)

The MLflow UI is exposed via an OpenShift Route with edge TLS termination.

## Why Port 8080 Instead of 4317

The AdminNetworkPolicy `restrict-<NAMESPACE>-egress` controls egress from `app=openclaw` pods. It only allows specific ports:

| Allowed Port | Purpose |
|-------------|---------|
| 53, 5353 | DNS |
| 6443, 443 | Kubernetes API, HTTPS |
| 8443 | Same-namespace pod communication |
| 8032, 8034, 8080 | TrustyAI guardrails ports |

Port 4317 (standard OTLP gRPC) is **not** in this list. The ANP's final deny-all rule blocks it. Since the LiteLLM container runs inside the `app=openclaw` pod, it cannot reach the collector on port 4317.

Port 8080 is allowed (Rule 4: allow-trustyai-guardrails), so the collector's gRPC receiver is configured to listen on 8080 instead of the standard 4317. This is a workaround — the proper fix would be to add port 4317 to the ANP, but that requires a PR to `nerc-ocp-config`.

The GuardrailsOrchestrator (`app=openclaw-guardrails`) is NOT subject to the ANP and could use any port, but it also uses 8080 for simplicity.

## Configuration

### GuardrailsOrchestrator env vars

```bash
oc set env deployment/openclaw-guardrails -n <NAMESPACE> \
  OTLP_EXPORT=traces \
  OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
  OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.<NAMESPACE>.svc:8080
```

Note: The `fms_guardrails_orchestr8` Rust binary uses a custom `OTLP_EXPORT=traces` env var, not the standard `OTEL_TRACES_EXPORTER`. See [the TrustyAI orchestrator docs](trustyai-orchestrator.md) for details.

### LiteLLM env vars

```bash
oc set env deployment/openclaw -n <NAMESPACE> -c litellm \
  OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.<NAMESPACE>.svc:8080 \
  OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
  OTEL_SERVICE_NAME=litellm
```

### LiteLLM ConfigMap

Add the OTEL callback to `litellm_settings`:

```yaml
litellm_settings:
  callbacks: ["otel"]
```

## NetworkPolicies

Two policies allow traffic to the observability components (the namespace has a default-deny ingress policy):

### allow-mlflow-from-router

Allows ingress to `app=mlflow` from the OpenShift router (for the UI Route) and from any pod in the same namespace (for the collector):

```yaml
spec:
  podSelector:
    matchLabels:
      app: mlflow
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              network.openshift.io/policy-group: ingress
        - podSelector: {}
      ports:
        - port: 5000
          protocol: TCP
```

### allow-collector-from-guardrails

Allows ingress to the OTEL Collector from the openclaw pod (LiteLLM) and the guardrails orchestrator:

```yaml
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: otel-collector
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: openclaw-guardrails
        - podSelector:
            matchLabels:
              app: openclaw
      ports:
        - port: 8080
          protocol: TCP
        - port: 4318
          protocol: TCP
```

## Verification

### Check traces via API

```bash
curl -sk "https://mlflow-<NAMESPACE>.apps.<CLUSTER_DOMAIN>/ajax-api/2.0/mlflow/traces?experiment_ids=0&max_results=5"
```

### Check traces in the UI

1. Open `https://mlflow-<NAMESPACE>.apps.<CLUSTER_DOMAIN>/`
2. Click the **Default** experiment in the left sidebar
3. Click the **Traces** tab
4. Each trace shows status, duration, token usage, and cost

### Generate a test trace

Send a message through the OpenClaw web UI. Within ~10 seconds, the trace should appear in MLflow.

### Check collector logs

```bash
# Should show "Traces" info lines with span counts, no "Exporting failed" errors
oc logs deployment/otel-collector -n <NAMESPACE> --tail=20
```

### Check LiteLLM OTEL export

```bash
# Should show POST /v1/chat/completions 200 with NO "StatusCode.UNAVAILABLE" errors
oc logs deployment/openclaw -n <NAMESPACE> -c litellm --tail=20
```

## What Didn't Work

### Cluster Tempo backend

The NERC cluster has a Tempo stack in the `opentelemetry` namespace. Two issues prevented using it:

1. **NetworkPolicy on the Tempo distributor**: Only allows ingress from pods with Tempo gateway labels. The OTEL collector doesn't have these labels, so connections time out.

2. **TLS CA mismatch**: The Tempo distributor's mTLS certificate is signed by Tempo's own CA (`opentelemetry_tempo-opentelemetry-signing-ca`), but the collector config uses `service-ca.crt` (the OpenShift service CA). Different CAs, TLS handshake fails.

Both require cluster admin action to fix. MLflow was deployed as an in-namespace alternative.

### Direct app-to-Tempo export

The `fms_guardrails_orchestr8` Rust binary uses `tonic` with `tls-native-roots` for its OTLP exporter. It does not read `OTEL_EXPORTER_OTLP_CERTIFICATE` or allow configuring a custom CA. The Tempo gateway requires the OpenShift service CA, so direct connections fail with "transport error".

### Tempo gateway RBAC

The Tempo gateway has an OPA sidecar enforcing RBAC. Only the `otel-collector` ServiceAccount in the `opentelemetry` namespace has the `tempostack-traces-write` ClusterRole. The app's ServiceAccount gets 403 "You don't have permission to access this tenant."

### MLflow OOMKill at 1Gi

MLflow's default memory footprint plus trace ingestion exceeded 1Gi. Bumped to 2Gi limit / 1Gi request to resolve.

### MLflow CORS origin blocking

MLflow 3.x's `fastapi_security` middleware blocks cross-origin requests by default. Since the OpenShift Route terminates TLS at the edge, the browser sends `Origin: https://mlflow-...` but the server sees itself on `http://localhost:5000`. Fixed by adding `--cors-allowed-origins=https://mlflow-<NAMESPACE>.apps.<CLUSTER_DOMAIN>` to the server args.

### Standard OTLP port blocked by ANP

Port 4317 (standard OTLP gRPC) is blocked by the AdminNetworkPolicy for `app=openclaw` pods. Worked around by configuring the collector to listen on port 8080 instead.
