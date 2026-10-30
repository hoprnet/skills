# Cascade gotchas

## Squash-merge erases the feature SHA

GitHub squash-merge creates a new commit on the target branch and discards the
feature branch's individual commits. A downstream `Cargo.toml` still pinned to a
pre-merge feature SHA then points at a commit not reachable from any branch — an
"impostor commit". It may still resolve from GitHub's object store for a while, so
the breakage is silent until the ref is GC'd or someone tries to check out the line.

Always repin to the squash-merge commit on the target branch. Confirm with
`git branch --contains <sha>`.

## Exhaustive literals are intentional tripwires

Downstream code constructs upstream structs/enums with exhaustive literals (no `..`
rest pattern, no `..Default::default()`) on purpose: when a repin pulls a hopr-lib
rev that added a field (e.g. new `SupervisorConfig` fields,
`commitment_recommit_interval`), the downstream build fails to compile. That failure
is the signal to wire the new field through deliberately, not a regression to be
silenced. Suppressing it with a rest pattern hides real upstream changes. This mirrors
the rust-engineer guidance; keep the two consistent.

## Publishing is a Nix workflow, not `cargo publish`

Cutting a hopr crate to crates.io runs through the repo's Nix `build-library`
workflow inside the dev shell. The GitHub release workflow only creates the
Release and tag — it does not publish the crate. Running `cargo publish` by hand
bypasses the reproducible build path.

## `[patch.crates-io]` git entries must be dropped at the end

During development a downstream repo may carry `[patch.crates-io] hopr-utilities = { git = ... }`
plus a git rev pin on `hopr-lib`. These override the published versions repo-wide and
keep CI yellow (git deps are never "released"). The final cascade step removes every
such git patch/pin and replaces it with the published crates.io version. Leaving one
behind is the usual reason a downstream release PR won't go green.

## `tokio_unstable` can vanish in Nix-built artifacts

`.cargo/config.toml` sets `rustflags` including `--cfg tokio_unstable`, but a Nix
package build sets `CARGO_BUILD_RUSTFLAGS`, which replaces (does not merge) the
config.toml rustflags — silently dropping `tokio_unstable` in the nix-built binary.
If runtime behavior differs between a `cargo build` and the nix artifact, check this
first. Covered in depth by the nix skill.

## A published head can still be stale

