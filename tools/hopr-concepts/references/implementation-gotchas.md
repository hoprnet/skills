# Implementation gotchas (not covered by the RFCs)

Read this when a live node misbehaves in a way the protocol summary does not explain — session
flow-control that does nothing, wire-format changes that silently misparse, a node climbing toward
OOM, or anything involving SURBs and return paths.

The RFCs describe protocol _design_; everything below is emergent behavior of the current Rust
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
  write pump forever; the shared _serial_ egress drain then burns a full backpressure timeout per
  packet for that one peer, head-of-line-blocking every other peer behind it, while the mixer queue
  climbs toward OOM. Signature: `hopr_egress_ring_buffer_dropped` pinned at a small constant nonzero
  rate — not zero, not climbing — while `hopr_mixer_queue_size` grows unbounded.
  (`stalled-write-pump-sink-eviction`)

## SURB balancer & return-path gotchas

These share one root cause: the entry's view of the exit's SURB supply is an _estimate_, fed only
by signals that travel over the same paths being measured.

| Gotcha                                              | What actually happens                                                                                                                                                                                                                                                                                                                                                                                          | Source                                                                                                        |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| SURB supply looks healthy right up to collapse      | `produced − consumed` only advances `consumed` when a reply reaches the entry. A return path silently dropping every reply is locally indistinguishable from a well-stocked, idle exit, so the balancer throttles production exactly when the exit is draining to empty — forward delivery can collapse to ~1% even on a 0-hop forward path sharing no node with the dead return relayer.                      | `surb-balancer-starves-on-return-path-loss`, `killing-a-return-relayer-collapses-the-0-hop-forward-direction` |
| Ending a session stops it consuming SURBs           | The entry's KeepAlive balancer keeps minting and delivering SURBs to the exit long after the app stops writing — only SURB-buffer TTL or session idle-timeout ends it, and stock defaults (600s vs 180s) never let TTL win first. To actually starve a peer in a test, open with no SURB management, not just stop writing.                                                                                    | `abandoned-session-keeps-replenishing`                                                                        |
| Automatic return-path recovery is a pure safety net | Both available recovery actions (dropping cached path candidates, forcing open-loop keep-alive minting) are independently destructive on a false positive — one dropped a 100% baseline to 1.3%, both together to 0.14%. False positives cost far more than false negatives cost in delay, so an aggressive trigger should bias hard against firing.                                                           | `return-path-recovery-actions-are-destructive`                                                                |
| SURB round-trip telemetry is a delivery-rate signal | The reported `expected` count is SURBs _minted_, not spent, and the balancer over-mints far beyond what's consumed — a 100%-healthy path can read 0.36. Total silence on a path also can't be told apart locally from "the peer has nothing to say"; only corroboration against sibling paths to the same destination separates the two. Treat both as comparative-only, never absolute.                       | `surb-ratio-is-comparative-only`, `surb-silence-is-evidence-only-relative-to-sibling-paths`                   |
| A gone counterparty just fails that one packet      | An unresolved SURB routing lookup for one pseudonym can retry inside an ordered per-node pipeline stage, withholding every packet queued behind it — the whole node can stop originating anything, with no error line above `trace!`. Bounded now (drop + warn + `ROUTING_RESOLUTION_SURB_TIMEOUTS`), but the signature is worth knowing: `sent` frozen while `forwarded`/`received`/`ack_sent` keep climbing. | `origination-stall-unbounded-surb-retry`                                                                      |
