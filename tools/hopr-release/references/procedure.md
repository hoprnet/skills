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

GitHub access uses `gh` when it is installed and logged in. Otherwise it uses the REST API
with `GH_TOKEN` or `GITHUB_TOKEN` (`--forge gh|api` forces one), so a VM with no `gh` works.
The token can also come from `--token-cmd CMD`, a shell command that prints one token (for
example a git credential helper), or from `--token-file PATH`; either option means the REST
API even when `gh` is installed. The order is `GH_TOKEN` or `GITHUB_TOKEN`, then the command,
then the file. The command runs again when its token is 50 minutes old, since an App
installation token lasts an hour, and after a 401, so `--wait-merge` can run longer. The script
needs Python 3.11 or newer and no packages. A GitHub App needs these permissions on the repos:
contents write (push), pull requests write, actions write (dispatch) and workflows (to push a
workflow file). A dry run needs none.
The token reaches git through an environment header; the script never puts it on a command
line, writes it to a file, or prints it.
`-o json` (default) prints indented JSON, `-o mjson` the same on one line, `-o plain` a text
rendering.

1. **Discover.** Read `Cargo.lock` at the start branch and walk the graph. A crate is an
   org crate when crates.io lists its `repository` under `github.com/hoprnet`. Lookups are
   cached in `~/.cache/hopr-release/`, one request per second. A crate whose repo is not
   accessible becomes a fixed input with a warning.
2. **Map crates to repos and branches.** The default branch as start gives every repo's
   default branch. A `release/X.Y` start gives `compat/vX`, else `release/vX`. A repo with
   no such branch is a fixed input, only checked to be on crates.io and not yanked.
   `hopr-bindings` comes from `hoprnet/contracts` this way.
3. **Order from the branch heads.** A repo comes after every repo whose crates it requires.
4. **Repin first when requirements lag.** A repo whose requirements do not admit the
   versions released below it needs a `repin` PR (file, line, current, required) before
   its release. A repo whose head is already published is bumped in that PR and released
   again.
5. **Release.** A crate is released unless its head version is already on crates.io, or the
   head is only CI's post-release bump: set by a `ci: Bump to version X` commit, with
   nothing but the `version` lines of a `Cargo.toml` and `Cargo.lock` changed by that commit or
   after it. A bump head with no published version to stand in for it stays a release. Such a head has no new code. It
   is listed in `bump_heads`, not released, and the newest published version stands in for
   it, so lock updates target versions that exist. A head set by a real change, or followed
   by one, is a release. A release waits only for what it needs: a repin, or an
   upstream crate whose new version its requirement needs because no published version
   meets it. Everything else is ready together. `--execute` asks `yes` once, triggers all
   ready releases at the same time, and continues for each repo once its versions show on
   crates.io. The run's `Publish library` step is only an early failure signal: crates.io
   decides, and later jobs of the run (for example `Close release`) do not matter. A repo
   with no release workflow is a manual step.
   - A run also releases only the crates in its first plan.
   - `--max-releases N` caps how many repos one run releases.
6. **Update the repo behind each git pin** (hoprnet) before the start repo. The library
   crates reach the start repo through it (`hopr-lib` and its siblings are git dependencies),
   so it is a step of the cascade: its branch comes from the pin (`branch = X`, or the default
   branch when a `rev` pin is on it). It gets a `repin` operation for requirements that do not
   admit the released versions and for a stale `Cargo.lock`, and it is never released. On
   v4 that is `hoprnet@release/4.0`; on v5, `hoprnet@master` (it requires `hopr-types` 4.1.0,
   which does not admit 5.0.0).
7. **Repin the start repo** last, with the same kind of PR. It waits for the repo behind the
   git pin, and moves the git revision: `cargo update -p hopr-lib` for a branch pin, an edit of
   the `rev` for a `rev` pin. The final operation also
   updates lock entries that pin older versions than the released ones, and swaps git-ref
   pins of org crates (`git = ..., branch/rev/tag = ...`) for a released version. A pin whose
   crate has no published version yet is a `blocked` `pin_swap` operation with the reason,
   unless its repo is a step of the cascade (hoprnet): then it is only a note in
   `git_pinned_swaps`, because that repo is never released and the final operation already
   moves the git revision. `[patch]` git entries are only warned about.
8. **Bump level.** A published crate whose requirements must be raised is bumped and
   released again, by a patch. When the raised dependency moves to another compatibility line
   (major, or minor for 0.x) and the crate re-exports it (`pub use`), its own public API
   changes too: the plan then proposes the next breaking version (major, or minor for 0.x) and
   marks it with `level` and `reason` in `version_bumps`. Dependents are re-checked against that
   version, so the change climbs the tree (on v5: `hopr-types` 5.0.0, then `hopr-api` 5.0.0, then
   everything that requires `hopr-api` 4.x). The bump is a proposal: it is a version edit in the
   repin PR, to be reviewed. The re-export check covers every crate of a repo, and a gap in a
   virtual workspace root counts for all its crates, so a multi-crate repo can get more bumps
   than it needs.
