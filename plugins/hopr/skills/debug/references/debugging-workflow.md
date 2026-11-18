# Debugging workflow

Use this procedure for one reported incident. Load the sibling `concepts` skill
and its protocol summary before interpreting the evidence. Keep an investigation
note beside the capture, not in the skill: symptom, versions, component timelines,
hypotheses, checks, and outcome.

## 1. Define the failure and map the participants

Write down the attempted operation, expected result, observed result, and time
window. Start with the affected client or application when one is available.
Record its version/revision, network, process lifetime and restarts, plus the
provenance and coverage of each capture. Separate an application's visible status
from the underlying component's observed behavior.

Map the roles involved in this operation using the concepts summary (§2.4, §3,
§5, §6, §7). Roles describe behavior, not binary names; one application can fulfill
more than one role.

| Participant | Role in the investigation |
|---|---|
| Client / Entry | Originates the application request and HOPR session; inspect the embedding application and edge client as well as the protocol library. |
| Relay | Forwards packets between hops; inspect transport, directed payment channels, tickets and acknowledgements for the relevant hop. |
| Exit | Terminates the session toward the target service and sends replies using SURBs supplied by the Entry. Inspect its target connection separately. |
| Chain/indexer service | Supplies chain state and transaction outcomes. Its availability and freshness are separate from packet transport. |
| Target application/service | Produces or consumes the traffic outside the HOPR session. Its failure can look like a session stall. |

A session is the Entry–Exit exchange defined in summary §5; its establishment,
data flow, and termination are separate stages. Forward and return paths may
involve different relays. Use the concepts reference to establish the expected
sequence for the failing stage before searching for its events.

