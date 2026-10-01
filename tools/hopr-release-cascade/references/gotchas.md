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
