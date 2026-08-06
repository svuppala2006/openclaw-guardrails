# Cluster Health Agent

## Description

Instead of admins reactively responding to pages after something is already broken, the Cluster Health Agent continuously monitors the MOC OpenShift cluster across nodes, etcd, the control plane, and critical operators. It correlates subtle signals that individually don't fire alerts but together indicate a problem forming — like etcd latency creeping up while a node starts showing memory pressure. It reports findings to admins before tenants are ever impacted. This is the "prevent the outage" agent rather than the "diagnose the outage" agent.

## What It Should Do

- Run continuously (polling loop or watch-based) against the cluster API, not just on-demand
- Monitor node conditions, etcd health, API server responsiveness, critical operator status, and certificate expiry
- Track trends over time — not just "is this metric bad right now" but "has this metric been slowly degrading over the last 6 hours"
- Correlate cross-component signals that static Prometheus rules miss (e.g., "etcd compaction falling behind + increasing API server latency + one node flapping = likely etcd storage pressure")
- Maintain state between checks so it can distinguish "new problem" from "ongoing known issue"
- Post proactive findings to an admin channel with severity, affected components, and recommended actions
- Generate periodic health digests (daily or weekly) summarizing cluster condition and any emerging trends
- Flag when tenant workloads are at risk of disruption due to cluster-level issues

## What It Shouldn't Do

- Take any remediation action — it watches and warns, admins act
- Replace Prometheus or Alertmanager — it adds a reasoning layer on top of existing monitoring, not a parallel monitoring stack
- Monitor tenant workloads in detail — it focuses on cluster infrastructure; the Tenant Support Agent handles namespace-level issues
- Generate noise — if the cluster is healthy, it should be quiet. No "everything is fine" spam
- Store historical metrics long-term — it relies on Prometheus/Thanos for that and queries as needed

## Tools / Plugins / MCPs Needed

- **Kubernetes/OpenShift MCP** — read-only access to nodes, pods (control plane namespace), events, certificates, and cluster operators
- **Prometheus API access** — to pull metric trends (etcd latency, API server request duration, node resource utilization) over time windows
- **Slack or Discord plugin** — for posting proactive alerts and health digests to the admin channel
- **LLM inference endpoint** — for the cross-component correlation reasoning
- **Persistent storage (PVC or local file)** — to maintain state between checks (what's already been reported, baseline values, trend history)

## What It Takes to Set Up

- Give the agent cluster-reader RBAC so it can see node conditions, control plane pods, cluster operators, and events
- Configure Prometheus API access so the agent can pull historical metrics for trend analysis
- Build the continuous monitoring loop — a daemon or cron that runs checks on a configurable interval (e.g., every 5 minutes)
- Implement state persistence so the agent doesn't re-alert on known issues and can track trends across check cycles
- Define the correlation heuristics — common OpenShift failure patterns the agent should watch for. This is where MOC-specific operational knowledge gets encoded
- Connect the output to an admin Slack/Discord channel
- Estimated effort: **2-4 weeks**

## Time Saved

Hard to quantify in hours because this agent prevents incidents rather than shortening them. A single prevented outage on a multi-tenant cluster saves **4-8 hours of incident response** plus the reputational cost of tenant disruption. Over a quarter, this is probably the highest-leverage agent on the list.

## New Functionality

Partially new. OpenClaw can already diagnose cluster issues on demand when an admin asks a question. The new pieces are making it proactive — running continuously, maintaining state between checks, and detecting trends over time rather than answering point-in-time questions.
