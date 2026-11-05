# Telemetry inventory and availability

Verified against the default-branch commits in [source-manifest.json](source-manifest.json)
on 2026-11-04. **Merged source is not proof of inclusion in a deployed release.**
No production endpoint was contacted and no runtime capture was available for this
preparation. Match the operator's binary/dependency versions and actual export first.
Pending additions are separate in [pending-features.md](pending-features.md).

## 1. Read surfaces before looking for individual signals

| Surface | Verified behavior | Interpretation/availability |
|---|---|---|
| HOPRd text/JSON logs | `HOPRD_LOG_FORMAT=json` enables the JSON formatter; `RUST_LOG` configures the process's EnvFilter | Fields can live in event or ancestor spans. Effective filter also includes built-in dependency directives. Missing logs can be filtering/build/capture loss. |
| HOPRd `GET /metrics` | Root endpoint, token-auth middleware, Prometheus text when built with telemetry | Not `/api/v4/node/metrics` in this snapshot. **All `hopr_session_*` lines, HELP and TYPE are filtered out.** Build without telemetry returns failure, not a zero inventory. |
| OTLP metrics/logs/traces | `HOPRD_USE_OPENTELEMETRY`, `HOPRD_OTLP_ENDPOINT`, `HOPRD_OTEL_SIGNALS` | Explicit false disables export. Nonempty HOPRD endpoint auto-enables it. Legacy `OTEL_EXPORTER_OTLP_ENDPOINT` alone does not auto-enable. Default signal is traces; request metrics/logs explicitly when needed. |
| OTLP metric cadence | `HOPRD_METRIC_EXPORT_INTERVAL` supports default/prefix-specific export intervals | Export interval is not necessarily the producer's observation window. Session-prefixed dedicated providers can be OTLP-only. |
| HOPRd health GETs | `/startedz`, `/readyz`, `/healthyz`, `/eligiblez` | Root routes. Running/readiness/health/eligibility are distinct. A degraded-but-not-unavailable chain can pass readiness. |
| Versioned REST GETs | `/node/version`, `/node/info`, `/node/status`, `/node/configuration`, `/account/addresses`, `/account/balances` under the API base | Base is `/api/v{REST_API_CRATE_MAJOR}`; derive it from the running API/OpenAPI, not a hardcoded historical version. Authentication normally applies. |
| Versioned network/channel GETs | `/channels`, `/channels/{address}`, `/network/connected`, `/network/announced`, `/network/graph`, `/network/price`, `/network/probability` | Read-only inventory/DOT graph, connectivity observations and ticket parameters. Source-visible cache state need not be current chain truth. |
| Session state | Versioned session/config/list GETs and embedding session telemetry APIs, plus OTLP where configured | Do not invent a dedicated REST session-metrics endpoint. Check capabilities and the running OpenAPI/types for available snapshot fields. |
| Future periodic `node_state` | Pending companion PRs, not this baseline | Do not assume it exists from INFO logging alone. See pending reference. |

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
| Graph edge state | `hopr_network_graph` observation records: `balance`, `is_connected`, probe observations/rate as exposed by that version | Point observations for directed edges. Connection/probe unknowns are not false/zero; an edge observation is not an atomic inventory of all channels. Some observation detail remains DEBUG in the pinned baseline. |
| Funding decisions | `hopr_strategy::channel_lifecycle::pipeline`: `channel-lifecycle: fund pass stopped`, budget/demand records and associated fields | Preserve the actual stop reason and Safe/allowance/pending-work context. `Running` strategy state alone cannot replace a funding-stop explanation. |
| Selector path health | `hopr_transport::path::selector`: `[forward] phase 1 candidates`, `[forward] phase 2 extended candidates`, `[return] candidates`; attached counts/direction/hop context | Query counts, not end-to-end delivered packets. Search results differ from validated candidates and cache draws. |
| Probe outcomes | `hopr_transport_probe::probe`: pongs and `neighbor probe failed`/`loopback probe failed`, `reason=timeout` | Actual reply/expiry events; match peer/path/pseudonym and avoid counting late replies twice. Not an application delivery test. |

These are distinct evidence sources. INFO does not provide a universal historical
per-channel/per-destination snapshot in this baseline. Pending dump sampling makes
selected detail available compactly but does not make named lists exhaustive.

## 3. Other useful log/trace signals

