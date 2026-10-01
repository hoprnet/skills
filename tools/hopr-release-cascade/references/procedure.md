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
