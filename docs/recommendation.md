# Deploying OpenClaw on the MOC: POC Findings and Recommendation

**Author:** Suhruth Vuppala
**Date:** August 2026
**Audience:** MOC Team
**Status:** Final POC Report

---

## Executive Summary

Several groups on the MOC want to deploy OpenClaw, an open-source AI agent runtime, on the shared OpenShift cluster. However, the security implications of running autonomous AI agents on shared infrastructure have not yet been fully evaluated — agents that can execute code, access tools, and make network calls present risks that standard workloads do not. This project set out to answer a central question: **is it feasible to securely run LLM-driven AI agents on the MOC?**

This document presents the results of that investigation. A proof-of-concept defense-in-depth security stack was designed, implemented, and tested across eight layers — covering inference guardrails, content safety, network isolation, egress control, observability, and agent sandboxing. Two additional layers (secrets management and tool governance) have been scoped with clear implementation paths. The POC demonstrates that securing OpenClaw on the MOC is feasible, but reaching production readiness requires further work and MOC involvement.

### What's Needed to Move Forward

1. **Policy approval** for running AI agent workloads on the cluster, including acceptance of the security posture documented here.
2. **Cluster-admin collaboration** on a small set of cluster-scoped resources that namespace users cannot self-manage (detailed in Section 5).
3. **Commitment to ongoing maintenance** of the security stack as OpenClaw and its dependencies evolve.

---

## 1. What is OpenClaw?

OpenClaw is a free and open-source personal AI agent that can execute tasks via large language models, using messaging platforms as its main user interface. It runs on users' own devices and meets them on the channels they already use — WhatsApp, Telegram, Slack, Discord, Signal, iMessage, and 20+ others — with built-in voice support, a visual Canvas workspace, and an extensible skills ecosystem backed by ClawHub, a public registry with over 13,000 community skills. OpenClaw is model-agnostic, supporting Claude, GPT-4o, Gemini, Nemotron, and locally-hosted models interchangeably. It is developed by the OpenClaw Foundation, a 501(c)(3) non-profit, and has grown to become one of the most-starred open-source projects on GitHub with 380,000+ stars. For Kubernetes environments, dedicated operators (including the claw-operator used in this project) manage OpenClaw instances declaratively through custom resources, handling credential proxying, network isolation, and multi-instance lifecycle.

### The Problem

The MOC serves a diverse community — researchers, professors running university courses, AI startups, and Red Hat engineers — all sharing the same OpenShift infrastructure. Several of these groups want to deploy OpenClaw on the cluster, but the security implications of running autonomous AI agents on shared infrastructure have not yet been fully evaluated. Agents that can execute code, access tools, and make network calls present risks that standard Kubernetes workloads do not.

This project set out to determine the feasibility of securely running OpenClaw on the MOC by building a proof-of-concept security stack. The result is a working defense-in-depth prototype that demonstrates what a production-ready deployment could look like, identifies the remaining gaps, and provides a foundation for the MOC to evaluate whether and how to move forward.

---

## 2. Security Posture: What's in Place

### Threat Model

The security stack was designed around four primary threats:

- **Prompt injection:** A malicious user crafts input that manipulates the LLM into bypassing its instructions, executing unintended actions, or revealing system prompts and internal context.
- **Data exfiltration:** A compromised or manipulated agent sends sensitive data (credentials, user content, internal configuration) to an external attacker-controlled destination.
- **Lateral movement:** An attacker who gains code execution inside one pod attempts to reach other pods, services, or the Kubernetes API to escalate access.
- **Credential leakage:** Agent code accesses secrets (API keys, tokens) stored in the gateway's environment and leaks them through LLM responses, tool calls, or network requests.

The defense-in-depth model ensures no single layer is solely responsible for any of these threats. If one layer is bypassed, others provide fallback protection.

### Deployed Security Layers

