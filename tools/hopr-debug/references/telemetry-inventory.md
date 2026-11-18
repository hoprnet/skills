# Telemetry inventory and availability

Use this page to find HOPRd's collection surfaces and browse metric definitions.
For an exact machine-readable lookup, see the [metric catalog](metric-catalog.json)
and its [query examples](catalog-maintenance.md#look-up-a-signal). To interpret a
captured incident, start with the [debugging workflow](debugging-workflow.md).

Verified against the six default-branch commits in [source-manifest.json](source-manifest.json)
on **2026-11-18**. hoprnet#8604 and hoprnet#8614 are merged into the reviewed baseline.
**Merged source is not proof of inclusion in a deployed release.** No production
endpoint was contacted and no runtime capture was available. Match the operator's
binary/dependency versions, enabled features and actual export first.

This document is the collection map and complete named-metric table for the pinned
scope. Exact event/span declarations are searchable in [log-catalog.jsonl](log-catalog.jsonl),
with interpretation in [logs-and-traces.md](logs-and-traces.md). Component snapshots,
internal counters and profiler outputs are in [state-snapshots.md](state-snapshots.md).
[pending-features.md](pending-features.md) documents the merged PRs and their
integration limits; [catalog-maintenance.md](catalog-maintenance.md) defines coverage.

## 1. Read surfaces before looking for individual signals

| Surface | Verified behavior | Interpretation/availability |
|---|---|---|
| HOPRd text/JSON logs | `HOPRD_LOG_FORMAT=json` enables the JSON formatter; `RUST_LOG` configures the process's EnvFilter | Fields can live in event or ancestor spans. Absent/invalid filters use built-in fallback directives. Missing logs can be filtering/build/capture loss. |
| HOPRd `GET /metrics` | Root endpoint, token-auth middleware, Prometheus text when built with telemetry | Not `/api/v4/node/metrics` in this snapshot. **All `hopr_session_*` lines, HELP and TYPE are filtered out.** Build without telemetry returns failure, not a zero inventory. |
| OTLP metrics/logs/traces | `HOPRD_USE_OPENTELEMETRY`, `HOPRD_OTLP_ENDPOINT`, `HOPRD_OTEL_SIGNALS` | Explicit false disables export. Nonempty HOPRD endpoint auto-enables it. Legacy `OTEL_EXPORTER_OTLP_ENDPOINT` alone does not auto-enable. Default signal is traces; request metrics/logs explicitly when needed. |
| OTLP metric cadence | `HOPRD_METRIC_EXPORT_INTERVAL` supports default/prefix-specific export intervals | Export interval is not necessarily the producer's observation window. Session-prefixed dedicated providers can be OTLP-only. |
| HOPRd health GETs | `/startedz`, `/readyz`, `/healthyz`, `/eligiblez` | Root routes. Running/readiness/health/eligibility are distinct. A degraded-but-not-unavailable chain can pass readiness. |
| Runtime API schema | `/api-docs/openapi.json`, `/swagger-ui`, `/scalar` | Discover the running version's schemas, fields and supported read endpoints before constructing requests. |
| Versioned REST GETs | `/node/version`, `/node/info`, `/node/status`, `/node/configuration`, `/account/addresses`, `/account/balances` under the API base | Base is `/api/v{REST_API_CRATE_MAJOR}`; derive it from the running API/OpenAPI, not a hardcoded historical version. Authentication normally applies. |
| Versioned network/channel GETs | `/channels`, `/channels/{address}`, `/peers/{address}`, `/tickets/statistics`, `/network/connected`, `/network/announced`, `/network/graph`, `/network/price`, `/network/probability` | Read-only inventory/DOT graph, connectivity observations and ticket parameters. Source-visible cache state need not be current chain truth. |
| Session state | Versioned `/session/config/{id}` and `/session/{protocol}` GETs, embedding telemetry APIs, plus OTLP where configured | Do not invent a dedicated REST session-metrics endpoint. Check capabilities and the running OpenAPI/types for available snapshot fields. |
| Component `component_state` | Chain/graph/P2P independent INFO reporters; HOPRd `state-dump` feature is enabled by default in the pinned source | Host integration, interval, logging filter, deployed version and features determine availability. See state-snapshot schema. |
| Historical `node_state` proposal | Earlier hoprnet#8614 design | Superseded by independent `component_state` records; do not assume a combined dump. |
| Profiling | Jemalloc stats/heap artifacts, optional Tokio console, controlled CPU harness | Platform/build/runtime gates apply; see state-snapshots reference. |

Do not include credentials, private keys or the contents of an API-token header in
reports. Use the node's already configured authentication when collecting evidence.
Read GET surfaces; POST ping/session creation or writes are active experiments.

The pinned HOPRd text exporter disables automatic counter suffixes and units. Use
the captured HELP/TYPE and exact names; do not append `_total` to every counter.
Counter names without that suffix and gauges with it both exist. Histogram exports
may include `_bucket`, `_sum`, `_count` series; these are parts of one instrument.
Lazy initialization, telemetry features, disabled strategies and optional subsystems
mean source declarations may not appear in a particular scrape. Startup and restart
boundaries matter; a missing series is not an observed zero.

## 2. Four primary operational signal groups

| Signal | Baseline location/fields | What it does and does not establish |
|---|---|---|
| Graph edge state | `hopr_network_graph` observation records: `balance`, `is_connected`, probe observations/rate as exposed by that version | Point observations for directed edges. Connection/probe unknowns are not false/zero; an edge observation is not an atomic inventory of all channels. Routine observations/traversals are TRACE in current hopr-impls; bounded component dumps and rate-limited DEBUG failures are separate. |
| Funding decisions | `hopr_strategy::channel_lifecycle::pipeline`: `channel-lifecycle: fund pass stopped`, budget/demand records and associated fields | Preserve the actual stop reason and Safe/allowance/pending-work context. `Running` strategy state alone cannot replace a funding-stop explanation. |
| Selector path health | `hopr_transport::path::selector`: `[forward] phase 1 candidates`, `[forward] phase 2 extended candidates`, `[return] candidates`; attached counts/direction/hop context | Query counts, not end-to-end delivered packets. Search results differ from validated candidates and cache draws. |
| Probe outcomes | `hopr_transport_probe::probe`: pongs and `neighbor probe failed`/`loopback probe failed`, `reason=timeout` | Actual reply/expiry events; match peer/path/pseudonym and avoid counting late replies twice. Not an application delivery test. |

These are distinct evidence sources. INFO does not provide a universal historical
per-channel/per-destination snapshot in this baseline. Optional component dump sampling makes
selected detail available compactly but does not make named lists exhaustive.

## 3. Other useful log/trace signals

| Group | Signals to locate in the captured version | Join keys and caution |
|---|---|---|
| Startup | Newer builds: `starting node` (`version`, `features`, `peer`, `chain_addr`), DEBUG `node configuration`, INFO `network information`; older builds: `Node information`; also `OS network tuning specs`, `OpenTelemetry gate decision` | Node/process lifetime; settings and exporter gate explain missing data. See hoprnet#8544 in the logging reference for exact fields. |
| Chain feed | Subscription error/reconnect/status records; last successful chain update | Preserve subscription name when present. Repeated retry is not a successful update. Client and server connection identifiers can differ. |
| Routing | Resolve failures, candidate/validation/cache messages, no-SURB routing waits | Destination/direction/hops, session parent, candidate set when available. Async queues can break context in older versions. |
| SURB telemetry | `surb round-trip pair` with forward/reply, expected/observed; `surb round-trip flush tick` with legs/totals | Minted versus returned is comparative, not absolute delivery probability. Empty flushes can be TRACE-only. |
| Session lifecycle | Session establishment/closure, frame discard/incomplete/duplicate summaries, balancer state | Session ID plus process identity. A periodic reason summary is not a per-frame event. |
| PIX | Cycle phase/stall, gate block, deposit/share/recovery and closure records | Session/cycle/role/phase and actual config. A retransmitted request is not a new cycle. |
| Backpressure | Encoding timeouts, ring-buffer drops, mixer occupancy/delay, CPU queue/task counters | Time-correlate; do not equate a coroutine timeout with stopping a running CPU closure. |

Tracing scope in logs does not by itself establish OTLP trace linkage. Verify the
subscriber, exporter signals, sampling and version before claiming an end-to-end
trace. The pinned hoprnet workspace overrides the tracing release ceiling; the downstream
HOPRd workspace must configure its own build. Runtime TRACE cannot restore events
compiled out of the running binary.

## 4. Metric reading guide

The catalog below records **153 statically named declarations** in the inspected
sources. It is a reviewable source inventory, not an exhaustive list of dynamic,
third-party or runtime-generated series and not a live availability guarantee.
The [JSON catalog](metric-catalog.json) also records source crate, OTLP kind,
histogram bucket expressions, source commits and delivery caveats.
HELP descriptions below are preserved from source; validate units against the
recording site when a name/description is ambiguous. Financial f64 gauges do not
replace exact base-unit chain reads.

- Counter: use reset-aware deltas/rates within matching labels/process lifetimes.
- Gauge: read the actual observed value; a `_total` name does not make it a counter.
- Histogram: preserve units and bucket/sum/count semantics and sampling coverage.
- Multi variants declare label keys; do not assume all possible label values exist.
- `hopr_session_*` declarations below are **excluded from HOPRd REST `/metrics`**
  in this snapshot. They may still be available through configured OTLP/embedding
  instrumentation. Other prefixes can also be absent due to features/providers.
- `auto_funding` and `channel_finalizer` metrics belong to legacy implementations;
  do not expect ChannelLifecycle to update them.

### hopr-types helper semantics

| Wrapper | OTLP instrument | Interpretation |
|---|---|---|
| `SimpleCounter`, `MultiCounter` | `Counter<u64>` | Monotonic observations; Multi keys become string attributes |
| `SimpleGauge`, `MultiGauge` | `UpDownCounter<f64>` | Non-monotonic sum at OTLP; the text exporter presents gauge semantics. `set` records the difference from a shadow value, not a new monotonic event |
| `SimpleHistogram`, `MultiHistogram` | `Histogram<f64>` | Declared boundaries in `buckets_expression`; `record_measure` records **seconds**, while direct `observe` uses the caller's unit |
| `GaugeGuard`, `MultiGaugeGuard` | Existing gauge | Increment on construction, decrement on Drop; no additional named metric |

`init_with_provider` must run before first instrument creation to replace the
global provider. `register_prefix_provider` routes by longest matching name prefix;
the default helper meter scope is `hopr`. Text export disables automatic counter
suffixes, unit suffixes, target-info and scope-info. These helpers define no fixed
metric families of their own.
[Metric helpers](https://github.com/hoprnet/hopr-types/blob/7639c6640a260add482ab6d261a9c11f3d39bac7/src/telemetry/metrics.rs),
[gauge guards](https://github.com/hoprnet/hopr-types/blob/7639c6640a260add482ab6d261a9c11f3d39bac7/src/telemetry/guard.rs).

Specific reading traps:

- `hopr_strategy_channel_lifecycle_required_safe_balance_hopr` and
  `hopr_strategy_pix_last_sweep_hopr` report **base units**, despite the `_hopr`
  suffix. The required balance retains its last successful value if chain reads
  fail; zero and stale are different states.
- PIX `*_uhopr_total` amounts are micro-HOPR (1e-6 HOPR), not base units. Financial
  floating-point gauges can lose precision; exact amounts come from chain reads.
- `hopr_session_*` remains excluded from `/metrics`; node-level `hopr_pix_*`
  aggregates have a different prefix and are eligible for that endpoint. Per-cycle
  PIX histograms do not cover every failed cycle, and undefined share ratios are
  skipped rather than recorded as zero.
- `hopr_tickets_unack_tickets_per_peer` additionally requires
  `HOPR_METRICS_UNACK_PER_PEER=1` (or case-insensitive `true`). Peer/session/destination labels can have high
  cardinality; match them exactly when joining series.
- `hopr_strategy_channel_lifecycle_latency_variance_ms` squares deviations of
  millisecond latencies, so its computed unit is **ms²**, despite the name and
  HELP shorthand. No latency observations also writes zero; that zero alone
  does not prove uniform latency.
  [Recording site](https://github.com/hoprnet/hopr-strategy/blob/c611a8f647fc1c01cb1bc8679d2ba94a2889f149/src/channel_lifecycle/pipeline.rs#L1764-L1777).
- `hopr_strategy_pix_deposits_pending_total` counts **deferral events**, not the
  current number of pending deposits. Replay-filter rotations shorten replay
  recognition coverage; SURB generation bumps follow dead-relayer verdicts.
- `hopr_surb_reserve_holds_total` counts held Exit data packets, once per hold,
  for PIX and non-PIX sessions; `hopr_surb_reserve_hold_seconds` observes hold
  duration on release, cancellation or teardown. Do not add these to PIX fill
  backoff or gate-block counts: they describe different events.
- `hopr_exit_control_packets_total` counts Exit control packets by `kind`,
  `session` and `outcome`, including replies dropped for lack of budget. It is
  not a measure of ticket cost. `hopr_surb_pending_lost_total` counts pending
  SURB records evicted before their replies arrive, not observed packet losses.

These contracts are in the linked declaration/recording files below and the
[PIX metric contract](https://github.com/hoprnet/hoprnet/blob/2dfb22d75cedecb3fd4ccdf7b2347a4248b459a2/transport/session/src/telemetry/pix.rs).
Histogram bucket expressions are Rust declarations, not already-expanded exported
series. The helper adds an implicit positive-infinity bucket; an empty boundary
list delegates boundary choice to the SDK/exporter. Inspect actual exposition.

### hoprnet/hopr-impls

| Instrument name | Declared kind | Label keys | Meaning from source | Definition |
|---|---|---|---|---|
| `hopr_network_health` | SimpleGauge | — | Connectivity health indicator | [source](https://github.com/hoprnet/hopr-impls/blob/fa551bdff192d91dd7a783c8905903105817e935/transport/p2p/src/swarm.rs#L32-L32) |
| `hopr_peer_count` | SimpleGauge | — | Number of all peers | [source](https://github.com/hoprnet/hopr-impls/blob/fa551bdff192d91dd7a783c8905903105817e935/transport/p2p/src/peer_store.rs#L19-L19) |
| `hopr_peers_by_quality` | SimpleHistogram | — | Distribution of the quality score of the node's directly-probed neighbors | [source](https://github.com/hoprnet/hopr-impls/blob/fa551bdff192d91dd7a783c8905903105817e935/graph/src/petgraph/update.rs#L11-L15) |
| `hopr_session_hoprd_target_connections` | MultiGauge | `type` | Number of currently active HOPR session target connections on this Exit node | [source](https://github.com/hoprnet/hopr-impls/blob/fa551bdff192d91dd7a783c8905903105817e935/session-server-forwarder/src/lib.rs#L26-L30) |
| `hopr_transport_p2p_active_connection_count` | SimpleGauge | — | Number of currently active p2p connections as observed from libp2p events | [source](https://github.com/hoprnet/hopr-impls/blob/fa551bdff192d91dd7a783c8905903105817e935/transport/p2p/src/swarm.rs#L23-L26) |
| `hopr_transport_p2p_nat_status` | SimpleGauge | — | Current NAT status as reported by libp2p autonat. 0=Unknown, 1=Public, 2=Private | [source](https://github.com/hoprnet/hopr-impls/blob/fa551bdff192d91dd7a783c8905903105817e935/transport/p2p/src/swarm.rs#L27-L30) |

### hoprnet/hopr-strategy

| Instrument name | Declared kind | Label keys | Meaning from source | Definition |
|---|---|---|---|---|
| `hopr_strategy_auto_funding_failure_count` | SimpleCounter | — | Count of failed automatic funding attempts | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/auto_funding.rs#L50-L50) |
| `hopr_strategy_auto_funding_funding_count` | SimpleCounter | — | Count of initiated automatic fundings | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/auto_funding.rs#L48-L48) |
| `hopr_strategy_auto_redeem_redeem_count` | SimpleCounter | — | Count of initiated automatic redemptions | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/auto_redeeming.rs#L44-L44) |
| `hopr_strategy_channel_lifecycle_bucket_count` | MultiGauge | `cell` | Number of open channels in each (latency, subnet) bucket cell | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/channel_lifecycle/mod.rs#L142-L146) |
| `hopr_strategy_channel_lifecycle_closes` | SimpleCounter | — | Count of initiated channel closures | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/channel_lifecycle/mod.rs#L112-L115) |
| `hopr_strategy_channel_lifecycle_effective_buckets` | SimpleGauge | — | Effective number of distinct (latency, subnet) bucket cells among open channels (2^H) | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/channel_lifecycle/mod.rs#L135-L138) |
| `hopr_strategy_channel_lifecycle_finalizations` | SimpleCounter | — | Count of initiated channel closure finalizations | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/channel_lifecycle/mod.rs#L117-L120) |
| `hopr_strategy_channel_lifecycle_fundings` | SimpleCounter | — | Count of initiated channel fundings | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/channel_lifecycle/mod.rs#L107-L110) |
| `hopr_strategy_channel_lifecycle_latency_variance_ms` | SimpleGauge | — | Variance of round-trip times (ms) across all open channels | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/channel_lifecycle/mod.rs#L150-L153) |
| `hopr_strategy_channel_lifecycle_opens` | SimpleCounter | — | Count of initiated channel opens | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/channel_lifecycle/mod.rs#L102-L105) |
| `hopr_strategy_channel_lifecycle_required_safe_balance_hopr` | SimpleGauge | — | wxHOPR the safe must hold to satisfy the demand of the most recent successful tick, in base units (may be stale if a required chain read fails) | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/channel_lifecycle/mod.rs#L127-L130) |
| `hopr_strategy_channel_lifecycle_score_axis` | MultiGauge | `axis` | Average per-axis score across open candidates in the last strategy tick | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/channel_lifecycle/mod.rs#L165-L169) |
| `hopr_strategy_channel_lifecycle_subnet_count` | SimpleGauge | — | Number of distinct subnet prefixes among open channels | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/channel_lifecycle/mod.rs#L157-L160) |
| `hopr_strategy_closure_auto_finalization_count` | SimpleCounter | — | Count of channels where closure finalizing was initiated automatically | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/channel_finalizer.rs#L29-L32) |
| `hopr_strategy_pix_deposit_data_total` | MultiCounter | `outcome` | Outcomes of the Exit asking its pool to generate PIX deposit data, per allocation | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/pix/strategy.rs#L81-L85) |
| `hopr_strategy_pix_deposit_tracking_total` | MultiCounter | `outcome` | Outcomes of the Exit waiting for an SSA deposit to land | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/pix/strategy.rs#L87-L91) |
| `hopr_strategy_pix_deposits_failed_total` | SimpleCounter | — | Count of SSA deposits that failed after exhausting retries | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/pix/strategy.rs#L71-L74) |
| `hopr_strategy_pix_deposits_over_budget_total` | SimpleCounter | — | Count of SSA deposits refused because they would cross max_spend_per_window | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/pix/strategy.rs#L66-L69) |
| `hopr_strategy_pix_deposits_pending_total` | SimpleCounter | — | Count of SSA deposits deferred until an earlier Curvy operation is reconciled | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/pix/strategy.rs#L76-L79) |
| `hopr_strategy_pix_deposits_rejected_total` | SimpleCounter | — | Count of SSA deposits refused because they exceed max_ssa_allocation | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/pix/strategy.rs#L61-L64) |
| `hopr_strategy_pix_deposits_total` | SimpleCounter | — | Count of SSA deposits successfully sent by the Entry | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/pix/strategy.rs#L56-L59) |
| `hopr_strategy_pix_keys_recovered_total` | SimpleCounter | — | Count of SSA stealth address private keys reconstructed by the Exit | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/pix/strategy.rs#L93-L96) |
| `hopr_strategy_pix_last_sweep_hopr` | SimpleGauge | — | wxHOPR moved by the most recent SSA sweep, in base units | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/pix/strategy.rs#L105-L108) |
| `hopr_strategy_pix_sweeps_total` | SimpleCounter | — | Count of recovered SSA deposits swept into the Exit's Safe | [source](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/pix/strategy.rs#L98-L101) |

### hoprnet/hopr-types

No production named instruments. This repository implements the metric helpers,
provider routing, text export and scoped gauge guards used by the other crates.
Its `otel_my_*` instruments are test-only; see the helper semantics above.

### hoprnet/hopr-utilities

| Instrument name | Declared kind | Label keys | Meaning from source | Definition |
|---|---|---|---|---|
| `hopr_rayon_execution_seconds` | MultiHistogram | `operation` | Time tasks spend executing in the Rayon thread pool | [source](https://github.com/hoprnet/hopr-utilities/blob/c404d77f7cbd855147e4baba93322d759cbf024d/src/parallelize/mod.rs#L134-L139) |
| `hopr_rayon_outstanding_tasks` | SimpleGauge | — | Current number of tasks queued or running in the Rayon pool | [source](https://github.com/hoprnet/hopr-utilities/blob/c404d77f7cbd855147e4baba93322d759cbf024d/src/parallelize/mod.rs#L142-L145) |
| `hopr_rayon_queue_limit` | SimpleGauge | — | Configured maximum outstanding tasks for the Rayon thread pool | [source](https://github.com/hoprnet/hopr-utilities/blob/c404d77f7cbd855147e4baba93322d759cbf024d/src/parallelize/mod.rs#L147-L150) |
| `hopr_rayon_queue_wait_seconds` | SimpleHistogram | — | Time tasks spend waiting in the Rayon queue before execution starts | [source](https://github.com/hoprnet/hopr-utilities/blob/c404d77f7cbd855147e4baba93322d759cbf024d/src/parallelize/mod.rs#L127-L131) |
| `hopr_rayon_tasks_cancelled_total` | SimpleCounter | — | Total number of Rayon tasks skipped because receiver was already dropped | [source](https://github.com/hoprnet/hopr-utilities/blob/c404d77f7cbd855147e4baba93322d759cbf024d/src/parallelize/mod.rs#L109-L112) |
| `hopr_rayon_tasks_completed_total` | SimpleCounter | — | Total number of Rayon tasks that completed and delivered results | [source](https://github.com/hoprnet/hopr-utilities/blob/c404d77f7cbd855147e4baba93322d759cbf024d/src/parallelize/mod.rs#L103-L106) |
| `hopr_rayon_tasks_orphaned_total` | SimpleCounter | — | Total number of Rayon tasks whose results were discarded after completion | [source](https://github.com/hoprnet/hopr-utilities/blob/c404d77f7cbd855147e4baba93322d759cbf024d/src/parallelize/mod.rs#L115-L118) |
| `hopr_rayon_tasks_rejected_total` | SimpleCounter | — | Total number of tasks rejected due to queue being full | [source](https://github.com/hoprnet/hopr-utilities/blob/c404d77f7cbd855147e4baba93322d759cbf024d/src/parallelize/mod.rs#L121-L124) |
| `hopr_rayon_tasks_submitted_total` | SimpleCounter | — | Total number of tasks submitted to the Rayon thread pool | [source](https://github.com/hoprnet/hopr-utilities/blob/c404d77f7cbd855147e4baba93322d759cbf024d/src/parallelize/mod.rs#L97-L100) |
| `hopr_udp_egress_packet_len` | SimpleHistogram | — | UDP packet lengths on egress | [source](https://github.com/hoprnet/hopr-utilities/blob/c404d77f7cbd855147e4baba93322d759cbf024d/src/network_types/udp.rs#L28-L32) |
| `hopr_udp_ingress_packet_len` | SimpleHistogram | — | UDP packet lengths on ingress | [source](https://github.com/hoprnet/hopr-utilities/blob/c404d77f7cbd855147e4baba93322d759cbf024d/src/network_types/udp.rs#L22-L26) |

### hoprnet/hoprd

| Instrument name | Declared kind | Label keys | Meaning from source | Definition |
|---|---|---|---|---|
| `hopr_strategy_enabled_strategies` | MultiGauge | `strategy` | List of enabled strategies | [source](https://github.com/hoprnet/hoprd/blob/097d1b80ef6fc69ff2c554bf3eab62d692e66cc1/hoprd/src/strategy.rs#L104-L108) |
| `hoprd_http_api_call_count` | MultiCounter | `path`, `method`, `status` | Number of different REST API calls and their statuses | [source](https://github.com/hoprnet/hoprd/blob/097d1b80ef6fc69ff2c554bf3eab62d692e66cc1/rest-api/src/middleware/prometheus.rs#L7-L11) |
| `hoprd_http_api_call_timing_sec` | MultiHistogram | `path`, `method` | Timing of different REST API calls in seconds | [source](https://github.com/hoprnet/hoprd/blob/097d1b80ef6fc69ff2c554bf3eab62d692e66cc1/rest-api/src/middleware/prometheus.rs#L13-L18) |
| `hoprd_http_api_last_used_time` | SimpleGauge | — | The unix timestamp in seconds at which any API endpoint was last fetched | [source](https://github.com/hoprnet/hoprd/blob/097d1b80ef6fc69ff2c554bf3eab62d692e66cc1/rest-api/src/middleware/prometheus.rs#L20-L23) |

### hoprnet/hoprnet

| Instrument name | Declared kind | Label keys | Meaning from source | Definition |
|---|---|---|---|---|
| `hopr_channels_count` | MultiGauge | `direction` | Number of open channels of the node per direction | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/hopr/hopr-lib/src/builder/chain_wiring.rs#L23-L27) |
| `hopr_egress_ring_buffer_dropped` | SimpleCounter | — | Number of packets dropped due to per-peer egress channel overflow (drop-newest) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/protocol/stream.rs#L30-L33) |
| `hopr_exit_control_packets_total` | MultiCounter | `kind`, `session`, `outcome` | Packets the Exit sent, or declined to send, on a peer's behalf, by kind, Session kind and outcome | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/exit_control.rs#L29-L33) |
| `hopr_lib_version` | MultiGauge | `version` | Executed version of hopr-lib | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/hopr/hopr-lib/src/builder.rs#L78-L82) |
| `hopr_mixer_average_packet_delay` | SimpleGauge | — | Average mixer packet delay averaged over a packet window | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/mixer/src/metrics.rs#L18-L21) |
| `hopr_mixer_effective_anonymity_set` | SimpleGauge | — | Estimated effective anonymity set (2^H) of the mixer's current occupancy | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/mixer/src/metrics.rs#L34-L37) |
| `hopr_mixer_packet_delay_ms` | SimpleHistogram | — | Distribution of per-packet mixer output delay in milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/mixer/src/metrics.rs#L24-L28) |
| `hopr_mixer_queue_size` | SimpleGauge | — | Current mixer queue size | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/mixer/src/metrics.rs#L16-L16) |
| `hopr_mixer_window_miss_ratio` | SimpleGauge | — | EMA fraction of packets delivered beyond the hard delay bound due to scheduling overruns | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/mixer/src/metrics.rs#L43-L46) |
| `hopr_node_addresses` | MultiGauge | `peerid`, `address`, `safe_address`, `module_address` | Node on-chain and off-chain addresses | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/hopr/hopr-lib/src/builder.rs#L83-L87) |
| `hopr_packet_decode_timeouts_total` | SimpleCounter | — | Number of incoming packets dropped due to decode timeout (sustained rate indicates Rayon pool saturation) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/protocol/pipeline/mod.rs#L123-L126) |
| `hopr_packet_rejected_count` | MultiCounter | `reason` | Number of incoming packets rejected due various reasons; reason=duplicate_ticket_index counts packets dropped before forwarding because their ticket index was already accepted on the same channel and epoch (replayed index attack); reason=replay counts packets dropped during decoding, as soon as their replay token is derived, because it had already been seen (these are not acknowledged) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/protocol/pipeline/mod.rs#L114-L118) |
| `hopr_packet_ticket_validation_errors` | MultiCounter | `type` | Number of different ticket validation errors encountered during packet processing | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/protocol/pipeline/mod.rs#L127-L131) |
| `hopr_packets_count` | MultiCounter | `type` | Number of processed packets of different types (sent, received, forwarded) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/protocol/pipeline/mod.rs#L109-L113) |
| `hopr_path_length` | SimpleHistogram | — | Distribution of number of hops of sent messages | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/path/planner.rs#L29-L33) |
| `hopr_pix_admission_rejections_total` | MultiCounter | `reason` | Incoming PIX sessions refused before establishment, by reason | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L290-L294) |
| `hopr_pix_closures_total` | MultiCounter | `reason` | Sessions closed by the PIX supervisor, by reason | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L353-L357) |
| `hopr_pix_cycle_accepted_share_fraction` | MultiHistogram | `outcome` | Shares accepted for an SSA cycle over its useful-share target, observed once at finalization; exceeds one for a conforming Entry's surplus | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L328-L333) |
| `hopr_pix_cycle_bytes_released_total` | SimpleCounter | — | Reconstructor-cycle bytes ever returned to the node's live-cycle budget | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L277-L280) |
| `hopr_pix_cycle_bytes_reserved_total` | SimpleCounter | — | Reconstructor-cycle bytes ever reserved at PIX session admission | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L273-L276) |
| `hopr_pix_cycle_egress_packets` | MultiHistogram | `outcome` | Packets served while one SSA cycle held the accounting front, observed once when it finalized | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L316-L321) |
| `hopr_pix_cycle_phase_seconds` | MultiHistogram | `phase` | How long an SSA cycle spent in one supervisor phase, observed when it leaves that phase | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L339-L344) |
| `hopr_pix_cycle_useful_share_fraction` | MultiHistogram | `outcome` | How far an SSA cycle got towards recovery, as useful shares over target, observed once at finalization | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L322-L327) |
| `hopr_pix_cycles_active` | MultiGauge | `phase` | SSA cycles currently retained by PIX supervisors, by phase (excludes recovered tombstones) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L256-L260) |
| `hopr_pix_cycles_total` | MultiCounter | `event` | SSA cycle lifecycle transitions observed by PIX supervisors, by event | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L281-L285) |
| `hopr_pix_deposits_confirmed_uhopr_total` | SimpleCounter | — | Deposit value confirmed for SSA cycles of this Exit, in uHOPR (1e-6 HOPR) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L345-L348) |
| `hopr_pix_deposits_recovered_uhopr_total` | SimpleCounter | — | Deposit value this Exit actually unlocked by fully recovering the cycle, in uHOPR (1e-6 HOPR) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L349-L352) |
| `hopr_pix_egress_packets_total` | MultiCounter | `mode` | Exit-to-Entry data packets admitted by PIX egress gates, by what paid for them | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L295-L299) |
| `hopr_pix_fill_backoff_total` | MultiCounter | `reason` | Times PIX fill held back, by reason | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L358-L362) |
| `hopr_pix_gate_block_seconds` | MultiHistogram | `reason` | How long PIX egress stayed blocked, from first refusal until service resumed or the session closed | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L305-L310) |
| `hopr_pix_gate_blocks_total` | MultiCounter | `reason` | PIX egress block episodes entered, by reason (counted once per episode, not per refused packet) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L300-L304) |
| `hopr_pix_held_shares` | SimpleGauge | — | Shares held by the part builders of live PIX cycles on this Exit, until their polynomial reconstructs | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L269-L272) |
| `hopr_pix_live_cycle_bytes` | SimpleGauge | — | Reconstructor-cycle bytes currently reserved against the node's max_live_cycle_bytes | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L265-L268) |
| `hopr_pix_predeposit_exposure_packets` | SimpleGauge | — | Packets served by currently unfunded PIX fronts and not yet converted into funded service | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L261-L264) |
| `hopr_pix_sessions_active` | MultiGauge | `gate_mode` | Incoming PIX sessions currently supervised by this Exit, by front-gate mode | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L251-L255) |
| `hopr_pix_sessions_stalled` | MultiGauge | `cause` | PIX sessions whose fill is currently not keeping up, by cause | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L334-L338) |
| `hopr_pix_share_insert_failures` | SimpleCounter | — | Number of failed encrypted share insertions into the exit acknowledgement processor | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/protocol/pipeline/mod.rs#L147-L150) |
| `hopr_pix_shares_total` | MultiCounter | `kind` | Supervisor-validated SSA shares newly accepted by this Exit, by whether they advanced reconstruction | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L311-L315) |
| `hopr_pix_ssa_requests_resent_total` | SimpleCounter | — | Successor SsaRequests the PIX supervisor asked to have sent again because the Entry had not answered them | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/pix.rs#L286-L289) |
| `hopr_protocol_ack_received_count` | MultiCounter | `valid` | Number of received acknowledgements | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/protocol/pipeline/mod.rs#L132-L136) |
| `hopr_protocol_ack_sent_count` | SimpleCounter | — | Number of sent message acknowledgements | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/protocol/pipeline/mod.rs#L137-L140) |
| `hopr_replay_filter_rotations_total` | SimpleCounter | — | Replay filter generations dropped because their shard filled; each rotation shortens the window in which a packet is still recognised as a replay | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/replay_filter.rs#L221-L224) |
| `hopr_session_ack_incoming_acknowledged_frames_total` | MultiCounter | `session_id` | Incoming session acknowledgements | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L140-L144) |
| `hopr_session_ack_incoming_retransmission_requests_total` | MultiCounter | `session_id` | Incoming session retransmission requests | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L135-L139) |
| `hopr_session_ack_incoming_segments_total` | MultiCounter | `session_id` | Incoming session segments | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L130-L134) |
| `hopr_session_ack_mode` | MultiGauge | `session_id` | Configured ack mode encoded as None=0, Partial=1, Full=2, Both=3 | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L125-L129) |
| `hopr_session_ack_outgoing_acknowledged_frames_total` | MultiCounter | `session_id` | Outgoing session acknowledgements | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L155-L159) |
| `hopr_session_ack_outgoing_retransmission_requests_total` | MultiCounter | `session_id` | Outgoing session retransmission requests | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L150-L154) |
| `hopr_session_ack_outgoing_segments_total` | MultiCounter | `session_id` | Outgoing session segments | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L145-L149) |
| `hopr_session_dispatched_messages` | MultiCounter | `kind` | Number dispatched HOPR session messages and their classification | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/manager.rs#L99-L103) |
| `hopr_session_established_sessions_count` | SimpleCounter | — | Number of sessions that were successfully established as an Exit node | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/manager.rs#L86-L89) |
| `hopr_session_frame_being_assembled` | MultiGauge | `session_id` | Number of frames currently being assembled | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L105-L109) |
| `hopr_session_frame_completed_total` | MultiCounter | `session_id` | Number of frames successfully completed | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L110-L114) |
| `hopr_session_frame_discarded_total` | MultiCounter | `session_id` | Number of frames discarded by the session protocol | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L120-L124) |
| `hopr_session_frame_emitted_total` | MultiCounter | `session_id` | Number of frames emitted from the sequencer | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L115-L119) |
| `hopr_session_frame_frame_capacity` | MultiGauge | `session_id` | Configured frame buffer capacity | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L100-L104) |
| `hopr_session_frame_mtu_bytes` | MultiGauge | `session_id` | Configured frame MTU in bytes | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L90-L94) |
| `hopr_session_frame_timeout_ms` | MultiGauge | `session_id` | Configured frame timeout in milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L95-L99) |
| `hopr_session_hoprd_clients` | MultiGauge | `type` | Number of clients connected at this Entry node | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/utils-session/src/lib.rs#L67-L71) |
| `hopr_session_initiated_sessions_count` | SimpleCounter | — | Number of sessions that were successfully initiated as an Entry node | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/manager.rs#L90-L93) |
| `hopr_session_lifetime_created_at_ms` | MultiGauge | `session_id` | Session creation time in unix milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L60-L64) |
| `hopr_session_lifetime_idle_ms` | MultiGauge | `session_id` | Session idle time in milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L75-L79) |
| `hopr_session_lifetime_last_activity_at_ms` | MultiGauge | `session_id` | Last session activity time in unix milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L65-L69) |
| `hopr_session_lifetime_pipeline_errors_total` | MultiCounter | `session_id` | Session pipeline processing errors | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L85-L89) |
| `hopr_session_lifetime_state` | MultiGauge | `session_id` | Session lifecycle state encoded as Active=0, Closing=1, Closed=2 | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L80-L84) |
| `hopr_session_lifetime_uptime_ms` | MultiGauge | `session_id` | Session uptime in milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L70-L74) |
| `hopr_session_num_active_sessions` | SimpleGauge | — | Number of currently active HOPR sessions | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/manager.rs#L82-L85) |
| `hopr_session_pix_fill_packets_total` | SimpleCounter | — | PIX fill keep-alives originated by this Exit, above its SURB-level notification rate | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L225-L228) |
| `hopr_session_pix_fill_rate` | MultiGauge | `session_id` | Planned rate in packets per second for the Exit's own PIX fill keep-alives; emission is bounded by the SURB reserve, so see hopr_session_pix_fill_packets_total for what went out | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L220-L224) |
| `hopr_session_pix_gate_mode` | MultiGauge | `session_id` | PIX egress gate mode encoded as Predeposit=0, Funded=1 | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L210-L214) |
| `hopr_session_pix_recovery_progress` | MultiGauge | `session_id` | Recovery progress of a session's most recently advanced SSA, as a ratio of useful shares to target | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L215-L219) |
| `hopr_session_received_error_count` | MultiCounter | `kind` | Number of HOPR session errors received from an Exit node | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/manager.rs#L94-L98) |
| `hopr_session_sent_error_count` | MultiCounter | `kind` | Number of HOPR session errors sent to an Entry node | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/manager.rs#L104-L108) |
| `hopr_session_snapshot_at_ms` | MultiGauge | `session_id` | Session telemetry sample time in unix milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L55-L59) |
| `hopr_session_surb_buffer_estimate` | MultiGauge | `session_id` | Balancer estimate of the SURBs the counterparty holds, bounded by its store and corrected by its reports | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L170-L174) |
| `hopr_session_surb_consumed_total` | MultiCounter | `session_id` | Consumed SURBs per session | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L165-L169) |
| `hopr_session_surb_produced_total` | MultiCounter | `session_id` | Produced SURBs per session | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L160-L164) |
| `hopr_session_surb_rate_per_sec` | MultiGauge | `session_id` | SURB production rate per second, averaged over at least one second; see hopr_surb_balancer_surbs_rate for the net rate | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L180-L184) |
| `hopr_session_surb_refill_in_flight` | MultiGauge | `session_id` | Whether SURB refill is currently configured for a session (1 or 0) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L185-L189) |
| `hopr_session_surb_target_buffer` | MultiGauge | `session_id` | Configured SURB target buffer size | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L175-L179) |
| `hopr_session_time_to_finish_frame` | SimpleHistogram | — | Measures time in milliseconds it takes a frame to be reassembled | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/session/src/processing/reassembly.rs#L29-L33) |
| `hopr_session_transport_bytes_in_total` | MultiCounter | `session_id` | Session ingress bytes | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L190-L194) |
| `hopr_session_transport_bytes_out_total` | MultiCounter | `session_id` | Session egress bytes | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L195-L199) |
| `hopr_session_transport_packets_in_total` | MultiCounter | `session_id` | Session ingress packets | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L200-L204) |
| `hopr_session_transport_packets_out_total` | MultiCounter | `session_id` | Session egress packets | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs#L205-L209) |
| `hopr_start_time` | SimpleGauge | — | The unix timestamp in seconds at which the process was started | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/hopr/hopr-lib/src/builder.rs#L74-L77) |
| `hopr_surb_balancer_control_output` | MultiGauge | `session_id` | Control output of the SURB balancer | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/balancer/controller.rs#L30-L34) |
| `hopr_surb_balancer_current_buffer_estimate` | MultiGauge | `session_id` | Estimated number of SURBs in the buffer | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/balancer/controller.rs#L36-L40) |
| `hopr_surb_balancer_current_buffer_target` | MultiGauge | `session_id` | Current target (setpoint) number of SURBs in the buffer | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/balancer/controller.rs#L42-L46) |
| `hopr_surb_balancer_surbs_rate` | MultiGauge | `session_id` | Estimation of SURB rate per second (positive is buffer surplus, negative is buffer loss) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/balancer/controller.rs#L48-L52) |
| `hopr_surb_balancer_target_error_estimate` | MultiGauge | `session_id` | Target error estimation by the SURB balancer | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/balancer/controller.rs#L24-L28) |
| `hopr_surb_generation_bumps_total` | SimpleCounter | — | SURB generation bumps after a dead relayer verdict | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/lib.rs#L152-L155) |
| `hopr_surb_pending_lost_total` | SimpleCounter | — | SURB records the capacity-bounded pending map could not hold and evicted before their replies came home: an evicted record settles no race and is never written off. A non-zero rate means the map is undersized for the node's outstanding SURBs; raise surb_store.pending_surb_max_bytes | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/lib.rs#L158-L161) |
| `hopr_surb_reserve_hold_seconds` | MultiHistogram | `pix` | How long Exit data stayed held by the SURB keep-alive reserve, by whether the Session runs PIX, observed once per hold on release, cancellation or Session teardown | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/surb.rs#L33-L38) |
| `hopr_surb_reserve_holds_total` | MultiCounter | `pix` | Exit data holds entered by the SURB keep-alive reserve, by whether the Session runs PIX (counted once per held packet, not per poll); distinct from hopr_pix_gate_blocks_total and from hopr_pix_fill_backoff_total, and raised for rate-controlled Sessions without PIX too | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/surb.rs#L28-L32) |
| `hopr_surb_store_admissions_count` | MultiCounter | `event` | Admission-control events in the SURB store, by event kind | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/surb_store.rs#L583-L587) |
| `hopr_surb_store_evictions_count` | MultiCounter | `cache`, `cause` | Number of entries evicted from the SURB store caches, by cache and removal cause | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/surb_store.rs#L571-L575) |
| `hopr_surb_store_evictions_last_interval` | MultiGauge | `cache`, `cause` | Entries evicted from the SURB store caches during the last completed report interval, by cache and removal cause | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/surb_store.rs#L577-L581) |
| `hopr_surb_store_surbs_held` | MultiGauge | `class` | SURBs currently held by the SURB store, by trust class | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/surb_store.rs#L589-L593) |
| `hopr_tickets_count` | MultiCounter | `type` | Number of tickets by type (winning, losing, rejected) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/protocol/pipeline/mod.rs#L141-L145) |
| `hopr_tickets_unack_evictions_total` | SimpleCounter | — | Total number of unacknowledged tickets evicted from cache due to TTL or capacity limits | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/ticket_processing.rs#L43-L46) |
| `hopr_tickets_unack_insertions_total` | SimpleCounter | — | Total number of unacknowledged tickets inserted into cache | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/ticket_processing.rs#L28-L31) |
| `hopr_tickets_unack_lookup_misses_total` | SimpleCounter | — | Total number of ticket lookup failures (unknown ticket) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/ticket_processing.rs#L38-L41) |
| `hopr_tickets_unack_lookups_total` | SimpleCounter | — | Total number of ticket acknowledgement lookups | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/ticket_processing.rs#L33-L36) |
| `hopr_tickets_unack_peer_evictions_total` | SimpleCounter | — | Total number of peer caches evicted from the outer unacknowledged ticket cache | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/ticket_processing.rs#L48-L51) |
| `hopr_tickets_unack_peers_total` | SimpleGauge | — | Number of peers with unacknowledged tickets in cache | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/ticket_processing.rs#L18-L21) |
| `hopr_tickets_unack_tickets_per_peer` | MultiGauge | `peer` | Number of unacknowledged tickets per peer in cache (enable with HOPR_METRICS_UNACK_PER_PEER=1) | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/ticket_processing.rs#L53-L57) |
| `hopr_tickets_unack_tickets_total` | SimpleGauge | — | Total number of unacknowledged tickets across all peer caches | [source](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/protocols/hopr/src/ticket_processing.rs#L23-L26) |

## 5. Fast checks against a local capture

List observed metric metadata, without assuming suffixes:

```sh
rg '^# (HELP|TYPE) ' metrics.prom
```

Inspect a family with its actual labels and timestamps in the same process window:

```sh
rg '^(# (HELP|TYPE) )?hopr_packets_count' metrics.prom
rg '^(# (HELP|TYPE) )?hopr_rayon_' metrics.prom
```

For session fields, inspect the configured OTLP/embedding export; an absent REST
match is expected at the pinned HOPRd version. PromQL examples must use TYPE from
the actual backend, matching labels and reset handling. Do not calculate a path
success rate by dividing mint counts from one interval into returns from another.

## 6. Pinned entry points

- [REST routes and API base](https://github.com/hoprnet/hoprd/blob/097d1b80ef6fc69ff2c554bf3eab62d692e66cc1/rest-api/src/lib.rs)
- [Metric endpoint and session-prefix filter](https://github.com/hoprnet/hoprd/blob/097d1b80ef6fc69ff2c554bf3eab62d692e66cc1/rest-api/src/root.rs)
- [Readiness and health predicates](https://github.com/hoprnet/hoprd/blob/097d1b80ef6fc69ff2c554bf3eab62d692e66cc1/rest-api/src/checks.rs)
- [JSON formatter and log filtering](https://github.com/hoprnet/hoprd/blob/097d1b80ef6fc69ff2c554bf3eab62d692e66cc1/hoprd/src/tracing_setup.rs)
- [OTLP gate, signals, providers and text exporter](https://github.com/hoprnet/hoprd/blob/097d1b80ef6fc69ff2c554bf3eab62d692e66cc1/hoprd/src/telemetry.rs)
- [Metric helper and export semantics](https://github.com/hoprnet/hopr-types/blob/7498c1b701d7ec188c1a0f1edab4674b9f34b67b/src/telemetry/metrics.rs)
- [Baseline selector phases](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/path/selector.rs)
- [Baseline probe outcomes](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/probe/src/probe.rs)
- [SURB mint/return accounting](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/hopr/src/protocol/surb_telemetry.rs)
- [Funding pass decisions](https://github.com/hoprnet/hopr-strategy/blob/de958cb79faf5c165cded725904ba7efc6edb371/src/channel_lifecycle/pipeline.rs)
