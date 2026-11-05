# Pending additions: never assume deployed availability

Status checked 2026-11-04. All PRs below were open at preparation. Re-check their
status, final schema and released dependency versions before using these signals
as evidence. Commit hashes in [source-manifest.json](source-manifest.json) pin the
reviewed proposal. They are not a recommendation to deploy Git overrides.

| Workstream | PR | Expected signal | Availability gate |
|---|---|---|---|
| Graph traversal scope | [hopr-impls#62](https://github.com/hoprnet/hopr-impls/pull/62) | Named graph traversal spans, returned count, duration and empty-result reason; observation details at TRACE | Published graph crate containing the final change and consumed by the binary |
| Routing/session scope | [hoprnet#8604](https://github.com/hoprnet/hoprnet/pull/8604) | Nested routing/selector/graph records, candidate-set correlation, retained session scope across polls/queues/CPU work | Final transport/session integration and built-in dependency version; detailed records require TRACE-capable build |
| Bounded collector/schema | [hopr-impls#76](https://github.com/hoprnet/hopr-impls/pull/76) | Shared collector, exact Safe reads, capped channel/peer/routing/SURB/probe summaries | Reporter started by embedding node with real producers, not merely a library dependency |
| Runtime dump integration | [hoprnet#8614](https://github.com/hoprnet/hoprnet/pull/8614) | Managed periodic INFO `node_state` events and transport/probe observation wiring | Final hopr-lib/transport integration consumed by the application and reporting enabled |
| Strategy-state observer | [hopr-strategy#130](https://github.com/hoprnet/hopr-strategy/pull/130) | Serializable strategy name/state snapshots | Application retains the observer and registers a dump section; not automatically wired by publishing the strategy crate |

## Periodic dump proposal

The inspected collector and host docs describe:

- `HOPR_STATE_DUMP_INTERVAL` as integer seconds; default 60, `0` disables reporting
  (and host producer counters); enabled values 5–86400. Invalid values fail host
  startup. This behavior is proposed, not an existing universal environment knob.
- One managed INFO event per interval, including idle intervals, with
  `.fields.event == "node_state"` and a JSON string in `.fields.state` when encoded
  through the inspected tracing JSON formatter. Reporter cancellation belongs to
  the host's process list. No `state-dump` Cargo feature is required by this proposal.
- A **32 KiB JSON payload** cap in the inspected implementation, excluding logger
  envelope; an older PR description says 24 KiB. Use the final code/schema, not
  that stale description, when validating the limit.
- `schema_version`, sequence, collection timestamp/duration, node/Safe identities,
  Safe balance/allowance, channels, connected/announced peers and routing/probe/SURB
  counters, plus optional named sections. Token amounts are decimal base-unit
  strings. Do not turn them into floating-point token values during extraction.
- Section status distinguishes `ok`, `partial`, `error`, `timeout` and `unavailable`.
  Missing detail must not become zero. Read scope/freshness/error/overflow fields.
- Source-visible local channels, not a guaranteed historical inventory of every
  closed channel or an atomic/freshness-verified chain snapshot. Up to sixteen
  underfunded outgoing channels and four problematic samples are retained.
  Incoming channels are not classified as locally underfunded.
- Problem samples carry counterparty/direction/status/balance and graph
  `is_connected`, transport connection state and `probe_success_rate` as available.
  Missing observations remain explicit; use omitted counts for capped coverage.
- Resolver counts include cached requests; selector counters represent actual
  searches and phase outcomes. Per-destination tables are capped. Replacement
  samples can carry `partial_window`; aggregate overflow preserves lost detail.
- SURB mint/return counters are independent of graph telemetry drains. Sibling
  pairs are bounded, with compact numeric path slots; mapping slots back to peers
  needs a compatible graph view. They are not per-window delivery probabilities.
- Matched replies and real probe expiries are counted; late/unknown pongs do not
  create another success. Disabled/unregistered producer is not an idle zero.
- Up to four optional named sections, each capped at 2 KiB. Oversized sections
  become a `too_large` marker. A strategy observer reports operational state,
  **not** the exact budget/reason behind a `fund pass stopped` warning.

Given a confirmed compatible capture, extract JSON without replacing the raw log:

```sh
jq -c '
  select(.fields.event? == "node_state")
  | .fields.state?
  | select(type == "string")
  | fromjson
' node.jsonl
```

Also inspect `node_state` records with encoding failures or no `state`; the
extraction intentionally skips those, so its line count is not the emission count.
An absent record can mean older binary, disabled reporter, not-yet-ready node,
filtering, task failure or missing capture—not a healthy idle node.

## Span proposal

Graph, selector and routing spans propagate the active parent instead of copying
session identifiers into every record. The inspected routing README distinguishes
DEBUG request/search summaries from TRACE nested detail, cache loads, candidate
weights and actual draws. Do not assume every nested stage is present at DEBUG.

Follow graph query → candidates → validation/cache set → selected route with the
available identifiers. Trace logging that was compiled out cannot be enabled by
`RUST_LOG` after the fact. A complete local session tree also does not demonstrate
cross-node distributed trace propagation; prove that separately from actual data.

## Promotion checklist after releases

1. Replace proposal references with released versions/commit provenance actually
   consumed by HOPRd/edge-client. Do not infer release inclusion from PR closure.
2. Capture startup, traffic, failure and recovery with the chosen filters/build.
   Retain one idle window and a disabled-reporting control.
3. Check the final schema, exact units, limits, unavailable/partial/error cases,
   sampling omissions and parent/session joins against emitted records.
4. Compare representative CPU/runtime and log volume. New counters/INFO records
   are not free merely because payload size is bounded.
5. Feed a real capture to the analysis workflow; verify it reports unknowns and
   can follow the fault using artifact evidence. Test both baseline and new builds.
6. Move verified material into the baseline inventory and keep historical version
   notes for older support bundles. Record the validation build and date.
