# Recommendation: Deploying OpenClaw on NERC

**Author:** Suhruth Vuppala
**Date:** August 2026
**Audience:** Mass Open Cloud (MOC) / NERC Team
**Status:** Final Recommendation

---

## Executive Summary

This document recommends that the MOC adopt OpenClaw as a supported AI agent gateway on the NERC MGHPCC OpenShift cluster, subject to three conditions outlined below.

Over the course of this project, a defense-in-depth security stack was designed, implemented, and tested to answer a central question: **is it feasible to securely run LLM-driven AI agents on the NERC?** The answer is a conditional yes. Eight security layers are deployed and operational today, covering inference guardrails, content safety, network isolation, egress control, observability, and agent sandboxing. Two additional layers (secrets management and tool governance) are planned and have clear implementation paths.

OpenClaw on NERC would serve three user groups: **researchers** leveraging AI agents for data analysis and experimentation, **professors** incorporating AI agents into coursework, and **AI startups** using NERC compute for agent-driven workflows.

### Conditions for Adoption

1. **Policy approval** for running AI agent workloads on NERC, including acceptance of the security posture documented here.
2. **Cluster-admin collaboration** on a small set of cluster-scoped resources that namespace users cannot self-manage (detailed in Section 5).
3. **Commitment to ongoing maintenance** of the security stack as OpenClaw and its dependencies evolve.

---

## 1. What is OpenClaw?

OpenClaw is an open-source AI agent gateway that allows users to interact with LLM-powered agents through a web interface. Agents can execute code, use tools via the Model Context Protocol (MCP), and perform multi-step reasoning. The gateway manages sessions, authentication, and tool routing.

On NERC, OpenClaw runs on OpenShift and connects to Claude on Vertex AI (us-east5 region) through a LiteLLM proxy. User authentication is handled by an OAuth proxy integrated with OpenShift's identity provider.

### Why NERC?

NERC provides the compute infrastructure, multi-tenancy model, and institutional backing that make it a natural fit for offering AI agent capabilities to the research community. Deploying OpenClaw on NERC avoids the need for individual researchers to set up and secure their own AI infrastructure, centralizes security and compliance controls, and provides a shared platform that can serve multiple user groups.

---

## 2. Security Posture: What's in Place

The security architecture follows a defense-in-depth model: no single layer is solely responsible for security. If one layer is bypassed, others provide fallback protection.

### Deployed Security Layers

| Layer | Technology | What It Protects Against |
|-------|-----------|--------------------------|
| Inference guardrails | NeMo Guardrails (sidecar) | Jailbreaks, prompt injection, abusive content |
| Content safety | TrustyAI GuardrailsOrchestrator | PII leakage (email, SSN, credit cards, phone numbers) |
| Ingress isolation | Kubernetes NetworkPolicies (x4) | Unauthorized pod-to-pod access |
| Egress control | AdminNetworkPolicy | Data exfiltration via non-approved ports |
| Domain filtering | OVN EgressFirewall | Data exfiltration to non-approved external hosts |
| Observability | MLflow + OTEL Collector | Undetected guardrail bypasses, forensic gaps |
| Agent sandboxing | NVIDIA OpenShell | Agent code escaping its execution environment |
| Self-sandboxing | NemoClaw | Agent access to host filesystem/network (lightweight alternative) |

### How the Layers Work Together

A typical request flows through these layers in sequence:

1. **Authentication:** OAuth proxy verifies the user's identity before the request reaches the gateway.
2. **Input guardrails:** NeMo Guardrails evaluates the user's message against safety policies. Blocked messages are refused without ever reaching the LLM.
3. **Content safety:** TrustyAI scans for PII and sensitive content using deterministic pattern matching, independent of LLM judgment.
4. **LLM call:** If the message passes both checks, it is forwarded to Claude via LiteLLM.
5. **Output guardrails:** The LLM's response is evaluated by NeMo's output rails before being returned to the user.
6. **Network isolation:** Even if an attacker gains code execution inside a pod, NetworkPolicies prevent lateral movement, AdminNetworkPolicy restricts egress ports, and EgressFirewall limits which external domains are reachable.
7. **Observability:** Every LLM interaction is traced end-to-end with token usage, cost, latency, and full request/response content, enabling detection of guardrail bypasses and forensic investigation.
8. **Sandboxing:** When an agent executes code, it runs in an isolated pod (OpenShell) or a restricted container (NemoClaw), not inside the gateway.

