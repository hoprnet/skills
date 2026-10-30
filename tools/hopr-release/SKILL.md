---
name: hopr-release
description: >
  Propagate a Rust fix through the HOPR multi-repo release cascade. Use whenever a
  change in a hoprnet crate (hopr-lib, hopr-utilities, hopr-utils-session,
  hopr-session-forwarder) has to reach downstream repos (hoprd, edge-client,
  gnosis_vpn) that pin it by git ref, when flipping a downstream Cargo.toml pin from
  a feature branch to a release branch to a published crates.io version, when the
  same fix must land on several parallel lines (master/main, release/4.0,
  release/4.1, release/v4, compat/v4), or when a downstream build breaks after
  repinning to a newer hopr-lib rev. Also covers release-cascade.py: plan and run the
  crates.io release of the crates edge-client or hoprd depend on (hopr-types, hopr-api,
  hopr-utilities, hopr-impls, hopr-strategy, ...) and the repin PRs that follow. Trigger on
  "propagate/backport the hoprnet change", "repin hoprd/edge-client/gnosis_vpn", "flip the
  pin to the release branch", "cut a hopr release", "release the v4 or v5 line", "release
  cascade", "release-cascade.py", "what needs to be released for edge-client/hoprd", "bump
  hopr-lib", "impostor commit", a "[patch.crates-io]" git dependency on a hopr crate, a release
  job whose version bump did not land, a "disabled due to previous publish" dry-run failure, or a
  repin PR that fails to compile after a hopr-types or hopr-api major bump.
  Not for the general git PR workflow (use git-workflow) or Rust code quality (use
  rust-engineer).
---

# HOPR release cascade

A HOPR fix is rarely one PR. It starts in a `hoprnet` crate and has to travel, by
git pin, into every downstream repo that consumes it, then settle from a feature
branch onto a release branch and finally onto a published crates.io version — often
on several release lines at once. General LLM reasoning about this is reliably wrong
on the sequencing and on which commit to pin, so follow the recorded procedure and
check the gotchas before touching a `Cargo.toml` ref.

This skill is the map and the order of operations. It does not replace reading the
actual `Cargo.toml` pins, the open PRs, or `git log` on the target branch — always
confirm the real commit you are pinning to against the branch it must live on.

## The cascade, in order

1. **Fix lands upstream.** The change goes into a `hoprnet` crate on a feature
   branch. Downstream repos that need it before it merges pin to that branch's HEAD
   (git ref in `Cargo.toml`, or a `[patch.crates-io]` git entry during active
   development).
2. **Upstream PR merges** to its target line (squash-merge). The feature-branch SHA
   now no longer exists in history — see gotchas before repinning.
3. **Repin downstream to the merge commit** on the target branch, not the vanished
   feature SHA.
4. **Replicate across release lines.** The same change usually has to reach
   master/main plus each active `release/*` and `compat/*` line. Each is its own
   PR; cherry-pick or re-land per line, and repin each downstream line to the
   matching upstream line.
5. **Settle to a published version.** Once the crate is released to crates.io, drop
   the git pin / `[patch.crates-io]` git entry and pin the published version. This
   is what finally turns downstream CI green — git-pinned deps keep checks yellow.

Full step-by-step with the repo and branch map: [references/procedure.md](references/procedure.md).

## Releasing with the script

`scripts/release-cascade.py` plans and runs the crates.io part of the cascade for one start
repo (`edge-client` or `hoprd`) on one release line. It reads the start repo's `Cargo.lock`,
finds the org crates in it, and lists every operation still to do: releases, repins, and the
final lock update. The detail is in "Publishing the crates.io crates, leaf to root" in
[references/procedure.md](references/procedure.md).

**Before you run it**

1. Go to the directory that holds the repo clones, or pass `--workspace`. Missing repos are
   cloned there.
2. Log in with `gh`, or set `GH_TOKEN` or `GITHUB_TOKEN`, or pass `--token-cmd CMD` (a shell
   command that prints one token, for example a git credential helper on a VM without `gh`) or
   `--token-file PATH`. The last two mean the REST API even when `gh` is installed. A dry run
   needs none of these. Python 3.11 or newer; no packages. To open PRs the machine also needs
   `git` and `cargo` (or `nix`, which provides them).
