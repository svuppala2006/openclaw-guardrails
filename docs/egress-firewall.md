# OVN EgressFirewall: Domain-Level Egress Restriction

## Overview

The EgressFirewall for the `suhruth-test` namespace restricts which external destinations pods can reach. It operates at the OVN-Kubernetes CNI level, filtering traffic as it leaves the namespace. Unlike NetworkPolicies (which are pod-scoped) or AdminNetworkPolicies (which are pod-label-scoped), the EgressFirewall applies to all pods in the namespace uniformly.

This resource is managed via ArgoCD in the `nerc-ocp-config` repository. It is not part of this repository and cannot be modified by namespace users.

## What is EgressFirewall?

EgressFirewall is an OpenShift-specific CRD implemented by OVN-Kubernetes. Each namespace can have exactly one EgressFirewall object (named `default`). It defines an ordered list of rules that match outbound traffic by destination (CIDR or DNS name) and port, then either allow or deny it.

Key characteristics:

- **Namespace-scoped** but typically managed by cluster administrators.
- **One per namespace.** Only one EgressFirewall object is permitted, and it must be named `default`.
- **Ordered rules.** Rules are evaluated top-to-bottom; the first match wins.
- **DNS and CIDR matching.** Rules can match by IP range or by DNS hostname.
- **Applied at the node level.** OVN-Kubernetes implements these rules in the OVN southbound database, enforcing them at the virtual switch before traffic leaves the node.

## Rules

The EgressFirewall defines the following rules, evaluated in order:

### Rule 1: Allow cluster DNS

| Field | Value |
|-------|-------|
| Type | Allow |
| Destination | 172.30.0.10/32 |
| Ports | 53 (UDP), 53 (TCP) |

Permits DNS queries to the cluster DNS service (CoreDNS). This is essential for any DNS resolution within the cluster.

### Rule 2: Allow internal pod network

| Field | Value |
|-------|-------|
| Type | Allow |
| Destination | 10.0.0.0/8 |
| Ports | All |

Permits all traffic to the internal pod network CIDR. This covers pod-to-pod communication within the cluster.

### Rule 3: Allow internal service network

| Field | Value |
|-------|-------|
| Type | Allow |
| Destination | 172.30.0.0/16 |
| Ports | All |

Permits all traffic to the Kubernetes service network (ClusterIP range). This covers communication with Kubernetes services.

### Rule 4: Allow OpenShift OAuth router

| Field | Value |
|-------|-------|
| Type | Allow |
| Destination | 199.94.63.12/32 |
| Ports | All |

Permits traffic to the OpenShift OAuth server's external IP. This is required for the OAuth proxy sidecar on OpenClaw to complete authentication flows.

### Rule 5: Allow Google OAuth

| Field | Value |
|-------|-------|
| Type | Allow |
| Destination | oauth2.googleapis.com |
| Ports | All |

Permits traffic to Google's OAuth2 token endpoint. Required for OpenClaw's Google OAuth authentication flow.

### Rule 6: Allow Vertex AI

| Field | Value |
|-------|-------|
| Type | Allow |
| Destination | us-east5-aiplatform.googleapis.com |
| Ports | All |

Permits traffic to the Vertex AI endpoint in the us-east5 region. This is the LLM inference endpoint used by LiteLLM. The specific region (us-east5) is determined by the LiteLLM configuration for the deployed Gemini model.

### Rule 7: Allow Google accounts

| Field | Value |
|-------|-------|
| Type | Allow |
| Destination | accounts.google.com |
| Ports | All |

Permits traffic to Google's account service. Required for the OAuth consent and login flow.

### Rule 8: Allow GitHub

| Field | Value |
|-------|-------|
| Type | Allow |
| Destination | github.com |
| Ports | All |

Permits traffic to GitHub. Required for OpenClaw's GitHub integration (repository access, webhook handling).

### Rule 9: Allow GitHub API

| Field | Value |
|-------|-------|
| Type | Allow |
| Destination | api.github.com |
| Ports | All |

Permits traffic to GitHub's API endpoint. Required for programmatic GitHub operations (API calls, authentication).

### Rule 10: Deny everything else

| Field | Value |
|-------|-------|
| Type | Deny |
| Destination | 0.0.0.0/0 |
| Ports | All |

