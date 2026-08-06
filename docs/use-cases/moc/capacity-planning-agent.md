# Capacity Planning Agent

## Description

MOC needs to know when to add nodes, when GPU capacity will be exhausted, and which tenants are driving growth — before it becomes an emergency. The Capacity Planning Agent tracks resource consumption trends over time, forecasts when capacity thresholds will be hit, and recommends scaling or rebalancing actions. It also identifies waste — idle GPUs, orphaned PVCs, over-provisioned namespaces that requested large quotas but aren't using them. Without this, capacity planning is a manual spreadsheet exercise that often misses fast-moving trends until they become crises.

## What It Should Do

- Track GPU, CPU, memory, and storage utilization across the cluster over time — not just current snapshots but trends
- Break down consumption by tenant namespace so MOC can see who's driving growth
- Forecast when capacity thresholds will be hit based on current trends (e.g., "at the current rate, GPU capacity will be fully allocated in 3 weeks")
- Identify waste and idle resources: GPUs allocated but sitting at 0% utilization for days, PVCs that haven't been accessed in weeks, namespaces with large quotas and minimal actual usage
- Detect seasonal patterns — e.g., end-of-semester surges from university tenants running training jobs before deadlines
- Generate weekly or monthly capacity reports with visualizations (or structured data an admin can drop into a dashboard)
- Recommend actions: scale up node pools, rebalance workloads across nodes, reach out to tenants about idle resources, adjust quotas
- Alert when capacity usage crosses configurable thresholds (e.g., 80% GPU allocation)

## What It Shouldn't Do

- Automatically scale the cluster, add nodes, or modify quotas — it recommends, admins execute
- Contact tenants directly about their idle resources — it reports to admins, who decide how to handle tenant communication
- Replace Prometheus or Grafana dashboards — it adds forecasting and analysis on top of existing metrics, not a parallel monitoring stack
- Make procurement decisions — it provides the data, MOC leadership decides the budget
- Track per-pod or per-container granularity — it works at the namespace and node level for capacity planning; fine-grained workload analysis is a different agent's job

## Tools / Plugins / MCPs Needed

- **Prometheus API access (with long-term storage)** — the agent needs to query historical metrics, not just current values. If MOC runs Thanos or a similar long-term Prometheus store, the agent should query that. Without historical data, forecasting isn't possible
- **Kubernetes/OpenShift MCP** — to query current resource quotas, PVC status, node allocatable capacity, and namespace resource requests/limits
- **LLM inference endpoint** — for trend analysis, anomaly detection, and generating human-readable reports and recommendations
- **Slack or Discord plugin** — for posting reports and threshold alerts to the admin channel
- **Persistent storage (PVC or local file)** — to store trend data, baselines, and previous report state between runs

## What It Takes to Set Up

- Ensure long-term Prometheus storage is available (Thanos, Mimir, or similar) — without historical metrics, this agent can't forecast. If MOC doesn't already have this, it's a prerequisite
- Give the agent read-only RBAC for namespaces, nodes, PVCs, and resource quotas
- Configure Prometheus API access with appropriate retention window (at least 30 days of history for meaningful trends, 90+ days for seasonal pattern detection)
- Build the trend analysis and forecasting logic — this is the core new capability. It can range from simple linear projection ("at this rate, full in X days") to more sophisticated pattern detection
- Implement the idle resource detection rules — configurable thresholds for what counts as "idle" (e.g., GPU at <5% utilization for >48 hours)
- Set up the reporting schedule (weekly digest, monthly deep report) and connect to admin channel
- Estimated effort: **3-5 weeks**

## Time Saved

Without this, capacity planning is a manual exercise done monthly or quarterly, taking **1-2 full days each time** and often missing fast-moving trends. The agent makes it continuous and eliminates surprise capacity crunches that cause emergency procurement scrambles — which are far more expensive than planned purchases. It also surfaces idle resources that tenants may not realize they're hoarding, recovering capacity without adding hardware.

## New Functionality

Yes. OpenClaw currently operates on point-in-time cluster state — it can tell you what's happening right now but not what's been happening over the last month or what will happen next week. This agent requires time-series analysis, historical trend tracking, and forecasting — capabilities that don't exist in the current agent. The Prometheus long-term storage integration is also new.