The head version of a crate is on crates.io, but main has since changed a requirement
without a version bump. The published crate then requires the old dependency versions.
Symptom in the consumer: `failed to select a version for the requirement blokli-client =
"^0.35.0"`, `required by package hopr-chain-connector v0.29.0`. hopr-chain-connector 0.29.0
was published with `blokli-client` ^0.35.0, while hopr-impls main required 0.36.1
(hopr-impls#68). The script now compares the published requirements (crates.io dependencies
API of that version) with the versions the run releases, bumps the crate and releases it again
(hopr-chain-connector 0.30.0, hopr-impls#69). A crate that is not on crates.io has no check.

## A bump must raise the repo's own requirements too

Bumping `hopr-network-graph` 0.8.0 to 0.9.0 while `hopr-ct-full-network` (same workspace) still
requires `^0.8.0` fails in `cargo metadata`. The repin edits every requirement the repo holds
on a bumped crate, in the root `[workspace.dependencies]` and in member manifests.

## Repos below a plan repo are part of the walk

`blokli-client` pins `hopli` by git (a dev-dependency used by its integration tests). `hopli`
still resolved `hopr-types` 4.1.0, so a `ServiceType` from 5.0.0 did not match the one in
`hopli_lib` (blokli-client#71, hopli#122). A walk down from the start `Cargo.lock` never sees
this: dev-dependencies and git pins of a step repo are not in that lock. The script now follows
the git pins of every plan repo, repins the repos it finds, and makes the pinning repo wait for
them. Cargo ignores a dependency's own `Cargo.lock`, so only requirement gaps count.

## Start at the repo furthest downstream

A run from `hopr-impls` treats it as the start repo: a final repin without a release or bumps.
The later run from `edge-client` then needs a second PR for the same repo (hopr-impls#68 and
#69), and a third one if the plan changed in between (#67). Start from `edge-client` or
`hoprd`. A start repo's final repin now also carries the bumps of its stale published crates.

## One run releases once

`--wait-merge` waits for the PRs, re-plans, opens the PRs that were waiting and exits. It never
releases in that run, even when a release is `ready` afterwards. Start a new `--execute` run.
`--execute` asks nothing more when you pass `--yes`, and a release cannot be undone, so read the
plan before the run.

## The post-release version bump can fail

`Close release` pushes `ci: Bump to version X` after publishing. For hopr-utilities 0.18.0 it
failed: the Nix dev shell installs the pre-commit hooks, the checkout has no
`.pre-commit-config.yaml`, and the commit stops with `No .pre-commit-config.yaml file was found`
(run 41082886651). `main` then keeps the published version, and `Build dry-run library` fails on
every PR with `disabled due to previous publish (0.18.0), skipping`. The `glibc` line from
`nix-store` in the same log is noise: the successful `blokli-client` release prints it too.
Fix: `env: PRE_COMMIT_ALLOW_NO_CONFIG: "1"` on the `release-version` step (as in blokli-client's
`release-library.yaml`), and push the bump by hand as `ci: Bump to version X`, so the script
still treats it as a bump head.

## Third-party versions are invisible to the script

hopr-utilities 0.18.0 was released from a `main` where a dependency update had raised
`multiaddr` to 0.19 and `libp2p-identity` to 0.3. hopr-impls uses libp2p 0.56 (0.18 and 0.2), so
`Multiaddr` and `PeerId` did not match and the repin PR failed with four E0308 errors. A release
cannot be undone: the fix was 0.18.1 with the old pair restored (hopr-utilities#55) and a Renovate
ignore for both crates with the reason in the rule `description` (JSON has no comments).
Whether to yank 0.18.0 is the owner's call. Check `cargo tree -d` on the consumer before a release.

## A major bump of hopr-types or hopr-api breaks code

Cases seen with `hopr-types` 5.0.0 and `hopr-ticket-manager` 0.9.0:

- New enum variants break exhaustive matches: `ParsedHoprChainAction::Approve` in the blokli
  emulator (hopr-utilities#54), `ChainEvent::SafeAllowanceChanged` in hopr-lib (hoprnet#8599).
  Wire the variant through. Do not add a catch-all.
- `Address` is its own type unless `hopr-types` has the `use-bindings` feature, which restores
  the alloy address types and `xhopr_token` in `ContractAddresses`. Enable it where code passes
  addresses to alloy-based crates (blokli-client integration tests).
- `ticket_factory_from_chain` and `ticket_manager_from_chain` take the store from the caller
  (`RedbStore::new_temp()?` in tests).
- `OffchainPublicKey` is split into a compact key and `ExpandedOffchainPublicKey`. hoprnet#8431
  carries that migration on top of `[patch.crates-io]` git entries. When the crates are released,
  merge its code into the repin PR and drop the patch block: take the released versions for
  `Cargo.toml` and `Cargo.lock`. Do not write the migration a second time.

Check for an open migration PR before you change downstream code.

## `cargo update -p name` is ambiguous with two versions in the lock

A transitive dependency can keep the old major in `Cargo.lock`, and `cargo update -p hopr-types`
then fails with `specification is ambiguous`. The script uses `name@version`. A `--precise` update
runs only while the old version is still locked: an earlier step can have moved it.

## Run the repo's own CI command, not a wider one

Use the recipe in the repo's `justfile` (`cargo clippy --workspace --all-targets --features testing
-- -D warnings` in hoprnet). `--all-features` can fail by itself: hoprnet has mutually exclusive
allocator features, and hopr-utilities has benches with lints CI does not run.

## Repin PRs need a second reviewer

The script opens PRs as the user who runs it, and GitHub rejects the author's own approval
(`Can not approve your own pull request`). Auto-merge (`gh pr merge N --auto --squash`) cannot
start on a draft: `gh pr ready N` first. The script closes its own earlier PRs when a changed
plan gives a new branch (hopr-strategy#126, replaced by #127). A `gh api` read can answer with
HTTP 5xx: the merge wait retries it.

