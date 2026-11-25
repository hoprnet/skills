# Debugging workflow

Use this procedure for one reported incident. Load the sibling `concepts` skill
and its protocol summary before interpreting the evidence. Keep an investigation
note beside the capture, not in the skill: symptom, versions, component timelines,
hypotheses, checks, and outcome.

## 1. Define the failure and map the participants

Write down the attempted operation, expected result, observed result, and time
window. Start with the affected client or application when one is available.
Record the actual process, network, lifetime and restarts, plus the provenance
and coverage of each capture. Identify whether HOPR runs as a library in an
embedding host or as an operator-run daemon. Record the host/binary version and,
where it does not identify the running code, the HOPR library revisions, build
feature flags, and enabled components/configuration. Verify which APIs, session
server capabilities, and relay-side ticket state exist before querying them;
a constructor such as `build_edge` alone does not establish those capabilities.
Separate an application's visible status from the component's observed behavior.

Record each producer's [telemetry setup and collection coverage](logs-and-traces.md#incident-telemetry-record),
including disabled or unavailable OTLP and unresolved fields. This establishes
which missing signals are evidence gaps before interpreting the incident.

Map the roles involved in this operation using the concepts summary (§2.4, §3,
§5, §6, §7, §8). Roles describe behavior, not binary names; one application can fulfill
more than one role.

| Participant | Role in the investigation |
|---|---|
| Host / Entry boundary | Establish whether the host submitted the operation to the HOPR entry library or daemon, and what HOPR returned. Diagnose entry-side HOPR behavior; hand off failures within the host. |
| Relay | Forwards packets between hops; inspect the relevant transport, directed channels, tickets and acknowledgements when implemented and enabled. |
| HOPR destination / Exit | Identify the HOPR endpoint receiving the session request, its admission/forwarding implementation, and evidence of replies using Entry-supplied SURBs. |
| Chain service (for example Blokli) | Treat connection state, freshness of indexed state, and transaction submission/outcomes as separate dependency checks. A healthy chain service does not establish packet-forwarding health. |
| PIX participants | PIX is an Exit-incentive mechanism, not a node role: where enabled, identify the Entry's payment strategy, the Exit's agreement and gate, and the configured pool/settlement implementation. Inspect agreement and settlement evidence separately from relay tickets (concepts §7). |
| Cover-traffic producer | Identify the application or service generating cover traffic, its configuration and the participating HOPR nodes. Determine whether the incident concerns the production cover-traffic service or an application's own cover-traffic/probing implementation; their settings and evidence need not match (concepts §6.2, §8). |
| Session target / external service | Identify the service the Exit is asked to reach. Use connection, request and response evidence at the Exit boundary; hand off diagnosis inside that service. |

Record the HOPR destination/Exit identity separately from the requested session
target (concepts summary §5.2). When the implementation resolves or restricts that
target, capture the selected endpoint and applicable policy as well. Reaching the
Exit does not establish that it accepted or connected to the target. Keep the
captured identities and their explicit bindings for correlation in step 3.

A session is the Entry–Exit exchange defined in summary §5; its establishment,
data flow, and termination are separate stages. Forward and return paths may
involve different relays. Use the concepts reference to establish the expected
sequence for the failing stage before searching for its events.

