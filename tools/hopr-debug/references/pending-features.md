# Merged telemetry changes and version boundaries

Reviewed **2026-11-18** against the pinned source snapshots in
[source-manifest.json](source-manifest.json). Merged source alone does not prove
a deployed binary contains it: check the binary version, resolved dependencies,
features, runtime configuration, log filter, and capture.

| Change | Merge | What it provides |
|---|---|---|
| [hoprnet#8604](https://github.com/hoprnet/hoprnet/pull/8604) | 2026-11-13 | Local session/routing span propagation and candidate-set correlation |
| [hoprnet#8614](https://github.com/hoprnet/hoprnet/pull/8614) | 2026-11-14 | Host-owned state-dump interfaces and optional transport observations |
| [hoprd#287](https://github.com/hoprnet/hoprd/pull/287) | 2026-11-17 | A host adapter starting independent chain, graph, and P2P reporters |
| [hopr-impls#62](https://github.com/hoprnet/hopr-impls/pull/62), [hopr-impls#76](https://github.com/hoprnet/hopr-impls/pull/76) | 2026-11-11 / 2026-11-12 | Graph diagnostics and independent bounded component snapshots |
| [hopr-strategy#130](https://github.com/hoprnet/hopr-strategy/pull/130) | 2026-11-11 | Strategy-state observer, scheduled and exported by the host |

## Routing and session diagnostics

The merged planner emits DEBUG `candidate validation` and `candidate set installed`.
TRACE `weighted candidate path` records `set_id`, `index`, path, weight, sampling
probability and available measurements; TRACE `candidate selected` carries
`set_id`, `index`, and direction. Match a selection to its candidate using both
identifiers plus node and process lifetime. The merged code also carries local
span context across relevant session, routing and queue operations.
[Planner source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/path/planner.rs).

Graph `selection_id`, `search_id`, and `edge_evaluation_id` are distinct from
planner `set_id` and `index`; join by captured context, not numeric equality.
Graph search diagnostics provide bounded examples and rate-limited DEBUG failures.
Routing context does not add a cross-node trace header to HOPR packets.

The hoprnet workspace includes a release-profile override for the `tracing`
dependency to retain TRACE. A downstream binary workspace must configure its
own build: the pinned HOPRd workspace still declares `release_max_level_debug`.
Check the actual binary before expecting TRACE. See [logs and traces](logs-and-traces.md).

## State-dump integration

hopr-lib exposes an optional `StateDumpFactory`: the host supplies the reporter
and may attach a non-blocking transport observer. The observer can receive
`PathResolved`, `PathSearched`, and `Surb` events. Probe outcomes stay in local
logs and graph telemetry; they are not forwarded to a central probe counter.
[Observer source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/observer.rs).

In the pinned HOPRd source, `state-dump` is a default feature. The application
starts independent INFO `component_state` reporters for chain, graph, and P2P
transport. The chain reporter receives the graph's ticket face value as its
funding policy. HOPRd supplies an empty graph path sample and no transport
observer or extra sections. `HOPR_STATE_DUMP_INTERVAL` defaults to 60 seconds;
`0` disables reporting and enabled intervals are 5–86400 seconds. An invalid
value stops reporting with an error while the node remains running.
[HOPRd adapter](https://github.com/hoprnet/hoprd/blob/097d1b80ef6fc69ff2c554bf3eab62d692e66cc1/hoprd/src/state_dump.rs).
Its lockfile pins hopr-lib to `2a67ecc94bab37d42e48d7246cfb05cc4b5c4fcd`,
which is a descendant of both tracked hoprnet merge commits. That dependency
pin is not the same as the newer hoprnet default-branch catalog snapshot; check
the build's actual sources before expecting any later instrument.

The early PR description proposed a combined `node_state` record with per-
destination and path-pair counters. The merged implementation uses separate
component records and does not ship that combined collector. The former test
adapter/API mismatch described in the 2026-11-13 review is historical, not a
claim about the merged implementation. See [state snapshots](state-snapshots.md)
for schemas, caps, failures and JSON extraction.

Routine probe, selector, route and SURB pair detail is TRACE; the planner keeps
a bounded DEBUG weighted-collection summary and active SURB flushes retain
aggregate INFO records. Actual output still depends on build and filtering.

## Validate a deployment

Record the deployed version and dependencies; then check one idle and one active
interval, component identities, partial/error states, collection timestamps and
logging filters. Confirm how the node was built before interpreting a missing
record. This source review did not validate a production capture.