For an edge-client incident, start with the embedding application's build and
[edge-client branch README](https://github.com/hoprnet/edge-client/blob/main/README.md).
Switch to the tracked branch or release and resolve the actual library revisions
from that build. Do the same for other producers; a current default branch is a
navigation aid, not proof of the deployed behavior.

## 2. Analyze each component independently

Group evidence by process and component (for example: host application, chain
connector, strategy, graph/path selection, transport, session, and Exit target
connection). Use captured targets, resource attributes, or source declarations
to identify ownership. Do not merge components merely because they log at the
same time.

For each component involved in the failed operation:

1. Establish its process lifetime and capture coverage, including filters,
   sampling, truncation, and clock uncertainty.
2. Build a local timeline: last successful operation, state change, first failure,
   retry/recovery, and final state. Preserve file/line or trace/query references.
3. Compare inputs, decisions, and outputs with its responsibility. Record both
   the evidence supporting a failure and evidence that contradicts it.
4. Mark its outcome as observed success, observed failure, or unknown. A generic
   ready/healthy state is insufficient evidence that this operation succeeded.

Choose the evidence path before querying:

- **Logs only / no OTLP:** follow the level-specific steps in
  [logs and traces](logs-and-traces.md#investigate-at-the-captured-log-level).
  Build local timelines with available identifiers; do not require a collector
  or an invented distributed trace to proceed.
- **OTLP available:** query each producer and process independently first, then
  follow recorded trace/span relationships. Verify signal receipt and filters
  using [the application documentation](logs-and-traces.md#otlp-configuration-by-application).
- **Metrics available:** use the measurements and metadata actually exposed as
  described below, alongside the component timeline.
- **A component dump is present:** only then load
  [state snapshots](state-snapshots.md) and the producer's schema.

### Use the metrics that reached this capture

Discover series in the supplied capture or the selected backend's metadata, with
its time range and resource/label filters. A collector or cluster can drop, rename,
aggregate, or relabel a producer's telemetry. If needed, compare an authorized
producer scrape with backend results to locate that gap.

Select measurements for the affected component using their names, HELP/description,
type, unit, and labels. Check the producer's versioned docs or source when semantics
are ambiguous. Record the query and filters so another investigator can repeat it.
Do not query a fixed catalog or treat a missing series as zero.

- **Counters:** compare reset-aware deltas/rates within the same process lifetime
  and label set; retain scrape gaps and reset uncertainty.
- **Gauges:** compare levels and changes around the failure with their sample time.
- **Histograms:** compare distributions for the same window and units, retaining
  counts and bucket boundaries; do not average quantiles across instances.

Compare the measurements to the operation being investigated: activity before and
after failure, arrivals versus departures, attempts versus outcomes. Check that
those measurements count the same thing before subtracting or forming ratios.

## 3. Correlate components at the failing boundary

Only after the local analyses, align the relevant timelines. Normalize timestamps
to UTC but retain original times and clock uncertainty. Use explicit captured
bindings to join process/node, request, session, peer, channel, and trace identities.
A session ID is scoped to the producer and process lifetime; a channel also needs
direction and, when present, epoch. Pseudonyms, peer IDs and chain addresses are
not interchangeable. Coincident timestamps alone establish no causal link.

Find the last demonstrated success and first demonstrated failure along the
expected operation. Use only the relevant rows below; each comparison needs
evidence from the owning components, or an explicit gap.

| Failing operation | Compare across the boundary | Check that distinguishes explanations |
|---|---|---|
| Client startup/readiness | Host's startup failure with connector initialization, backend compatibility and chain synchronization | Compare the last completed stage with the next error and the deployed configuration/schema; successful synchronization does not prove later initialization succeeded. |
| No usable route | Connector's fresh directed-channel view, transport reachability, strategy decisions and graph/path rejection | Inspect the rejected edge or constraint at the failure time. Distinguish unavailable funds/allowance, stale topology, disconnected peers and path constraints. Apply the final-hop exception from concepts. |
| Session will not establish | Entry's request and challenge with Exit's admission/response and target connection | Determine whether the request arrived, was rejected, or its response failed to return. A local send proves neither remote receipt nor target availability. |
| Established session stalls | Entry's sends/receives, Exit's target reads/writes, session progress and usable SURB supply | Separate no target response, no usable reply path, session loss/retries, and local backpressure. A reported buffer estimate is not proof that its contents are usable; check invalidation or expiry evidence when present. |
| PIX prevents progress | Session's agreement/gate decision with strategy/pool submission and observed settlement | Correlate the same agreement and phase. Separate a retry or queued submission from successful settlement. |
| Relay/ticket failure | Hop transport and acknowledgement evidence with directed channel state and ticket lifecycle | Check the relevant hop and epoch; distinguish forwarding, acknowledgement, winning and redemption using concepts. |

For example, when a client connects but receives no data, first determine whether
its host submitted data to the session. Next look for Exit receipt and target
response. If the target produced a response but the client did not receive it,
inspect the Exit's reply/SURB evidence and then the Entry's receive/reassembly
evidence. If no Exit capture exists, report the boundary as unknown and request
that bounded window; do not label the Exit healthy from the client's ready state.

## 4. Test an explanation

State a falsifiable hypothesis about the first failed boundary and the observation
that would distinguish it from the strongest remaining alternative. Prefer an
existing log window, trace, dump or read-only query. Check the relevant implementation
at the deployed revision when the meaning of a field or transition is uncertain.
Use concepts' implementation references as leads, then confirm against that revision.

If the evidence is insufficient, specify a bounded reproduction: the same operation,
participating processes, required identifiers, component/target log filter, collection
window, expected success/failure evidence, and how to restore any changed setting.
Use the application's documented controls and obtain authorization for changes
outside the agreed scope. Escalate INFO to focused DEBUG, then TRACE only if the
missing observation requires it and the build supports it. Change one relevant
condition at a time and compare the outcome with the prediction.

A source-only walkthrough or synthetic fixture can check reasoning and parsing;
it cannot establish a live root cause. Stop at the evidence gap if no authorized
check is available, and record the exact evidence needed to continue.

## 5. Return a reproducible finding

Report the failed operation and window, producer versions and capture limitations,
component findings, the correlation that supports the explanation, and the check
that confirmed or rejected it. Cite artifact locations and the applicable concepts
summary sections. Distinguish observations, inferences, and untested alternatives.

End with the smallest next action and expected result. If a fix or mitigation was
applied within scope, repeat the original operation and check both recovery and
new errors. Record what actually ran; repeated real-incident validation must not
be claimed from documentation review alone.
