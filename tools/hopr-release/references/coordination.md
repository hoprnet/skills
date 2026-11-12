# Release coordination

`cascade_state.py` coordinates cooperative callers across VMs through one org VFS registry.
It does not use local locks, initialize state, launch workers, or enforce platform tool access.
Callers can bypass this protocol. This helper is not a sandbox.

## Deployment and storage

Before deployment, initialize `experts/rust-dep-propagator/state.json` once. For a fresh
installation use this JSON object:

<augment_code_snippet mode="EXCERPT">
````json
{"version":1,"runs":{},"routes":{},"events":{},"resources":{}}
````
</augment_code_snippet>

For this deployment, import the unfinished 2026-11-12 hoprd/edge-client main request as
an expired, unbound reservation. This blocks fresh writers until an operator preserves the
old work and chooses an owner. Never silently initialize over a legacy run.

The helper never creates a missing registry or replaces an invalid registry.
Do not initialize the registry again to resolve an error.
All timestamps use epoch seconds. The lease TTL is 900 seconds by default.

Supply the absolute org VFS prefix through global `--scope-prefix`, before the subcommand.
For example, `/root/.augment/vfs/TENANT/org` is a scope prefix, not a registry download location.
Without this option, the helper uses `$AUGMENT_VFS_DIR/org`.
It rejects a missing prefix, a relative prefix, and a prefix that does not end with `/org`.

Each read runs `auggie cloud vfs get-url <scope-prefix>/experts/rust-dep-propagator/state.json`.
It then runs `auggie cloud vfs get <URL> --output <scratch-file>`.
It reads `seq=N` from stdout or stderr. Missing or inconsistent sequences fail closed.
Each write runs `auggie cloud vfs put <scratch-file> experts/rust-dep-propagator/state.json --filesystem-id org --base-seq N`.
Scratch files use `TemporaryDirectory` and are removed when the operation ends.
Only authenticated durable `/files/` URLs without a query, fragment, or credentials may enter
subprocess arguments. Signed URLs are rejected. Subprocess output, errors, and child arguments
are never printed.

Only explicit CAS rejection diagnostics permit retries: `sequence conflict`, `sequence mismatch`,
`base-seq mismatch`, `precondition failed`, or `409 Conflict: VFS upload-file failed`.
The base-sequence keyword also accepts `_` or a space instead of `-`.
Diagnostics containing timeout, network, connection, or transport keywords remain uncertain failures.
Each conflict retry reads the registry again and recomputes the operation.
The default limit is five attempts. Other failures do not trigger retries.

## Exact CLI

Invoke `python3 tools/hopr-release/scripts/cascade_state.py [--scope-prefix PREFIX] SUBCOMMAND ...` from the skills repository.
All command results and errors use JSON on stdout. `--help` prints argparse help instead.
Successful commands exit with 0. Coordination failures exit with 1. Invalid arguments exit with 2.

In the table, `OWNER` means these three required flags:
`--run-id RUN --owner-session SESSION --generation INTEGER`.
`RESOURCE` means `owner/repo@base`. Repository names are case-insensitive; base names are case-sensitive.
Use the actual branch name, not ref aliases or commit IDs.

| Subcommand | Exact arguments after the subcommand |
| --- | --- |
| `lookup` | `--topic-key KEY` |
| `route` | `--topic-key KEY --router-session SESSION --event-id ID --target RESOURCE [--target RESOURCE ...]` |
| `bind` | `--run-id RUN --router-session SESSION --owner-session SESSION` |
| `alias` | `OWNER --topic-key KEY` |
| `inbox` | `OWNER` |
| `ack` | `OWNER --event-id ID` |
| `renew` | `OWNER` |
| `check` | `OWNER` |
| `claim-operation` | `OWNER --resource RESOURCE` |
| `record-pr` | `OWNER --resource RESOURCE --url URL` |
| `finish-operation` | `OWNER --resource RESOURCE --outcome merged\|skipped` |
| `complete` | `OWNER` |
| `handover` | `OWNER --new-owner-session SESSION` |
| `prepare` | `OWNER --resource RESOURCE [--action-id ID]` |
| `succeed` | `OWNER --resource RESOURCE --mutation-id ID` |
| `push` | `OWNER --resource RESOURCE --repo-path PATH --branch HEAD_BRANCH --expected-head SHA --action-id ID` |
| `exec` | `OWNER --resource RESOURCE [--action-id ID] [--timeout SECONDS] -- COMMAND [ARG ...]` |

