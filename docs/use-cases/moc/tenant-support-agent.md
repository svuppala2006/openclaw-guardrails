# Tenant Support Agent

## Description

MOC admins are currently the bottleneck for every tenant question — "why is my pod pending?", "how much GPU quota do I have left?", "why did my job get evicted?" These are all answerable by querying cluster state, but tenants don't have the access or kubectl knowledge to do it themselves. The Tenant Support Agent gives tenants a self-service chat interface that queries their namespace directly and answers in plain language, scoped strictly to their own resources. This frees up MOC admin time and eliminates the wait time tenants currently experience.

## What It Should Do

- Accept natural language questions from tenants via a chat interface (Slack, Discord, or a web UI)
- Identify which tenant is asking and scope all queries to only their namespace(s) — strict isolation
- Query pod status, events, resource usage, quota remaining, recent deployments, and logs within the tenant's namespace
- Explain issues in plain language — not just "CrashLoopBackOff" but "your pod is crashing on startup because it can't find the environment variable `DB_HOST`"
- Suggest fixes the tenant can apply themselves (e.g., "you need to create a ConfigMap with these keys")
- Escalate to MOC admins when the issue is outside the tenant's namespace (node problems, cluster-wide issues, network policy conflicts) with a pre-gathered context summary
- Provide quota and usage summaries on demand ("how much GPU time have I used this week?")

## What It Shouldn't Do

- Show any resources outside the requesting tenant's namespace — no cross-tenant visibility, ever
- Make changes to the cluster — it's read-only; tenants still apply their own fixes
- Answer questions about other tenants, cluster internals, or MOC infrastructure
- Replace the MOC admin team — it handles the common, self-serviceable questions and escalates everything else
- Expose raw YAML, secrets, or credentials in its responses
- Guess at answers when it doesn't have enough information — it should say "I can't determine this, I'm escalating to the MOC team"

## Tools / Plugins / MCPs Needed

- **Kubernetes/OpenShift MCP (namespace-scoped)** — the agent needs read-only access to tenant namespaces, but RBAC must be configured per-tenant so it can only see the requesting tenant's resources
- **Slack, Discord, or web chat plugin** — the tenant-facing communication channel
- **LLM inference endpoint** — for natural language understanding and response generation
- **Tenant directory/identity integration** — something that maps an incoming chat user to their namespace(s). Could be as simple as a config file mapping Slack user IDs to namespaces, or as robust as an OIDC integration
- **Escalation channel plugin** — when the agent can't help, it needs to post a summary to an internal MOC admin channel

## What It Takes to Set Up

- Build or configure a multi-tenant access layer — this is the hardest part. The agent needs to dynamically scope its RBAC based on who's asking. Options range from a simple lookup table to a proper identity-aware proxy
- Set up the tenant-facing chat channel and connect it to OpenClaw's gateway
- Define the escalation rules — what triggers an escalation vs. what the agent handles itself
- Write the agent's system prompt with MOC-specific context (what quotas mean on this cluster, common tenant issues, platform conventions)
- Test tenant isolation thoroughly — a tenant should never be able to trick the agent into revealing another tenant's data
- Estimated effort: **3-5 weeks**

## Time Saved

If MOC handles 5-10 tenant support requests per week, each taking 15-30 minutes of admin context-switching, that's **5-15 hours/week returned to the admin team**. Tenants also stop waiting hours or days for answers to questions the cluster can answer in seconds.

## New Functionality

Yes. OpenClaw today is admin-facing. Making it tenant-facing requires a multi-tenant access layer, per-tenant RBAC scoping, tenant identity mapping, and an external-facing communication channel — none of which exist yet.
