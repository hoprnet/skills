---
name: hopr-release-cascade
description: >
  Propagate a Rust fix through the HOPR multi-repo release cascade. Use whenever a
  change in a hoprnet crate (hopr-lib, hopr-utilities, hopr-utils-session,
  hopr-session-forwarder) has to reach downstream repos (hoprd, edge-client,
  gnosis_vpn) that pin it by git ref, when flipping a downstream Cargo.toml pin from
  a feature branch to a release branch to a published crates.io version, when the
  same fix must land on several parallel lines (master/main, release/4.0,
  release/4.1, release/v4, compat/v4), or when a downstream build breaks after
  repinning to a newer hopr-lib rev. Trigger on "propagate/backport the hoprnet
  change", "repin hoprd/edge-client/gnosis_vpn", "flip the pin to the release
  branch", "cut a hopr release", "bump hopr-lib", "impostor commit", or a
  "[patch.crates-io]" git dependency on a hopr crate. Not for the general git PR
  workflow (use git-workflow) or Rust code quality (use rust-engineer).
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
  plain `cargo publish`.** The GitHub release workflow only creates the Release/tag.

## Governance

Repinning and release work is the canonical "babysit until green, merge nothing until
told" loop: keep failing checks and review threads resolved across every PR in the
cascade, but gate every merge, publish, and tag on explicit approval. Branching,
committing, and draft PRs follow git-workflow; the never-merge/never-tag-without-
in-the-moment-approval rule holds here especially, because a premature merge on one
line forces the whole cascade to be redone.

## Related skills

- `hopr-concepts` — protocol ground truth (RFCs, incentives, SURBs). Reach for it
  when the question is what the change does to the network, not how to ship it.
- `rust-engineer` for the exhaustive-literal tripwire and test conventions, `nix`
  for the `tokio_unstable`/`CARGO_BUILD_RUSTFLAGS` trap, and `git-workflow` for the
  branch, PR, and merge-gate mechanics this cascade runs on.
