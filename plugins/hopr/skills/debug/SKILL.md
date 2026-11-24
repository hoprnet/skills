---
name: debug
description: >
  Diagnose HOPR protocol and implementation failures from logs, metrics,
  traces, and captured state: entry, relay, routing, sessions, SURBs, tickets,
  PIX, exit forwarding, and HOPR chain dependencies. External application and
  Gnosis VPN product debugging belongs to the application's owner.
---

# Diagnose a HOPR incident

Scope covers the application-to-HOPR entry boundary, entry and relay transport,
routing, sessions, SURBs, exit/session forwarding, and HOPR-related chain state
and transaction dependencies.

Host applications and target services provide boundary evidence. When evidence
points inside an external application or target service, report the last HOPR
boundary supported by evidence, the remaining uncertainty, and what its owner
should investigate. Hand off product diagnosis, including Gnosis VPN internals.

1. **Load [concepts](../concepts/SKILL.md) first.** Follow its protocol-summary
   instructions before interpreting HOPR behavior, including sessions, network
   roles, channels, SURBs, and PIX. Use its implementation references when the
   evidence points below the protocol layer.
2. **Follow the [debugging workflow](references/debugging-workflow.md).** Scope
   one incident, analyze each component independently, then correlate the
   components to locate the first failed boundary and test an explanation.
3. **Load only the evidence guidance needed for that incident:**
   - [Logs and traces](references/logs-and-traces.md) for log-only captures,
     INFO/DEBUG/TRACE investigation, or OTLP collection and correlation.
   - [State snapshots](references/state-snapshots.md) only when the capture
     contains a component dump; resolve its schema from the producer's branch.

Use the application's documentation and deployed revision for collection and
configuration. Start from the evidence actually available; telemetry differs
between applications, builds, nodes, and collectors. For each incident, record
the [producer and collection coverage](references/logs-and-traces.md#incident-telemetry-record).
OTLP is optional: continue with local logs, metrics, or captured state when no
collector is available. No specific deployment is required.

Strongly prefer reproducing suitable HOPR failures in
[hopr-integration-tests](https://github.com/hoprnet/hopr-integration-tests/blob/main/README.md)
as described in the [debugging workflow](references/debugging-workflow.md#4-test-an-explanation).
Continue investigating available evidence when reproduction is not feasible.

Return the supported finding, evidence locations, remaining uncertainty, and
smallest next check. Prefer existing captures and read-only queries. Changes to
configuration, restarts, funding, channel operations, or new traffic require
scope and authorization from the user.
