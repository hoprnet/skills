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

Step 5 above assumes the crate is already on crates.io. When several library crates need
a release (hopr-types, hopr-api, hopr-utilities, hopr-impls, hopr-strategy, blokli-client),
publish them in dependency order, once per release line, then repin hoprd and edge-client.
`scripts/release-cascade.py` plans and drives this; the graph lives in `configs/`.

| Line | Config | Library branch | Root repos (repinned last) |
| --- | --- | --- | --- |
| v5 | `configs/release-cascade.v5.yaml` | `main` | `edge-client` `main`, `hoprd` `main` |
| v4 | `configs/release-cascade.v4.yaml` | `compat/v4` | `edge-client` `release/4.1`, `hoprd` `release/4.1` |

Run the script from the directory that holds the repo clones, or pass `--workspace`:

1. Plan (dry run, triggers nothing): `release-cascade.py --line v4`. It fetches each
   clone, compares the version at each branch head with crates.io, and prints the
   `gh workflow run` commands and the root-repo requirements that are behind.
2. Check what the plan cannot: that each repo's own requirements already name the new
   versions of the crates above it (for example hopr-api on `hopr-types` 5.x).
3. Release: `release-cascade.py --line v4 --execute`. It asks `yes` before each step,
   triggers the repo's release workflow, and continues once the run's `Publish library`
   step succeeds and the crates show on crates.io. A later failure in the same run (for
   example `Close release`) does not block the next step. A failed publish step stops
   the cascade.
4. Repin by hand: open PRs in `edge-client` and `hoprd` that raise the requirements the
   plan listed and refresh `Cargo.lock`. Keep requirements compatible with the pinned
   hoprnet rev, or two copies of `hopr-api`/`hopr-types` end up on either side of the
   strategy boundary.
5. Regenerate the configs when the dependency graph changes. They are built from the
   `Cargo.lock` of the top repos, `edge-client` and `hoprd`, so they cover everything
   either one consumes: `release-cascade.py --regenerate` builds v5 from their default
   branches and v4 from their highest common `release/X.Y` branch (override with
   `--release-branch`). `--regenerate --default-only` builds v5 alone.

Facts the configs encode, as of 2026-10-29:

- **Fixed inputs.** `hopr-bindings` is published from `hoprnet/contracts`. On v4,
  `blokli-client` 0.31.0 is a fixed published input: `blokli-client` has no v4 branch
  (the `blokli` repo's `release/0.11`..`0.14` branches are the server). The plan only
  checks fixed inputs exist and are not yanked. v4 locks pinned `hopr-bindings` 4.13.0,
  which is yanked.
- **Git-pinned crates are out of scope.** `hopr-lib`, `hopr-transport` and the other
  hoprnet crates are git dependencies in edge-client and hoprd. Moving them is a repin
  (steps 1 to 4 above), not a crates.io release.
- **Publish goes through the shared workflow.** The step named `Publish library` in
  `hoprnet/hopr-workflows` `build-library.yaml` runs `cargo release publish --execute`.
  hopr-impls is a virtual workspace: its release publishes only the crates whose version
  is not yet on crates.io.

Sources: `.github/workflows/release.yaml` and `release-library.yaml` in `hopr-types`,
`hopr-api`, `hopr-utilities`, `hopr-impls`, `hopr-strategy`, `blokli-client`;
`.github/workflows/build-library.yaml` in `hoprnet/hopr-workflows`; crates.io API
(`https://crates.io/api/v1/crates/<name>`).
