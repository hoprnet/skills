# Operational debugging workflow

Use this with the version/availability rules in [SKILL.md](../SKILL.md).
Examples below operate on local capture copies. They do not connect to a node,
change its configuration, or submit chain transactions.

## 1. Build the incident timeline

Record application/worker and crate versions, process restarts, network, node
PeerId/offchain key/chain address, Safe address, active configuration, available
files, and timestamp offsets. Distinguish entry/client, relay, and exit roles.

Look for startup `node information`, `hopr_lib_version`, and settings records
when that build supplies them. A crate version in Cargo.toml is not proof of
what is running; prefer the capture's build identity and deployment provenance.

For JSON logs, select a session without losing ancestor fields:

```sh
jq -c --arg sid 'SESSION_ID_FROM_CAPTURE' '
  select(.fields.session_id? == $sid
      or .span.session_id? == $sid
      or any(.spans[]?; .session_id? == $sid))
' node.jsonl
```

This assumes one valid JSON object per line. If the bundle includes mixed text,
identify the JSON section first and record excluded/unparseable lines. This
selection finds records containing the key; it cannot reconstruct missing spans.

## 2. Chain and connectivity before path speculation

- Inspect component status and the last successful chain update. Subscription
  retries, reconnects and explicit degraded status are evidence of feed problems.
  A snapshot can look plausible while its underlying chain cache is stale.
- `/readyz` and `/healthyz` are limited predicates. In the pinned HOPRd source,
  readiness allows a degraded chain that is not unavailable; health checks node
  running/connectivity, not chain freshness. Do not use HTTP 200 to rule out a
  subscription outage.
- Compare announced peers, graph connectivity observations and actual transport
  connections. These are different populations. Missing graph observations are
  unknown, not disconnected. A remote channel elsewhere is not a local channel.
- Confirm node announcement/address bindings when available. Do not infer ETH
  addresses from a PeerId string without the binding.

## 3. Channel funding: the original high-value signals

Read edge `balance`, `is_connected` and `probe_success_rate` together. Then inspect
`channel-lifecycle: fund pass stopped` records and their attached budget fields.
Use the captured winning-ticket face value/funding policy and channel direction;
an incoming channel is funded by its counterparty. Do not hardcode a historical
balance multiplier or infer the funding budget solely from the node's gas balance.

Check Safe token balance, allowance/spendable budget, funds already reserved by
pending transactions, channel state and chain freshness. A funded Safe with low
allowance can still prevent funding. A warning about funding is not evidence that
an already usable channel stopped working, and a quiet legacy strategy metric is
not evidence that the current lifecycle strategy never ran.

Current baseline can require graph DEBUG observations plus strategy warnings.
Pending state dumps supply capped samples, not every channel; use omitted/error
counts before concluding that a particular channel is healthy or absent.

## 4. Path-health: separate search, validation, cache and selection

- Read forward selector phase-1 and phase-2 candidate counts and the direction,
  source, destination and hop request. Phase 2 may not run; absence is not failure.
- Separate graph-returned candidates from eligible/validated candidates. Nonzero
  graph count followed by zero eligible routes points to pruning/validation, not
  automatically to missing topology. Read the actual rejection evidence.
- Cache hits need not trigger another graph search. Resolve request counts and
  search counts have different denominators. A selected path is a weighted draw,
  not proof that the path with the best apparent metric must always be selected.
- A graph completion reason such as `no_eligible_paths` can describe an empty
  result without identifying the individual rejected edge or its cause.
- With pending correlated diagnostics, follow candidate-set identifiers and
  selection records. Actual draws/weights may be TRACE-only; mark them unknown
  if this build compiled TRACE out.

## 5. Connectivity: actual probe outcomes

Count matched neighbor/loopback pongs and real expiries over the same window.
Read the baseline `neighbor probe failed` and `loopback probe failed` messages
with `reason=timeout` and their peer/path context. An unknown or late pong must
not be counted as a second independent success after its timeout.

Many timeouts to already offline peers can dominate logs without explaining an
active session's failure. Match the affected peer/path to the session before
attributing causality. Probe success and successful application forwarding are
different observations; a connected socket is not proof of packet delivery.

## 6. SURBs and the reply direction

Use `surb round-trip pair` details and the aggregate `surb round-trip flush tick`
when present. Compare siblings serving the same destination in a common window.
`expected`/minted means created SURBs, not necessarily SURBs spent by the exit.
Returns can cross reporting windows. The ratio is not an absolute delivery
probability and can be low on a healthy path or exceed one in a short window.

Read produced/consumed SURBs, buffer estimate, target, refill activity and actual
returned traffic together. An entry can overestimate the exit's stock when
consumption/reports disappear. Total silence cannot by itself distinguish an
idle exit from a broken return path. Favor corroboration from sibling paths,
probe/connection changes and the exit's own records.

If `sent` stops advancing while received/forwarded/ack traffic continues, inspect
routing/SURB-resolution waits and queue pressure. It is a signature to investigate,
not proof that the historic unbounded-retry defect is present in this version.

## 7. Sessions, frames and PIX

- Use the same `session_id`, capability set, roles and process lifetime. Check
  segmentation, retransmission/ACK/NACK settings before assuming flow control is
  active. Frame MTU, frame timeout and transport packet size are different values.
- Distinguish intentional datagram frame discards from protocol failure. Use
  aggregate incomplete/discard/duplicate summaries and sampled frame counters.
  Do not treat a frame-ID range as an exact delivered-frame denominator without
  checking wrap, gaps, retransmissions and session boundaries.
- `hopr_session_*` instruments exist in source but HOPRd's REST `/metrics` removes
  that prefix. Check the OTLP metrics path or the embedding session snapshot API;
  do not conclude that there are no sessions from an absent REST series.
- PIX questions: active cycles, gate state/block time, accepted versus invalid
  shares, phase latency, stalled reason, deposits/recoveries and cycle closure.
  Match the event/role/phase labels. A retransmitted SSA request is not a newly
  opened cycle. Packet-layer ticket revenue does not pay the final destination.

## 8. Throughput, runtime and OS pressure

Compare packet sent/received/forwarded, rejects, ACKs, mixer queue/delay, egress
ring-buffer drops, Rayon outstanding/queue-wait/execution/cancellation/orphans,
and UDP packet lengths. Queue size, latency and discard rate answer different
questions. Configured mixing is intentional latency, not automatically congestion.

Read `OS network tuning specs` if captured. Low UDP buffers or a congestion-control
setting are hypotheses to test against packet/queue evidence, not a diagnosis
from a startup warning alone. A CPU timeout does not guarantee work already
running in Rayon stopped; check orphaned/cancelled task behavior for that build.

## 9. Cross the Gnosis VPN boundary explicitly

HOPR/session evidence ends at a boundary. If a routed echo succeeds but the VPN
still fails, inspect the worker/app/TUN, routing, DNS and killswitch timeline with
the application-side debugger. Do not change firewall/kill-switch settings just
to improve a log. A killswitch transition near a chain-feed disconnect is a timing
correlation; it does not establish why every subsequent retry failed.

## 10. Minimum useful handoff

Provide: exact version/build and network; role/identities; UTC incident window;
artifact paths and excerpt locations; expected versus observed behavior; evidence
and counter denominators; leading hypothesis and alternatives; missing signals;
smallest next check; ownership/confidence. Keep a full local capture when making
an excerpt so the next reviewer can inspect context. Share it only through the
user's authorized workflow.
