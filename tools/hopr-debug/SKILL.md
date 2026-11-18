---
name: hopr-debug
description: >
  Find and interpret HOPRd telemetry, or diagnose HOPR incidents from logs,
  metrics, traces, state snapshots, and support bundles. Use for channel funding,
  routing, connectivity, probes, SURBs, sessions, PIX, chain feeds, or throughput.
---

# HOPR telemetry and debugging

Use this skill to answer either of two questions:

- **What telemetry is available?** Find the signal, what it measures, how it is
  exported, and which source version provides it.
- **What happened to a node or session?** Correlate the captured signals, explain
  what they show, and identify the smallest useful next check.

The inventory covers source snapshots from `hoprnet`, `hopr-impls`,
`hopr-strategy`, `hopr-types`, `hoprd`, and `hopr-utilities`, including
the merged changes in hoprnet#8604 and hoprnet#8614. Sources were checked on
**2026-11-18**. See [source-manifest.json](references/source-manifest.json) for
exact commits and historical PR status. A source declaration does not guarantee
that a deployed binary includes, enables, or exports the signal.

## Choose the right reference

| If you need to... | Start here |
|---|---|
| Find collection endpoints or browse the metric list | [Telemetry inventory](references/telemetry-inventory.md) |
| Look up an exact metric by name, labels, type, description, or source | [Metric catalog](references/metric-catalog.json); see [lookup examples](references/catalog-maintenance.md#look-up-a-signal) |
| Find a log message, span, field, or logging level | [Logs and traces](references/logs-and-traces.md), including queries for the [log catalog](references/log-catalog.jsonl) |
| Interpret component snapshots, internal counters, or profiles | [State snapshots and other diagnostics](references/state-snapshots.md) |
| Investigate a symptom using captured evidence | [Debugging workflow](references/debugging-workflow.md) |
| Interpret the merged PR changes or check their version boundaries | [PR and version notes](references/pending-features.md) |
| Understand historical logging changes or reproduce an integration capture | [Logging changes](references/logging-changes.md) |
| Check coverage or regenerate the catalogs | [Catalog maintenance](references/catalog-maintenance.md) |

The metric catalog and log catalog describe **source declarations**. They are
not measurements. The source manifest records where those declarations came
from; it is not a deployment manifest. For protocol terminology and semantics,
consult [hopr-concepts](../hopr-concepts/SKILL.md).

For readers new to these terms: **HOPRd** is the node daemon; a **SURB** is a
single-use reply block used to route a reply; **PIX** is a HOPR payment protocol;
and **OTLP** is the OpenTelemetry protocol used to export telemetry. A **Safe**
is the on-chain account whose balance and allowance can affect channel funding.

## First establish what you are looking at

For either a telemetry question or an incident, identify the running binary and
its dependencies, network, node and role, enabled features, log filter, capture
period, time zone, and process restarts. Use the running API/OpenAPI and captured
metric HELP/TYPE when available. If version or export configuration is unknown,
state that limitation rather than assuming the pinned source matches the node.

The main evidence surfaces are:

- **Metrics:** numeric counters, gauges, and histograms. The pinned HOPRd
  `GET /metrics` endpoint filters out `hopr_session_*`; configured OTLP or an
  embedding application may expose them. An absent series is not an observed zero.
- **Logs and spans:** events and their operation context. JSON fields may appear
  in `.fields`, `.span`, or `.spans`. A local span is not proof of a complete
  OpenTelemetry trace or a distributed trace across nodes.
- **State and health:** read-only API responses, optional component snapshots,
  health checks, and embedding diagnostics. Their coverage and freshness vary.
  A ready node or successful probe alone does not prove a usable funded path.
- **Profiles:** optional runtime or heap artifacts that help explain resource
  use. Their build, platform, workload, and sampling conditions matter.

The pinned HOPRd source enables independent chain, graph, and transport
`component_state` reporters by default; deployed versions and feature flags can
differ. The earlier combined `node_state` design was superseded. See the
[version notes](references/pending-features.md) for the merged behavior and limits.

## Diagnose an incident

1. **Preserve and scope the evidence.** Work on copies. Record source files,
   timestamps, nodes, versions, restarts, and collection gaps. Normalize a
   working timeline to UTC without assuming node clocks are synchronized.
2. **Check the chain and connectivity.** Examine chain-feed freshness, readiness,
   transport connections, and actual probe replies or timeouts. Keep these as
   separate observations.
3. **Check funding and routing.** Compare outgoing channel balance and Safe
   allowance with the funding strategy's decisions. Follow path search, candidate
   validation, caching, and selection as distinct stages.
4. **Follow the affected traffic.** Examine SURB supply, session lifecycle and
   frame summaries, PIX, mixer, CPU, UDP, and API pressure as relevant. Use the
   symptom-specific steps in the [debugging workflow](references/debugging-workflow.md).
5. **Report the evidence and uncertainty.** Give the symptom and time range,
   versions and capture scope, relevant measurements and artifact locations,
   supported explanation, alternatives, and the next read-only check or
   controlled reproduction. Separate observations from inference.

Use session ID together with node and process lifetime. For channels, include
direction, identity, and epoch when available. Peer IDs, offchain keys, chain
addresses, pseudonyms, and SURB slots are different identifiers: join them only
when the capture or a source lookup establishes the binding.

## Interpret evidence carefully

- Check the declared metric type and units. Counters need reset-aware differences
  within a process lifetime; gauges can move in either direction; histograms
  summarize observations. A `_total` suffix does not establish the type.
- Frame-loss summaries are event-driven, at least 30 seconds apart, with a final
  flush on drop. `discarded` and `not_reassembled` can overlap; `duplicates`
  counts segments. A missing idle-window summary is not proof of zero loss.
- Candidate counts describe searches, not delivered packets; cached resolutions
  may not start a search. SURB mints and returns can cross time windows. Bounded
  snapshots and sampled paths are not exhaustive inventories.
- Logging filters, compiled-out levels, exporter setup, sampling, and truncated
  captures can all hide evidence. Runtime TRACE cannot restore events compiled
  out of a release build. Choose a narrow target and bounded window when a
  controlled reproduction needs more logging.
- Preserve unknowns: distinguish unavailable, disabled, missing, stale, and
  measured zero. For machine-readable output, use `null` with a reason when a
  value is unavailable, unless the caller specifies another report schema.

Prefer existing captures and read-only endpoints. Do not change settings or node
state, fund or close channels, restart a node, or publish captures as part of
analysis unless the user authorizes that action. Qualify issue and PR numbers with
the repository name, such as `hoprnet#8604`.