### Test Results

The guardrails system was validated against a suite of adversarial test cases:

- **7 out of 7** malicious inputs were blocked, including jailbreak attempts, prompt injection, requests for explicit content, abusive language, and PII extraction. All returned a fixed refusal message; the LLM was never called.
- **2 out of 2** safe inputs passed through normally with expected Claude responses, confirming that the guardrails do not over-block legitimate use.
- **PII detection** successfully identifies email addresses, Social Security numbers, credit card numbers, phone numbers, person names, and physical addresses with a configurable confidence threshold.

Full test details and screenshots are available in the [openclaw-guardrails repository](docs/nemo-guardrails.md).

---

## 3. What's Not Yet in Place

Two security layers are planned but not yet deployed on NERC:

| Layer | Technology | Status | Path Forward |
|-------|-----------|--------|--------------|
| Secrets management | HashiCorp Vault via ESO | Planned | External Secrets Operator is already installed cluster-wide. An external Vault instance can be connected without requiring cluster-admin permissions. |
| Tool governance | MCP Gateway | Planned | Awaiting general availability from upstream. Kuadrant (the underlying policy engine) is already available on OpenShift. |

**Secrets management** is the higher priority gap. Currently, six secrets (API keys, SSH credentials, LiteLLM master key) are stored as base64-encoded Kubernetes Secrets. While functional, this provides no audit trail, no automatic rotation, and relies on etcd encryption-at-rest configuration. The recommended path is to use the already-installed External Secrets Operator to sync from an external Vault instance, which requires no elevated cluster permissions.

**Tool governance** will become important as OpenClaw's MCP tool ecosystem grows and multiple user groups share the platform. MCP Gateway will provide identity-based tool filtering and per-tool authorization, ensuring that different users can only access the tools appropriate to their role.

---

## 4. Resource Overhead

The security stack adds resource consumption on top of the base OpenClaw deployment. The table below summarizes the overhead of each layer:

| Component | CPU Request | Memory Request | Memory Limit | Pods | Storage |
|-----------|------------|---------------|-------------|------|---------|
| NeMo Guardrails (sidecar) | Minimal | Included in OpenClaw pod | Included in OpenClaw pod | 0 (sidecar) | None |
| TrustyAI Orchestrator | ~200m | ~256Mi | ~512Mi | 1 (2 containers) | None |
| NetworkPolicies | None | None | None | 0 | None |
| AdminNetworkPolicy | None | None | None | 0 | None |
| EgressFirewall | None | None | None | 0 | None |
| OTEL Collector | ~100m | ~128Mi | ~256Mi | 1 | None |
| MLflow | ~200m | ~1Gi | ~2Gi | 1 | 5Gi PVC |
| OpenShell (per session) | Variable | Variable | Variable | 1 per active session | None |

**Key takeaways:**

- Network policies (ingress, egress, domain filtering) add **zero** resource overhead. They are enforced by the cluster's CNI plugin at no cost to the namespace.
- The guardrails infrastructure (NeMo sidecar + TrustyAI + OTEL + MLflow) adds approximately **3 pods** and **~4Gi of memory** to the namespace.
- OpenShell sandboxing creates ephemeral pods per agent session. Resource usage scales with concurrent active sessions and is bounded by namespace resource quotas.
- NeMo Guardrails runs as a sidecar within the existing OpenClaw pod, so it does not add a separate pod but does share the pod's resource allocation.

### Latency Impact

The guardrails add latency to each LLM request:

- **NeMo input rail check:** The LLM itself evaluates whether the input is safe. This adds one additional LLM call before the actual request, typically adding 1-3 seconds depending on model response time.
- **NeMo output rail check:** Similarly, one additional LLM call after the response, adding 1-3 seconds.
- **TrustyAI content check:** Pattern-based analysis, typically sub-second.

