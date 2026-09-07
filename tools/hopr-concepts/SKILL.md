---
name: hopr-debug
description: >
  HOPR mixnet debugging aid. Loads ground-truth HOPR protocol knowledge
  (RFC-0001–0014: SPHINX packets, Proof of Relay, tickets and incentives,
  mixing, sessions, path-finding, PIX) so reasoning about a live network is
  correct rather than plausible-but-wrong. Use whenever debugging or operating
  over a HOPR network — a node is not relaying, tickets are not winning or
  redeeming, a payment channel will not open or close, packets are dropped, a
  session will not establish, path-finding returns no route, mixing latency
  looks wrong, or Exit/PIX incentives misbehave. Trigger on "debug HOPR",
  "HOPR node", "hoprd", "channel graph", "relay", "ticket", "Proof of Relay",
  "PoR", "SURB", "why is my node not earning", "path not found", or mentions
  of hops, the channel graph, or the HOPR reward/Cover-Traffic system.
---

# HOPR Debug — protocol knowledge aid for network debugging

This skill is an **aid for the debugger, not a debugger**. It supplies the
side knowledge you need to _see and interpret_ what is happening inside a HOPR
network — node state, the channel graph, packet and ticket flow, incentives —
so your debugging is grounded in how the protocol actually works. General
reasoning about HOPR is reliably wrong on the incentive and topology
specifics; this skill exists to correct that before you diagnose anything.

## How to use