`--timeout` applies only to the child command and defaults to 900 seconds.
The guard renews the lease in the background during execution. A renewal failure leaves uncertainty.
A distinct action ID permits a later repair; reusing a completed action ID cannot repeat its write.
No force, takeover, initialization, release-child, or unlock subcommand exists.

### Routing and ownership

1. Join normalized realm URL, numeric stream ID, and exact topic with `|` for the key.
   For DMs join realm URL, `dm`, and verified sender email. Call `lookup` before parsing a new run.
   It returns `missing`, `existing`, `blocked`, or `complete` with the existing targets when present.
2. Use normalized realm URL plus `|` plus Zulip message ID as `--event-id`.
3. Launch one manager only if `action` is `launch`.
4. Bind that manager's session with the reservation's router session.
5. Attach the AI Automation output topic through `alias`.
6. The owner subscribes directly to CUSTOM `custom.webhook` events for the output topic
   and origin topic or DM sender. Keep one five-minute scheduled backstop. End the turn while
   waiting. A fresh router need not message a worker it does not own. Recreate subscriptions on resume.
7. The owner reads `inbox`, fetches authoritative messages, and calls `ack` after ingestion.

`route` commits its reservation before it returns `launch`.
It returns `run_id`, `generation`, `router_session`, `owner_session`, `status`, `lease_until`, `targets`, and `action`.
The owner is null until `bind` succeeds.
Only the reservation's router can bind. Repeating the same binding is safe; a different binding fails.
The router cannot bind again after handover.

An active topic route or reserved target joins the existing run instead of launching another manager.
The existing run reserves the union of targets and receives the event ID.
`action` is `forward` when the run has an owner, or `pending` when it does not.
An overlap across different active runs fails with `overlapping_runs`, even if their owner session IDs match.
This conservative rule does not merge independent run histories.
An alias cannot replace another active run's topic route.

Every event ID remains associated with its original run after acknowledgement and completion.
A replay returns `action: duplicate`, never `launch`, and does not enqueue the event again.
`inbox` returns only `{"event_ids": ["ID", ...]}`. It does not store message text.
`ack` returns `event_id` and `handled: true`; repeating acknowledgement is safe.

Ownership checks require the current owner session, generation, and an unexpired lease.
`check` returns run metadata and the current `mutation` marker, or null.
`renew` extends the same owner's lease from the current time.
Neither command replaces the owner.
Expired runs retain every reservation. New routes to them fail with `lease_expired`, rather than launch a replacement.
Expired owners cannot renew, bind, hand over, or mutate state.
Event replay and duplicate completion remain read-only and safe after expiry.

`handover` requires a live current owner and no mutation marker.
It changes the owner and increments the generation without resetting tasks, messages, or the lease.
The new owner must use the returned generation. Stale owner calls fail.
Standdown requires cooperative handover or parent completion after all work finishes.

### Operations and parent completion

`claim-operation` reserves `owner/repo@base` globally across active runs.
Operation identity does not include source revision, operation kind, or caller session.
Repeated claims return the same `operation_id`, `pr`, `status`, `mutation_done`, and `completed_actions`, plus `resource`.
Claims can add resources beyond the route's initial targets.

`record-pr` accepts only `https://github.com/<expected-owner>/<expected-repo>/pull/<positive-number>`.
It rejects another repository, query strings, fragments, and a second PR URL for the same operation.
The same canonical URL is idempotent. PR URLs and operation IDs persist through handover.

`finish-operation` changes one claimed operation to `merged` or `skipped`.
Repeating the same outcome is safe; changing a terminal outcome fails.
It does not complete the parent run or release its reservations.

`complete` requires every target to have a terminal operation.
It also requires every additional claimed operation to be terminal, every event acknowledged, and no mutation marker.
Duplicate completion is idempotent. Completion releases reservations for future runs, but retains the original history.
A future run receives new operation IDs. It does not erase previous PR records or event associations.

### Managed PR creation

Pass `--cascade-run`, `--owner-session`, `--generation`, and `--coordination-prefix`
to the deployed `release-cascade.py`. It claims each operation and records its PR with a
run/operation body marker. It renews ownership during local builds, then guards the remote
push and PR creation. A local build failure occurs before that mutation boundary.
Registered PRs survive plan changes. Unknown existing cascade PRs block creation.
Managed runs never close sibling PRs, publish crates, or use blocking `--wait-merge`.
Unmanaged interactive CLI execution retains legacy behavior and is not a concurrency guarantee.

### Guarded mutation and uncertain outcomes

1. Claim the operation before any external mutation.
2. Use `exec --action-id <stable-purpose-and-head>` for a command, or `prepare` and `succeed`
   around exactly one approved GitHub tool action. Never send child output to the transcript.
