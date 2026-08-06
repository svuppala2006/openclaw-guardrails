# Alert Triage Agent

## Description

During a cascading failure on the MOC cluster, admins get buried under dozens of correlated Prometheus/Alertmanager alerts firing at once — most of them symptoms, not causes. The Alert Triage Agent ingests the alert stream, deduplicates and correlates alerts, separates root cause from downstream noise, and posts a single prioritized summary to the on-call channel with recommended investigation steps. Instead of an admin spending 30-60 minutes sorting through alert spam before even starting to diagnose, they get a clear picture in seconds.

## What It Should Do

- Ingest firing alerts from Prometheus/Alertmanager via webhook or polling
- Deduplicate alerts that are firing for the same underlying reason (e.g., 15 pods on the same node all going unhealthy because the node itself is down)
- Correlate alerts across subsystems — if etcd latency spikes and API server requests start timing out, those are the same incident, not two
- Identify the most likely root cause and rank it above the symptom alerts
- Post a single, structured summary to a Slack or Discord on-call channel with: root cause hypothesis, affected tenants/namespaces, severity assessment, and recommended next steps
- Include links or commands the admin can run to verify the diagnosis (e.g., `oc get nodes`, `oc logs`, relevant Grafana dashboard URLs)
- Track whether the alert storm is growing, stabilizing, or resolving and post follow-up updates

## What It Shouldn't Do

- Automatically remediate anything — it diagnoses and recommends, the admin acts
- Suppress or silence alerts in Alertmanager — it's a read-only consumer of the alert stream
- Replace Alertmanager's own grouping and routing — it works on top of it, adding LLM-powered correlation that static rules can't do
- Page people or escalate on its own — it reports into the existing on-call channel and lets humans decide escalation
- Store or forward sensitive alert data outside the cluster boundary

## Tools / Plugins / MCPs Needed

- **Alertmanager webhook receiver or API poller** — to ingest the alert stream into OpenClaw
- **Kubernetes/OpenShift MCP** — so the agent can query cluster state (nodes, pods, events) to enrich its analysis beyond what's in the alert labels
- **Prometheus API access** — to pull recent metrics for context (e.g., "etcd latency has been climbing for 20 minutes, not just a spike")
- **Slack or Discord plugin** — to post summaries to the on-call channel
- **LLM inference endpoint** — for the correlation reasoning (this is where the value over static rules comes from)

## What It Takes to Set Up

- Stand up a webhook endpoint or polling loop that receives alerts from Alertmanager — this is the new integration point that doesn't exist in OpenClaw today
- Give the agent read-only RBAC access to cluster resources so it can enrich alerts with live state
- Configure the Prometheus API endpoint so the agent can pull recent metric context
- Connect a Slack/Discord channel for output
- Build the correlation reasoning layer — this is the bulk of the work. It needs to understand common OpenShift failure patterns (node pressure → pod evictions → service degradation, etc.) and apply that knowledge to incoming alert clusters
- Estimated effort: **2-4 weeks**

## Time Saved

An admin spends 30-60 minutes per incident just sorting through alert noise before they start fixing anything. With a few incidents per month, that's **5-10 hours of senior engineer time recovered monthly** — and faster diagnosis means shorter outages for tenants.

## New Functionality

Yes. OpenClaw doesn't currently do real-time alert stream ingestion or cross-alert correlation. This requires a new Alertmanager integration, a continuous ingestion loop, and a correlation reasoning layer that doesn't exist today.
