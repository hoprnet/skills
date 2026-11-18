# State snapshots, embedding diagnostics and profiles

Reviewed 2026-11-18 against the pinned sources. These are separate surfaces from
Prometheus instruments. Read the captured event name and schema before selecting
a parser; the earlier `node_state` design was superseded by `component_state`.

## Merged component diagnostics in hopr-impls

The chain connector, graph and P2P network expose optional diagnostics through
`hopr_api::StateDump` with `serde` (graph also requires its `petgraph` implementation).
The legacy `state-dump` feature aliases `serde`. The host must call/poll the dump
or run each reporter. The pinned HOPRd source installs the reporters when its
`state-dump` feature is enabled, as it is in its default features. Other embedders
must supply their own reporter; an environment variable alone does not install one.

| Producer/API | On-demand output | Periodic output |
|---|---|---|
| Chain `connector.state_view(safe_address, funding_value).dump().await`; `connector.state(...)` convenience method | `{state, analysis_required, reason}` with chain schema below | `run_state_reporting(instance, safe_address, funding_value)`; changing funding policy can use `run_state_reporting_with_funding_policy` |
| Graph `graph.dump().await`, `graph.state_view(bounded_paths)`; synchronous `graph.state(paths)` convenience method | Same outer shape, graph-owned schema | `run_state_reporting(instance, bounded_paths)`; path callback must be bounded and non-draining |
| P2P `network.dump().await`, `network.state().await` | Same outer shape, transport-owned schema | `run_state_reporting(instance)` |
| Strategy `MultiStrategy::state_observer()` | `snapshot()` gives ordered `name`/`state` records; `dump().await` with `serde` | Host schedules/logs it; no automatic periodic exporter |