For most use cases (research, coursework, exploratory agent tasks), this added latency is acceptable. For latency-sensitive applications, the guardrails configuration can be tuned — for example, disabling output rails while keeping input rails, or adjusting the self-check prompts for faster evaluation.

---

## 5. What the MOC Needs to Provide

The current security stack was built entirely within namespace-level permissions, with one exception: the AdminNetworkPolicy and EgressFirewall were deployed through the [nerc-ocp-config](https://github.com/OCP-on-NERC/nerc-ocp-config) repository via ArgoCD with cluster-admin assistance. Going forward, the MOC would need to provide or approve the following:

### Cluster-Admin Actions Required

| Action | Why It's Needed | Effort |
|--------|----------------|--------|
| Maintain AdminNetworkPolicy for OpenClaw namespaces | Egress control cannot be enforced at namespace level alone. ANPs are cluster-scoped and require cluster-admin to create/modify. | Low — already templated, needs replication per namespace |
| Maintain EgressFirewall per namespace | DNS-based domain filtering is namespace-scoped but requires OVN-Kubernetes configuration. | Low — already templated |
| Grant `anyuid` SCC for Vault namespace (if in-cluster Vault is pursued) | Vault containers require a specific non-root UID. | One-time |
| Grant privileged SCC for OpenShell namespace (if per-session sandboxing is needed) | OpenShell init containers set up kernel-level isolation. | One-time |
| ClusterRoleBinding for Vault's Kubernetes auth (if in-cluster Vault is pursued) | Vault needs TokenReview API access to authenticate pods by their service account. | One-time |

### Policy Decisions Needed

1. **Approval to run AI agent workloads on NERC.** OpenClaw enables LLM-powered agents that can execute code and use external tools. The MOC should determine whether this class of workload is appropriate for the NERC and under what conditions.

2. **Multi-tenancy model.** If OpenClaw is offered to multiple user groups (researchers, courses, startups), the MOC should decide whether each group gets a dedicated namespace with its own security stack, or whether a shared deployment with role-based access is preferred.

3. **Model provider policy.** The current deployment uses Claude on Vertex AI via GCP. The MOC should determine whether this model provider is acceptable, whether alternative providers should be supported, and who is responsible for the API costs.

4. **Data handling policy.** LLM requests and responses pass through Google's Vertex AI infrastructure. The MOC should evaluate whether this is acceptable for the types of data NERC users will process, particularly for research involving sensitive or regulated data.

5. **Incident response.** The observability stack (MLflow traces) provides forensic capability, but the MOC should define who is responsible for monitoring traces, responding to guardrail bypass incidents, and updating safety policies.

---

## 6. Risks and Limitations

### Guardrails Are Probabilistic

NeMo Guardrails uses the LLM itself to evaluate safety. This means:

- **False negatives are possible.** A sufficiently sophisticated prompt injection may bypass the self-check. The defense-in-depth model mitigates this: network isolation prevents data exfiltration even if the LLM is manipulated, and observability enables after-the-fact detection.
- **False positives occur.** Legitimate messages may occasionally be blocked. The guardrail prompts and rules can be tuned to adjust the sensitivity-specificity tradeoff.

### Namespace-Level Limitations

Without cluster-admin access, namespace users cannot:

- Create or modify AdminNetworkPolicies or EgressFirewalls
- Install operators (TrustyAI, ESO are already available cluster-wide)
- Grant SCCs required for Vault or OpenShell
- Access the cluster's Tempo backend for centralized tracing (RBAC and mTLS constraints)

This means the security posture is partially dependent on cluster-admin cooperation. The current workaround (in-namespace MLflow instead of cluster Tempo, ESO instead of in-cluster Vault) is functional but represents a tradeoff.

### Maintenance Burden

The security stack requires ongoing attention:

- **NeMo Guardrails** safety policies need periodic updates as new attack patterns emerge.
- **EgressFirewall** domain allowlists must be updated when model providers change endpoints.
- **TrustyAI** operator updates may require CR migrations.
- **OpenShell** versions must match exactly between the CLI and Helm chart; upgrades require coordination.
- **MLflow** storage will grow over time and needs monitoring/rotation.

An estimated **2-4 hours per month** of maintenance should be budgeted for routine updates and monitoring, with additional time for incident response or major version upgrades.

### Latency Overhead

As noted in Section 4, the guardrails add 2-6 seconds of latency per LLM request due to the input and output self-check calls. This is acceptable for interactive use but may be a concern for batch or automated workflows. The configuration is tunable per deployment.

---

## 7. Recommendation

**Deploy OpenClaw on NERC as a supported platform for AI agent workloads, with the security stack documented in this project.**

The security architecture addresses the three core risks identified at the project's outset:

1. **Resource access:** Network isolation (NetworkPolicies + AdminNetworkPolicy + EgressFirewall) constrains what agents can reach. Agent sandboxing (OpenShell or NemoClaw) constrains what agents can do on the host.
2. **Secret keeping:** Secrets are isolated by network policy today and have a clear upgrade path to Vault via the already-installed External Secrets Operator.
3. **Prompt injection:** NeMo Guardrails and TrustyAI provide two independent layers of input/output screening, and the observability stack enables detection of bypasses.

This recommendation is conditional on the MOC addressing the policy decisions in Section 5. The technical infrastructure is in place; what remains is governance.

### Suggested Next Steps

1. **MOC policy review** of Sections 5 and 6 to determine which conditions can be met and which require further discussion.
2. **Pilot deployment** for a single research group or course to validate the security posture in a real multi-user scenario before broader rollout.
3. **Secrets management deployment** using ESO + external Vault as the first post-adoption enhancement.
4. **MCP Gateway evaluation** when it reaches general availability, to enable tool-level governance for multi-tenant use.

---

## Appendix A: Detailed Architecture

For full technical details on each security layer, see the following documentation:

- [Architecture Overview](architecture.md) — full security stack design, traffic flows, and network diagrams
- [How Each Layer Works](how-it-works.md) — plain-language explanation of every security layer
- [NeMo Guardrails](nemo-guardrails.md) — LLM input/output guardrails, test results, configuration
- [TrustyAI Orchestrator](trustyai-orchestrator.md) — content safety orchestration, PII detection
- [Network Policies](network-policies.md) — Kubernetes ingress isolation rules
- [Admin Network Policy](admin-network-policy.md) — cluster-level egress control
- [Egress Firewall](egress-firewall.md) — DNS-based domain filtering
- [Observability](observability.md) — MLflow trace capture via OTEL Collector
- [NVIDIA OpenShell](openshell.md) — per-session agent sandboxing
- [NemoClaw](nemoclaw.md) — self-sandboxed agent deployment
- [Future Work](future-work.md) — Vault and MCP Gateway plans

## Appendix B: Experiments and Lessons Learned

Over the course of this project, several approaches were tried that did not work as expected. These are documented in full in the repository and summarized here for context:

| What Was Tried | What Happened | Resolution |
|---------------|---------------|------------|
| Wildcard DNS in EgressFirewall | OVN-Kubernetes silently ignores wildcards despite the API accepting them | Replaced with explicit subdomain entries |
| NeMo's built-in jailbreak heuristics | Requires PyTorch, which is not in the container image | Used LLM-based self-check flows instead |
| Embedding-based flow matching in NeMo | Requires HuggingFace model downloads, blocked by EgressFirewall | Switched to keyword-based (`simple`) provider |
| Cluster Tempo for tracing | NetworkPolicy and mTLS constraints prevent namespace-level access | Deployed in-namespace MLflow as alternative |
| In-cluster Vault deployment | Requires `anyuid` SCC and ClusterRoleBindings (cluster-admin) | Identified ESO + external Vault as alternative |
| Standard OTLP port 4317 | Blocked by AdminNetworkPolicy | OTEL Collector listens on port 8080 instead |
| Presidio PII detection via LiteLLM hooks | Egress to Presidio pods blocked by ANP; hooks failed silently | Superseded by TrustyAI's built-in detector |

These failures informed the final architecture. Each workaround is documented to prevent future teams from re-encountering the same issues and to illustrate the constraints of operating within namespace-level permissions on a shared cluster.

## Appendix C: Repository

All configuration templates, documentation, and the NeMo Guardrails proxy are available in the [openclaw-guardrails](https://github.com/) repository. Configuration templates use placeholder values and contain no credentials.