For an edge-client incident, locate its HOPR integration through the host's build and
[edge-client branch README](https://github.com/hoprnet/edge-client/blob/main/README.md).
Switch to the tracked branch or release and the dependencies identified above.
Do the same for other producers; a default branch or supplied architecture
snapshot is a navigation aid, not proof of deployed behavior. Use concepts for
protocol semantics and that deployed implementation for component behavior.

## 2. Analyze each component independently

Group evidence by process and component (for example: host/HOPR boundary, chain
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

### Optional operational sources

When the incident involves the relevant production environment and access is
available, use these sources alongside the affected component's own capture:

- **Cover traffic:** open the [cover-traffic Grafana dashboard](https://grafana.prod.hoprnet.link/d/ct-core-v5/ct-core-v5).
  Select the incident window and the relevant network, service or node using the
  filters actually available. Inspect the displayed series and their labels,
  units and gaps; compare changes with the producer and node logs. A dashboard
  aggregate alone does not identify a failed hop or prove session delivery.
- **Chain state and transactions:** inspect Blokli logs in
  [Cloud Logging](https://cloudlogging.app.goo.gl/cVYqZcKeNe5Xu5dEA) or
  [Graylog](https://graylog.prod.hoprnet.link/search/6a4e844c6a54421c503dc32f).
  The actual time window should be adjusted according to the relevant timestamps
  in the logs.
  Filter the incident window and actual network/instance using available fields;
  correlate observed request, transaction, channel or account identifiers to
  client and node records. Separate submission, chain outcome and indexed state;
  check freshness before interpreting a missing update.
- **Session evidence:** inspect [HOSE peers](https://hose.prod.hoprnet.link/peers)
  when it covers the relevant deployment. Use observed peer/session bindings to
  find the applicable capture, then check its participants, time window, signals
  and retention before comparing it with component logs. A peer's presence or a
  missing record does not alone establish an operation's outcome.
- **Other tools:** use the [production tools landing page](https://landing.prod.hoprnet.link/)
  to discover relevant sources, then verify their environment and coverage before
  treating results as incident evidence.

Record the source, time range, filters and evidence locations used. These links
are production-specific starting points; if a source is inaccessible or does not
cover the incident, mark the gap and continue with available local logs, metrics
or captured state. Opening a source does not imply permission to change its
configuration, start a capture, or send traffic.

Optional specialist skills can help when installed:

- **Measured performance problem:** use
  [swe:performance](https://github.com/Teebor-Choka/skills/blob/main/engineering/swe/swe/skills/performance/SKILL.md)
  for profiling and before/after measurement, alongside the HOPR-specific
  [benchmarking guidance](../../concepts/references/benchmarking.md).
- **Suspected Rust concurrency problem:** use the concurrency references in
  [swe:rust](https://github.com/Teebor-Choka/skills/blob/main/engineering/swe/swe/skills/rust/SKILL.md)
  for stalls, cancellation, locks, queues, or shutdown. Treat suggested causes
  as hypotheses and check them against the deployed code and dependencies.

These skills are not required for an investigation. If unavailable, continue with
the evidence and HOPR guidance here; follow the affected repository's own rules.

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
Record each identity's kind and the evidence establishing a binding: chain address,
transport peer/offchain identity, session ID or pseudonym, and directed channel
source/destination, ID and epoch when available. Scope session IDs to producer and
process lifetime; retain the Exit identity and session target as separate fields.
Join unlike identifier types only through evidenced bindings, never coincident
timestamps.

Find the last demonstrated success and first demonstrated failure along the
expected operation. Use only the relevant rows below; each comparison needs
evidence from the owning components, or an explicit gap.

Relay tickets and PIX Exit incentives are distinct mechanisms (concepts §3.2 and
§7). Establish the deployed revision and enabled features for each before using
its state; evidence of one mechanism does not establish the other's health.

| Failing operation | Compare across the boundary | Check that distinguishes explanations |
|---|---|---|
| HOPR startup/readiness | HOPR initialization result at the host boundary with connector initialization, backend compatibility and chain synchronization | Compare the last completed HOPR stage with the next error and the deployed configuration/schema; successful synchronization does not prove later initialization succeeded. Hand off failures confined to host startup. |
| HOPR chain transaction stalls | HOPR submission request, chain-service acceptance or rejection, chain execution and indexed outcome | Correlate a request/transaction binding and timestamps where available. Distinguish failure to submit, pending execution, execution failure and stale observation; a healthy read path does not prove submission works. |
| No usable route | Connector's fresh directed-channel view, transport reachability, strategy decisions and graph/path rejection | Inspect the rejected edge or constraint at the failure time. Distinguish unavailable funds/allowance, stale topology, disconnected peers and path constraints. Apply the final-hop exception from concepts. |
| Session will not establish | Entry request/challenge, Exit admission, target selection/connection, and response-path evidence | Reconstruct the stages and their order from the deployed Exit implementation. Distinguish an undelivered request, admission rejection, target-policy rejection, connection failure and missing return response. Check effective target restrictions/configuration where relevant; a successful local send proves neither Exit receipt nor target connection. |
| Established session stalls | Forward delivery to the Exit, target writes/responses, then reply delivery and Entry receive/reassembly | Find the first unsupported or failed boundary. Check usable SURBs and return-path evidence independently of forward delivery; routes need not match. Distinguish target silence, reply-path failure, session loss/retries and HOPR backpressure. A positive buffer estimate does not prove usable contents; inspect invalidation/expiry evidence when present. |
| PIX prevents progress | Session's agreement/gate decision with strategy/pool submission and observed settlement | Correlate the same agreement and phase. Separate a retry or queued submission from successful settlement. |
| Cover traffic stops or degrades | Producer configuration and attempts, available dashboard measurements, node/session outcomes and relevant chain-service evidence | Check the same deployment and time window; distinguish a disabled producer, missing or failed attempts, route/session failures and a telemetry gap. Use identifiers or explicit bindings to correlate individual operations; a change in an aggregate is a lead, not a root cause. |
| Relay/ticket failure | Hop transport and acknowledgement evidence with directed channel state and ticket lifecycle | Check the relevant hop and epoch; distinguish forwarding, acknowledgement, winning and redemption using concepts. |

For example, when a client connects but receives no data, first determine whether
its host submitted data to the session. Next look for Exit receipt, target
connection/write and target response. If the target produced a response but the
Entry did not receive it, inspect the Exit's reply/SURB evidence and the Entry's
receive/reassembly evidence. If HOPR handed the response to the host but the
product failed afterward,
prepare a handoff for the application owner with the corresponding operation,
payload/error metadata and time window. A target connection alone proves neither
a target response nor successful delivery back to the host.
If no Exit capture exists, report the boundary as unknown and request
that bounded window; do not label the Exit healthy from the client's ready state.

## 4. Test an explanation

State a falsifiable hypothesis about the first failed boundary and the observation
that would distinguish it from the strongest remaining alternative. Prefer an
existing log window, trace, dump or read-only query. Check the relevant implementation
at the deployed revision when the meaning of a field or transition is uncertain.
Use concepts' implementation references as leads, then confirm against that revision.

**Strongly prefer a HOPR integration reproduction** when the failing behavior can
be exercised by [hopr-integration-tests](https://github.com/hoprnet/hopr-integration-tests/blob/main/README.md).
Read its README and select a suitable existing scenario, or add a focused scenario
when within scope. Use the matching release line and the closest available component
revisions, features, configuration, topology and traffic to the incident; check the
harness's actual resolved binaries and dependency pins rather than assuming a branch
matches the deployment. Record any differences and how they limit the conclusion.

Reproduce the observed symptom and a distinguishing prediction, keeping the test
command, harness and component revisions, settings, results and relevant run artifacts.
Use the harness's documented local log collection; OTLP is optional. Repeat when
needed to characterize intermittent failures. If a fix is in scope and the scenario
exercises the right boundary, run it before and after the fix and retain a regression
test when appropriate. A local reproduction supports an explanation but does not by
itself prove that the live incident had the same cause.

If the harness cannot represent the incident, prerequisites or authorization are
missing, or the attempt does not reproduce it, record what was tried and why; continue
with local logs, metrics, captured state, or another suitable check. Do not require a
collector or a particular deployment. Do not run funded or external-network scenarios
without authorization for that scope.

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

For a substantial issue, PR description, or incident write-up, optionally use
[write-clear-explainers](https://github.com/SCBuergel/sebs-ai-tools/blob/main/skills/write-clear-explainers/SKILL.md)
if it is installed and the user requests writing or review. Base the text on the
investigation's evidence and preserve its uncertainties. If it is unavailable,
write the report using the guidance here; it is not a dependency of debugging.

For an external-application handoff, name the last evidenced HOPR boundary, the
unobserved or failing next stage, and the specific input/output or error the owner
should inspect. Limit a success claim to the observed operation and direction;
do not infer end-to-end health from a connection or a ready state.

End with the smallest next action and expected result. If a fix or mitigation was
applied within scope, repeat the original operation and check both recovery and
new errors. Record what actually ran; repeated real-incident validation must not
be claimed from documentation review alone.