Sources: [chain](https://github.com/hoprnet/hopr-impls/blob/3b96c2796ddc41562f6e4688eab6f8aff2a0f0bd/connector/README.md),
[graph](https://github.com/hoprnet/hopr-impls/blob/3b96c2796ddc41562f6e4688eab6f8aff2a0f0bd/graph/README.md),
[P2P](https://github.com/hoprnet/hopr-impls/blob/3b96c2796ddc41562f6e4688eab6f8aff2a0f0bd/transport/p2p/README.md),
[strategy](https://github.com/hoprnet/hopr-strategy/blob/c611a8f647fc1c01cb1bc8679d2ba94a2889f149/README.md#diagnostic-state-snapshots).

### HOPRd host integration

In the pinned [HOPRd reporter](https://github.com/hoprnet/hoprd/blob/097d1b80ef6fc69ff2c554bf3eab62d692e66cc1/hoprd/src/state_dump.rs),
the `state-dump` feature starts chain, graph and P2P reporters after account
readiness. The instance is the node's chain address. The chain reporter reads
the graph's current ticket face value as its funding policy. The graph reporter
is given an empty path sample; HOPRd supplies neither a transport observer nor
extra sections. Do not infer per-destination transport counters or probe counts
from these records. Observations across components are not atomic.

`HOPR_STATE_DUMP_INTERVAL` defaults to 60 seconds; `0` disables periodic reads;
enabled intervals are 5–86400 seconds. Invalid values stop reporting with an
error while the node continues running. INFO filtering and build/deployment
features also determine whether records are visible. The reporter uses the
existing log sink; it does not create an endpoint. Older deployments may not
include this integration.

### Periodic envelope

INFO `event=component_state`, message `component operational state`, carries
`component` (`chain`, `graph`, `transport`), `instance`, `sequence`, `reason`,
`analysis_required`, and JSON string `state`. On encoding failure it carries an
error and `reason=encoding_error`, with no payload. Decoded `state` is a record:

| JSON field | Type / interpretation |
|---|---|
| `schema_version` | Integer, currently 1; private component schemas may change |
| `component`, `instance` | Component and host-supplied identity; instance truncated to 120 characters |
| `sequence` | Per-reporter integer starting at zero; join with node/process/component/instance |
| `collected_at_ms`, `collection_ms` | Unix milliseconds and elapsed collection milliseconds |
| `reason`, `analysis_required` | Diagnostic assessment; `true` does not assign operational `Failed` |
| `state.status` | `ok`, `partial`, `error`, `timeout`; value present for `ok`/`partial`, message for `error` |
| `state.value` | Component schema below; completeness of nested sections still matters |

The reporter defaults to 60 s, accepts integer seconds 5–86400, and disables
periodic reads at `0`; invalid configuration returns an error to the caller.
Explicit on-demand collection still reads when periodic reporting is disabled.
Each reporter has a 5 s collection deadline, 32 KiB encoded-record budget excluding
the logger wrapper, independent sequence and cancellation owned by its host.
Collection errors, panics, timeouts and encoding failures preserve diagnostic
reasons; a successful envelope may contain partial/error subreads.
[Envelope source](https://github.com/hoprnet/hopr-impls/blob/3b96c2796ddc41562f6e4688eab6f8aff2a0f0bd/connector/src/state_reporting.rs).

Decode while preserving the outer record and failures:

```sh
jq -c 'select(.fields.event? == "component_state") |
  {log: ., decoded: (if (.fields.state? | type) == "string"
    then (.fields.state | fromjson) else null end)}' node.jsonl
```

### Chain schema (`state.value` in periodic records)

| Fields | Meaning |
|---|---|
| `component_state` | Operational `Running`, `Degraded`, `Failed` from the existing chain lifecycle; independent of collection completeness |
| `node_address`, `safe_address`, `chain_freshness` | Identity and explicit freshness qualification; this is a cached/source-visible view, not an atomic fresh chain read |
| `safe_balance`, `safe_allowance`, `announced_peers` | Individually status-wrapped sections. Token amounts are decimal base-unit strings; announced peers exclude self |
| `channels.status`, `channels.value.scope` | Section coverage and inventory scope |
| `channels.value.reads` | `read_ok`, `missing`, `backend_errors`, `join_errors`, `identity_errors`, `timed_out`, `complete`, `error_samples` (bounded rows with `entity`, `kind`, `message`) |
| `channels.value.total`, `incoming`, `outgoing` | Total and direction-specific `closed`, `open`, `pending_to_close` counts |
| `channels.value.funding`, `funding_value` | Open outgoing channels `below`, `equal`, `above`, `unknown` relative to supplied policy; policy amount optional |
| `channels.value.peers.total` | Distinct peers with local open channels, not connected peers |
| `channels.value.problematic`, `omitted`, `sample` | Problem count, omitted detail and at most 4 retained problematic channels |
| `channels.value.underfunded`, `underfunded_omitted` | At most 16 underfunded outgoing channels, lowest balance first, plus omission count |
| Channel sample row | `counterparty`, `direction`, `status`, `balance`, `below_funding_value`; last field is null for incoming channels |

Subreads have 2.5 s timeouts; channels are read in blocking batches of at most 64.
Missing funding policy leaves funding unknown without itself requesting analysis.
Incoming channels are funded by their counterparty. Missing/read-error/timeout
states are distinct from a measured zero. No complete historical channel inventory
is promised. [Schema and collection source](https://github.com/hoprnet/hopr-impls/blob/3b96c2796ddc41562f6e4688eab6f8aff2a0f0bd/connector/src/diagnostics.rs).
[Read-error schema](https://github.com/hoprnet/hopr-impls/blob/3b96c2796ddc41562f6e4688eab6f8aff2a0f0bd/connector/src/connector/state_reads.rs).

### Graph schema

| Fields | Meaning |
|---|---|
| `scope`, `node_count`, `edge_count`, `local_node_present` | Graph coverage and topology totals |
| `ticket_face_value`, `edge_penalty`, `min_ack_rate` | Current graph policy; ticket amount is optional decimal base units |
| `incoming_edges`, `outgoing_edges`, `local_edges`, `local_edges_omitted` | Incident directed edge counts and up to 8 sampled edges, including transport-only edges |
| `paths` | Null if no path provider. Otherwise `sampled_paths`, `paths_omitted`, `invalid_paths`, `requested_edges`, `missing_edges`, `missing_node_edges`, `ambiguous_slot_edges`, `edges_omitted`, `edges` |
| Edge row | `source_slot`, `destination_slot`, optional `source`/`destination` public keys, `status` (`present`, `missing_edge`, `missing_node`, `ambiguous_slot`), optional `observation` |
| Edge observation | Optional `balance` (decimal base units), `is_connected`, `ack_rate`, `immediate_probe_rate`, `intermediate_probe_rate`, `immediate_latency_ms`, `intermediate_latency_ms`, `score`, `health`; `updated_at_ms` is Unix ms, zero means no recorded update |
| `searches` | `scope`, `total`, `empty`, `contended_samples`, `samples_unavailable`, `samples_omitted`, `debug_suppressed`, `samples` |
| Search sample identity/query | `selection_id`, `search_id`, `phase`, `kind`, `direction`, `source`, optional `destination`, `length`, optional `take_count`, `value_function` |
| Search sample decision-time policy/result | `graph_ticket_face_value`, `graph_min_ack_rate`, `graph_edge_penalty`, `outcome`, `returned`, `limit_reached`, `cycles_skipped`, `excluded_skipped`, `below_minimum`, `target_too_early`, `depth_limited` |
| Search sample rejected evidence | `rejected_prefix`, `rejected_prefix_nodes`, `rejected_reason`, `rejected_evaluation_id`, `rejected_examples_omitted`, `rejected_prefix_truncated`, `rejected_edge` |
| Search sample returned evidence/time | `returned_path`, `returned_path_nodes`, `returned_paths_omitted`, `elapsed_us`, `recorded_at_ms`, `occurrences` |

At most 32 supplied paths, 4 unique directed path edges and 2 search examples are
inspected/retained under a shared 3 KiB graph budget. Byte caps can remove rows
below the count limits. Slots are graph-specific correlation aids, not guaranteed
collision-free global identities. Search totals are cumulative and **not drained**
by snapshot reads; examples coalesce repeated queries and preserve latest evidence.
Queries that reach a cap do not establish that more/better paths exist. Only
decision-time policy events can refine `value_below_minimum` for an opaque value
function. The INFO assessment requests analysis for new empty searches or degraded/
incomplete local evidence; missing optional remote probe measurements alone do not
mean failure. [Full graph schema](https://github.com/hoprnet/hopr-impls/blob/3b96c2796ddc41562f6e4688eab6f8aff2a0f0bd/graph/src/diagnostics.rs).

### Transport and strategy schemas

P2P transport has `peer_id`, `connected_peers`, `discovered_peers`, `health`; counts
and classification are independently observed and contain no peer list. Unknown,
red or orange health requests analysis; yellow/green is connectivity evidence, not
application/probe delivery evidence.
[P2P source](https://github.com/hoprnet/hopr-impls/blob/3b96c2796ddc41562f6e4688eab6f8aff2a0f0bd/transport/p2p/src/state_dump.rs).

Strategy observer entries retain construction order and duplicate `name`s; `state`
is `Running`, `Degraded` or `Failed`. Reads are non-consuming and non-atomic across
strategies. Strategies without a health implementation return Running. Retain the
observer before `MultiStrategy::run` consumes children. The host chooses scheduling,
serialization and section naming; there is no implicit REST/metric endpoint.

## Embedding-only counters and protocol telemetry

The following process-global `AtomicUsize` counters are always compiled in and
read through the corresponding snake-case `*_count()` accessors in
[`hopr_transport_session::counters`](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/counters.rs).
They are **not named Prometheus instruments or REST endpoints**. An in-process
consumer/load generator must read them; compare reset-aware deltas per process.

| Symbol | Counts |
|---|---|
| `SESSION_INBOX_DROPS` | Full session inbox drops |
| `SESSION_INBOX_CLOSED_DROPS` | Closed inbox drops, including teardown races |
| `SESSION_UNKNOWN_DATA_DROPS` | Data with no matching established session slot |
| `SESSION_UNRELATED_DATA_DISPATCHES` | Data dispatched outside session protocol/application tags |
| `ROUTING_RESOLUTION_SURB_TIMEOUTS` | Outgoing packets dropped after return-SURB wait expires |
| `ROUTING_RESOLUTION_FAILURES` | Routing failures before encoding |
| `ROUTING_RESOLUTION_ATTEMPTS` | Routing-stage attempts; retry behavior must be checked at the recording site |
| `ENCODE_STAGE_ENTRIES` | SPHINX encode-stage entries |
| `DISPATCH_MESSAGE_CALLS` | Calls reaching session dispatch |
| `SESSION_STALE_START_MESSAGES` | Start messages naming no valid local state |
| `SESSION_MALFORMED_START_MESSAGES` | Start-tagged packets failing decode |
| `START_PROTOCOL_SEND_DROPS` | Start messages lost before a processing lane due to failed send |
| `START_CONTROL_SEND_DROPS` | Control-lane Start messages lost to a full queue |
| `APP_INCOMING_TIMEOUT_DROPS` | App-ingress queue timeout drops |
| `START_REFUSAL_REPLY_DROPS` | Start-session refusal replies dropped because the reply budget was spent |
| `START_REFUSALS` | Start-session refusals decided, whether or not the reply was sent |

`PeerProtocolCounterRegistry` records per-peer sent messages and acknowledgements.
`take*`, `drain` and `drain_settled` consume/reset observations; the settled variant
waits an acknowledgement grace period. These feed graph telemetry, so adding a
second draining consumer changes the original observer's data. They are not a
passive external metrics endpoint.
[Registry source](https://github.com/hoprnet/hoprnet/blob/2dfb22d75cedecb3fd4ccdf7b2347a4248b459a2/transport/hopr/src/protocol/counters.rs).

Session protocol tracker hooks report frames completed/emitted/discarded/incomplete,
errors, and incoming/outgoing Segment/Request/Acknowledge activity. HOPR's production
adapter feeds the `hopr_session_*` instruments; the serializable test tracker is a
test facility. Per-session lifetime/SURB samples update on activity and can become
stale while idle; compare `hopr_session_snapshot_at_ms` before interpreting them.
[Tracker hooks](https://github.com/hoprnet/hoprnet/blob/2dfb22d75cedecb3fd4ccdf7b2347a4248b459a2/protocols/session/src/socket/telemetry.rs),
[production adapter](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/transport/session/src/telemetry/mod.rs).

## Profiling and host artifacts

| Surface | Gate / contents / interpretation |
|---|---|
| Jemalloc INFO logs | Linux plus `allocator-jemalloc-stats`; startup configuration and startup/60 s memory samples. `jemalloc_config:` has version, narenas, tcache_max, background_thread; `jemalloc_stats:` has allocated, active, mapped, retained, arenas_active, cache_efficiency, uptime_secs. Memory is human-formatted decimal size text; efficiency is allocated/active. These are formatted messages, not numeric JSON fields. |
| Jemalloc heap files | Linux `allocator-jemalloc-profiling` build plus `_RJEM_MALLOC_CONF` profiling options; sampled allocations in `.heap` files analyzed with matching binary/debug symbols and `jeprof`. `memprof` retains symbols. These are allocation profiles, not network traces. |
| Tokio console | HOPRd `prof` feature installs `console_subscriber::spawn()` and Tokio/runtime TRACE directives; requires a compatible instrumented runtime/build. This is task/resource profiling, not a HOPR metric family. |
| CPU flamegraphs/load generator | hopr-lib `testing,profiling` harness can produce pprof flamegraphs and throughput-window counters. This is an explicit reproduction workload, not an always-on daemon endpoint. Retain workload/build parameters; mock-chain runs do not measure the live chain connector. |
| Support captures | Operator-collected JSON/text logs, scrapes, OTLP exports, read-only API results, version/configuration and host/container samples. External node-exporter/cAdvisor/OS statistics are separate producers. No universal HOPRd support-bundle schema is established here. |

Sources: [jemalloc logging](https://github.com/hoprnet/hoprd/blob/e0f02e09dac85da4c1adce6749996a604765e9dd/hoprd/src/jemalloc_stats.rs),
[heap profiling](https://github.com/hoprnet/hoprd/blob/e0f02e09dac85da4c1adce6749996a604765e9dd/profiling/memory-profiling.md),
[console layer](https://github.com/hoprnet/hoprd/blob/e0f02e09dac85da4c1adce6749996a604765e9dd/hoprd/src/tracing_setup.rs),
[CPU harness](https://github.com/hoprnet/hoprnet/blob/2dfb22d75cedecb3fd4ccdf7b2347a4248b459a2/hopr/hopr-lib/src/testing/loadgen.rs).