The default deny rule. All traffic to destinations not matched by rules 1-9 is dropped.

## OVN-Kubernetes Wildcard DNS Limitation

OVN-Kubernetes does **not** support wildcard DNS entries in EgressFirewall rules. This is a critical operational consideration.

### The Problem

The original EgressFirewall configuration in `nerc-ocp-config` used wildcard entries:

```yaml
# BROKEN - wildcards silently fail in OVN-Kubernetes
- type: Allow
  to:
    dnsName: "*.aiplatform.googleapis.com"
- type: Allow
  to:
    dnsName: "*.github.com"
```

These rules were accepted by the API server without error, but OVN-Kubernetes silently ignored them. The result was that traffic to `us-east5-aiplatform.googleapis.com` and `api.github.com` was blocked by the final deny-all rule, despite appearing to be allowed.

### The Fix

PR #963 in the `nerc-ocp-config` repository replaced the wildcard entries with explicit subdomain entries:

```yaml
# WORKING - explicit subdomains
- type: Allow
  to:
    dnsName: "us-east5-aiplatform.googleapis.com"
- type: Allow
  to:
    dnsName: "api.github.com"
```

### Identifying the Correct Subdomains

- **Vertex AI region:** Determined by the LiteLLM configuration. The model is deployed in `us-east5`, so the endpoint is `us-east5-aiplatform.googleapis.com`. If the Vertex AI region changes in LiteLLM config, this EgressFirewall rule must be updated to match.
- **GitHub API:** The GitHub REST API lives at `api.github.com`. This is a well-known, stable subdomain.

### Lesson Learned

Always test EgressFirewall DNS rules after applying them. Wildcards will not produce errors in `oc describe` or ArgoCD sync status, but they will silently fail to match any traffic. Verify with actual outbound connection tests from a pod in the namespace.

## Layering with AdminNetworkPolicy

The EgressFirewall and AdminNetworkPolicy (ANP) work together to control egress, but they operate at different levels:

```
  Egress request from a pod in suhruth-test:

  +---------------------------+
  |  AdminNetworkPolicy (ANP) |
  |  - Per-pod-label filtering|
  |  - Port-level control     |
  |  - Only targets app:      |
  |    openclaw pods          |
  +---------------------------+
              |
              v
  +---------------------------+
  |  EgressFirewall            |
  |  - Namespace-wide          |
  |  - Domain-level control    |
  |  - Applies to ALL pods     |
  +---------------------------+
              |
              v
         External network
```

**ANP restricts which pods can make HTTPS calls.** Only pods with `app: openclaw` are allowed to reach external port 443. Other pods in the namespace do not have this ANP rule (though they are still subject to the EgressFirewall).

**EgressFirewall restricts which domains those HTTPS calls can reach.** Even though the ANP allows port 443 to 0.0.0.0/0, the EgressFirewall limits the actual destinations to the five allowed domains (oauth2.googleapis.com, us-east5-aiplatform.googleapis.com, accounts.google.com, github.com, api.github.com).

Both must allow the traffic for it to succeed. If either denies, the connection is blocked.

## Verification

### View the EgressFirewall

```bash
oc get egressfirewall -n suhruth-test
oc describe egressfirewall default -n suhruth-test
```

### Test outbound connectivity

From within a pod in the namespace, test against an allowed domain:

```bash
oc exec -n suhruth-test deploy/openclaw -- curl -s -o /dev/null -w "%{http_code}" https://api.github.com/
```

This should return a 200 (or other valid HTTP status), confirming the domain is reachable.

Test against a blocked domain:

```bash
oc exec -n suhruth-test deploy/openclaw -- curl -s --connect-timeout 5 https://example.com/
```

This should time out or fail, confirming the EgressFirewall is blocking traffic to unlisted domains.

### Check OVN logs for denied traffic

If traffic is unexpectedly blocked, check the OVN-Kubernetes logs on the node where the pod is running for denied flows. Coordinate with NERC cluster administrators for node-level log access.

## Management

This EgressFirewall is managed in the `nerc-ocp-config` repository and deployed via ArgoCD. To modify the allowed domains:

1. Open a PR in `nerc-ocp-config` with the updated EgressFirewall spec.
2. Ensure no wildcard DNS entries are used (they will silently fail).
3. After the ArgoCD sync completes, verify connectivity from a pod in the namespace.
