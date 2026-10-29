# Cascade procedure and repo map

## Repos and roles

| Repo | Role in the cascade |
| --- | --- |
| `hoprnet` | Source of the fix. Crates: `hopr-lib`, `hopr-utilities`, `hopr-utils-session`, `hopr-session-forwarder`, and siblings. |
| `hoprd` | Daemon. Pins hoprnet crates by git ref / published version. |
| `edge-client` (edgli) | Edge client. Pins hoprnet crates; often the reason a fix is urgent. |
| `gnosis_vpn` | GnosisVPN client. Pins hoprnet crates; deployed via the gnosis infra Ansible path (see environment-deploy). |

Downstream repos consume hoprnet in two forms:

- a direct git dependency pin in `Cargo.toml` (branch or exact rev), and/or
- a `[patch.crates-io]` git entry while a crate is under active development, which
  overrides the published version repo-wide.

## Release lines

A change frequently has to reach several lines in parallel. The ones seen in
practice: `master`/`main`, `release/4.0`, `release/4.1`, `release/v4`, `compat/v4`.
Treat each as an independent destination: its own upstream PR (cherry-pick or
re-land), and its own downstream repin to the matching line.

## Step-by-step

1. **Land the fix upstream on the first target line.** Feature branch off that line,
   open the PR, babysit to green. Downstream repos that need it now pin to the
   feature branch HEAD (or add a `[patch.crates-io]` git entry).
2. **Squash-merge** the upstream PR once approved. Note the squash-merge commit SHA
   on the target branch (`git log` on that branch, not the PR's branch).
3. **Repin downstream to the squash-merge commit.** Replace the feature-branch ref
   with the merge commit SHA on the target branch. Never leave the pre-merge feature
   SHA in a `Cargo.toml` — it is not reachable from the branch after the squash.
4. **Fan out to the other lines.** For each remaining `release/*` / `compat/*` line
   that needs the fix: re-land it upstream (cherry-pick), merge, and repin the
   downstream line that tracks it. A downstream repo's `release/4.1` tracks
   hoprnet's `release/4.1`, and so on — keep the pairing straight.
5. **Publish, then settle to the version.** When the crate is cut to crates.io
   (via the Nix `build-library` workflow in the dev shell — not `cargo publish`),
   drop every git pin and `[patch.crates-io]` git entry for that crate downstream
   and pin the published version. Git-pinned deps hold downstream CI yellow; the
   version pin is what turns it green.

## Verifying a pin before you trust it

- Confirm the commit you are pinning to is reachable on the branch it must live on:
  `git -C hoprnet branch --contains <sha>` should list the target line.
- After repinning, a `cargo update -p <crate>` (or lockfile refresh) plus a build is
  the cheapest proof the ref resolves and the API still lines up.
- If the build fails on a missing/extra struct field, that is the exhaustive-literal
  tripwire doing its job — wire the field, do not suppress it.

## Publishing the crates.io crates, leaf to root

Step 5 above assumes the crate is already on crates.io. When a repo depends on org crates
that need a release, `scripts/release-cascade.py` plans and drives the cascade from that
repo:

```
release-cascade.py --from edge-client@main            # v5 line, dry run
release-cascade.py --from edge-client@release/4.1     # v4 line, dry run
release-cascade.py --from edge-client@main --execute  # release every ready step
```

Run it from the directory that holds the repo clones, or pass `--workspace`. Missing org
repos are cloned there (partial clone). No config file exists: each run derives the graph.

1. **Discover.** Read `Cargo.lock` at the start branch and walk the graph. A crate is an
   org crate when crates.io lists its `repository` under `github.com/hoprnet`. Lookups are
   cached in `~/.cache/hopr-release/`, one request per second. A crate whose repo is not
   accessible becomes a fixed input with a warning.
2. **Map crates to repos and branches.** The default branch as start gives every repo's
   default branch. A `release/X.Y` start gives `compat/vX`, else `release/vX`. A repo with
   no such branch is a fixed input, only checked to be on crates.io and not yanked.
   `hopr-bindings` comes from `hoprnet/contracts` this way.
3. **Order from the branch heads.** A repo comes after every repo whose crates it requires.
4. **Block on stale requirements.** A step is `blocked_repin` while its repo's requirements
   do not admit the versions released below it. The report lists file, line, current and
   required requirement. Open a PR with those edits and a refreshed `Cargo.lock`, merge it,
   and run the script again.
5. **Release.** `--execute` asks `yes` before each step, runs the repo's release workflow,
   and continues once the run's `Publish library` step succeeds and the crates show on
   crates.io. A later failure in the same run (for example `Close release`) does not
   block the next step. A repo with no release workflow is a manual step.
6. **Repin the start repo** last, with the same kind of PR.

### Output

stdout is always one JSON document, schema `hopr-release-cascade/v1`. Progress goes to
stderr. Exit code 0 for every state except `error` (2). Read these fields:

| Field | Meaning |
| --- | --- |
| `state` | `release_ready`, `repin_required`, `final_repin_required`, `manual_release_required`, `complete`, `paused`, `error` |
| `summary` | One sentence on where the cascade stands. |
| `steps` | Per repo in release order: `status` (`published`, `ready`, `blocked_repin`), crates with `on_crates_io`, `repin_edits`. |
| `next` | The single next action: `release` (command, crates), `release_manual`, `repin` (edits), or `none`. |
| `conditions` | What must hold before the cascade continues, each with a `verify` command. |
| `resume` | The command to run once the conditions hold. `null` when complete. |
| `fixed_inputs`, `git_pinned_out_of_scope`, `warnings` | Inputs only checked on crates.io, org crates pinned by git (repin, not released), and gaps such as a repo that does not consume a new version. |

The cascade pauses between releases whenever a repin PR must merge. After the merge, run
the `resume` command. Each run reads the branch heads and crates.io again, so the report
always reflects the current state.

Facts that shape the cascade, as of 2026-10-29:

- `hopr-lib`, `hopr-transport` and the other hoprnet crates are git dependencies in
  edge-client and hoprd. Moving them is a repin (steps 1 to 4 above), not a release.
- hopr-impls is a virtual workspace with independent crate versions. Its release
  publishes only the crates whose version is not yet on crates.io.
- The step named `Publish library` in `hoprnet/hopr-workflows` `build-library.yaml` runs
  `cargo release publish --execute`. It is the same step in `hoprnet/contracts`.

Sources: `.github/workflows/release*.yaml` in the library repos and `hoprnet/contracts`;
`.github/workflows/build-library.yaml` in `hoprnet/hopr-workflows`; crates.io API
(`https://crates.io/api/v1/crates/<name>`).