| Layer | Technology | What It Protects Against |
|-------|-----------|--------------------------|
| Inference guardrails | NeMo Guardrails (sidecar) | Jailbreaks, prompt injection, abusive content |
| Content safety | TrustyAI GuardrailsOrchestrator | PII leakage (email, SSN, credit cards, phone numbers) |
| Ingress isolation | Kubernetes NetworkPolicies (x4) | Unauthorized pod-to-pod access |
| Egress control | AdminNetworkPolicy | Data exfiltration via non-approved ports |
| Domain filtering | OVN EgressFirewall | Data exfiltration to non-approved external hosts |
| Observability | MLflow + OTEL Collector | Undetected guardrail bypasses, forensic gaps |
| Per-session sandboxing | NVIDIA OpenShell | Agent code escaping to other sessions or the gateway |
| Whole-pod sandboxing | NemoClaw | Agent access to host filesystem, network, and elevated privileges |

### Request Flow and Always-On Protections

A typical request flows through these layers in sequence:

1. **Authentication:** OAuth proxy verifies the user's identity before the request reaches the gateway.
2. **Input guardrails:** NeMo Guardrails evaluates the user's message against safety policies. Blocked messages are refused without ever reaching the LLM.
3. **Content safety:** TrustyAI scans for PII and sensitive content using deterministic pattern matching, independent of LLM judgment.
4. **LLM call:** If the message passes both checks, it is forwarded to the LLM provider via LiteLLM.
5. **Output guardrails:** The LLM's response is evaluated by NeMo's output rails before being returned to the user.

Independent of the request flow, three always-on protections operate continuously:

- **Network isolation:** NetworkPolicies prevent lateral pod-to-pod movement, AdminNetworkPolicy restricts egress ports, and EgressFirewall limits which external domains are reachable — regardless of whether a request is in flight.
- **Observability:** Every LLM interaction is traced end-to-end with token usage, cost, latency, and full request/response content, enabling detection of guardrail bypasses and forensic investigation.
- **Sandboxing:** When an agent executes code, it runs in an isolated disposable pod (OpenShell) or within a locked-down gateway container (NemoClaw), not with full access to the host.

### Test Results

The guardrails system was validated against a targeted set of adversarial test cases as a proof-of-concept, not a comprehensive test suite:

- **7 out of 7** malicious inputs were blocked, including jailbreak attempts, prompt injection, requests for explicit content, abusive language, and PII extraction. All returned a fixed refusal message; the LLM was never called.
- **2 out of 2** safe inputs passed through normally with expected Claude responses, confirming that the guardrails do not over-block legitimate use.
- **PII detection** successfully identifies email addresses, Social Security numbers, credit card numbers, phone numbers, person names, and physical addresses with a configurable confidence threshold.

A production deployment would require broader adversarial testing across a wider range of attack patterns and edge cases. Full test details and screenshots are available in the [NeMo Guardrails documentation](nemo-guardrails.md).

---

## 3. What's Not Yet in Place

Three security gaps remain, in order of priority:

| Layer | Status | Path Forward |
|-------|--------|--------------|
| Credential isolation | Partially addressed | Evaluate OpenShell's built-in credential proxy; Kagenti for multi-tenant scoping |
| Secrets at rest | Not yet implemented | Move from base64-encoded K8s Secrets to encrypted, audited storage |
| Tool governance | Awaiting upstream GA | Identity-based tool filtering via MCP Gateway |

### Credential Isolation (High Priority)

The core risk is not where secrets are *stored* but whether agent code can *access* them at runtime. The claw-operator already partially addresses this with a MITM credential proxy — the gateway never sees raw LLM API keys, and all outbound traffic is forced through the proxy via NetworkPolicy.

OpenShell may address this further. Its documentation describes an inference routing proxy that injects credentials at the network boundary — agents call a virtual host with placeholder tokens, and real credentials are injected by the proxy without the agent ever seeing them. These capabilities were not fully explored or validated as part of this POC and should be investigated as a next step.

