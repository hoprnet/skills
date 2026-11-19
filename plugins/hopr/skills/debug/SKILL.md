---
name: debug
description: >
  Diagnose HOPR incidents from client, entry, relay, or exit logs, metrics,
  traces, and captured state. Use for failed startup, connectivity, routing,
  channels, sessions, SURBs, PIX, or stalled traffic.
---

# Diagnose a HOPR incident

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
between applications, builds, nodes, and collectors.

Return the supported finding, evidence locations, remaining uncertainty, and
smallest next check. Prefer existing captures and read-only queries. Changes to
configuration, restarts, funding, channel operations, or new traffic require
scope and authorization from the user.
