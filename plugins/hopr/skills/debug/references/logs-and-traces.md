# Logs, spans and trace export

Catalog refreshed 2026-11-18; use [source-manifest.json](source-manifest.json) for pins.
For an exact message, level, field expression or span, query
[log-catalog.jsonl](log-catalog.jsonl). It indexes the six baseline repositories
including the merged PR changes. Read [catalog-maintenance.md](catalog-maintenance.md)
for overlay and lexical-extraction semantics. A declaration is not evidence that
an event occurred or that its level was enabled in a capture.

## Collect and decode

| Surface/control | Contract |
|---|---|
| Normal log sink | HOPRd installs a `tracing_subscriber` formatter. Text is default; `HOPRD_LOG_FORMAT=json` selects JSON case-insensitively. Level, target and thread ID are enabled. Collection/rotation belongs to the hosting application/service. |
| `RUST_LOG` | EnvFilter for the process. Absent/invalid filter falls back to INFO plus dependency directives; an accepted custom filter replaces that fallback. `prof` adds Tokio/runtime TRACE directives. Changes require the applicable process configuration/restart procedure. |
| JSON event | Keep timestamp, level, target, thread ID, `.fields`, `.span` and `.spans`. Fields may be inherited through spans. `%value`/`?value` are Display/Debug representations, often strings rather than a typed nested object. |
| Event versus span | An event has a message and fields at a point in time; a span supplies operation scope. The normal formatter is not configured to print every span open/close. A span declaration need not produce a standalone text log. |
| Static level | Dependencies use `release_max_level_debug`. The hoprnet workspace has a tracing-package debug-assertions override, while downstream binary workspaces need their own configuration. Runtime TRACE cannot recover compiled-out events. Check the actual build, including feature unification. |
| Timing | Keep units exactly as declared: `elapsed_us`, `duration_ms`, `window_s`, etc. Wall-clock timestamps and monotonic elapsed durations are different clocks. |

