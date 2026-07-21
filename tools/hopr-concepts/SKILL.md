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

1. **Read the ground truth first.** Before reasoning about node state, the
   channel graph, or packet/ticket flow, read the relevant sections of
   [references/hopr-protocol-summary.md](references/hopr-protocol-summary.md)
   (a verbatim condensation of RFC-0001–0014). Do not answer from memory — the
   summary is authoritative here, and the RFCs behind it are authoritative over
   the summary.
2. **Check the misconceptions below** against whatever theory you are forming.
   Most confident-but-wrong HOPR diagnoses trace to one of them.
3. **Cite section numbers** (e.g. §3.2, §6.3) from the summary when you explain
   a finding, so the reasoning is checkable.

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

## Reference

- [references/hopr-protocol-summary.md](references/hopr-protocol-summary.md) —
  full condensation of RFC-0001–0014 (packet layer, Proof of Relay, tickets,
  mixing, application/session layers, discovery/probing/path-finding, PIX,
  economic reward system). Read on demand; the RFCs themselves are the ultimate
  authority.
