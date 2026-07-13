# Presidio PII Detection

This document describes the integration of Microsoft Presidio for PII (Personally Identifiable Information) detection in OpenClaw. Presidio was deployed as a standalone detection layer before the TrustyAI built-in detector was available.

**Current status:** This approach has been superseded by the TrustyAI GuardrailsOrchestrator's built-in PII detector, which provides equivalent functionality without requiring separate pods. This document is retained as a reference for cases where standalone, fine-grained Presidio deployment is needed.

## Overview

Presidio is an open-source PII detection and anonymization framework developed by Microsoft. It provides two core services:

- **Presidio Analyzer** -- identifies PII entities in text using a combination of pattern matching, named entity recognition, and configurable recognizers.
- **Presidio Anonymizer** -- replaces, masks, or redacts detected PII entities in text.

Both services were deployed as separate pods in the OpenShift namespace and integrated with OpenClaw through LiteLLM's guardrail hooks.

## Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│  Request Flow                                                         │
│                                                                       │
│  User Input                                                           │
│    --> LiteLLM Presidio pre_call hook                                 │
│          --> Presidio Analyzer (port 3000): detect PII                │
│          --> Presidio Anonymizer (port 5002): redact PII              │
│          <-- sanitized input (or block if PII found)                  │
│    --> Claude (Vertex AI)                                             │
│    <-- LLM response                                                   │
│    --> LiteLLM Presidio post_call hook                                │
│          --> Presidio Analyzer (port 3000): detect PII in response    │
│          --> Presidio Anonymizer (port 5002): redact PII              │
│          <-- sanitized response                                       │
│    --> User Output                                                    │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

## Detected Entity Types

Presidio was configured to detect six entity types:

| Entity Type | Description | Example |
|-------------|-------------|---------|
| Email address | Email addresses in standard format | `user@example.com` |
| SSN | US Social Security numbers | `123-45-6789` |
| Credit card number | Major card network formats | `4111-1111-1111-1111` |
| Phone number | US and international formats | `(555) 123-4567` |
| Person name | Full names detected via NER | `John Smith` |
| Physical address | Street addresses and locations | `123 Main St, Springfield, IL` |

## Components

### Presidio Analyzer Pod

- **Image:** `mcr.microsoft.com/presidio-analyzer`
- **Port:** 3000
- **Purpose:** Receives text, returns a list of detected PII entities with confidence scores and character positions.
- **API endpoint:** `POST /analyze`

### Presidio Anonymizer Pod

- **Image:** `mcr.microsoft.com/presidio-anonymizer`
- **Port:** 5002
- **Purpose:** Receives text and a list of detected entities, returns the text with PII replaced by placeholder tokens (e.g., `<EMAIL_ADDRESS>`, `<CREDIT_CARD>`).
- **API endpoint:** `POST /anonymize`

### LiteLLM Guardrail Hooks

LiteLLM supports `pre_call` and `post_call` guardrail hooks that execute custom logic before and after each LLM call. The Presidio integration uses these hooks to:

1. **pre_call** -- analyze the user's input for PII before sending it to the LLM. If PII is detected, the input can be anonymized or the request can be blocked entirely.
2. **post_call** -- analyze the LLM's response for PII before returning it to the user. Any PII in the response is redacted.

## Deployment

### Step 1: Deploy Presidio Analyzer

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: presidio-analyzer
  namespace: <YOUR_NAMESPACE>
spec:
  replicas: 1
  selector:
    matchLabels:
      app: presidio-analyzer
  template:
    metadata:
      labels:
        app: presidio-analyzer
    spec:
      containers:
        - name: presidio-analyzer
          image: mcr.microsoft.com/presidio-analyzer:latest
          ports:
            - containerPort: 3000
          resources:
            requests:
              memory: "256Mi"
              cpu: "250m"
            limits:
              memory: "1Gi"
              cpu: "500m"
---
apiVersion: v1
kind: Service
metadata:
  name: presidio-analyzer
  namespace: <YOUR_NAMESPACE>
spec:
  selector:
    app: presidio-analyzer
  ports:
    - port: 3000
      targetPort: 3000
