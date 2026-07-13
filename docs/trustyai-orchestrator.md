# TrustyAI GuardrailsOrchestrator

This document describes the deployment and configuration of the TrustyAI GuardrailsOrchestrator for content safety detection in OpenClaw.

## What is TrustyAI

TrustyAI is an AI trustworthiness toolkit from the Open Data Hub project. It provides operators and tools for deploying AI safety infrastructure on OpenShift, including model monitoring, bias detection, and guardrails orchestration.

The TrustyAI operator manages the lifecycle of GuardrailsOrchestrator custom resources, handling deployment, scaling, and configuration of content safety detection pipelines.

## What the Orchestrator Does

The GuardrailsOrchestrator acts as a centralized content safety gateway. It receives text content from client applications, routes it through one or more configured detectors, and returns aggregated detection results with confidence scores.

Key capabilities:

- **Detector orchestration** -- routes content through multiple detectors in a configurable pipeline.
- **Built-in PII detection** -- includes a text content analyzer that detects personally identifiable information without requiring external services.
- **Configurable thresholds** -- each detector has a confidence threshold; content is flagged only when the detector's confidence exceeds the threshold.
- **Header passthrough** -- forwards authentication and API headers to downstream services, enabling integration with authenticated LLM providers.
- **gRPC and HTTP endpoints** -- exposes both gRPC (port 8032) and HTTP (port 8034) interfaces for flexibility.

## Architecture

The orchestrator deploys as a pod with two containers:

| Container | Port | Role |
|-----------|------|------|
| `openclaw-guardrails` | 8032 (gRPC), 8034 (HTTP) | Orchestrator process that manages detector routing and aggregation |
| `built-in-detector` | 8080 | Text content analysis detector for PII and sensitive data detection |

Both containers run in the same pod, communicating over localhost. The built-in detector is enabled by the `enableBuiltInDetectors: true` flag in the CR spec.

## Built-in PII Detector

The built-in detector uses text content analysis (`text_contents` type) to scan input and output text for personally identifiable information. It operates with the `whole_doc_chunker` strategy, meaning it analyzes the entire document rather than breaking it into smaller chunks.

The default confidence threshold is `0.75`. Content is flagged when the detector's confidence that PII is present exceeds this threshold. You can adjust this value in the orchestrator configuration to make detection more or less sensitive.

Detected entity types include email addresses, phone numbers, Social Security numbers, credit card numbers, person names, and physical addresses.

## Integration with OpenClaw

The OpenClaw gateway sends content to the orchestrator for safety checks. The orchestrator evaluates the content against all configured detectors and returns a result indicating whether the content should be passed through or blocked.

```
OpenClaw gateway (port 18789)
  --> GuardrailsOrchestrator (port 8032 gRPC or 8034 HTTP)
    --> built-in-detector (port 8080)
      <-- detection result (confidence score)
    <-- orchestrated response (pass/block)
  <-- gateway acts on result
```

The orchestrator forwards authentication headers (`authorization`, `x-api-key`, `anthropic-version`) to the downstream LLM service, which allows it to sit in the request path without breaking authentication flows.

The orchestrator configuration points its OpenAI-compatible backend to `localhost:4000`, which is the LiteLLM sidecar inside the OpenClaw pod. When the orchestrator runs as a separate pod, this should be updated to the appropriate service address.

## Deployment

### Prerequisites

- OpenShift cluster with the TrustyAI operator installed (available through the Open Data Hub operator)
- `oc` CLI authenticated to the cluster
- The target namespace exists

### Step 1: Create the Orchestrator Configuration

Create a ConfigMap containing the orchestrator configuration:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: trustyai-orchestrator-config
  namespace: <YOUR_NAMESPACE>
data:
  config.yaml: |
    passthrough_headers:
      - authorization
      - x-api-key
      - anthropic-version
    openai:
      service:
        hostname: localhost
        port: 4000
    detectors:
      built-in-detector:
        type: text_contents
        service:
          hostname: localhost
          port: 8080
        chunker_id: whole_doc_chunker
        default_threshold: 0.75
```

Apply it:

```bash
oc apply -f configs/trustyai/orchestrator-config.yaml
```

### Step 2: Create the GuardrailsOrchestrator CR

Create the custom resource that tells the TrustyAI operator to deploy the orchestrator:

```yaml
apiVersion: trustyai.opendatahub.io/v1alpha1
kind: GuardrailsOrchestrator
metadata:
  name: openclaw-guardrails
  namespace: <YOUR_NAMESPACE>
spec:
  orchestratorConfig: trustyai-orchestrator-config
  enableBuiltInDetectors: true
  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"
    limits:
      memory: "2Gi"
      cpu: "1000m"
  replicas: 1
  serviceConfig:
    type: ClusterIP
    port: 8033
```

Apply it:

```bash
oc apply -f configs/trustyai/guardrails-orchestrator.yaml
```

### Step 3: Verify Deployment

Check that the orchestrator pod is running with both containers ready:

```bash
oc get pods -l app=openclaw-guardrails -n <YOUR_NAMESPACE>
```

Expected output shows a pod with `2/2` containers ready:

```
NAME                                    READY   STATUS    RESTARTS   AGE
openclaw-guardrails-<hash>              2/2     Running   0          2m
```

Verify the service is created:

```bash
oc get svc openclaw-guardrails-service -n <YOUR_NAMESPACE>
```

### Step 4: Configure Network Policies

If you are using the default-deny ingress NetworkPolicy described in the [architecture document](architecture.md), you need to add a policy that allows traffic from the OpenClaw pod to the orchestrator:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-openclaw-to-guardrails
  namespace: <YOUR_NAMESPACE>
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
```

## Customizing Detectors

### Adjusting the PII Detection Threshold

To make PII detection more sensitive (catch more potential PII, with more false positives), lower the threshold:

```yaml
detectors:
  built-in-detector:
    type: text_contents
    service:
      hostname: localhost
      port: 8080
    chunker_id: whole_doc_chunker
    default_threshold: 0.5
```

To make it less sensitive (fewer false positives, but may miss some PII), raise the threshold:

```yaml
detectors:
  built-in-detector:
    default_threshold: 0.9
```

### Adding External Detectors

You can add additional detectors by extending the `detectors` section in the orchestrator configuration. Each detector needs a type, service endpoint, and threshold:

```yaml
detectors:
  built-in-detector:
    type: text_contents
    service:
      hostname: localhost
      port: 8080
    chunker_id: whole_doc_chunker
    default_threshold: 0.75
  custom-toxicity-detector:
    type: text_contents
    service:
      hostname: toxicity-detector-service
      port: 8080
    chunker_id: whole_doc_chunker
    default_threshold: 0.8
```

After modifying the configuration, update the ConfigMap and restart the orchestrator:

```bash
oc apply -f configs/trustyai/orchestrator-config.yaml
oc rollout restart deployment/openclaw-guardrails -n <YOUR_NAMESPACE>
```

## Resource Considerations

The default resource requests and limits are conservative:

| Resource | Request | Limit |
|----------|---------|-------|
| Memory | 512Mi | 2Gi |
| CPU | 250m | 1000m |

For production deployments with high traffic, consider increasing these values. The built-in detector performs in-memory text analysis, so memory usage scales with the size of the content being analyzed.

## References

- [TrustyAI / Open Data Hub](https://opendatahub.io/)
- [TrustyAI Operator Repository](https://github.com/trustyai-explainability/trustyai-service-operator)
- [GuardrailsOrchestrator API Reference](https://github.com/trustyai-explainability/guardrails-orchestrator)