9. **Order rule.** The start repo's PR is never opened while the PR of the repo it waits for
   (hoprnet) is open: that operation is `pending` until the PR is merged. `--wait-merge MINUTES`
   keeps the script running after it opens PRs: it waits for them to merge (it never merges),
   re-plans at once, and opens the PRs that were waiting. A PR closed without a merge, or the time
   limit, ends the run with state `paused`. A pin whose crate has a published version becomes a
   version requirement (one edit per line), and its git revision is no longer moved. Inline pins
   (`k = { git = ... }`) and table pins (`[dependencies.k]`) are both edited.
10. **Open the PRs.** `--execute-no-release` opens a PR for each ready repin or final
   operation, without releasing: it creates a branch in a git worktree
   (`<workspace>/<repo>-worktrees/`), edits `Cargo.toml`, refreshes `Cargo.lock` with cargo
   (inside `nix develop` when the repo has a flake, so the hooks run), commits, pushes and
   opens the PR. It never merges. A branch name is a hash of everything the PR holds (edits, version bumps, lock updates, the git revision move and the base commit), so a re-run finds
   the open PR. `--execute` releases first, then does the same.

### Output

With `-o json` (default) or `-o mjson`, stdout is one JSON document, schema `hopr-release-cascade/v2`
(`-o plain` prints the same report as text). A malformed `--from` and any runtime error also
end in this document, with `state` `error`. Progress goes to
stderr. Exit code 0 for every state except `error` (2). The dry run lists every operation
still to do, leaf first, so a bot can propagate the repins, turn the list into a workflow,
or run the releases itself.

| Field | Meaning |
| --- | --- |
| `state` | `release_ready`, `repin_required`, `final_repin_required`, `manual_release_required`, `blocked`, `complete`, `paused`, `error` |
| `summary` | One sentence on where the cascade stands. |
| `repos_to_update` | Per repo and branch: its operation ids, `needs_pr`, and the crates it releases. |
| `crates_to_change` | Per crate: the version to publish, repo, branch, `republish`, operation id. |
| `operations` | The full ordered list. Each has `id`, `kind`, `repo`, `branch`, `status` (`ready`, `pending` or `blocked`), `depends_on`, `description`, `layer`, `concurrency_group`. |
| `ready_operations`, `next` | Ids that can run now, and the first of them. |
| `layers` | Operation ids per layer. A layer is 1 + the highest layer among an operation's `depends_on`. Operations in one layer are independent; each also carries `layer` and `concurrency_group` (`layer-N`). |
| `conditions` | What must hold before the cascade continues, each with a `verify` command. |
| `resume` | The command to run once the conditions hold. `null` when complete. |
| `later_heads` | Real unpublished heads outside this run's first plan. Not released now; run again. Each also gives a warning. |
| `bump_heads` | Heads that are only CI's version bump after a release: crate, head, newest published. Not released. |
| `git_pinned_swaps` | Git pins whose repo is a step (hoprnet): kept as notes. They can become version requirements once its crates are on crates.io. |
| `pull_requests` | Draft PRs opened or found by `--execute` and `--execute-no-release`: operation, repo, base, branch, url, state (`opened` or `exists`). |
| `fixed_inputs`, `git_pinned_out_of_scope`, `warnings` | Inputs only checked on crates.io, org crates pinned by git (repin, not released), and problems such as a yanked fixed input. |

A `repin` operation looks like this (abridged). The edit names the file, the line and the exact
requirement to change, and the bump (when the repo's head is already published) the version
line to raise:

```json
{"id": "op-3", "kind": "repin", "repo": "hopr-api", "branch": "main", "status": "pending",
 "depends_on": ["op-2"], "layer": 2, "concurrency_group": "layer-2",
 "edits": [{"file": "Cargo.toml", "line": 85, "key": "hopr-types", "crate": "hopr-types",
            "current": "4.1.0", "required": "5.0.0"}],
 "version_bumps": []}
```

Operation kinds:

| `kind` | Fields | Meaning |
| --- | --- | --- |
| `release` | `crates`, `workflow`, `command`, `republish` | Run the repo's release workflow. `republish` means the head version is already published, so the preceding `repin` bumps it. |
| `release_manual` | `crates` | The repo has no release workflow. Publish by hand. |
| `repin` | `edits`, `version_bumps`, `then` | Raise requirements in a library repo (file, line, key, current, required). Needs a PR with a refreshed `Cargo.lock`. |
| `repin` (git-pinned repo) | `edits`, `lock_updates`, `then` | The repo behind a git pin (hoprnet): requirement edits and a lock refresh, without a version bump or a release. |
| `final_repin` | `edits`, `lock_updates`, `git_updates`, `then` | Update the start repo: requirement edits, git-pin swaps (`edits` with `swap: git_to_version`), and lock entries that pin older versions than the released ones (`cargo update -p <crate>`). |
| `pin_swap` | `blocked_reason` | A git pin that cannot be swapped yet. `status` is `blocked`. |

Run the script once per start repo. `--from edge-client@release/4.1` and
`--from hoprd@release/4.1` share the library operations and differ in the final one.

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
