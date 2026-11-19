# Interpret a captured component dump

Load this reference only after finding a component dump in the incident evidence.
Do not assume an application supports dumps, a particular event name, an endpoint,
a schedule, or a fixed schema.

## Resolve the producer and schema

1. Identify the producing application, component, process lifetime, capture time
   and revision. Preserve the envelope and raw payload before decoding it.
2. Read that application's dump documentation on its tracked branch or release.
   Follow its dependency versions to the schema and implementation. Branch links
   below help locate the owner; use the deployed revision to interpret the capture.
3. Resolve only the symbols and fields actually present. Search for the captured
   event discriminator, payload type or field in the matching source, then inspect
   serialization, collection limits, error handling and update behavior.
4. Decode according to that schema. If a field contains JSON serialized as a string,
   decode it once after checking its type. Keep absent payloads and encoding errors
   as evidence gaps rather than substituting an empty state.

| Possible owner | Starting point when the dump identifies it |
|---|---|
| Edge client or embedding host | [edge-client `main` README](https://github.com/hoprnet/edge-client/blob/main/README.md) and the host's own documentation |
| Core protocol/session component | [hoprnet `master` README](https://github.com/hoprnet/hoprnet/blob/master/README.md) and the corresponding component source on that branch |
| Concrete graph, chain or transport implementation | [hopr-impls `main` README](https://github.com/hoprnet/hopr-impls/blob/main/README.md) |
| Strategy component | [hopr-strategy `main`: diagnostic state snapshots](https://github.com/hoprnet/hopr-strategy/blob/main/README.md#diagnostic-state-snapshots) |
| Shared dump/state trait | [hopr-api `main` README](https://github.com/hoprnet/hopr-api/blob/main/README.md); inspect the producing implementation as well as the trait |

## Use the dump in the component timeline

Check whether fields are instantaneous, cumulative, sampled, capped, or retained
from an earlier operation. Verify freshness and whether collection is atomic
before comparing fields. A generic health value may be a default implementation;
check what updates it before treating it as evidence of successful work.

Compare before/after dumps for the same component, process and relevant identity.
Use reset-aware counter differences and retain truncation/error metadata. Join
the changed state to the corresponding decisions in logs, traces or metrics.
A missing record in a bounded sample is not proof that it never existed.

Report the field and source definition that support the finding, the capture
location, and any limit on that interpretation. If the producer's revision or
schema cannot be established, preserve the raw observation and leave its meaning
unresolved. Return to the [debugging workflow](debugging-workflow.md) to correlate
with other components; do not infer their health from this one dump.