The emerging industry consensus, formalized in the IETF CB4A (Credential Broker for Agents) draft, is that **agents should never hold credentials — a proxy should inject them at the network boundary.** Beyond OpenShell, several other tools implement this pattern and are worth evaluating — particularly for multi-tenant scenarios where different users need different credential scoping:

- **Kagenti (Recommended for multi-tenant)** — Red Hat incubation project providing Kubernetes-native agent lifecycle management with SPIFFE-based workload identity. Agents get cryptographic identities instead of shared API keys, with per-user delegation chains and automatic token exchange. On track for OpenShift AI integration, making it the closest fit for the MOC's OpenShift environment.
- **Kloak** — Uses eBPF to intercept TLS calls at the kernel level. Applications only see placeholder strings; real secrets are injected at the kernel boundary right before encryption. No sidecars or code changes required.
- **Infisical Agent Proxy** — Open-source credential broker with presets for common AI services (Anthropic, OpenAI, GitHub). Injects credentials into outbound requests so the agent never holds them.
- **agentgateway** (Linux Foundation / Solo.io) — Implements the CB4A standard, where a gateway injects credentials at the network boundary based on workload identity.

### Secrets at Rest (Medium Priority)

Currently, six secrets (API keys, SSH credentials, LiteLLM master key) are stored as base64-encoded Kubernetes Secrets. While functional, this provides no audit trail, no automatic rotation, and relies on etcd encryption-at-rest configuration. The recommended path is to use the already-installed External Secrets Operator to sync from the MOC's existing in-cluster Vault instance, which requires no elevated cluster permissions — only a SecretStore CR pointing to the Vault endpoint. This doesn't address runtime credential leakage (that's what credential isolation is for), but it remains an important step for auditing, rotation, and encrypted storage.

### Tool Governance (Future)

MCP Gateway will provide identity-based tool filtering and per-tool authorization, ensuring that different users can only access the tools appropriate to their role. It is currently in developer preview. Kuadrant, the underlying policy engine, is already available on OpenShift.

---

## 4. Resource Overhead

The security stack adds resource consumption on top of the base OpenClaw deployment. The estimates below were **not benchmarked as part of this POC** unless noted — production sizing would require load testing under realistic conditions.

| Component | Pods | CPU | Memory | Storage | Source |
|-----------|------|-----|--------|---------|--------|
| NeMo Guardrails (sidecar) | 0 (sidecar) | 500m-1000m | 1-2Gi | None | NVIDIA docs |
| TrustyAI Orchestrator | 1 (2 containers) | ~200m | ~256Mi-512Mi | None | Community estimate |
| NetworkPolicies / ANP / EgressFirewall | 0 | None | None | None | N/A — enforced by CNI |
| OTEL Collector | 1 | ~100m | ~128-256Mi | None | OpenTelemetry docs |
| MLflow | 1 | ~200-500m | 2Gi+ | 5Gi PVC (SQLite) | Observed in POC (OOMKilled at 1Gi; SQLite not suitable for multi-user production) |
| OpenShell (per session) | 1 per session | 2 CPU | 4Gi | None | NVIDIA docs (configurable) |

### Latency Impact

The guardrails add latency to each LLM request, though exact overhead was not benchmarked in this POC:

- **NeMo input/output rail checks:** Each check makes an additional LLM call to evaluate safety. NVIDIA benchmarks show a range of 50ms-1.5s per check depending on configuration — optimized NIM classifiers sit at the low end, while LLM-based self-check (used in this POC) sits at the high end.
- **TrustyAI content check:** Pattern-based analysis, expected to be sub-second.

For most use cases (research, coursework, exploratory agent tasks), added latency from guardrails is likely acceptable. For latency-sensitive applications, the guardrails configuration can be tuned — for example, disabling output rails while keeping input rails, switching to faster classifier models, or adjusting the self-check prompts. A production deployment should benchmark latency under realistic load.

---

## 5. Areas Requiring MOC Involvement

The current security stack was built entirely within namespace-level permissions, with one exception: the AdminNetworkPolicy and EgressFirewall were deployed through the [nerc-ocp-config](https://github.com/OCP-on-NERC/nerc-ocp-config) repository via ArgoCD with cluster-admin assistance. Moving beyond the POC would require additional cluster-admin support and policy decisions.

### Cluster-Admin Actions

| Action | Why It's Needed | Effort |
|--------|----------------|--------|
| Maintain AdminNetworkPolicy for OpenClaw namespaces | Egress control cannot be enforced at namespace level alone. ANPs are cluster-scoped and require cluster-admin to create/modify. | Low — already templated, needs replication per namespace |
| Maintain EgressFirewall per namespace | DNS-based domain filtering requires applying a CR per namespace. | Low — already templated |
| Grant privileged SCC for OpenShell namespace | OpenShell init containers set up kernel-level isolation for per-session sandboxing. | One-time |
| Configure ESO SecretStore | The External Secrets Operator is already installed cluster-wide; a SecretStore CR pointing to the MOC's existing in-cluster Vault instance needs to be created. | One-time |
| Support for credential isolation tooling (if pursued) | Tools like Kagenti may require SPIRE server deployment, mutating webhook configuration, or additional ClusterRoleBindings. | TBD — depends on tool selected |

### Questions to Answer Before Moving Forward

1. **Is this class of workload acceptable?** OpenClaw enables LLM-powered agents that can execute code and use external tools. The MOC should determine whether autonomous AI agent workloads are appropriate for the shared cluster and under what conditions.

2. **What multi-tenancy model?** If OpenClaw is offered to multiple user groups (researchers, courses, startups), should each group get a dedicated namespace with its own security stack, or should a shared deployment with role-based access be preferred?

3. **Which model providers are acceptable?** The current deployment uses Claude on Vertex AI via GCP. Are alternative providers needed? Who is responsible for API costs?

4. **What are the data handling requirements?** LLM requests and responses pass through Google's Vertex AI infrastructure. Is this acceptable for the types of data users will process, particularly research involving sensitive or regulated data?

5. **Who owns incident response?** The observability stack (MLflow traces) provides forensic capability, but who is responsible for monitoring traces, responding to guardrail bypass incidents, and updating safety policies?

---

## 6. Risks and Limitations

### Guardrails Are Probabilistic

NeMo Guardrails uses the LLM itself to evaluate safety. This means:

- **False negatives are possible.** A sufficiently sophisticated prompt injection may bypass the self-check. The defense-in-depth model mitigates this: network isolation prevents data exfiltration even if the LLM is manipulated, and observability enables after-the-fact detection.
- **False positives occur.** Legitimate messages may occasionally be blocked. The guardrail prompts and rules can be tuned to adjust the sensitivity-specificity tradeoff.

### Namespace-Level Limitations

Without cluster-admin access, namespace users cannot:

- Create or modify AdminNetworkPolicies or EgressFirewalls
- Grant SCCs required for OpenShell
- Install additional operators if needed in the future
- Access the cluster's Tempo backend for centralized tracing (RBAC and mTLS constraints)

This means the security posture is partially dependent on cluster-admin cooperation. The current workaround (in-namespace MLflow instead of cluster Tempo) is functional but represents a tradeoff.

### Maintenance Burden

The security stack requires ongoing attention:

- **NeMo Guardrails** safety policies need periodic updates as new attack patterns emerge.
- **EgressFirewall** domain allowlists must be updated when model providers change endpoints.
- **TrustyAI** operator updates may require CR migrations.
- **OpenShell** versions must match exactly between the CLI and Helm chart; upgrades require coordination.
- **MLflow** storage will grow over time and needs monitoring/rotation.

Maintenance effort was not formally estimated as part of this POC, but the number of moving parts suggests it is non-trivial and should be factored into any adoption decision.

### Latency Overhead

The guardrails add latency to each LLM request (see Section 4 for details). This should be benchmarked under realistic load before production use, particularly for batch or automated workflows where added latency compounds.

---

## 7. Cost-Benefit Analysis

### What the Security Stack Costs

| Cost Category | Details |
|---------------|---------|
| **Compute resources** | ~3 additional pods (TrustyAI, OTEL Collector, MLflow) plus 1 pod per active OpenShell session. NeMo Guardrails runs as a sidecar. See Section 4 for estimates. |
| **Latency** | 50ms-1.5s per guardrail check depending on configuration. Two checks per request (input + output) with LLM-based self-check at the higher end. |
| **LLM API costs** | Each guardrail check makes an additional LLM call (input check + output check). These are shorter classification calls, not full generations, so they add overhead but do not double the per-request cost. At scale, this is still a meaningful ongoing expense. |
| **Maintenance** | Multiple components with independent update cycles, version compatibility requirements, and policy tuning. Not formally estimated, but non-trivial. |
| **Cluster-admin dependency** | AdminNetworkPolicy, EgressFirewall, SCC grants, and ESO configuration require cluster-admin involvement for initial setup and ongoing changes. |
| **Complexity** | Eight deployed security layers across multiple technologies (NeMo, TrustyAI, OVN, OTEL, MLflow, OpenShell). Debugging issues requires familiarity with all of them. |

### What the Security Stack Provides

| Benefit | Details |
|---------|---------|
| **Prompt injection defense** | Two independent layers (NeMo + TrustyAI) screening inputs and outputs, with observability to detect bypasses after the fact. |
| **Data exfiltration prevention** | Three layers of network control (NetworkPolicies, AdminNetworkPolicy, EgressFirewall) ensure that even a compromised agent can only reach approved destinations on approved ports. |
| **PII protection** | Deterministic pattern-based detection of email addresses, SSNs, credit cards, phone numbers, and other sensitive data before it reaches the LLM or is returned to the user. |
| **Agent isolation** | Per-session sandboxing (OpenShell) prevents agent code from accessing other sessions, the gateway, or the host. |
| **Forensic capability** | End-to-end tracing of every LLM interaction with full request/response content, token usage, cost, and latency. |
| **Credential protection** | Claw-operator's credential proxy keeps LLM API keys away from the gateway process. OpenShell's inference routing proxy likely extends this to agent code (pending validation). |

### What Happens Without It

If the MOC does not adopt a security stack and users deploy OpenClaw on their own:

- **No guardrails.** Users interact directly with LLMs with no input/output screening. Prompt injection, PII leakage, and abusive content are unmitigated.
- **No network isolation.** Agent pods can reach any destination on any port. A compromised agent can exfiltrate data to arbitrary external hosts or move laterally within the cluster.
- **No observability.** There is no record of what agents do, what they send to LLMs, or what they receive back. Incidents cannot be investigated after the fact.
- **No sandboxing.** Agent code executes inside the gateway pod with access to all gateway credentials, environment variables, and the shared filesystem.
- **Fragmented security.** Each user group would need to implement their own security measures, leading to inconsistent protection levels, duplicated effort, and gaps where no one takes responsibility.

### Why the MOC Should Support OpenClaw

The MOC's core mission is enabling research. AI agents are increasingly central to how researchers work — from data analysis and code generation to literature review and experiment automation. Supporting OpenClaw on the MOC means researchers, professors, and startups can access AI agent capabilities through the infrastructure they already use, without each group needing to build, secure, and maintain their own AI stack. A centralized, secured deployment lowers the barrier to entry and ensures consistent protections across all users.

### Open-Weight Models and GPU Costs

The current POC uses Claude on Vertex AI, which incurs per-request API costs. An alternative is running open-weight models (e.g., Nemotron, Llama, Qwen) locally on the MOC's existing GPU nodes, eliminating API costs entirely and keeping all data on-cluster — which addresses the data handling concerns raised in Section 5.

The tradeoff is GPU compute cost. Running inference locally requires dedicated GPU allocation per model:

| Model Size | Typical GPU Requirement | Notes |
|-----------|------------------------|-------|
| 7-8B parameters | 1x GPU (16-24GB VRAM) | Suitable for lightweight agent tasks |
| 13-14B parameters | 1x GPU (24-48GB VRAM) | Better reasoning, still single-GPU |
| 70B+ parameters | 2-4x GPUs | Near-frontier quality, significant resource commitment |

These are general estimates, not benchmarked or validated — actual requirements depend on quantization, batch size, serving framework (vLLM, Ollama, etc.), and the specific GPU hardware available on the MOC. OpenClaw is model-agnostic, so a deployment could use local models for routine tasks and fall back to a cloud provider for more demanding workloads.

### If the MOC Decides Not to Proceed

If the MOC does not support OpenClaw, users who want AI agent capabilities will likely pursue one of these alternatives:

- **Deploy on their own cloud accounts.** Users run OpenClaw on AWS, GCP, or other providers outside the MOC. This works but means no centralized security controls, no MOC oversight, and each user bears the full cost and responsibility of securing their deployment.
- **Use commercial AI platforms directly.** Users go to ChatGPT, Claude, or other hosted services. This avoids infrastructure complexity but offers no agent autonomy, no tool integration, no self-hosted data control, and no customization.
- **Deploy unsecured on the MOC.** Some users may deploy OpenClaw on the MOC without the security stack, resulting in the risks described in "What Happens Without It" above.

None of these alternatives provide the combination of AI agent capabilities, centralized security, and institutional oversight that a supported MOC deployment would offer.

---

## 8. Conclusion

This POC demonstrates that securely running OpenClaw on the MOC is feasible. The defense-in-depth security stack addresses the three core risks identified at the project's outset:

1. **Resource access:** Network isolation (NetworkPolicies + AdminNetworkPolicy + EgressFirewall) constrains what agents can reach. Agent sandboxing (OpenShell or NemoClaw) constrains what agents can do on the host.
2. **Credential leakage:** The claw-operator's credential proxy keeps LLM API keys away from the gateway. OpenShell's inference routing proxy likely provides stronger isolation at the sandbox level, though this was not validated in the POC. A clear path exists to the MOC's existing Vault instance via ESO for secrets at rest.
3. **Prompt injection:** NeMo Guardrails and TrustyAI provide two independent layers of input/output screening, and the observability stack enables detection of bypasses.

The POC is not production-ready — credential isolation, resource benchmarking, and tool governance remain open. But the foundation is in place, and the remaining gaps have clear paths forward.

### Suggested Next Steps

1. **MOC policy review** of Sections 5 and 6 to determine which questions can be answered and which require further discussion.
2. **Evaluate OpenShell's credential isolation** — validate the inference routing proxy and placeholder token system to determine if it meets the MOC's requirements, or whether additional tooling (e.g., Kagenti) is needed for multi-tenant credential scoping.
3. **Pilot deployment** for a single research group or course to validate the security posture in a real multi-user scenario and benchmark resource and latency overhead.
4. **Secrets at rest** — configure ESO to sync from the MOC's existing Vault instance.
5. **MCP Gateway evaluation** when it reaches general availability, to enable tool-level governance for multi-tenant use.

---

## Appendix A: Detailed Architecture

All documentation and configuration templates are available in the [openclaw-guardrails](https://github.com/svuppala2006/openclaw-guardrails) repository. Configuration templates use placeholder values and contain no credentials.

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
| In-cluster Vault deployment | Requires `anyuid` SCC and ClusterRoleBindings (cluster-admin) | Identified ESO + MOC's existing Vault instance as alternative |
| Standard OTLP port 4317 | Blocked by AdminNetworkPolicy | OTEL Collector listens on port 8080 instead |
| Presidio PII detection via LiteLLM hooks | Egress to Presidio pods blocked by ANP; hooks failed silently | Superseded by TrustyAI's built-in detector |

These failures informed the final architecture. Each workaround is documented to prevent future teams from re-encountering the same issues and to illustrate the constraints of operating within namespace-level permissions on a shared cluster.