| Group | Signals to locate in the captured version | Join keys and caution |
|---|---|---|
| Startup | `Node information`, node/build/settings, `OS network tuning specs`, `OpenTelemetry gate decision` | Node/process lifetime; settings and exporter gate explain missing data. |
| Chain feed | Subscription error/reconnect/status records; last successful chain update | Preserve subscription name when present. Repeated retry is not a successful update. Client and server connection identifiers can differ. |
| Routing | Resolve failures, candidate/validation/cache messages, no-SURB routing waits | Destination/direction/hops, session parent, candidate set when available. Async queues can break context in older versions. |
| SURB telemetry | `surb round-trip pair` with forward/reply, expected/observed; `surb round-trip flush tick` with legs/totals | Minted versus returned is comparative, not absolute delivery probability. Empty flushes can be TRACE-only. |
| Session lifecycle | Session establishment/closure, frame discard/incomplete/duplicate summaries, balancer state | Session ID plus process identity. A periodic reason summary is not a per-frame event. |
| PIX | Cycle phase/stall, gate block, deposit/share/recovery and closure records | Session/cycle/role/phase and actual config. A retransmitted request is not a new cycle. |
| Backpressure | Encoding timeouts, ring-buffer drops, mixer occupancy/delay, CPU queue/task counters | Time-correlate; do not equate a coroutine timeout with stopping a running CPU closure. |

Tracing scope in logs does not by itself establish OTLP trace linkage. Verify the
subscriber, exporter signals, sampling and version before claiming an end-to-end
trace. The pinned hoprnet workspace compiles release TRACE out through
`release_max_level_debug`; enabling TRACE at runtime cannot recover those events.

## 4. Metric reading guide

The catalog below records **146 statically named declarations** in the inspected
sources. It is a reviewable source inventory, not an exhaustive list of dynamic,
third-party or runtime-generated series and not a live availability guarantee.
The [JSON catalog](metric-catalog.json) has source commits and delivery caveats.
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

### hoprnet/hopr-impls

