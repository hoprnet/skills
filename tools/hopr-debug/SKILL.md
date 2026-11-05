---
name: hopr-debug
description: >
  Interpret HOPRd and HOPR client logs, metrics, traces, and support bundles.
  Use for channel funding, missing paths, connectivity/probe failures, SURB
  starvation, session loss, PIX failures, chain-feed outages, or unexplained
  throughput. Inventory available signals, correlate evidence across components,
  and produce a source-backed incident finding. Use hopr-concepts for protocol
  semantics. Not a token-market, deployment, or automatic remediation skill.
---

# HOPR telemetry and debugging

Prepared for [hoprnet#8410](https://github.com/hoprnet/hoprnet/issues/8410), under
[hoprnet#8456](https://github.com/hoprnet/hoprnet/issues/8456). Source verification:
2026-11-04. This skill reads evidence; it does not change node settings or state.

## Start here

1. Establish **which binary and dependency versions produced the evidence**:
   application/worker version, hopr-lib version, network, node identity, enabled
   features, log filter, capture period, and restart boundaries. Do not apply a
   pending-PR schema to an older release. Source verification against a default
   branch does not establish that a deployed release includes that code.
2. Read [the available surfaces and metric inventory](references/telemetry-inventory.md).
   The companion [JSON catalog](references/metric-catalog.json) records exact
   instrument names, declared types, labels, meanings, and immutable source links.
3. Read the symptom's section in [the debugging workflow](references/debugging-workflow.md).
   Use the existing `hopr-concepts` skill for protocol semantics; implementation
   observations here do not override RFCs or version-specific source.
4. Check [pending features](references/pending-features.md) before interpreting a
   `node_state` dump, new graph spans, or strategy-state snapshots. These were
   open PRs when this document was prepared, not baseline guarantees.
5. Produce a finding with the evidence contract below. An unavailable signal is
   a limitation to record, not permission to manufacture its value.

## Evidence rules

- Preserve the original capture. Work on extracted copies and record file names,
  line ranges or timestamps, source nodes, and time zones. Normalize a working
  timeline to UTC; do not mistake ingestion time for event time or assume clocks
  on different nodes are synchronized.
- Read a complete relevant window, including startup/reconnect and recovery,
  before treating repeated lines as independent failures. Count events and
  unique affected sessions separately.
- Join session records by `session_id` plus node/process lifetime. Join channel
  events by direction, channel identity, and epoch where present. PeerId,
  offchain key, chain address, pseudonym, and SURB path slot are different keys;
  correlate them only with a captured binding or matching source lookup.
- In tracing JSON, relevant fields may be in `.fields`, `.span`, or any entry
  of `.spans`. A child need not repeat its parent's `session_id`. Preserve the
  full span chain rather than flattening away provenance or overwriting fields.
- A log span is not automatically an OpenTelemetry trace. Span inheritance,
  subscriber/exporter setup, sampling, and build-time level filtering are
  separate prerequisites for a complete exported trace.
- Missing metric series, a disabled producer, truncated logs, an empty window,
  and an observed zero are different states. Read HELP/TYPE and exporter
  configuration; do not infer a metric's type from `_total` alone.
- Use counter differences within one process lifetime, with reset handling.
  Gauges can move either way; several session snapshot fields named `_total`
  are declared gauges. Histograms describe sampled observations, not every
  packet. Respect seconds/milliseconds and token/base-unit distinctions.
- A healthy probe, connection, or readiness check alone does not prove a
  channel is sufficiently funded, an application session works, or the chain
  view is current. A bounded candidate list is not an exhaustive topology search.
- Treat the four operational signals separately: graph edge balance/connectivity/
  probe observations; funding-pass outcome; selector phase outcomes; actual
  probe replies/timeouts. Correlate them before assigning a root cause.

## Inspection order

1. Startup identity/configuration and capture/build coverage.
2. Chain-feed health and freshness; node readiness and actual connectivity.
3. Outgoing channel funding and the strategy's recorded decisions.
4. Forward/return selector phases, validation, and routing outcomes.
5. SURB supply and comparative sibling-path behavior.
6. Session/frame/PIX behavior, then mixer, CPU pool, UDP and API pressure.

Prefer existing captures and read-only endpoints. Start at normal INFO. If more
logging is necessary, identify the exact target and a bounded reproduction
window; broad DEBUG can flood storage, and runtime TRACE cannot restore code
compiled out by `release_max_level_debug`. Do not assume changing `RUST_LOG`
affects an already-running process.

## Finding format

Return a concise report containing:

- **Scope:** versions, features, network, identities, capture interval and timezone.
- **Observed failure:** user-visible symptom and when it starts/ends.
- **Evidence:** metric types/labels, measured changes, log excerpts, correlation
  keys and exact artifact locations. Separate direct observations from inference.
- **Assessment:** supported explanation, confidence, alternative explanations,
  and the evidence that would distinguish them.
- **Ownership:** HOPR chain/graph/routing/session/PIX/transport versus the Gnosis
  VPN application/worker/OS layer; leave ownership uncertain when evidence stops
  at a boundary. A successful HOPR echo does not prove tunnel/DNS correctness.
- **Next step:** smallest read-only check or controlled reproduction that resolves
  the uncertainty. Do not automatically fund/close channels, reset stores,
  disable protections, restart a node, or publish logs as part of diagnosis.

For automated consumers, represent unavailable values as `null` with a reason.
Quote the observed schema/version and do not silently synthesize missing stages.
Follow a caller's existing report schema rather than inventing a competing one.

## Companion knowledge and maintenance

- Protocol context: [hopr-concepts](../hopr-concepts/SKILL.md), including its
  implementation and benchmarking references. Verify historical gotchas against
  the captured version before treating them as a currently unfixed defect.
- Application-side handoff: [Gnosis VPN client](https://github.com/gnosis/gnosis_vpn-client)
  and [Gnosis VPN distribution](https://github.com/gnosis/gnosis_vpn). Share the
  versioned incident timeline with the companion debugging skill when available;
  its canonical skill path was not verified in this preparation.
- [Source manifest and verification scope](references/source-manifest.json).
  Update this skill after relevant releases and validate against real captures.
  Issue closure or a merged PR alone is not proof of an end-to-end usable signal.

Always qualify issue/PR numbers with the repository name, including report text
and link labels, for example `hoprnet#8458` or `hopr-impls#76`.
