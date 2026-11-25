# Triage session logs

Use this procedure for a stall, low throughput, session-establishment failure or
suspected return-path problem. Start from whichever participant's evidence is
available; a relay capture may contain no identifiable client or host application.
Keep original logs and cite each supporting record and its time window. Follow
[logs and traces](logs-and-traces.md) for capture coverage and level-specific steps.

The message names below are search leads from particular implementations, not a
shared log schema. Locate each event and its fields in the producing component's
deployed revision, including its level, feature gates, counter semantics and
effective runtime configuration, before interpreting it. Missing events are
unknown when the capture or level cannot show them.

## 1. Select the operation

1. Record producer, version, process lifetime, clock uncertainty, log level and
   time coverage. Identify available configuration, capabilities and session mode
   from the running process or the matching implementation. Keep restarts distinct.
2. Mark the attempt, establishment or failure, stall, recovery, termination and
   capture end when observed. Count events in short windows around the symptom and
   compare with a quiet baseline; retain the surrounding initialization and teardown.
3. Identify the affected data session using recorded request, peer, target and
   session bindings. Separate it from other sessions and probes; do not infer a
   binding from matching timestamps. Extract state transitions and their causes
   where the records support them. If only a relay log exists, select the relevant
   peer, channel, packet or acknowledgement evidence and keep the client unknown.

For example, a GnosisVPN host capture may name `open_wg_session` and
`WgStatsSample`. Use those only when present to locate its data-session boundary;
verify their meaning and relationship to HOPR in the host's deployed source.
Routing, WireGuard and application state are host-side evidence, not generic HOPR
semantics. Hand off failures inside that application to its owner.

## 2. Locate the first unsupported stage

For the same session, direction, process lifetime and time window, compare the
available measures of packet arrival, session processing, and delivery to the host
or target. Check units, sampling intervals and what increments each counter first;
packet and byte counters cannot be directly subtracted. A reset may indicate a
restart or a recreated counter, but does not by itself prove a session rebuild.

- **Arrival and delivery both fall:** investigate upstream delivery, the return
  path and the sender's behavior. This pattern alone cannot identify the lost hop.
- **Arrival continues while delivery falls:** inspect local reassembly, sequencing,
  flow control, backpressure and host handoff. Match records to the data session;
  distinguish discarded, incomplete, late and successfully emitted frames.
- **HOPR delivers to the host but the application still fails:** report that last
  supported boundary and hand off the host-side failure. A successful connection
  or a short probe alone does not prove the data session is healthy.

When enabled, check a PIX gate and its associated agreement, payment submission
and observed outcome as a possible explanation for a stall. For example, the
GnosisVPN message `inbound stalled while the exit awaits PIX payment` is a host
observation to correlate with payment and Exit evidence, not proof of settlement
or a universal time threshold. Continue considering transport and local processing
when the payment state is unknown.

When recorded, `frame already seen`, `failed to reassemble frame`, and `frame
discarded` can lead to the relevant stages. Search exact event text and inspect
its context, session and error; similar wording can occur in different stages or
versions. Compare gap and age settings with observed arrival timing, session mode
and actual sequencing behavior. Treat a proposed rate threshold as a hypothesis,
not a universal diagnosis; verify it against the versioned implementation and
measurements. Absence of frame errors in a short or low-volume probe is not proof
that a busy data session has no loss.

## 3. Check the return path and controls

Compare the Entry's estimate of counterparty SURB supply with the Exit's reported
level and actual send/receive evidence when those are available. Estimates, reports,
and usable contents are different observations; identify their freshness and
scope. A falling report or an increasing mint count alone does not prove the Exit
sent replies or that they reached the Entry.

For a suspected failing return route, compare path resolution, relayer diversity,
health decisions and any recorded per-path observations with the timing of the
stall and corroborating relay or Exit records. Check the relevant directed channel
and connectivity under the deployed route rules; the final-hop exception still
applies. For example, `surb balancer state`, `surb round-trip flush tick`, and
`surb round-trip pair` can be useful leads when emitted by that revision. Their
levels and fields differ across versions. `expected` may count minted SURBs,
not packets actually sent: do not interpret `observed / expected` as a packet-loss
rate. See the [SURB implementation gotchas](../../concepts/references/implementation-gotchas.md)
and verify the matching source.

Compare a concurrent control only after establishing what it actually exercises:
a separate probe to the same Exit may share some paths or resources while bypassing
the affected data session. Multiple affected destinations may suggest a shared
component; they do not locate it without further evidence. Compare window, load,
session mode and path coverage before using a control to distinguish hypotheses.
Check recorded Exit capacity and local load where available; correlated SURB mint
rate and throughput can have shared causes and do not establish congestion by themselves.

## 4. Return to the incident workflow

Record the observed boundary, competing explanations, evidence against each, and
the smallest check or counterpart capture that would distinguish them. Prefer
effective configuration recorded at the incident time, then verify how that
revision uses it. Do not apply pasted thresholds or defaults to another build.
Return to [test an explanation](debugging-workflow.md#4-test-an-explanation),
including a suitable integration reproduction when feasible.