3. Choose the start repo and branch. The default branch is the v5 line
   (`edge-client@main`). `release/4.1` is the v4 line (`edge-client@release/4.1`).
4. Start at the repo furthest downstream (`edge-client` or `hoprd`), never at a repo in the
   middle such as `hopr-impls`. A run from `hopr-impls` treats it as the start repo, which is
   never released, so it gets a repin without a release. The run from `edge-client` then needs a
   second PR for the same repo (hopr-impls#68, then #69). If you must start in the middle, the
   start repo's PR now carries its own version bumps, so a later run only releases.

**Commands** (all take `--from REPO@BRANCH`)

| Add this | What happens |
| --- | --- |
| nothing | Dry run. Prints the plan and changes nothing. |
| `-o plain` | The same report as text. `-o mjson` gives one-line JSON. |
| `--execute-no-release` | Opens a PR for each ready repin or lock update. Does not release. |
| `--execute` | Releases the ready crates at the same time, after one `yes`, then opens the PRs. A release cannot be undone. |
| `--wait-merge MINUTES` | After it opens PRs, waits for them to merge, then opens the PRs that were waiting. It never merges. |

Other flags: `--yes` skips the prompts, `--max-releases N` caps the repos released,
`--release-type` sets the version bump after a release, `--forge gh|api` forces the GitHub
access, `--token-cmd` and `--token-file` give the token, `--workspace DIR` sets the clone directory.

**A normal run**

1. Run the dry run. Read `state`, `next` and `operations`.
2. Run `--execute` to release the ready crates. Each repo is done when its versions show on
   crates.io.
3. Open the PRs for the repo behind the git pin (`hoprnet`) first. A person merges them.
4. Run again. The start repo's PR opens only after the `hoprnet` PR is merged.
5. Repeat the dry run until `state` is `complete`.

A run releases only at its start. With `--wait-merge` it waits for its PRs, re-plans, opens the
PRs that were waiting and exits, even when a release is now `ready`. After each merge, start a
new `--execute` run to publish the next layer. Expect one run per layer of the plan.

**Read the report**

- `state` says where the cascade stands: `release_ready`, `repin_required`,
  `final_repin_required`, `manual_release_required`, `blocked`, `complete`, `paused` or `error`.
- `operations` is the ordered list. Each has a `status` (`ready`, `pending` or `blocked`), the
  operations it waits for (`depends_on`), and a `layer`. Operations in one layer are independent.
- `next` is the first ready operation. `conditions` lists what must hold before the cascade
  continues. `resume` is the command to run then.
- A `repin` lists `edits` (file, line, current, required) and `version_bumps`. A
  `final_repin` also lists `lock_updates` and `git_updates`.
- With `-o plain`, the report is text. With `-o json` or `-o mjson`, stdout is the JSON and
  progress goes to stderr. Exit code 0 for every state except `error` (2).

**Rules the script follows**

- A crate is released unless its head version is on crates.io, or the head is only CI's
  post-release version bump. crates.io decides when a release is done.
- Order: library crates first, then the repo behind the git pin (`hoprnet`), then the start repo.
  A PR that waits for another is never opened early.
- `hoprnet` is repinned and its lock is refreshed. It is not released. A git pin becomes a
  version requirement only when crates.io has a version at or above the locked one.
- A crate that re-exports a dependency whose version moves to another compatibility line gets
  a proposed major bump (minor for 0.x), not a patch. Review the number in the repin PR.
- A crate whose head version is on crates.io is still released again when its published
  requirements do not admit the version the run releases for a dependency (main changed the
  requirement without a version bump). The repin operation then lists the new version in
  `version_bumps`, and `crates_to_change` marks the release `republish`; the check reads the
  dependencies API of the published version.
- A bump also raises the repo's own requirements on the bumped crate (workspace and member
  manifests). Cargo fails without that.
- The walk also covers repos that a plan repo pins by git or by dev-dependency (`hopli` under
  `blokli-client`). They get a repin when a requirement lags, and the pinning repo waits for it
  and refreshes its lock. A lock-only update in such a repo is not planned: cargo ignores a
  dependency's own `Cargo.lock`. The pinning repo also gets a repin when its locked commit of
  such a repo still lacks the fix, after that repo's own PR merged (a `rev` pin is edited to
  the branch head). A pin from one repo below a plan repo to another is not edited.