Sources: [formatter and filtering](https://github.com/hoprnet/hoprd/blob/e0f02e09dac85da4c1adce6749996a604765e9dd/hoprd/src/tracing_setup.rs),
[baseline build](https://github.com/hoprnet/hoprnet/blob/ff0076efaa58655e282ee7e2fa6b685ecd330a5d/Cargo.toml),
[PR build override](https://github.com/hoprnet/hoprnet/blob/b8cc3e18a7297de1f4aab70d9b8f3407558196a5/Cargo.toml#L209-L214).

## OTLP is a separate export contract

| Setting/behavior | Verified meaning |
|---|---|
| `HOPRD_USE_OPENTELEMETRY` | True values `1,true,yes,on`; false values `0,false,no,off`, normalized for case/whitespace. Explicit false wins. Otherwise a nonempty `HOPRD_OTLP_ENDPOINT` auto-enables export. |
| `HOPRD_OTLP_ENDPOINT` | Overrides the legacy `OTEL_EXPORTER_OTLP_ENDPOINT`. That legacy variable alone does not enable export. The effective endpoint scheme selects HTTP/protobuf for `http`/`https`, otherwise the transport falls back to gRPC. |
| `HOPRD_OTEL_SIGNALS` | Comma-separated `traces,metrics,logs`. Default or empty valid set falls back to traces. Enabling OTLP does not implicitly enable all three signals. |
| `OTEL_SERVICE_NAME` | Nonempty override for the service name; otherwise the HOPRd package name. Resource attributes include `node_address`, `node_peer_id` and application-supplied extra labels. |
| `HOPRD_METRIC_EXPORT_INTERVAL` | Comma-separated default duration and/or `prefix=duration`, e.g. `60s,hopr_session_=5s`. Bare integers are milliseconds; `ms`, `s`, `m` are accepted, positive only. Default interval is 60 s. Longest registered metric-name prefix wins. |
| Trace SDK | `AlwaysOn` sampler, random IDs, batch processor, 5 s exporter timeout, at most 64 events and 16 attributes per span. Filtering, caps, queueing, shutdown and build gates still limit completeness. |
| OTLP logs | Custom bridge emits event severity/target, message under body key `message`, event fields plus `module_path`, `file`, `line`, `target`. It does **not** copy the JSON formatter's ancestor-span chain; its callback ignores subscriber context. Do not assume JSONL parent fields are present in exported log attributes or that log records have matching trace IDs. |
| Delivery | Handles own provider shutdown. Crash/forced termination can lose queued data. Local JSON timestamps/IDs are not proof of collector ingestion. |

Source: [HOPRd telemetry configuration, providers and log bridge](https://github.com/hoprnet/hoprd/blob/e0f02e09dac85da4c1adce6749996a604765e9dd/hoprd/src/telemetry.rs).
HOPR packets do not themselves establish distributed trace propagation. Verify
session/node/process joins from actual data; PR hoprnet#8604 restores local context
across queues and polls, not a cross-node trace header.

## Event families and correlation

This table routes to the complete source index; it is not a second exhaustive list.

| Family / source crate | Look for | Correlation and interpretation |
|---|---|---|
| Startup / `hopr-lib`, `hoprd` | `starting node`, `node configuration`, `network information`, `OpenTelemetry gate decision`, OS tuning | Version/features, peer, chain address, network, process lifetime. Configuration may be DEBUG. |
| Chain / `hopr-chain-connector` | Subscription state/retries, synchronization, transaction and channel outcomes | Subscription, transaction/channel identity, chain freshness; retries are not successful updates. |
| Graph / `hopr-network-graph` | TRACE observations/traversals; DEBUG `graph_search_failure`; opt-in INFO `component_state` | `selection_id`, `search_id`, `phase`, `edge_evaluation_id`, directed edge/path identities. Failure summaries are limited to one per graph per minute; cumulative dump counts retain suppressed work. |
| Strategy / `hopr-strategy` | Funding pass stops, tick summaries, confirmed open/fund/close/finalize, PIX deposit/recovery | Budget, Safe allowance, pending work, actual stop reason. Observer state is not a funding explanation. |
| Routing / `hopr-transport` | Candidate/search/validation/cache records, route failure, SURB waits, diversity transitions | Direction, destination, hops, node/process/session. With hoprnet#8604, `set_id` and `index` connect weighted candidates to draws. |
| Probes / `hopr-transport-probe` | Neighbor and loopback reply, expiry and timeout | Peer/pseudonym/tag/nonce as present. Late or unknown replies are not another success. Level differs on hoprnet#8614. |
| SURBs / `hopr-transport`, `hopr-protocol-hopr` | `surb round-trip pair`, `surb round-trip flush tick`, `relayer judged dead`, overdue-reply explanations | Forward/reply path and affected hop when present. Returns cross windows; pair ratios are not unbiased delivery probabilities. |
| Session / `hopr-transport-session`, `hopr-protocol-session` | `new session established`, `session closed`, `session frames dropped in the last window`, invalid session header | `session_id` plus process lifetime. Frame loss is event-driven, minimum 30 s plus final flush; discarded and incomplete can overlap. See [logging-changes.md](logging-changes.md). |
| PIX / session, protocol, strategy crates | Admission/gate blocks, SSA request/commit/deposit/recovery, Curvy replay/retry, stalls, closure reasons | Session, role, cycle/SSA identity, phase and source freshness. Re-sends are not new cycles; packet refusal episodes and refused packets differ. |
| Pressure / mixer, utilities, REST API | Encoding/decoding/queue timeouts, UDP drops, CPU pool errors, API failures | Match metric family, timing and process lifetime. A timed-out await need not stop a CPU task. |
| Memory / `hoprd` | `jemalloc_config:`, `jemalloc_stats:` | Formatted message strings, not structured numeric fields. Feature/platform gates and units are in [state-snapshots.md](state-snapshots.md). |

Exact declaration sources are attached to every indexed record. For merged epic
changes and capture instructions see [logging-changes.md](logging-changes.md);
for merged PR changes and version boundaries see [pending-features.md](pending-features.md).

## Query the inventory without loading it all

Commands below run from `plugins/hopr/skills/debug`. They inspect documentation, not a node.

```sh
# Find the source/level/fields for a message family in baseline source.
jq -c 'select(.snapshot == "hoprnet" and
  (.name // "" | contains("surb round-trip"))) |
  {kind,level,name,fields,source_url}' references/log-catalog.jsonl

# Trace declarations from the graph crate.
jq -c 'select(.snapshot == "hopr-impls" and
  .crate == "hopr-network-graph" and .kind == "span") |
  {level,name,declaration,source_url}' references/log-catalog.jsonl

# Find merged planner events by name in the current source snapshot.
jq -c 'select(.snapshot == "hoprnet" and .name == "candidate selected") |
  {kind,level,name,fields,source_url}' references/log-catalog.jsonl
```

For an actual JSONL capture, preserve the full matching object so parent-span
context survives. Match the captured session's serialized value, not an assumed
number/string encoding. A `node_state` or `component_state` payload is a JSON
string inside `.fields.state`; decode once after checking its event discriminator.
Records with an encoding error can have no `state` and still count as emissions.
