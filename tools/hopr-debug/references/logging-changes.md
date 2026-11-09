# Logging changes tracked by hoprnet#8456

Reviewed 2026-11-09 from the epic timeline, all 13 current sub-issue timelines,
and linked PR descriptions and changed-file patches: 19 implementation/consumer
PRs plus the existing [skills#9](https://github.com/hoprnet/skills/pull/9) proposal.
Exact heads and merge commits are in [source-manifest.json](source-manifest.json)
under `pr_review`. Merged source does not establish deployed availability; match
the capture's build and dependencies. No real production capture was parsed
end-to-end during this documentation review; the epic's acceptance still needs
that validation.

## Merged changes

| PR | Observed contract and diagnostic use |
|---|---|
| [hoprnet#8509](https://github.com/hoprnet/hoprnet/pull/8509) | INFO frame-loss summaries replace per-frame noise. INFO `new session established` and `session closed` join by `session_id`, with closure `reason`. Failed setup is not an established lifetime. |
| [hoprnet#8544](https://github.com/hoprnet/hoprnet/pull/8544) | INFO `starting node`: `version`, `features`, `peer`, `chain_addr`, before config validation. DEBUG `node configuration`: full `config`. INFO `network information`: `network`, optional `chain_id`, `network_min_ticket_price`, `network_min_win_prob`. Failed network lookup warns, leaving network empty/chain ID absent while preserving ticket minimums. |
| [hoprnet#8549](https://github.com/hoprnet/hoprnet/pull/8549) | Generation eviction `Expired` and `Explicit` causes log DEBUG; size/replacement causes remain WARN. All feed the existing eviction counters/window summaries. Routine expiry alone is not an active-session failure. |
| [hoprnet#8557](https://github.com/hoprnet/hoprnet/pull/8557) | Empty `surb round-trip flush tick` moves to TRACE; active flushes retain INFO `legs`, `expected`, `observed`. No idle INFO tick is expected. |
| [hoprnet#8574](https://github.com/hoprnet/hoprnet/pull/8574) | Malformed messages with trusted boundaries are consumed. Wrong version/impossible length ends the reader after one `invalid session header; terminating session reader` error. Repeated legacy `unparseable packet` errors can represent one buffered message. |
| [hopr-strategy#108](https://github.com/hoprnet/hopr-strategy/pull/108) | `auto-funding tick` and `closure finalizer tick`: INFO for activity, DEBUG for idle. Confirmed channel lifecycle open/fund/close/finalize logs are INFO, per-channel detail DEBUG/TRACE. Background work inherits the initiating tick span. Legacy strategies and ChannelLifecycle are distinct implementations. |
| [hopr-utilities#46](https://github.com/hoprnet/hopr-utilities/pull/46) | CPU-pool tasks enter the caller's captured tracing span, when present at submission. This alone does not fix every queue boundary or install an exporter. |
| [blokli-client#60](https://github.com/hoprnet/blokli-client/pull/60) | `subscribe_*_with_health` returns a stream and health handle. Keepalives count as traffic; default stall threshold is 90 s, configurable/disableable. Reconnects back off; refusal is surfaced once and retries continue slowly. `connections` counts accepted connections including the first, excludes refusals, and stays zero for the in-memory client. |
| [hopr-impls#54](https://github.com/hoprnet/hopr-impls/pull/54) | Checks six subscription handles every 5 s. Stalled/rejected/ended handles degrade a ready chain; recovery restores it. Brief client outages do not flip chain health. Logs identify `subscription`; permanently ended streams can make chain data unavailable. |
| [blokli#554](https://github.com/hoprnet/blokli/pull/554) | DEBUG `SSE connection established`; one `SSE connection closed` record with `connection`, `subscription`, `reason`, `duration_ms`, `events`, `errors`. Close is INFO only when GraphQL errors occurred, otherwise DEBUG. Reasons: `client_gone` and `stream_ended`; neither proves the original fault's cause. |
| [hopr-integration-tests#73](https://github.com/hoprnet/hopr-integration-tests/pull/73) | Retained run folders, TRACE builds and opt-in node/edgli OTLP spans. See capture procedure below. |
| [zelus#14](https://github.com/hoprnet/zelus/pull/14) | Analyzer consumer loads hopr-concepts and emits the A2 handoff contract. Prompt/configuration work does not prove tenant activation or end-to-end capture analysis. Follow the caller's A2 schema when invoked by that workflow. |

The final startup patch does **not** emit every setting/Safe/module/host on one
INFO line as its original PR body suggested. Preserve older `Node information`
and `hopr_lib_version` schemas for historic bundles. hoprnet#8544 also updates
mixer configuration docs: inspect `protocol.mixer.mixer_type` and the actual
engine/build instead of assuming a historical uniform delay.

### Frame-loss summary semantics

`session frames dropped in the last window` reports `session_id`, `emitted`,
`discarded`, `not_reassembled`, `duplicates`, `window_s`. The minimum interval is
30 seconds, checked when events arrive, with a final flush when the last report
owner drops. There is no background timer. Only windows with discarded or
unreassembled frames log INFO; duplicates alone do not trigger it.

`not_reassembled` and `discarded` can count the same frame; `duplicates` counts
segments while `emitted` counts complete frames delivered to the reader. Do not
sum loss columns or derive packet-loss percentages from them. A quiet window
or short excerpt does not establish zero loss.

### Capture a reproducible integration run

In a checkout containing hopr-integration-tests#73, a controlled reproduction can
use the runner:

```sh
HOPR_LOG_DIR=/tmp/hopr-debug-runs \
  RUST_LOG=info,hopr_protocol_session=trace \
  just integration-trace zero_hop
```

`HOPRD_SRC` selects the local hoprd checkout (default `../hoprd`); binaries go
under its `target/trace`. Debug assertions retain TRACE despite
`release_max_level_debug`, with optimizations but no LTO. Use this for diagnosis,
not as a performance baseline. The checkout is built as-is; retain its recorded
branch/revision rather than assuming release-line provenance.

Retained `run-<time>-XXXX` folders include `run.env`, `trace-build.txt`, `run.status`,
`client-<target>.log`, node/localcluster logs under `hoprd-it-*/`, and chain logs
under `chain/`. Retention covers success, failure, interruption and cluster startup
failure. Attached-cluster logs remain in `HOPRD_CLUSTER_DATA_DIR`; shared-chain
logs remain in `CHAIN_DATA_DIR`, with `chain-slice.txt` boundaries for this run.

Harness span export is off by default. With an existing collector, set
`HOPRD_USE_OPENTELEMETRY=true` and `HOPRD_OTLP_ENDPOINT`. Node signals default to
`traces` unless overridden by `HOPRD_OTEL_SIGNALS`. Client export uses OTLP/HTTP.
Correlate `run_id`, `component=hoprd` or `component=edgli`, time and node identity.
Packets carry no trace context, so client/node spans are not automatically one
distributed trace. Node termination or a killed client can lose queued spans.

## Open proposals and closed-unmerged work

| PR | Review status and interpretation |
|---|---|
| [hopr-impls#62](https://github.com/hoprnet/hopr-impls/pull/62) | Open: DEBUG traversal spans/completion and TRACE candidates; no new INFO health classification. |
| [hoprnet#8604](https://github.com/hoprnet/hoprnet/pull/8604) | Open: session context across polls/queues and correlated planner/selector/graph diagnostics. Depends on the graph implementation. |
| [hopr-impls#76](https://github.com/hoprnet/hopr-impls/pull/76) | Open: bounded collector/graph snapshots, sampled search explanations and reduced DEBUG volume. Its newer logging policy differs from hopr-impls#62; do not combine both into an assumed schema. |
| [hoprnet#8614](https://github.com/hoprnet/hoprnet/pull/8614) | Open: optional reporter interfaces/transport observers. Requires `hopr-lib/state-dump` plus an application-installed reporter factory; an environment variable alone does not install it. |
| [hopr-strategy#130](https://github.com/hoprnet/hopr-strategy/pull/130) | Open: strategy-state observer; the consumer must retain/register it. Does not replace funding-budget explanations. |
| [hoprnet#8575](https://github.com/hoprnet/hoprnet/pull/8575) | Closed **without merge**: proposed hopr-utilities 0.17.4 repin. Check the actual dependency graph; closure does not establish inclusion. |
| [hopr-integration-tests#29](https://github.com/hoprnet/hopr-integration-tests/pull/29) | Closed **without merge**: return-path scenarios referenced by the frame-noise issue. Not proof these tests/stack fixes shipped. |

See [pending-features.md](pending-features.md) for proposal contracts. The metric
catalog retains its pinned 2026-11-04 sources; this review does not refresh or
validate every metric declaration.