1. **Fetch the ground truth first.** Before reasoning about node state, the
   channel graph, or packet/ticket flow, fetch the HOPR protocol summary (a
   condensation of RFC-0001–0014) on demand from its upstream permalink and read
   the relevant sections. Do not answer from memory — the summary is
   authoritative here, and the RFCs behind it are authoritative over the summary.
   See [Fetching the summary](#fetching-the-summary) below.
2. **Check the misconceptions below** against whatever theory you are forming.
   Most confident-but-wrong HOPR diagnoses trace to one of them.
3. **Cite section numbers** (e.g. §3.2, §6.3) from the summary when you explain
   a finding, so the reasoning is checkable.

## Fetching the summary

The summary is **not shipped with this skill** — it is maintained upstream in
[`hoprnet/rfc`](https://github.com/hoprnet/rfc) as `SUMMARY.md`. Fetch it on
demand from a **permalink pinned to a specific commit** so the section numbers
this skill cites (§3.2, §6.3, …) always resolve against the exact snapshot they
were written for:

```sh
curl -fsSL \
  https://raw.githubusercontent.com/hoprnet/rfc/0b1eb50cf8e11a312ce3f29e723fc97e1d47bf32/SUMMARY.md \
  -o /tmp/hopr-protocol-summary.md
```

Then read `/tmp/hopr-protocol-summary.md`. The web permalink (for reference) is
<https://github.com/hoprnet/rfc/blob/0b1eb50cf8e11a312ce3f29e723fc97e1d47bf32/SUMMARY.md>.

If you need the newest version of the summary rather than the pinned snapshot,
fetch `…/hoprnet/rfc/main/SUMMARY.md`. Note that section numbers may then have
drifted from the citations below — prefer the pinned permalink for debugging.

## Common LLM misconceptions (read first)

Each is stated as _wrong → right_, with the summary section that settles it.

- **Channels are bidirectional** → they are **unidirectional**. `A→B` and
  `B→A` are separate channels; at most one per direction, both may coexist.
  (§3.1)
- **The recipient earns from receiving** → the **destination earns nothing at
  the packet layer**. The last ticket is zero-value, zero-probability. Paying
  Exit/destination nodes is precisely the gap PIX (§7) fills — do not expect a
  recipient to be paid by the relay mechanism. (§3.2, §7)
- **Every relayed packet pays** → tickets pay **probabilistically** via a VRF
  (`luck < encoded_win_prob`). A relayer with few or no redeemed tickets over a
  short window is not necessarily broken. (§3.2)
- **A ticket is redeemable once issued** → it becomes redeemable only **after
  the next hop acknowledges** (Proof of Relay). An "unredeemable ticket" usually
  means the downstream acknowledgement never arrived. (§2.5, §3.2)
- **Every hop needs an open channel** → the **final hop needs no channel**;
  every _non-final_ edge does. (§6.1)
- **Path-finding picks the shortest / lowest-latency path** → it samples
  **weighted-random by path value** (product of edge costs), capping
  `max_paths = 8`, cached 60 s. Two runs can pick different paths. (§6.3)
- **Extra latency means a fault** → the **mixer deliberately delays** every
  forwarded packet by a uniform random delay (default 0–200 ms). Added latency
  is by design. (§4)
- **On-chain channel = usable edge** → a usable edge needs **both** an OPEN
  on-chain channel **and** transport connectivity, plus the node's on-chain
  announcement. (§6.1)
- **Dropped packets mean packet loss** → replay protection **silently drops
  duplicate `ReplayTag`s**; retried/looped packets can be dropped as replays.
  (§2.2)

## Implementation gotchas (not covered by the RFCs)

The RFCs describe protocol *design*; everything below is emergent behavior of the current Rust
implementation, learned by debugging and load-testing the monorepo itself. It won't be in any RFC
and can drift as the code changes — treat it as implementation ground truth, not a protocol
guarantee.

- **`FlowControlConfig` always applies once passed** → it is silently a **no-op** unless the
  session's capability set includes `RetransmissionAck` or `RetransmissionNack`. The field is
  parsed, stored, and never read — no error, no log. Check session capabilities before trusting any
  flow-control setting. (`flow-control-needs-retransmission`)
- **A packet-internal wire-format change is safe as long as the packet still decodes** → it isn't:
  `HoprPacket::SIZE` is fixed by `PAYLOAD_SIZE_INT`, not derived from the size of what's inside it
  (e.g. a SURB), so growing an internal structure leaves the frame length unchanged — old and new
  nodes will silently misparse each other instead of failing to connect. Any such change needs a
  `CURRENT_HOPR_MSG_PROTOCOL` bump so mismatched versions simply fail to negotiate.
  (`surb-generation-tagging`)
- **The mixer queue is bounded, so it can't run away** → the accounting is bounded, but the sink
  behind it accepts unboundedly onto a heap. One peer whose remote stops reading parks that peer's
  write pump forever; the shared *serial* egress drain then burns a full backpressure timeout per
  packet for that one peer, head-of-line-blocking every other peer behind it, while the mixer queue
  climbs toward OOM. Signature: `hopr_egress_ring_buffer_dropped` pinned at a small constant nonzero
  rate — not zero, not climbing — while `hopr_mixer_queue_size` grows unbounded.
  (`stalled-write-pump-sink-eviction`)

### SURB balancer & return-path gotchas

These share one root cause: the entry's view of the exit's SURB supply is an *estimate*, fed only
by signals that travel over the same paths being measured.

| Gotcha | What actually happens | Source |
| --- | --- | --- |
| SURB supply looks healthy right up to collapse | `produced − consumed` only advances `consumed` when a reply reaches the entry. A return path silently dropping every reply is locally indistinguishable from a well-stocked, idle exit, so the balancer throttles production exactly when the exit is draining to empty — forward delivery can collapse to ~1% even on a 0-hop forward path sharing no node with the dead return relayer. | `surb-balancer-starves-on-return-path-loss`, `killing-a-return-relayer-collapses-the-0-hop-forward-direction` |
| Ending a session stops it consuming SURBs | The entry's KeepAlive balancer keeps minting and delivering SURBs to the exit long after the app stops writing — only SURB-buffer TTL or session idle-timeout ends it, and stock defaults (600s vs 180s) never let TTL win first. To actually starve a peer in a test, open with no SURB management, not just stop writing. | `abandoned-session-keeps-replenishing` |
| Automatic return-path recovery is a pure safety net | Both available recovery actions (dropping cached path candidates, forcing open-loop keep-alive minting) are independently destructive on a false positive — one dropped a 100% baseline to 1.3%, both together to 0.14%. False positives cost far more than false negatives cost in delay, so an aggressive trigger should bias hard against firing. | `return-path-recovery-actions-are-destructive` |
| SURB round-trip telemetry is a delivery-rate signal | The reported `expected` count is SURBs *minted*, not spent, and the balancer over-mints far beyond what's consumed — a 100%-healthy path can read 0.36. Total silence on a path also can't be told apart locally from "the peer has nothing to say"; only corroboration against sibling paths to the same destination separates the two. Treat both as comparative-only, never absolute. | `surb-ratio-is-comparative-only`, `surb-silence-is-evidence-only-relative-to-sibling-paths` |
| A gone counterparty just fails that one packet | An unresolved SURB routing lookup for one pseudonym can retry inside an ordered per-node pipeline stage, withholding every packet queued behind it — the whole node can stop originating anything, with no error line above `trace!`. Bounded now (drop + warn + `ROUTING_RESOLUTION_SURB_TIMEOUTS`), but the signature is worth knowing: `sent` frozen while `forwarded`/`received`/`ack_sent` keep climbing. | `origination-stall-unbounded-surb-retry` |

## Node state & channel-graph mental model

- **Channel lifecycle:** `OPEN → PENDING_TO_CLOSE` (grace period `T_closure`
  for the destination to redeem outstanding tickets) `→ CLOSED` (which
  increments `channel_epoch`). Tickets carry a `channel_epoch` and a
  monotonic `ticket_index`; a mismatch makes them unredeemable. (§3.1)
- **Identity binding:** the announcement contract binds off-chain packet key ↔
  chain account ↔ transport multiaddress. A node missing any binding is not a
  usable relay. (§6.1)
- **The channel graph is the canonical topology store.** Reason about
  reachability and routing from it (OPEN channels + connectivity + scores),
  not from raw peer lists. (§3.1, §6.1, §6.3)
- **Edge scoring:** probe success rate × step-function latency score, with
  `edge_penalty = 0.5` for unprobed edges and `min_ack_rate = 0.1`. Low-scoring
  edges are passively starved of traffic. (§6.2, §6.3)

## Symptom → likely cause

Use as a starting hypothesis set, then confirm against the summary and live data.

| Symptom                     | Likely causes (check in order)                                                                                                                                   | §                |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- |
| Node relays but never earns | Probabilistic win prob (small sample); downstream acks missing (PoR incomplete); it is acting as the final hop                                                   | §2.5, §3.2       |
| Ticket will not redeem      | `channel_epoch` mismatch (channel closed/reopened); `ticket_index` ordering; PoR challenge never completed; channel `PENDING_TO_CLOSE`/`CLOSED`                  | §2.5, §3.1, §3.2 |
| No path found               | A non-final edge lacks an OPEN channel or connectivity; edges starved below `min_ack_rate`; more than 3 relay hops requested; `max_paths` exhausted              | §6.1, §6.2, §6.3 |
| Session will not establish  | 30 s handshake timeout; no SURBs available; Exit out of slots or busy (`SessionError` `0x01`/`0x02`); unknown target (`0x00`)                                    | §5.2             |
| Packets dropped             | Mixer backpressure (bounded queue); duplicate `ReplayTag`; reliable-mode retransmissions exhausted (≤3)                                                          | §2.2, §4, §5.3   |
| High/variable latency       | Per-hop mixing delay by design (0–200 ms × hops); path re-sampled from cache                                                                                     | §4, §6.3         |
| Exit / recipient not paid   | Packet layer never pays the destination — need PIX; PIX agreement aborted; fewer than `t+1` valid shares; no relay on forward or return path; allocation expired | §3.2, §7         |
| Reply cannot be sent        | Out of SURBs (`0x03`) / SURB distress (`0x01`); `ReplyOpener` state lost; return path edges down                                                                 | §2.4, §5.1       |

## Benchmarking HOPR components

Domain gotchas on top of the general `performance` skill's benchmarking methodology:

- Use realistic parameters — e.g. a winning probability around 1%, not 100%. An always-win ticket changes the code path under test (PoR, redemption) and doesn't represent production load.
- Use bounded channels matching production capacity, not unbounded ones.
- Include the mixer adapter so its per-hop delay counts as real cost instead of being stripped out as noise. (§4)
- n-hop terminology: `n` = number of relayers (intermediate hops), not the total number of hops in the path; `n=0` means point-to-point (direct, no relay).

## Reference

- **HOPR protocol summary** — full condensation of RFC-0001–0014 (packet layer,
  Proof of Relay, tickets, mixing, application/session layers,
  discovery/probing/path-finding, PIX, economic reward system). Fetched on demand
  from the upstream permalink (see [Fetching the summary](#fetching-the-summary));
  the RFCs themselves are the ultimate authority.