3. For additive repair pushes, use `push` with the reviewed full remote SHA. It validates the
   remote URL and head, preserves its ancestor commit, and uses normal push without force.
   A concurrent non-fast-forward update is rejected.
4. Record its PR and finish the operation after the required external checks.

`exec` atomically checks live ownership and the claimed resource, then stores a unique uncertain mutation marker.
It executes an argument vector without a shell. It suppresses stdin, stdout, and stderr.
Success clears the run marker, sets `mutation_done`, and records the completed action ID.
The same action cannot execute again. A distinct repair action can use the same operation.
Use purpose plus reviewed source SHA or authorizing human message ID, not a random retry ID.
Read-only subprocess checks do not require a mutation guard.

Nonzero exit, exception, timeout, process crash, or uncertain registry write leaves the run blocked.
No child command starts if the begin write fails or exhausts its CAS attempts.
No TTL clears the marker. While it exists, another mutation, handover, operation finish, and parent completion fail.
A lease expiry can also prevent the success write, even when the child command succeeds.
Do not repeat a command to determine whether its first execution succeeded.

## Public Python API

Import `scripts/cascade_state.py` through its file path or place its directory on the module search path.
All methods return JSON-compatible dictionaries. `CoordinationError` contains a safe error code.
`Conflict` is its subclass and identifies only a positively rejected conditional write.

- `VfsStore(scope_prefix=None, *, runner=subprocess.run, timeout=30)` implements remote storage.
- `VfsStore.read()` returns `(state_dict, sequence_int)` and validates the registry.
- `VfsStore.write(state_dict, sequence_int)` validates the registry and performs one conditional write.
- `Registry(store, clock=time.time, *, lease_ttl=900, retries=5)` accepts any store with that read/write contract.

The registry methods use positional or keyword arguments:

| Method | Parameters |
| --- | --- |
| `lookup` | `topic_key` |
| `route` | `topic_key, router_session, event_id, targets` |
| `bind` | `run_id, router_session, owner_session` |
| `alias` | `run_id, owner_session, generation, topic_key` |
| `inbox`, `check`, `renew`, `complete` | `run_id, owner_session, generation` |
| `ack` | `run_id, owner_session, generation, event_id` |
| `claim_operation` | `run_id, owner_session, generation, resource` |
| `record_pr` | `run_id, owner_session, generation, resource, url` |
| `finish_operation` | `run_id, owner_session, generation, resource, outcome` |
| `handover` | `run_id, owner_session, generation, new_owner_session` |
| `begin_mutation`, `guard` | `run_id, owner_session, generation, resource, action_id="initial"` |
| `keepalive` | `run_id, owner_session, generation` |
| `push` | `run_id, owner_session, generation, resource, repo_path, branch, expected_head, action_id` |
| `end_mutation` | `run_id, owner_session, generation, resource, mutation_id` |
| `exec` | `run_id, owner_session, generation, resource, command, *, timeout=900, action_id="initial"` |

`begin_mutation` returns `mutation_id`, `resource`, `action_id`, `started_at`, and `state: uncertain`.
`guard` is a context manager around one mutation. `keepalive` renews during local preparation
without authorizing an external mutation.
Call `end_mutation` with that exact token only after confirmed success.
It returns `mutation_id` and `status: succeeded`, as does successful `exec`.
On exception or uncertain external effects, do not call `end_mutation` in a `finally` block.
The parent can use this pair around its existing apply operation without a subprocess wrapper.

## Operator recovery

An expired lease or uncertain mutation requires explicit operator reconciliation, not autonomous replacement.

1. Stop all former owners and any remaining child commands across VMs.
2. Inspect the remote registry, GitHub PRs, branches, and other external effects without repeating mutations.
3. Determine which effects succeeded and which tasks remain.
4. Preserve event history, operation IDs, PR bindings, and unrelated reservations.
5. Apply an operator-reviewed conditional registry correction with its current sequence.
6. If ownership changes, increment the generation and set an explicitly reviewed lease.
7. Clear an uncertain marker only after the operator reconciles its external effects.
8. Resume one confirmed owner, or leave the run blocked if its effects remain unknown.

Do not use `end_mutation` as an automatic recovery or unlock mechanism.
Do not infer that a command stopped merely because its lease or timeout expired.
A child can create descendants or external work that continues after its process exits.
The registry is cooperative coordination, not platform-level fencing of already-running tools.

## Offline verification

Run `python3 -I tools/hopr-release/scripts/test_cascade_state.py` from the skills repository.
The tests use a thread-safe shared store, forced competing reads, mocked VFS commands, and a standard-library child process.
They do not contact the live control plane.