```

### Step 2: Deploy Presidio Anonymizer

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: presidio-anonymizer
  namespace: <YOUR_NAMESPACE>
spec:
  replicas: 1
  selector:
    matchLabels:
      app: presidio-anonymizer
  template:
    metadata:
      labels:
        app: presidio-anonymizer
    spec:
      containers:
        - name: presidio-anonymizer
          image: mcr.microsoft.com/presidio-anonymizer:latest
          ports:
            - containerPort: 5002
          resources:
            requests:
              memory: "128Mi"
              cpu: "100m"
            limits:
              memory: "512Mi"
              cpu: "250m"
---
apiVersion: v1
kind: Service
metadata:
  name: presidio-anonymizer
  namespace: <YOUR_NAMESPACE>
spec:
  selector:
    app: presidio-anonymizer
  ports:
    - port: 5002
      targetPort: 5002
```

### Step 3: Configure LiteLLM Guardrail Hooks

Add the Presidio guardrail configuration to the LiteLLM config:

```yaml
litellm_settings:
  guardrails:
    - guardrail_name: presidio-pii
      litellm_params:
        guardrail: presidio
        mode: pre_call post_call
        presidio_analyzer_api_base: http://presidio-analyzer:3000
        presidio_anonymizer_api_base: http://presidio-anonymizer:5002
        output_parse_pii: true
        presidio_ad_hoc_recognizers: null
```

### Step 4: Update Network Policies

The Presidio pods need to be reachable from the OpenClaw pod. If you are using default-deny ingress NetworkPolicies, add a policy to allow traffic:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-openclaw-to-presidio
  namespace: <YOUR_NAMESPACE>
spec:
  podSelector:
    matchLabels:
      app: presidio-analyzer
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: openclaw
      ports:
        - port: 3000
          protocol: TCP
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-openclaw-to-presidio-anonymizer
  namespace: <YOUR_NAMESPACE>
spec:
  podSelector:
    matchLabels:
      app: presidio-anonymizer
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: openclaw
      ports:
        - port: 5002
          protocol: TCP
```

Additionally, the AdminNetworkPolicy must be updated to allow egress from the OpenClaw pod to the Presidio pod ports (3000 and 5002). Without this, the LiteLLM guardrail hooks will fail with connection timeouts.

## Known Issue: Network Policy for Egress

During initial deployment, the Presidio integration failed silently because the AdminNetworkPolicy did not allow egress from the OpenClaw pod to the Presidio pods on ports 3000 and 5002. The LiteLLM pre_call hook timed out when trying to reach the Presidio Analyzer, and the error was not surfaced clearly in the LiteLLM logs.

**Resolution:** Update the AdminNetworkPolicy to include egress allow rules for the Presidio service ports:

```yaml
- action: Allow
  to:
    - pods:
        podSelector:
          matchLabels:
            app: presidio-analyzer
  ports:
    - portNumber:
        port: 3000
        protocol: TCP
- action: Allow
  to:
    - pods:
        podSelector:
          matchLabels:
            app: presidio-anonymizer
  ports:
    - portNumber:
        port: 5002
        protocol: TCP
```

## Comparison with TrustyAI Built-in Detector

| Aspect | Presidio (standalone) | TrustyAI Built-in Detector |
|--------|----------------------|---------------------------|
| Deployment | Separate pods (Analyzer + Anonymizer) | Sidecar in orchestrator pod |
| Pod count | 2 additional pods | 0 additional pods (included in orchestrator) |
| Integration point | LiteLLM guardrail hooks | GuardrailsOrchestrator API |
| PII detection | Pattern matching + NER | Text content analysis |
| Anonymization | Built-in (redact/replace/mask) | Detection only (blocking at orchestrator level) |
| Configuration | LiteLLM config + Presidio recognizers | Orchestrator config with threshold |
| Network requirements | Egress to Presidio pods required | Localhost only (sidecar) |

The TrustyAI built-in detector is simpler to deploy and does not require additional pods or network policy changes. Presidio is preferable when you need fine-grained anonymization (replacing PII with specific placeholder formats) or custom recognizers for domain-specific entity types.

## References

- [Microsoft Presidio Documentation](https://microsoft.github.io/presidio/)
- [Presidio Analyzer API](https://microsoft.github.io/presidio/api-docs/api-docs.html#tag/Analyzer)
- [LiteLLM Guardrails](https://docs.litellm.ai/docs/proxy/guardrails)
- [LiteLLM Presidio Integration](https://docs.litellm.ai/docs/proxy/guardrails/presidio)
