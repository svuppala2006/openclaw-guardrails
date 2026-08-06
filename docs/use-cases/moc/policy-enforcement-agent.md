# Policy Enforcement Agent

## Description

MOC runs a shared cluster with multiple tenants, some from different institutions. If a tenant accidentally deploys a privileged container, exposes a service to the internet, pulls from an unapproved registry, or does something that violates MOC's acceptable-use policies, it's a security risk to the entire platform. The Policy Enforcement Agent continuously audits tenant workloads against MOC's policies and flags violations in real-time, rather than waiting for a quarterly manual audit to catch things weeks after the fact.

## What It Should Do

- Continuously scan tenant namespaces for policy violations on a configurable interval
- Check for common security misconfigurations: privileged containers, hostNetwork/hostPID/hostIPC usage, containers running as root, missing resource limits, services exposed via LoadBalancer or NodePort without approval
- Verify images are pulled from approved registries only (e.g., quay.io, registry.redhat.io — not random Docker Hub images)
- Detect workloads that look like resource abuse (crypto mining signatures, unusually high GPU consumption with no associated research project)
- Flag RBAC misconfigurations — service accounts with more permissions than they should have, role bindings that grant cluster-level access
- Post violations to an admin channel with: tenant namespace, the specific violation, the offending resource, and a recommended remediation
- Optionally notify the tenant directly with a plain-language explanation of what's wrong and how to fix it
- Generate periodic compliance reports summarizing the state of all tenants

## What It Shouldn't Do

- Automatically delete, block, or modify tenant workloads — it flags violations, admins and tenants remediate
- Replace OPA/Gatekeeper or Kyverno admission controllers — those prevent violations at deploy time, this agent catches things that slip through or were deployed before policies were in place
- Make judgment calls on research workloads it doesn't understand — if a workload looks unusual, it flags it for human review rather than assuming it's malicious
- Access tenant data, secrets, or application logs — it audits configurations, not content
- Enforce policies that haven't been explicitly defined — no surprises for tenants

## Tools / Plugins / MCPs Needed

- **Kubernetes/OpenShift MCP** — read-only access across all tenant namespaces to inspect pod specs, service types, RBAC bindings, and security contexts
- **Policy definition file** — a structured config (YAML or markdown) where MOC admins define what's allowed and what's not. The agent reads this as its ruleset
- **Slack or Discord plugin** — for posting violation alerts to the admin channel
- **LLM inference endpoint** — for analyzing ambiguous cases (e.g., "this workload has an unusual resource pattern, does it look like abuse?") though most checks are deterministic
- **Optional: tenant notification channel** — if MOC wants the agent to message tenants directly about their violations

## What It Takes to Set Up

- Define MOC's acceptable-use policies in a structured format the agent can consume — this is mostly a documentation exercise, turning implicit policies into explicit rules
- Give the agent cluster-reader RBAC (read-only across all namespaces)
- Build the continuous scan loop — similar to the Cluster Health Agent, a daemon or cron on a configurable interval
- Implement the deterministic policy checks — most of these are straightforward Kubernetes API queries (does this pod have `privileged: true`? is this service type `LoadBalancer`?)
- Add the LLM layer for ambiguous cases — resource abuse detection, unusual workload pattern analysis
- Connect output to admin channel and optionally to tenant notification
- Estimated effort: **1-3 weeks** (lighter than the others since most checks are deterministic, not LLM-heavy)

## Time Saved

Manual security audits across a multi-tenant cluster are typically done quarterly and take **2-3 full days each time**. This agent makes auditing continuous, so those quarterly marathons go away. More importantly, it catches violations in real-time instead of weeks later — preventing the far more expensive cleanup of a security incident that could have been caught on day one.

## New Functionality

Partially new. OpenClaw can already query cluster resources and analyze configurations on demand. The new pieces are a policy definition format (so admins can codify their rules), a continuous scanning loop, and a violation notification pipeline. It's a lighter lift than the other agents since the checks are mostly deterministic rather than requiring deep LLM reasoning.
