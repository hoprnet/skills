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

Use separate timelines for host/HOPR boundary events and HOPR library components
even when they share one log file. For an embedded entry, select the host's HOPR
initialization, submitted operations and returned results alongside the enabled
connector, strategy, transport and session records. Record their library revisions
and feature flags when the host version alone is insufficient. Read the
[edge-client README](https://github.com/hoprnet/edge-client/blob/main/README.md)
on the matching branch for that integration; daemon controls need not apply.
Preserve the request/challenge, Exit identity, requested/selected target and
session binding when captured, so admission, connection and response failures
can be separated. Route failures inside the host or target to its owner with this
boundary evidence, rather than expanding into product-specific log investigation.

## Without OTLP

Use local logs, available metrics, and captured state to build component timelines
and follow the failed operation with recorded identifiers.
Keep log spans as local context; a span name or thread ID does not prove a
cross-process relationship. Compare counterpart captures only where IDs or recorded
bindings establish a join. If only the client capture exists, distinguish what it
observed from assumptions about relays or the Exit. State which counterpart window
would close the gap; enabling OTLP is not a prerequisite for analysis.

## OTLP configuration by application

When OTLP export is available, configuration belongs to the application or
embedding host. Use hoprd, blokli, edge-client, and hopr-integration-tests as
versioned examples of telemetry producers. These branch links locate their
Markdown documentation; they are not a shared configuration contract or an
exhaustive list of producers:

| Producer example | Branch documentation | What to resolve at the incident revision |
|---|---|---|
| HOPRd | [hoprd `main`: Compose README profiles](https://github.com/hoprnet/hoprd/blob/main/deploy/compose/README.md#profiles) | Endpoint-driven enablement, explicit suppression, signal selection and metric export cadence; Compose is one configuration example. |
| Blokli daemon | [blokli `main`: OTLP.md](https://github.com/hoprnet/blokli/blob/main/OTLP.md) | Signal defaults, environment overrides, HTTP/gRPC selection, per-signal paths and independent Prometheus metrics. |
| Edge client / embedding application | [edge-client `main`: README feature flags](https://github.com/hoprnet/edge-client/blob/main/README.md#feature-flags); also read the host application's exporter setup | Verify telemetry build support, then resolve enabled signals, transport, endpoint and resource attributes from the running host's exporter initialization and effective settings. |
| Integration-test nodes and edgli client | [hopr-integration-tests `main`: README span export](https://github.com/hoprnet/hopr-integration-tests/blob/main/README.md#exporting-spans-to-a-collector) | Harness revision and actual node/client builds, run settings, per-producer signals and transport, identity labels, separate traces and shutdown losses. |

Switch each link to the deployed branch or release tag when it differs from
`main`, then record the resolved commit and a permalink to the Markdown used.
If documentation omits or contradicts a detail, inspect the effective configuration
and exporter implementation at that revision. Do not assume that all components
export the same signals, use the same transport, or honor the same settings.

### Incident telemetry record

For every incident, record the following for each relevant producer/process.
Mark fields as disabled, unavailable, not applicable, or unknown with the evidence
for that status; an unavailable collector does not block local investigation.

| Field | Evidence to record |
|---|---|
| Actual producer | Binary or embedding host, emitting component, node/instance and process lifetime; distinguish harness settings from the processes that emit telemetry. |
| Deployed revision | Running binary/image revision and relevant library/build features; record the harness and deployment revisions separately when applicable. |
| Enabled signals | Traces, logs and metrics individually: supported by the build, enabled in effective runtime configuration, and observed in the capture. Local logging or a metrics endpoint does not establish OTLP export. |
| Transport | Actual OTLP/HTTP or OTLP/gRPC selection for each exporter; verify compatibility through each collector/receiver hop. |
| Endpoint | Effective destination, scheme, port and per-signal path where applicable, including configuration overrides; redact credentials. |
| Resource attributes | Actual exported keys and values identifying service, instance, node and run; distinguish resource attributes from span/event fields and collector-added labels. |
| Collector coverage | Collector/backend and configuration revision, covered producers, signals and time windows, observed receipt, pipeline routing, filters, sampling, drops and retention. Record unavailable or unverified coverage explicitly. |

Also check export cadence and shutdown/flush behavior when they could explain
gaps in the incident window.

### Query and correlate received telemetry

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
   connection. Spans from different nodes do not automatically form one
   distributed trace: require evidence of trace-context propagation and recorded
   parent/link relationships. A shared run ID or matching timestamps can select
   a comparison window but cannot establish a causal join.

Local logs, local spans and enabled metrics alone do not prove OTLP delivery.
A complete-looking trace also does not establish that all participants or signals
were captured.
