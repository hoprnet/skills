# Logs and traces

Use the producing application's README and telemetry documentation on the tracked
branch or deployed release. Record the resolved revision with the investigation.
Formatting, enabled levels, exported signals, and collection controls belong to
that application or embedding host.

## Preserve and select records

Keep the original capture and work on a selected copy. Record its producer,
process lifetime, timezone, time range and collection gaps. Keep surrounding
records when selecting the failing request/session so initialization and teardown
remain visible.

For structured logs, preserve the complete matching event, including timestamp,
level, target, fields and inherited span context. Inspect the actual schema before
writing a filter: IDs may be strings or numbers, nested fields may be serialized
JSON strings, and an identifier may live on a parent span. For text logs, retain
source file/line references and multiline errors. Do not silently discard malformed
or non-JSON records while treating the remainder as a complete capture.

Resolve a message or span's meaning by searching its exact text or target in the
producer's source at the deployed revision. Inspect the declaration, caller,
fields and gates. Only fetch the relevant source; an exhaustive event catalog
adds no evidence that an event occurred.

## Investigate at the captured log level

Levels express what the producer chose to record. Inspect the capture and its
filters before interpreting missing events; these steps do not assume every
application logs the same events at a given level.

| Available level | Analysis | When evidence is missing |
|---|---|---|
| INFO (including WARN/ERROR) | Build the component lifecycle and failure timeline from recorded transitions, errors and summaries. Identify the last observed successful stage and first error; compare with state/metrics if present. | If the decision causing the failure is absent, name that decision and request focused DEBUG for its owning component and operation. Silence is not success. |
| DEBUG | Follow the operation's recorded inputs, decisions, retries and outcomes. Bind request/session IDs to their parent spans and compare decisions with the deployed configuration. | If aggregate decisions cannot distinguish the hypotheses, identify the missing packet/frame or internal transition before requesting TRACE. |
| TRACE | Inspect the smallest relevant window for the recorded sequence of transitions, packets or frames; match attempts and results, keeping duplicates and retries distinct. | Check build-time level gates, target filters, sampling, rotation and collector drops. A runtime TRACE setting cannot restore compiled-out events. |

Use separate timelines for the host application and its library components even
when they share one log file. For an edge client, inspect the host's initialization
and request handling alongside the connector, strategy, transport and session
records. Read the [edge-client README](https://github.com/hoprnet/edge-client/blob/main/README.md)
on the matching branch for the embedding boundary.

## Without OTLP

Use the local component timelines and identifiers to follow the failed operation.
Keep log spans as local context; a span name or thread ID does not prove a
cross-process relationship. Compare counterpart captures only where IDs or recorded
bindings establish a join. If only the client capture exists, distinguish what it
observed from assumptions about relays or the Exit. State which counterpart window
would close the gap; enabling OTLP is not a prerequisite for analysis.

## OTLP configuration by application

Export uses OTLP. Configuration belongs to the application or embedding host.
These maintained branch links are starting points, not a fixed configuration
contract or an exhaustive list of producers:

| Application / context | Branch documentation |
|---|---|
| Edge client / embedding application | [edge-client `main`: README feature flags](https://github.com/hoprnet/edge-client/blob/main/README.md#feature-flags); also read the host application's exporter setup |
| HOPRd deployment | [hoprd `main`: Compose profiles](https://github.com/hoprnet/hoprd/blob/main/deploy/compose/README.md#profiles) |
| Blokli daemon | [blokli `main`: OTLP configuration](https://github.com/hoprnet/blokli/blob/main/OTLP.md) |
| Integration-test nodes and edgli client | [hopr-integration-tests `main`: Exporting spans to a collector](https://github.com/hoprnet/hopr-integration-tests/blob/main/README.md#exporting-spans-to-a-collector) |

Switch each link to the deployed branch or release tag when it differs from
`main`, and record the resolved revision. Check documented build features,
enablement, selected signals, endpoint/transport, resource identity, sampling,
export cadence and shutdown behavior. If documentation omits a detail, inspect
the exporter implementation on that revision.

1. Verify receipt for each relevant producer, process and time window in the
   collector/backend. Compare with local evidence to detect export gaps.
2. Query the affected component using the resource identity actually exported.
   Keep restarted instances separate; check backend filters and sampling before
   interpreting an absent span or metric.
3. Follow captured trace IDs, parent/span IDs and explicit links for the failed
   operation. Inspect status, events, duration and attributes at the first failed
   boundary; account for clock uncertainty in cross-process timing.
4. Correlate to logs/metrics through recorded bindings. Where propagation stops,
   return to independently established component timelines and mark the missing
   connection. Do not synthesize a distributed trace from matching timestamps.

Local logs, local spans and enabled metrics alone do not prove OTLP delivery.
A complete-looking trace also does not establish that all participants or signals
were captured.