- The start repo is never released, but its stale published crates are bumped in its own repin PR.
- A new repin PR closes the script's own earlier open PRs of the same kind on the same base,
  named `chore/release-cascade-<kind>-*` and opened for the same start repo (named in the PR
  body). Other PRs, and the PRs of another start repo's run, stay open.
- The wait for a merge retries a failed GitHub read (HTTP 5xx) and ends the run only after five
  failures in a row.
- The script never merges, never tags and never runs `cargo publish`. Re-running is safe: an
  open PR is found and not opened twice.

Tests: `python3 -I scripts/test_release_cascade.py`.

## Gotchas that bite every time

Read [references/gotchas.md](references/gotchas.md) before repinning or publishing. The
load-bearing ones:

- **After a squash-merge, pin to the squash-merge commit on the target branch, never
  the pre-merge feature SHA.** The feature SHA is gone from history, so a downstream
  pin to it references an "impostor commit" not reachable from the branch — this is a
  real boundary, not a style call.
- **A downstream build that breaks right after repinning to a newer hopr-lib rev is
  usually correct.** Downstream deliberately uses exhaustive struct/enum literals (no
  `..`) as tripwires, so a new upstream field becomes a compile error. Wire the new
  field through; do not paper over it with `..Default::default()`. (See rust-engineer.)
- **Publishing goes through the Nix `build-library` workflow in the dev shell, not
  plain `cargo publish`.** For the library crates that is the `Publish library` step of
  each repo's release workflow, which `scripts/release-cascade.py` triggers (see
  "Publishing the crates.io crates, leaf to root" in the procedure); the job after it
  creates the Release/tag and bumps the version.

- **A release job can finish without its version bump.** The `Close release` job pushes
  `ci: Bump to version X` after the publish. If that push fails (hopr-utilities 0.18.0:
  `No .pre-commit-config.yaml file was found`), `main` keeps the published version and every PR
  fails `Build dry-run library` with `disabled due to previous publish`. Set
  `PRE_COMMIT_ALLOW_NO_CONFIG: "1"` on the `release-version` step, as `blokli-client` does, and
  push the bump by hand with that exact subject.
- **A published release can carry a third-party version the consumers cannot take.**
  hopr-utilities 0.18.0 shipped `multiaddr` 0.19 and `libp2p-identity` 0.3 while hopr-impls
  uses libp2p 0.56 (0.18 and 0.2). The `Multiaddr` types then differ and the repin PR fails
  with E0308. The script does not see third-party crates. Run `cargo tree -d` on the consumer
  before you release, and read the first repin PR build as the check.
- **A major bump of `hopr-types` or `hopr-api` breaks code, not only `Cargo.toml`.** Expect new
  enum variants in exhaustive matches, changed function signatures and moved types. Read
  [references/gotchas.md](references/gotchas.md) for the cases seen.

## Governance

Repinning and release work is the canonical "babysit until green, merge nothing until
told" loop: keep failing checks and review threads resolved across every PR in the
cascade, but gate every merge, publish, and tag on explicit approval. Branching,
committing, and PRs follow git-workflow; the never-merge/never-tag-without-
in-the-moment-approval rule holds here especially, because a premature merge on one
line forces the whole cascade to be redone.

The script opens every PR as the person who runs it, and GitHub does not let an author approve
their own PR. Each repin PR therefore needs a second reviewer. Set auto-merge only when told
to, with `gh pr merge N --auto --squash`, and not on a draft (`gh pr ready N` first). A PR
that is `BLOCKED` with a green CI waits for that approval. Before you push to a repin PR, run
the repo's own CI recipe with the exact feature set it uses (`just` recipes in the repo).
`--all-features` is not that set: mutually exclusive features make it fail on its own.

## Related skills

- `hopr-concepts` — protocol ground truth (RFCs, incentives, SURBs). Reach for it
  when the question is what the change does to the network, not how to ship it.
- `rust-engineer` for the exhaustive-literal tripwire and test conventions, `nix`
  for the `tokio_unstable`/`CARGO_BUILD_RUSTFLAGS` trap, and `git-workflow` for the
  branch, PR, and merge-gate mechanics this cascade runs on.