| Instrument name | Declared kind | Label keys | Meaning from source | Definition |
|---|---|---|---|---|
| `hopr_network_health` | SimpleGauge | — | Connectivity health indicator | [source](https://github.com/hoprnet/hopr-impls/blob/36fd2732cb489cee4624e1dc7705947044639888/transport/p2p/src/swarm.rs#L32) |
| `hopr_peer_count` | SimpleGauge | — | Number of all peers | [source](https://github.com/hoprnet/hopr-impls/blob/36fd2732cb489cee4624e1dc7705947044639888/transport/p2p/src/peer_store.rs#L19) |
| `hopr_peers_by_quality` | SimpleHistogram | — | Distribution of the quality score of the node's directly-probed neighbors | [source](https://github.com/hoprnet/hopr-impls/blob/36fd2732cb489cee4624e1dc7705947044639888/graph/src/petgraph/update.rs#L11) |
| `hopr_session_hoprd_target_connections` | MultiGauge | `type` | Number of currently active HOPR session target connections on this Exit node | [source](https://github.com/hoprnet/hopr-impls/blob/36fd2732cb489cee4624e1dc7705947044639888/session-server-forwarder/src/lib.rs#L26) |
| `hopr_transport_p2p_active_connection_count` | SimpleGauge | — | Number of currently active p2p connections as observed from libp2p events | [source](https://github.com/hoprnet/hopr-impls/blob/36fd2732cb489cee4624e1dc7705947044639888/transport/p2p/src/swarm.rs#L23) |
| `hopr_transport_p2p_nat_status` | SimpleGauge | — | Current NAT status as reported by libp2p autonat. 0=Unknown, 1=Public, 2=Private | [source](https://github.com/hoprnet/hopr-impls/blob/36fd2732cb489cee4624e1dc7705947044639888/transport/p2p/src/swarm.rs#L27) |

### hoprnet/hopr-strategy

| Instrument name | Declared kind | Label keys | Meaning from source | Definition |
|---|---|---|---|---|
| `hopr_strategy_auto_funding_failure_count` | SimpleCounter | — | Count of failed automatic funding attempts | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/auto_funding.rs#L50) |
| `hopr_strategy_auto_funding_funding_count` | SimpleCounter | — | Count of initiated automatic fundings | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/auto_funding.rs#L48) |
| `hopr_strategy_auto_redeem_redeem_count` | SimpleCounter | — | Count of initiated automatic redemptions | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/auto_redeeming.rs#L44) |
| `hopr_strategy_channel_lifecycle_bucket_count` | MultiGauge | `cell` | Number of open channels in each (latency, subnet) bucket cell | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/channel_lifecycle/mod.rs#L142) |
| `hopr_strategy_channel_lifecycle_closes` | SimpleCounter | — | Count of initiated channel closures | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/channel_lifecycle/mod.rs#L112) |
| `hopr_strategy_channel_lifecycle_effective_buckets` | SimpleGauge | — | Effective number of distinct (latency, subnet) bucket cells among open channels (2^H) | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/channel_lifecycle/mod.rs#L135) |
| `hopr_strategy_channel_lifecycle_finalizations` | SimpleCounter | — | Count of initiated channel closure finalizations | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/channel_lifecycle/mod.rs#L117) |
| `hopr_strategy_channel_lifecycle_fundings` | SimpleCounter | — | Count of initiated channel fundings | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/channel_lifecycle/mod.rs#L107) |
| `hopr_strategy_channel_lifecycle_latency_variance_ms` | SimpleGauge | — | Variance of round-trip times (ms) across all open channels | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/channel_lifecycle/mod.rs#L150) |
| `hopr_strategy_channel_lifecycle_opens` | SimpleCounter | — | Count of initiated channel opens | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/channel_lifecycle/mod.rs#L102) |
| `hopr_strategy_channel_lifecycle_required_safe_balance_hopr` | SimpleGauge | — | wxHOPR the safe must hold to satisfy the demand of the most recent successful tick, in base units (may be stale if a required chain read fails) | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/channel_lifecycle/mod.rs#L127) |
| `hopr_strategy_channel_lifecycle_score_axis` | MultiGauge | `axis` | Average per-axis score across open candidates in the last strategy tick | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/channel_lifecycle/mod.rs#L165) |
| `hopr_strategy_channel_lifecycle_subnet_count` | SimpleGauge | — | Number of distinct subnet prefixes among open channels | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/channel_lifecycle/mod.rs#L157) |
| `hopr_strategy_closure_auto_finalization_count` | SimpleCounter | — | Count of channels where closure finalizing was initiated automatically | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/channel_finalizer.rs#L29) |
| `hopr_strategy_pix_deposit_data_total` | MultiCounter | `outcome` | Outcomes of the Exit asking its pool to generate PIX deposit data, per allocation | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/pix/strategy.rs#L76) |
| `hopr_strategy_pix_deposit_tracking_total` | MultiCounter | `outcome` | Outcomes of the Exit waiting for an SSA deposit to land | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/pix/strategy.rs#L82) |
| `hopr_strategy_pix_deposits_failed_total` | SimpleCounter | — | Count of SSA deposits that failed after exhausting retries | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/pix/strategy.rs#L71) |
| `hopr_strategy_pix_deposits_over_budget_total` | SimpleCounter | — | Count of SSA deposits refused because they would cross max_spend_per_window | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/pix/strategy.rs#L66) |
| `hopr_strategy_pix_deposits_rejected_total` | SimpleCounter | — | Count of SSA deposits refused because they exceed max_ssa_allocation | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/pix/strategy.rs#L61) |
| `hopr_strategy_pix_deposits_total` | SimpleCounter | — | Count of SSA deposits successfully sent by the Entry | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/pix/strategy.rs#L56) |
| `hopr_strategy_pix_keys_recovered_total` | SimpleCounter | — | Count of SSA stealth address private keys reconstructed by the Exit | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/pix/strategy.rs#L88) |
| `hopr_strategy_pix_last_sweep_hopr` | SimpleGauge | — | wxHOPR moved by the most recent SSA sweep, in base units | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/pix/strategy.rs#L100) |
| `hopr_strategy_pix_sweeps_total` | SimpleCounter | — | Count of recovered SSA deposits swept into the Exit's Safe | [source](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/pix/strategy.rs#L93) |

### hoprnet/hopr-utilities

| Instrument name | Declared kind | Label keys | Meaning from source | Definition |
|---|---|---|---|---|
| `hopr_rayon_execution_seconds` | MultiHistogram | `operation` | Time tasks spend executing in the Rayon thread pool | [source](https://github.com/hoprnet/hopr-utilities/blob/1d433273a441e038f4f62f95b14a56c6ee206782/src/parallelize/mod.rs#L134) |
| `hopr_rayon_outstanding_tasks` | SimpleGauge | — | Current number of tasks queued or running in the Rayon pool | [source](https://github.com/hoprnet/hopr-utilities/blob/1d433273a441e038f4f62f95b14a56c6ee206782/src/parallelize/mod.rs#L142) |
| `hopr_rayon_queue_limit` | SimpleGauge | — | Configured maximum outstanding tasks for the Rayon thread pool | [source](https://github.com/hoprnet/hopr-utilities/blob/1d433273a441e038f4f62f95b14a56c6ee206782/src/parallelize/mod.rs#L147) |
| `hopr_rayon_queue_wait_seconds` | SimpleHistogram | — | Time tasks spend waiting in the Rayon queue before execution starts | [source](https://github.com/hoprnet/hopr-utilities/blob/1d433273a441e038f4f62f95b14a56c6ee206782/src/parallelize/mod.rs#L127) |
| `hopr_rayon_tasks_cancelled_total` | SimpleCounter | — | Total number of Rayon tasks skipped because receiver was already dropped | [source](https://github.com/hoprnet/hopr-utilities/blob/1d433273a441e038f4f62f95b14a56c6ee206782/src/parallelize/mod.rs#L109) |
| `hopr_rayon_tasks_completed_total` | SimpleCounter | — | Total number of Rayon tasks that completed and delivered results | [source](https://github.com/hoprnet/hopr-utilities/blob/1d433273a441e038f4f62f95b14a56c6ee206782/src/parallelize/mod.rs#L103) |
| `hopr_rayon_tasks_orphaned_total` | SimpleCounter | — | Total number of Rayon tasks whose results were discarded after completion | [source](https://github.com/hoprnet/hopr-utilities/blob/1d433273a441e038f4f62f95b14a56c6ee206782/src/parallelize/mod.rs#L115) |
| `hopr_rayon_tasks_rejected_total` | SimpleCounter | — | Total number of tasks rejected due to queue being full | [source](https://github.com/hoprnet/hopr-utilities/blob/1d433273a441e038f4f62f95b14a56c6ee206782/src/parallelize/mod.rs#L121) |
| `hopr_rayon_tasks_submitted_total` | SimpleCounter | — | Total number of tasks submitted to the Rayon thread pool | [source](https://github.com/hoprnet/hopr-utilities/blob/1d433273a441e038f4f62f95b14a56c6ee206782/src/parallelize/mod.rs#L97) |
| `hopr_udp_egress_packet_len` | SimpleHistogram | — | UDP packet lengths on egress | [source](https://github.com/hoprnet/hopr-utilities/blob/1d433273a441e038f4f62f95b14a56c6ee206782/src/network_types/udp.rs#L28) |
| `hopr_udp_ingress_packet_len` | SimpleHistogram | — | UDP packet lengths on ingress | [source](https://github.com/hoprnet/hopr-utilities/blob/1d433273a441e038f4f62f95b14a56c6ee206782/src/network_types/udp.rs#L22) |

### hoprnet/hoprd

| Instrument name | Declared kind | Label keys | Meaning from source | Definition |
|---|---|---|---|---|
| `hopr_strategy_enabled_strategies` | MultiGauge | `strategy` | List of enabled strategies | [source](https://github.com/hoprnet/hoprd/blob/2d3ee8925c2ad913801c771b574f922bd9e84fec/hoprd/src/strategy.rs#L104) |
| `hoprd_http_api_call_count` | MultiCounter | `path`, `method`, `status` | Number of different REST API calls and their statuses | [source](https://github.com/hoprnet/hoprd/blob/2d3ee8925c2ad913801c771b574f922bd9e84fec/rest-api/src/middleware/prometheus.rs#L7) |
| `hoprd_http_api_call_timing_sec` | MultiHistogram | `path`, `method` | Timing of different REST API calls in seconds | [source](https://github.com/hoprnet/hoprd/blob/2d3ee8925c2ad913801c771b574f922bd9e84fec/rest-api/src/middleware/prometheus.rs#L13) |
| `hoprd_http_api_last_used_time` | SimpleGauge | — | The unix timestamp in seconds at which any API endpoint was last fetched | [source](https://github.com/hoprnet/hoprd/blob/2d3ee8925c2ad913801c771b574f922bd9e84fec/rest-api/src/middleware/prometheus.rs#L20) |

### hoprnet/hoprnet

| Instrument name | Declared kind | Label keys | Meaning from source | Definition |
|---|---|---|---|---|
| `hopr_channels_count` | MultiGauge | `direction` | Number of open channels of the node per direction | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/hopr/hopr-lib/src/builder/chain_wiring.rs#L23) |
| `hopr_egress_ring_buffer_dropped` | SimpleCounter | — | Number of packets dropped due to per-peer egress channel overflow (drop-newest) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/hopr/src/protocol/stream.rs#L30) |
| `hopr_lib_version` | MultiGauge | `version` | Executed version of hopr-lib | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/hopr/hopr-lib/src/builder.rs#L72) |
| `hopr_mixer_average_packet_delay` | SimpleGauge | — | Average mixer packet delay averaged over a packet window | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/mixer/src/metrics.rs#L18) |
| `hopr_mixer_effective_anonymity_set` | SimpleGauge | — | Estimated effective anonymity set (2^H) of the mixer's current occupancy | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/mixer/src/metrics.rs#L34) |
| `hopr_mixer_packet_delay_ms` | SimpleHistogram | — | Distribution of per-packet mixer output delay in milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/mixer/src/metrics.rs#L24) |
| `hopr_mixer_queue_size` | SimpleGauge | — | Current mixer queue size | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/mixer/src/metrics.rs#L16) |
| `hopr_mixer_window_miss_ratio` | SimpleGauge | — | EMA fraction of packets delivered beyond the hard delay bound due to scheduling overruns | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/mixer/src/metrics.rs#L43) |
| `hopr_node_addresses` | MultiGauge | `peerid`, `address`, `safe_address`, `module_address` | Node on-chain and off-chain addresses | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/hopr/hopr-lib/src/builder.rs#L77) |
| `hopr_packet_decode_timeouts_total` | SimpleCounter | — | Number of incoming packets dropped due to decode timeout (sustained rate indicates Rayon pool saturation) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/hopr/src/protocol/pipeline/mod.rs#L108) |
| `hopr_packet_rejected_count` | MultiCounter | `reason` | Number of incoming packets rejected due various reasons; reason=duplicate_ticket_index counts packets dropped before forwarding because their ticket index was already accepted on the same channel and epoch (replayed index attack) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/hopr/src/protocol/pipeline/mod.rs#L99) |
| `hopr_packet_ticket_validation_errors` | MultiCounter | `type` | Number of different ticket validation errors encountered during packet processing | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/hopr/src/protocol/pipeline/mod.rs#L112) |
| `hopr_packets_count` | MultiCounter | `type` | Number of processed packets of different types (sent, received, forwarded) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/hopr/src/protocol/pipeline/mod.rs#L94) |
| `hopr_path_length` | SimpleHistogram | — | Distribution of number of hops of sent messages | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/hopr/src/path/planner.rs#L28) |
| `hopr_pix_admission_rejections_total` | MultiCounter | `reason` | Incoming PIX sessions refused before establishment, by reason | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L276) |
| `hopr_pix_closures_total` | MultiCounter | `reason` | Sessions closed by the PIX supervisor, by reason | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L339) |
| `hopr_pix_cycle_accepted_share_fraction` | MultiHistogram | `outcome` | Shares accepted for an SSA cycle over its useful-share target, observed once at finalization; exceeds one for a conforming Entry's surplus | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L314) |
| `hopr_pix_cycle_bytes_released_total` | SimpleCounter | — | Reconstructor-cycle bytes ever returned to the node's live-cycle budget | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L263) |
| `hopr_pix_cycle_bytes_reserved_total` | SimpleCounter | — | Reconstructor-cycle bytes ever reserved at PIX session admission | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L259) |
| `hopr_pix_cycle_egress_packets` | MultiHistogram | `outcome` | Packets served while one SSA cycle held the accounting front, observed once when it finalized | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L302) |
| `hopr_pix_cycle_phase_seconds` | MultiHistogram | `phase` | How long an SSA cycle spent in one supervisor phase, observed when it leaves that phase | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L325) |
| `hopr_pix_cycle_useful_share_fraction` | MultiHistogram | `outcome` | How far an SSA cycle got towards recovery, as useful shares over target, observed once at finalization | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L308) |
| `hopr_pix_cycles_active` | MultiGauge | `phase` | SSA cycles currently retained by PIX supervisors, by phase (excludes recovered tombstones) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L242) |
| `hopr_pix_cycles_total` | MultiCounter | `event` | SSA cycle lifecycle transitions observed by PIX supervisors, by event | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L267) |
| `hopr_pix_deposits_confirmed_uhopr_total` | SimpleCounter | — | Deposit value confirmed for SSA cycles of this Exit, in uHOPR (1e-6 HOPR) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L331) |
| `hopr_pix_deposits_recovered_uhopr_total` | SimpleCounter | — | Deposit value this Exit actually unlocked by fully recovering the cycle, in uHOPR (1e-6 HOPR) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L335) |
| `hopr_pix_egress_packets_total` | MultiCounter | `mode` | Exit-to-Entry data packets admitted by PIX egress gates, by what paid for them | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L281) |
| `hopr_pix_fill_backoff_total` | MultiCounter | `reason` | Times PIX fill held back, by reason | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L344) |
| `hopr_pix_gate_block_seconds` | MultiHistogram | `reason` | How long PIX egress stayed blocked, from first refusal until service resumed or the session closed | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L291) |
| `hopr_pix_gate_blocks_total` | MultiCounter | `reason` | PIX egress block episodes entered, by reason (counted once per episode, not per refused packet) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L286) |
| `hopr_pix_held_shares` | SimpleGauge | — | Shares held by the part builders of live PIX cycles on this Exit, until their polynomial reconstructs | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L255) |
| `hopr_pix_live_cycle_bytes` | SimpleGauge | — | Reconstructor-cycle bytes currently reserved against the node's max_live_cycle_bytes | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L251) |
| `hopr_pix_predeposit_exposure_packets` | SimpleGauge | — | Packets served by currently unfunded PIX fronts and not yet converted into funded service | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L247) |
| `hopr_pix_sessions_active` | MultiGauge | `gate_mode` | Incoming PIX sessions currently supervised by this Exit, by front-gate mode | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L237) |
| `hopr_pix_sessions_stalled` | MultiGauge | `cause` | PIX sessions whose fill is currently not keeping up, by cause | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L320) |
| `hopr_pix_share_insert_failures` | SimpleCounter | — | Number of failed encrypted share insertions into the exit acknowledgement processor | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/hopr/src/protocol/pipeline/mod.rs#L132) |
| `hopr_pix_shares_total` | MultiCounter | `kind` | Supervisor-validated SSA shares newly accepted by this Exit, by whether they advanced reconstruction | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L297) |
| `hopr_pix_ssa_requests_resent_total` | SimpleCounter | — | Successor SsaRequests the PIX supervisor asked to have sent again because the Entry had not answered them | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/pix.rs#L272) |
| `hopr_protocol_ack_received_count` | MultiCounter | `valid` | Number of received acknowledgements | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/hopr/src/protocol/pipeline/mod.rs#L117) |
| `hopr_protocol_ack_sent_count` | SimpleCounter | — | Number of sent message acknowledgements | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/hopr/src/protocol/pipeline/mod.rs#L122) |
| `hopr_session_ack_incoming_acknowledged_frames_total` | MultiCounter | `session_id` | Incoming session acknowledgements | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L136) |
| `hopr_session_ack_incoming_retransmission_requests_total` | MultiCounter | `session_id` | Incoming session retransmission requests | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L131) |
| `hopr_session_ack_incoming_segments_total` | MultiCounter | `session_id` | Incoming session segments | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L126) |
| `hopr_session_ack_mode` | MultiGauge | `session_id` | Configured ack mode encoded as None=0, Partial=1, Full=2, Both=3 | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L121) |
| `hopr_session_ack_outgoing_acknowledged_frames_total` | MultiCounter | `session_id` | Outgoing session acknowledgements | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L151) |
| `hopr_session_ack_outgoing_retransmission_requests_total` | MultiCounter | `session_id` | Outgoing session retransmission requests | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L146) |
| `hopr_session_ack_outgoing_segments_total` | MultiCounter | `session_id` | Outgoing session segments | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L141) |
| `hopr_session_dispatched_messages` | MultiCounter | `kind` | Number dispatched HOPR session messages and their classification | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/manager.rs#L90) |
| `hopr_session_established_sessions_count` | SimpleCounter | — | Number of sessions that were successfully established as an Exit node | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/manager.rs#L77) |
| `hopr_session_frame_being_assembled` | MultiGauge | `session_id` | Number of frames currently being assembled | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L101) |
| `hopr_session_frame_completed_total` | MultiCounter | `session_id` | Number of frames successfully completed | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L106) |
| `hopr_session_frame_discarded_total` | MultiCounter | `session_id` | Number of frames discarded by the session protocol | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L116) |
| `hopr_session_frame_emitted_total` | MultiCounter | `session_id` | Number of frames emitted from the sequencer | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L111) |
| `hopr_session_frame_frame_capacity` | MultiGauge | `session_id` | Configured frame buffer capacity | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L96) |
| `hopr_session_frame_mtu_bytes` | MultiGauge | `session_id` | Configured frame MTU in bytes | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L86) |
| `hopr_session_frame_timeout_ms` | MultiGauge | `session_id` | Configured frame timeout in milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L91) |
| `hopr_session_hoprd_clients` | MultiGauge | `type` | Number of clients connected at this Entry node | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/utils-session/src/lib.rs#L67) |
| `hopr_session_initiated_sessions_count` | SimpleCounter | — | Number of sessions that were successfully initiated as an Entry node | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/manager.rs#L81) |
| `hopr_session_lifetime_created_at_ms` | MultiGauge | `session_id` | Session creation time in unix milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L56) |
| `hopr_session_lifetime_idle_ms` | MultiGauge | `session_id` | Session idle time in milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L71) |
| `hopr_session_lifetime_last_activity_at_ms` | MultiGauge | `session_id` | Last session activity time in unix milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L61) |
| `hopr_session_lifetime_pipeline_errors_total` | MultiCounter | `session_id` | Session pipeline processing errors | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L81) |
| `hopr_session_lifetime_state` | MultiGauge | `session_id` | Session lifecycle state encoded as Active=0, Closing=1, Closed=2 | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L76) |
| `hopr_session_lifetime_uptime_ms` | MultiGauge | `session_id` | Session uptime in milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L66) |
| `hopr_session_num_active_sessions` | SimpleGauge | — | Number of currently active HOPR sessions | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/manager.rs#L73) |
| `hopr_session_pix_fill_packets_total` | SimpleCounter | — | PIX fill keep-alives originated by this Exit, above its SURB-level notification rate | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L221) |
| `hopr_session_pix_fill_rate` | MultiGauge | `session_id` | Planned rate in packets per second for the Exit's own PIX fill keep-alives; emission is bounded by the SURB reserve, so see hopr_session_pix_fill_packets_total for what went out | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L216) |
| `hopr_session_pix_gate_mode` | MultiGauge | `session_id` | PIX egress gate mode encoded as Predeposit=0, Funded=1 | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L206) |
| `hopr_session_pix_recovery_progress` | MultiGauge | `session_id` | Recovery progress of a session's most recently advanced SSA, as a ratio of useful shares to target | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L211) |
| `hopr_session_received_error_count` | MultiCounter | `kind` | Number of HOPR session errors received from an Exit node | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/manager.rs#L85) |
| `hopr_session_sent_error_count` | MultiCounter | `kind` | Number of HOPR session errors sent to an Entry node | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/manager.rs#L95) |
| `hopr_session_snapshot_at_ms` | MultiGauge | `session_id` | Session telemetry sample time in unix milliseconds | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L51) |
| `hopr_session_surb_buffer_estimate` | MultiGauge | `session_id` | Balancer estimate of the SURBs the counterparty holds, bounded by its store and corrected by its reports | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L166) |
| `hopr_session_surb_consumed_total` | MultiCounter | `session_id` | Consumed SURBs per session | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L161) |
| `hopr_session_surb_produced_total` | MultiCounter | `session_id` | Produced SURBs per session | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L156) |
| `hopr_session_surb_rate_per_sec` | MultiGauge | `session_id` | SURB production rate per second, averaged over at least one second; see hopr_surb_balancer_surbs_rate for the net rate | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L176) |
| `hopr_session_surb_refill_in_flight` | MultiGauge | `session_id` | Whether SURB refill is currently configured for a session (1 or 0) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L181) |
| `hopr_session_surb_target_buffer` | MultiGauge | `session_id` | Configured SURB target buffer size | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L171) |
| `hopr_session_time_to_finish_frame` | SimpleHistogram | — | Measures time in milliseconds it takes a frame to be reassembled | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/session/src/processing/reassembly.rs#L26) |
| `hopr_session_transport_bytes_in_total` | MultiCounter | `session_id` | Session ingress bytes | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L186) |
| `hopr_session_transport_bytes_out_total` | MultiCounter | `session_id` | Session egress bytes | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L191) |
| `hopr_session_transport_packets_in_total` | MultiCounter | `session_id` | Session ingress packets | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L196) |
| `hopr_session_transport_packets_out_total` | MultiCounter | `session_id` | Session egress packets | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/telemetry/mod.rs#L201) |
| `hopr_start_time` | SimpleGauge | — | The unix timestamp in seconds at which the process was started | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/hopr/hopr-lib/src/builder.rs#L68) |
| `hopr_surb_balancer_control_output` | MultiGauge | `session_id` | Control output of the SURB balancer | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/balancer/controller.rs#L36) |
| `hopr_surb_balancer_current_buffer_estimate` | MultiGauge | `session_id` | Estimated number of SURBs in the buffer | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/balancer/controller.rs#L42) |
| `hopr_surb_balancer_current_buffer_target` | MultiGauge | `session_id` | Current target (setpoint) number of SURBs in the buffer | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/balancer/controller.rs#L48) |
| `hopr_surb_balancer_surbs_rate` | MultiGauge | `session_id` | Estimation of SURB rate per second (positive is buffer surplus, negative is buffer loss) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/balancer/controller.rs#L54) |
| `hopr_surb_balancer_target_error_estimate` | MultiGauge | `session_id` | Target error estimation by the SURB balancer | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/session/src/balancer/controller.rs#L30) |
| `hopr_surb_store_admissions_count` | MultiCounter | `event` | Admission-control events in the SURB store, by event kind | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/hopr/src/surb_store.rs#L525) |
| `hopr_surb_store_evictions_count` | MultiCounter | `cache`, `cause` | Number of entries evicted from the SURB store caches, by cache and removal cause | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/hopr/src/surb_store.rs#L513) |
| `hopr_surb_store_evictions_last_interval` | MultiGauge | `cache`, `cause` | Entries evicted from the SURB store caches during the last completed report interval, by cache and removal cause | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/hopr/src/surb_store.rs#L519) |
| `hopr_surb_store_surbs_held` | MultiGauge | `class` | SURBs currently held by the SURB store, by trust class | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/hopr/src/surb_store.rs#L531) |
| `hopr_tickets_count` | MultiCounter | `type` | Number of tickets by type (winning, losing, rejected) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/hopr/src/protocol/pipeline/mod.rs#L126) |
| `hopr_tickets_unack_evictions_total` | SimpleCounter | — | Total number of unacknowledged tickets evicted from cache due to TTL or capacity limits | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/hopr/src/ticket_processing.rs#L43) |
| `hopr_tickets_unack_insertions_total` | SimpleCounter | — | Total number of unacknowledged tickets inserted into cache | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/hopr/src/ticket_processing.rs#L28) |
| `hopr_tickets_unack_lookup_misses_total` | SimpleCounter | — | Total number of ticket lookup failures (unknown ticket) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/hopr/src/ticket_processing.rs#L38) |
| `hopr_tickets_unack_lookups_total` | SimpleCounter | — | Total number of ticket acknowledgement lookups | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/hopr/src/ticket_processing.rs#L33) |
| `hopr_tickets_unack_peer_evictions_total` | SimpleCounter | — | Total number of peer caches evicted from the outer unacknowledged ticket cache | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/hopr/src/ticket_processing.rs#L48) |
| `hopr_tickets_unack_peers_total` | SimpleGauge | — | Number of peers with unacknowledged tickets in cache | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/hopr/src/ticket_processing.rs#L18) |
| `hopr_tickets_unack_tickets_per_peer` | MultiGauge | `peer` | Number of unacknowledged tickets per peer in cache (enable with HOPR_METRICS_UNACK_PER_PEER=1) | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/hopr/src/ticket_processing.rs#L53) |
| `hopr_tickets_unack_tickets_total` | SimpleGauge | — | Total number of unacknowledged tickets across all peer caches | [source](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/protocols/hopr/src/ticket_processing.rs#L23) |

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

- [REST routes and API base](https://github.com/hoprnet/hoprd/blob/2d3ee8925c2ad913801c771b574f922bd9e84fec/rest-api/src/lib.rs)
- [Metric endpoint and session-prefix filter](https://github.com/hoprnet/hoprd/blob/2d3ee8925c2ad913801c771b574f922bd9e84fec/rest-api/src/root.rs)
- [Readiness and health predicates](https://github.com/hoprnet/hoprd/blob/2d3ee8925c2ad913801c771b574f922bd9e84fec/rest-api/src/checks.rs)
- [JSON formatter and log filtering](https://github.com/hoprnet/hoprd/blob/2d3ee8925c2ad913801c771b574f922bd9e84fec/hoprd/src/tracing_setup.rs)
- [OTLP gate, signals, providers and text exporter](https://github.com/hoprnet/hoprd/blob/2d3ee8925c2ad913801c771b574f922bd9e84fec/hoprd/src/telemetry.rs)
- [Metric helper and export semantics](https://github.com/hoprnet/hopr-types/blob/1f8cd38664d6c8c1fe6b342b2dd0f36c8fcfde48/src/telemetry/metrics.rs)
- [Baseline selector phases](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/hopr/src/path/selector.rs)
- [Baseline probe outcomes](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/probe/src/probe.rs)
- [SURB mint/return accounting](https://github.com/hoprnet/hoprnet/blob/13c4c81611e472e874484c1f21a083c3840629dc/transport/hopr/src/protocol/surb_telemetry.rs)
- [Funding pass decisions](https://github.com/hoprnet/hopr-strategy/blob/a8a79baa45c64ad66ade0e10124e27d828feb6a3/src/channel_lifecycle/pipeline.rs)
