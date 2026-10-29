"""Tests for release-cascade.py: version admission and the operation list.

  python3 -m unittest tools/hopr-release/scripts/test_release_cascade.py
"""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("rc", Path(__file__).with_name("release-cascade.py"))
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)
rc.line_of = lambda *a: 7  # no git in these tests


def gap(crate, req, want, file="Cargo.toml"):
    return dict(file=file, key=crate, crate=crate, req=req, want=want)


def step(repo, crates, todo=None, gaps=(), requires=(), workflow="release.yaml", branch="main"):
    head = dict(crates)
    todo = dict(todo or {})
    republish = bool(gaps) and not todo
    return dict(repo=repo, branch=branch, workflow=workflow, crates=sorted(head), head=head, todo=todo,
                gaps=list(gaps), requires=list(requires), hard_requires=list(requires), files={c: "Cargo.toml" for c in head}, republish=republish,
                release_versions=({c: rc.bump_patch(v) for c, v in head.items()} if republish else todo))


def plan(steps, repin=(), fixed=(), locked=None, target=None, pins=()):
    return dict(start=("edge-client", "main"), steps=steps, fixed=list(fixed), pinned=[], repin=list(repin),
                locked=locked or {}, target=target or {}, pins=list(pins))


class Admits(unittest.TestCase):
    def test_caret_major(self):
        self.assertTrue(rc.admits("4.1.0", "4.9.2"))
        self.assertFalse(rc.admits("4.1.0", "5.0.0"))
        self.assertFalse(rc.admits("4.1.0", "4.0.9"))

    def test_caret_zero_minor_is_the_breaking_digit(self):
        self.assertTrue(rc.admits("0.35.0", "0.35.4"))
        self.assertFalse(rc.admits("0.35.0", "0.36.1"))

    def test_caret_zero_zero(self):
        self.assertTrue(rc.admits("0.0.3", "0.0.3"))
        self.assertFalse(rc.admits("0.0.3", "0.0.4"))

    def test_exact_and_compound(self):
        self.assertTrue(rc.admits("=4.4.0", "4.4.0"))
        self.assertFalse(rc.admits("=4.4.0", "4.4.1"))
        self.assertTrue(rc.admits(">=1.2, <2", "1.9.0"))
        self.assertFalse(rc.admits(">=1.2, <2", "2.0.0"))

    def test_tilde(self):
        self.assertTrue(rc.admits("~1.2.3", "1.2.9"))
        self.assertFalse(rc.admits("~1.2.3", "1.3.0"))

    def test_prerelease_needs_a_prerelease_requirement(self):
        self.assertFalse(rc.admits("1.0.0", "1.1.0-rc.1"))


class HardNeeds(unittest.TestCase):
    published = staticmethod(lambda crate: {"hopr-types": ["2.3.0"], "hopr-utilities": ["0.9.1", "0.9.3"]}[crate])

    def entry(self, crate, req):
        return dict(file="Cargo.toml", key=crate, crate=crate, req=req)

    def test_a_requirement_met_by_a_published_version_does_not_wait(self):
        got = rc.hard_needs([self.entry("hopr-types", "2.3.0")], own=set(), releasing={"hopr-types"}, published=self.published)
        self.assertEqual(got, [])

    def test_a_requirement_only_the_new_release_meets_waits(self):
        got = rc.hard_needs([self.entry("hopr-types", "2.3.1")], own=set(), releasing={"hopr-types"}, published=self.published)
        self.assertEqual(got, ["hopr-types"])

    def test_own_crates_and_crates_not_being_released_are_ignored(self):
        entries = [self.entry("hopr-types", "9.0.0"), self.entry("hopr-utilities", "9.0.0")]
        self.assertEqual(rc.hard_needs(entries, own={"hopr-types"}, releasing={"hopr-types"}, published=self.published), [])

    def test_a_crate_listed_twice_is_reported_once(self):
        entries = [self.entry("hopr-types", "2.3.1"), self.entry("hopr-types", "2.4.0")]
        self.assertEqual(rc.hard_needs(entries, own=set(), releasing={"hopr-types"}, published=self.published), ["hopr-types"])


class Batch(unittest.TestCase):
    def op(self, id, crate, status="ready", kind="release"):
        return dict(id=id, kind=kind, status=status, repo=crate, branch="b", crates=[dict(name=crate, version="1.0.0")])

    def test_only_planned_crates_are_released(self):
        # a crate whose head was bumped by CI after an earlier release is not in the first plan
        report = dict(operations=[self.op("op-1", "hopr-types"), self.op("op-2", "hopr-bindings")])
        got = rc.next_batch(report, planned={"hopr-types"}, remaining=None)
        self.assertEqual([o["id"] for o in got], ["op-1"])

    def test_pending_and_non_release_operations_are_skipped(self):
        report = dict(operations=[self.op("op-1", "a", status="pending"), self.op("op-2", "b", kind="repin"),
                                  self.op("op-3", "c", kind="release_manual"), self.op("op-4", "d")])
        got = rc.next_batch(report, planned={"a", "b", "c", "d"}, remaining=None)
        self.assertEqual([o["id"] for o in got], ["op-4"])

    def test_remaining_caps_the_batch(self):
        report = dict(operations=[self.op("op-1", "a"), self.op("op-2", "b"), self.op("op-3", "c")])
        self.assertEqual([o["id"] for o in rc.next_batch(report, planned={"a", "b", "c"}, remaining=2)], ["op-1", "op-2"])
        self.assertEqual(rc.next_batch(report, planned={"a", "b", "c"}, remaining=0), [])

    def test_planned_crates_are_every_release_crate_of_the_first_plan(self):
        report = dict(operations=[self.op("op-1", "a"), self.op("op-2", "b", status="pending"), self.op("op-3", "c", kind="repin")])
        self.assertEqual(rc.planned_crates(report), {"a", "b"})


class Operations(unittest.TestCase):
    def setUp(self):
        self.types = step("hopr-types", {"hopr-types": "5.0.0"}, todo={"hopr-types": "5.0.0"})
        self.api = step("hopr-api", {"hopr-api": "4.4.1"}, todo={"hopr-api": "4.4.1"},
                        gaps=[gap("hopr-types", "4.1.0", "5.0.0")], requires=["hopr-types"])

    def by_id(self, ops):
        return {o["id"]: o for o in ops}

    def test_repin_waits_for_the_release_it_needs_and_release_waits_for_the_repin(self):
        ops = rc.operations(plan([self.types, self.api]))
        kinds = [(o["kind"], o["repo"]) for o in ops]
        self.assertEqual(kinds, [("release", "hopr-types"), ("repin", "hopr-api"), ("release", "hopr-api")])
        by = self.by_id(ops)
        self.assertEqual(ops[0]["depends_on"], [])
        self.assertEqual(ops[1]["depends_on"], [ops[0]["id"]])
        self.assertEqual(sorted(ops[2]["depends_on"]), sorted([ops[0]["id"], ops[1]["id"]]))
        self.assertEqual(ops[1]["edits"][0]["current"], "4.1.0")
        self.assertEqual(ops[1]["edits"][0]["required"], "5.0.0")
        self.assertEqual(ops[1]["edits"][0]["line"], 7)
        self.assertEqual(ops[1]["version_bumps"], [])  # head is unpublished: no bump
        self.assertTrue(all(d in by for o in ops for d in o["depends_on"]))

    def test_repo_with_a_published_head_is_bumped_and_republished(self):
        impls = step("hopr-impls", {"hopr-chain-connector": "0.28.0"}, gaps=[gap("hopr-types", "4.1.0", "5.0.0")],
                     requires=["hopr-types"])
        ops = rc.operations(plan([self.types, impls]))
        repin, release = ops[1], ops[2]
        self.assertEqual(repin["version_bumps"], [dict(crate="hopr-chain-connector", file="Cargo.toml",
                                                       current="0.28.0", suggested="0.28.1")])
        self.assertTrue(release["republish"])
        self.assertEqual(release["crates"], [dict(name="hopr-chain-connector", version="0.28.1")])

    def test_final_repin_waits_for_the_releases_it_names(self):
        ops = rc.operations(plan([self.types], repin=[gap("hopr-types", "4.1.0", "5.0.0")]))
        final = ops[-1]
        self.assertEqual(final["kind"], "final_repin")
        self.assertEqual(final["repo"], "edge-client")
        self.assertEqual(final["depends_on"], [ops[0]["id"]])

    def test_final_repin_is_ready_when_its_crates_are_already_published(self):
        published = step("hopr-strategy", {"hopr-strategy": "5.5.1"})
        ops = rc.operations(plan([published], repin=[gap("hopr-strategy", "5.4.0", "5.5.1")]))
        self.assertEqual([o["kind"] for o in ops], ["final_repin"])
        self.assertEqual(ops[0]["depends_on"], [])

    def test_start_repo_lock_behind_the_released_versions_is_a_final_operation(self):
        # requirements already admit the new version, so no edit, but the lock still pins the old one
        ops = rc.operations(plan([self.types], locked={"hopr-types": "4.1.0"}, target={"hopr-types": "5.0.0"}))
        final = ops[-1]
        self.assertEqual(final["kind"], "final_repin")
        self.assertEqual(final["edits"], [])
        self.assertEqual(final["lock_updates"], [dict(crate="hopr-types", locked="4.1.0", target="5.0.0")])
        self.assertEqual(final["depends_on"], [ops[0]["id"]])

    def test_no_final_operation_when_the_lock_is_current(self):
        ops = rc.operations(plan([step("hopr-api", {"hopr-api": "2.0.0"})], locked={"hopr-api": "2.0.0"},
                                 target={"hopr-api": "2.0.0"}))
        self.assertEqual(ops, [])

    def test_releases_with_no_hard_dependency_are_ready_together(self):
        a = step("hopr-types", {"hopr-types": "2.3.1"}, todo={"hopr-types": "2.3.1"})
        b = step("hopr-utilities", {"hopr-utilities": "0.9.4"}, todo={"hopr-utilities": "0.9.4"}, requires=[])
        b["requires"] = ["hopr-types"]  # it requires hopr-types, but a published version already meets that
        ops = rc.operations(plan([a, b]))
        self.assertEqual([o["status"] for o in ops], ["ready", "ready"])
        self.assertEqual(rc.status(plan([a, b]))["ready_operations"], ["op-1", "op-2"])

    def test_a_hard_requirement_orders_the_release(self):
        a = step("hopr-types", {"hopr-types": "2.3.1"}, todo={"hopr-types": "2.3.1"})
        b = step("hopr-utilities", {"hopr-utilities": "0.9.4"}, todo={"hopr-utilities": "0.9.4"}, requires=["hopr-types"])
        ops = rc.operations(plan([a, b]))
        self.assertEqual(ops[1]["depends_on"], ["op-1"])

    def test_nothing_to_do(self):
        self.assertEqual(rc.operations(plan([step("hopr-api", {"hopr-api": "4.4.0"})])), [])

    def test_repo_without_a_release_workflow_is_a_manual_release(self):
        manual = step("rust-stream-ext-concurrent", {"rust-stream-ext-concurrent": "2.0.1"},
                      todo={"rust-stream-ext-concurrent": "2.0.1"}, workflow=None)
        ops = rc.operations(plan([manual]))
        self.assertEqual(ops[0]["kind"], "release_manual")


class Report(unittest.TestCase):
    def setUp(self):
        self.types = step("hopr-types", {"hopr-types": "5.0.0"}, todo={"hopr-types": "5.0.0"})
        self.api = step("hopr-api", {"hopr-api": "4.4.1"}, todo={"hopr-api": "4.4.1"},
                        gaps=[gap("hopr-types", "4.1.0", "5.0.0")], requires=["hopr-types"])

    def test_first_ready_operation_is_next(self):
        r = rc.status(plan([self.types, self.api]))
        self.assertEqual(r["schema"], "hopr-release-cascade/v2")
        self.assertEqual(r["state"], "release_ready")
        self.assertEqual(r["next"]["id"], "op-1")
        self.assertEqual(r["ready_operations"], ["op-1"])
        self.assertEqual([c["crate"] for c in r["crates_to_change"]], ["hopr-types", "hopr-api"])
        self.assertEqual([x["repo"] for x in r["repos_to_update"]], ["hopr-types", "hopr-api"])
        self.assertEqual(r["repos_to_update"][1]["operations"], ["op-2", "op-3"])
        self.assertTrue(r["repos_to_update"][1]["needs_pr"])
        self.assertEqual([o["status"] for o in r["operations"]], ["ready", "pending", "pending"])

    def test_repin_is_next_once_its_release_is_done(self):
        r = rc.status(plan([step("hopr-types", {"hopr-types": "5.0.0"}), self.api]))
        self.assertEqual(r["state"], "repin_required")
        self.assertEqual(r["next"]["kind"], "repin")
        self.assertEqual(r["next"]["repo"], "hopr-api")

    def test_complete(self):
        r = rc.status(plan([step("hopr-api", {"hopr-api": "4.4.0"})]))
        self.assertEqual((r["state"], r["operations"], r["next"], r["resume"]), ("complete", [], {"kind": "none"}, None))


class LatestCompatible(unittest.TestCase):
    versions = ["2.3.0", "2.3.1", "2.3.2", "3.0.1", "3.1.0", "0.9.3", "0.9.4", "0.10.0"]

    def test_same_major(self):
        self.assertEqual(rc.pick_latest_compatible(self.versions, "2.3.3"), "2.3.2")
        self.assertEqual(rc.pick_latest_compatible(self.versions, "3.0.2"), "3.1.0")

    def test_zero_major_follows_the_minor(self):
        self.assertEqual(rc.pick_latest_compatible(self.versions, "0.9.5"), "0.9.4")
        self.assertEqual(rc.pick_latest_compatible(self.versions, "0.10.1"), "0.10.0")

    def test_nothing_published_in_the_line_returns_none(self):
        self.assertIsNone(rc.pick_latest_compatible(self.versions, "7.0.0"))

    def test_prereleases_are_skipped(self):
        self.assertEqual(rc.pick_latest_compatible(["1.0.0", "1.1.0-rc.1"], "1.1.0"), "1.0.0")


class EditText(unittest.TestCase):
    manifest = ('[package]\nname = "hopr-api"\nversion = "4.4.1"\n\n[dependencies]\n'
                'hopr-types = { version = "4.1.0", features = ["a"] }\nblokli-client = "0.35.0"\nhopr-utils = "=0.17.3"\n'
                '\n[dependencies.serde]\nversion = "4.4.1"\n')

    def edit(self, key, current, required, line):
        return dict(file="Cargo.toml", line=line, key=key, crate=key, current=current, required=required)

    def test_new_req_keeps_an_operator_and_replaces_complex_ranges(self):
        self.assertEqual(rc.new_req("4.1.0", "5.0.0"), "5.0.0")
        self.assertEqual(rc.new_req("=4.4.0", "4.4.1"), "=4.4.1")
        self.assertEqual(rc.new_req("~1.2.3", "1.3.0"), "~1.3.0")
        self.assertEqual(rc.new_req(">=1.2, <2", "2.1.0"), "2.1.0")

    def test_edit_replaces_only_the_requirement_on_its_line(self):
        out = rc.edit_text(self.manifest, self.edit("hopr-types", "4.1.0", "5.0.0", 6))
        self.assertIn('hopr-types = { version = "5.0.0", features = ["a"] }', out)
        self.assertIn('version = "4.4.1"', out)  # the package version is untouched
        self.assertEqual(out.count("\n"), self.manifest.count("\n"))

    def test_edit_keeps_an_exact_pin_exact(self):
        out = rc.edit_text(self.manifest, self.edit("hopr-utils", "=0.17.3", "0.17.5", 8))
        self.assertIn('hopr-utils = "=0.17.5"', out)

    def test_edit_that_does_not_match_its_line_is_an_error(self):
        with self.assertRaises(rc.Stop) as e:
            rc.edit_text(self.manifest, self.edit("hopr-types", "3.0.0", "5.0.0", 6))
        self.assertIn("Cargo.toml:6", str(e.exception))

    def test_version_bump_changes_the_package_version_only(self):
        out = rc.bump_text(self.manifest, dict(crate="hopr-api", file="Cargo.toml", current="4.4.1", suggested="4.4.2"))
        self.assertIn('[package]\nname = "hopr-api"\nversion = "4.4.2"', out)
        self.assertIn('[dependencies.serde]\nversion = "4.4.1"', out)  # a dependency table with the same text stays

    def test_version_bump_in_a_workspace_package_table(self):
        text = '[workspace]\nmembers = ["a"]\n\n[workspace.package]\nversion = "0.28.0"\n'
        out = rc.bump_text(text, dict(crate="x", file="Cargo.toml", current="0.28.0", suggested="0.28.1"))
        self.assertIn('version = "0.28.1"', out)


class ApplyOps(unittest.TestCase):
    def op(self, **kw):
        base = dict(id="op-1", kind="repin", repo="hopr-api", branch="main", depends_on=[], status="ready",
                    edits=[dict(file="Cargo.toml", line=6, key="hopr-types", crate="hopr-types", current="4.1.0", required="5.0.0")],
                    version_bumps=[dict(crate="hopr-api", file="Cargo.toml", current="4.4.1", suggested="4.4.2")],
                    lock_updates=[])
        base.update(kw)
        return base

    def test_lock_commands(self):
        cmds = rc.lock_commands(self.op(lock_updates=[dict(crate="hopr-utilities", locked="0.9.3", target="0.9.4")]))
        self.assertEqual(cmds[0], "cargo metadata --format-version 1 >/dev/null")
        self.assertIn("cargo update -p hopr-types", cmds)
        self.assertIn("cargo update -p hopr-utilities@0.9.3 --precise 0.9.4", cmds)

    def test_branch_name_is_stable_and_changes_with_the_edits(self):
        a, b = self.op(), self.op()
        c = self.op(edits=[dict(a_edit_changed="x", file="Cargo.toml", line=7, key="k", crate="k", current="1", required="2")])
        self.assertEqual(rc.branch_name(a), rc.branch_name(b))
        self.assertNotEqual(rc.branch_name(a), rc.branch_name(c))
        self.assertTrue(rc.branch_name(a).startswith("chore/release-cascade-repin-"))

    def test_pr_text_names_the_crates_and_the_versions(self):
        title, body = rc.pr_text(self.op(), ("edge-client", "release/4.1"))
        self.assertIn("hopr-types", title)
        self.assertIn("hopr-types 4.1.0 -> 5.0.0", body)
        self.assertIn("edge-client@release/4.1", body)


class GitFlow(unittest.TestCase):
    """The git side of --execute-no-release against a local bare origin. cargo and nix are stubbed."""

    def setUp(self):
        import os, subprocess, tempfile
        self.tmp = Path(tempfile.mkdtemp())
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.env = env
        sh = lambda *a, cwd=None: subprocess.run(a, cwd=cwd, env=env, check=True, capture_output=True, text=True)
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.tmp / "origin.git"))
        sh("git", "clone", "-q", str(self.tmp / "origin.git"), str(self.tmp / "hopr-api"))
        repo = self.tmp / "hopr-api"
        (repo / "Cargo.toml").write_text(EditText.manifest)
        (repo / "Cargo.lock").write_text("# lock\n")
        sh("git", "add", "Cargo.toml", "Cargo.lock", cwd=repo)
        sh("git", "commit", "-q", "-m", "init", cwd=repo)
        sh("git", "push", "-q", "-u", "origin", "main", cwd=repo)
        self.sh, self.repo = sh, repo
        self.saved = (rc.WORKSPACE, rc.FORGE, dict(os.environ))
        os.environ.update(env)
        rc.WORKSPACE = self.tmp

        class Forge:
            prs = []
            def git_env(self): return {}
            def find_pr(self, repo, head): return None
            def create_pr(self, repo, head, base, title, body, draft=True):
                self.prs.append((repo, head, base, title, draft))
                return "https://github.com/hoprnet/hopr-api/pull/1"
        rc.FORGE = Forge()
        self.forge = rc.FORGE
        self.cmds = []

    def tearDown(self):
        import os, shutil
        rc.WORKSPACE, rc.FORGE = self.saved[0], self.saved[1]
        os.environ.clear(); os.environ.update(self.saved[2])
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stub_shell(self, wt, cmds):
        self.cmds.append(cmds)  # a real run would update Cargo.lock here
        (Path(wt) / "Cargo.lock").write_text("# lock updated\n")

    def test_edits_are_committed_on_a_branch_pushed_and_a_draft_pr_is_opened(self):
        op = ApplyOps().op()
        result = rc.apply_op(op, ("edge-client", "release/4.1"), shell=self.stub_shell)
        branch = rc.branch_name(op)
        self.assertEqual(result["url"], "https://github.com/hoprnet/hopr-api/pull/1")
        self.assertEqual(result["state"], "opened")
        # the branch exists on the remote with the edited manifest and the updated lock
        shown = self.sh("git", "--git-dir", str(self.tmp / "origin.git"), "show", f"{branch}:Cargo.toml").stdout
        self.assertIn('hopr-types = { version = "5.0.0"', shown)
        self.assertIn('version = "4.4.2"', shown)
        lock = self.sh("git", "--git-dir", str(self.tmp / "origin.git"), "show", f"{branch}:Cargo.lock").stdout
        self.assertEqual(lock, "# lock updated\n")
        msg = self.sh("git", "--git-dir", str(self.tmp / "origin.git"), "log", "-1", "--format=%B", branch).stdout
        self.assertIn("Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>", msg)
        self.assertEqual(self.forge.prs[0][:3] + self.forge.prs[0][4:], ("hopr-api", branch, "main", True))
        self.assertTrue(self.cmds and self.cmds[0][0].startswith("cargo metadata"))
        # the main branch is untouched
        main = self.sh("git", "--git-dir", str(self.tmp / "origin.git"), "show", "main:Cargo.toml").stdout
        self.assertIn('version = "4.1.0"', main)

    def test_an_existing_open_pr_is_reported_and_nothing_is_pushed_again(self):
        self.forge.find_pr = lambda repo, head: "https://github.com/hoprnet/hopr-api/pull/9"
        result = rc.apply_op(ApplyOps().op(), ("edge-client", "release/4.1"), shell=self.stub_shell)
        self.assertEqual((result["state"], result["url"]), ("exists", "https://github.com/hoprnet/hopr-api/pull/9"))
        self.assertEqual(self.forge.prs, [])
        self.assertEqual(self.cmds, [])


class GitPins(unittest.TestCase):
    ms = {"Cargo.toml": {
        "dependencies": {
            "hopr-lib": {"git": "https://github.com/hoprnet/hoprnet", "branch": "master", "features": ["serde"]},
            "hopr-utils-session": {"git": "https://github.com/hoprnet/hoprnet", "rev": "af80993c", "default-features": False},
            "other": {"git": "https://github.com/somebody/else", "branch": "main"},
            "plain": "1.0"},
        "workspace": {"dependencies": {"hopr-transport-mixer": {"git": "https://github.com/hoprnet/hoprnet", "tag": "v1"}}},
        "patch": {"crates-io": {"hopr-api": {"git": "https://github.com/hoprnet/hopr-api", "branch": "x"}}}}}

    def test_org_git_pins_are_found_with_their_ref(self):
        pins = rc.git_pins(self.ms)
        got = {p["crate"]: p["ref"] for p in pins}
        self.assertEqual(got, {"hopr-lib": "branch master", "hopr-utils-session": "rev af80993c",
                               "hopr-transport-mixer": "tag v1"})

    def test_pins_of_other_owners_and_plain_requirements_are_ignored(self):
        self.assertNotIn("other", {p["crate"] for p in rc.git_pins(self.ms)})
        self.assertNotIn("plain", {p["crate"] for p in rc.git_pins(self.ms)})

    def test_patch_entries_are_not_pins_but_are_reported(self):
        self.assertEqual(rc.patch_pins(self.ms), ["hopr-api"])

    def pin(self, line, **kw):
        return dict(file="Cargo.toml", line=line, key="hopr-lib", crate="hopr-lib", current="git x", required="5.0.0", swap="git_to_version", **kw)

    def test_swap_a_branch_pin_that_spans_lines(self):
        text = 'a = 1\nhopr-lib = { git = "https://github.com/hoprnet/hoprnet", branch = "master", features = [\n  "serde",\n] }\n'
        out = rc.swap_git_text(text, self.pin(2))
        self.assertIn('hopr-lib = { version = "5.0.0", features = [\n  "serde",\n] }', out)
        self.assertNotIn("git", out)

    def test_swap_a_rev_pin(self):
        text = 'x = { git = "https://github.com/hoprnet/hoprnet", rev = "af80993c", default-features = false }\n'
        out = rc.swap_git_text(text, dict(self.pin(1), key="x"))
        self.assertEqual(out, 'x = { version = "5.0.0", default-features = false }\n')

    def test_swap_a_pin_with_nothing_else(self):
        text = 'x = { git = "https://github.com/hoprnet/hoprnet", branch = "main" }\n'
        self.assertEqual(rc.swap_git_text(text, dict(self.pin(1), key="x")), 'x = { version = "5.0.0" }\n')

    def test_swap_on_a_line_without_a_git_pin_is_an_error(self):
        with self.assertRaises(rc.Stop):
            rc.swap_git_text('hopr-lib = "1.0"\n', self.pin(1))

    def test_edit_text_routes_swap_edits(self):
        text = 'hopr-lib = { git = "https://github.com/hoprnet/hoprnet", branch = "master" }\n'
        self.assertIn('version = "5.0.0"', rc.edit_text(text, self.pin(1)))


class PinOperations(unittest.TestCase):
    def pin(self, target):
        return dict(file="Cargo.toml", line=97, key="hopr-lib", crate="hopr-lib", git="https://github.com/hoprnet/hoprnet",
                    ref="branch master", locked="5.0.0-rc.5", target=target)

    def test_a_published_version_makes_the_swap_part_of_the_final_operation(self):
        ops = rc.operations(plan([], pins=[self.pin("5.0.0")]))
        self.assertEqual([o["kind"] for o in ops], ["final_repin"])
        e = ops[0]["edits"][0]
        self.assertEqual((e["crate"], e["required"], e["swap"], e["line"]), ("hopr-lib", "5.0.0", "git_to_version", 97))
        self.assertIn("git https://github.com/hoprnet/hoprnet branch master", e["current"])
        self.assertEqual(ops[0]["status"], "ready")
        self.assertIn("cargo update -p hopr-lib", rc.lock_commands(ops[0]))

    def test_a_pin_with_no_published_version_is_a_blocked_operation_with_the_reason(self):
        ops = rc.operations(plan([], pins=[self.pin(None)]))
        self.assertEqual([o["kind"] for o in ops], ["pin_swap"])
        self.assertEqual(ops[0]["status"], "blocked")
        self.assertIn("hopr-lib", ops[0]["blocked_reason"])
        self.assertIn("crates.io", ops[0]["blocked_reason"])

    def test_report_state_is_blocked_when_only_blocked_operations_remain(self):
        r = rc.status(plan([], pins=[self.pin(None)]))
        self.assertEqual(r["state"], "blocked")
        self.assertEqual(r["ready_operations"], [])
        self.assertEqual(r["next"], {"kind": "none"})
        self.assertIn("hopr-lib", r["summary"])

    def test_blocked_operations_do_not_stop_ready_ones(self):
        a = step("hopr-types", {"hopr-types": "5.0.0"}, todo={"hopr-types": "5.0.0"})
        r = rc.status(plan([a], pins=[self.pin(None)]))
        self.assertEqual(r["state"], "release_ready")
        self.assertEqual(r["ready_operations"], ["op-1"])


class Resume(unittest.TestCase):
    def test_resume_keeps_the_flags_that_shape_the_run(self):
        saved = rc.RESUME_FLAGS
        try:
            rc.RESUME_FLAGS = "--workspace /w --forge api -o mjson"
            cmd = rc.resume_command(("edge-client", "release/4.1"))
        finally:
            rc.RESUME_FLAGS = saved
        self.assertIn("--from edge-client@release/4.1", cmd)
        self.assertIn("--workspace /w --forge api -o mjson", cmd)
        self.assertTrue(cmd.endswith("--execute"))


class BumpOnly(unittest.TestCase):
    commits = [("c4", "fix(ci): sync Cargo.lock with bumped crate versions (#67)"), ("c3", "ci: Bump to version 0.36.1"),
               ("c2", "feat: report the health of each subscription (#60)"), ("c1", "ci: Bump to version 0.36.0")]

    def test_the_bump_commit_for_a_version_is_found_by_its_subject(self):
        self.assertEqual(rc.find_bump(self.commits, "0.36.1"), "c3")
        self.assertEqual(rc.find_bump(self.commits, "0.36.0"), "c1")

    def test_a_version_set_by_a_real_change_has_no_bump_commit(self):
        self.assertIsNone(rc.find_bump(self.commits, "0.37.0"))

    def test_only_cargo_files_count_as_a_bump(self):
        self.assertTrue(rc.only_cargo_files(["Cargo.toml", "Cargo.lock", "ethereum/bindings/Cargo.toml"]))
        self.assertFalse(rc.only_cargo_files(["Cargo.lock", "src/lib.rs"]))
        self.assertTrue(rc.only_cargo_files([]))


class BumpOnlyGit(unittest.TestCase):
    """The git side against a temporary repository."""

    def setUp(self):
        import os, subprocess, tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.sh = lambda *a: subprocess.run(a, cwd=self.tmp / "r", env=self.env, check=True, capture_output=True, text=True)
        (self.tmp / "r").mkdir()
        self.sh("git", "init", "-q", "-b", "main")
        self.saved = rc.WORKSPACE
        rc.WORKSPACE = self.tmp
        self.commit("src/lib.rs", "a", "feat: something")

    def tearDown(self):
        import shutil
        rc.WORKSPACE = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, path, text, subject):
        f = self.tmp / "r" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text + str(len(self.sh("git", "log", "--oneline").stdout) if (self.tmp / "r/.git/refs/heads/main").exists() else 0))
        self.sh("git", "add", "-A")
        self.sh("git", "commit", "-q", "-m", subject)

    def ref(self):
        self.sh("git", "update-ref", "refs/remotes/origin/main", "HEAD")
        return "origin/main"

    def test_head_set_by_a_ci_bump_with_nothing_after_it_is_bump_only(self):
        self.commit("Cargo.toml", "v", "ci: Bump to version 1.0.1")
        self.assertTrue(rc.bump_only("r", self.ref(), "1.0.1"))

    def test_a_lock_only_commit_after_the_bump_is_still_bump_only(self):
        self.commit("Cargo.toml", "v", "ci: Bump to version 1.0.1")
        self.commit("Cargo.lock", "l", "fix(ci): sync Cargo.lock")
        self.assertTrue(rc.bump_only("r", self.ref(), "1.0.1"))

    def test_a_source_change_after_the_bump_is_real_work(self):
        self.commit("Cargo.toml", "v", "ci: Bump to version 1.0.1")
        self.commit("src/lib.rs", "new", "fix: something real")
        self.assertFalse(rc.bump_only("r", self.ref(), "1.0.1"))

    def test_a_version_not_set_by_a_ci_bump_is_not_bump_only(self):
        self.commit("Cargo.toml", "v", "feat: add a thing and bump to 1.1.0")
        self.assertFalse(rc.bump_only("r", self.ref(), "1.1.0"))


class BumpHeads(unittest.TestCase):
    def test_report_lists_the_bump_heads_that_were_skipped(self):
        r = rc.status(dict(plan([]), bump_heads=[dict(crate="hopr-types", head="2.3.3", published="2.3.2")]))
        self.assertEqual(r["bump_heads"], [dict(crate="hopr-types", head="2.3.3", published="2.3.2")])
        self.assertIn("hopr-types 2.3.3", rc.render(r, "plain"))


class Layers(unittest.TestCase):
    def test_independent_operations_share_a_layer_and_dependents_come_after(self):
        a = step("hopr-types", {"hopr-types": "5.0.0"}, todo={"hopr-types": "5.0.0"})
        b = step("hopr-bindings", {"hopr-bindings": "5.3.0"}, todo={"hopr-bindings": "5.3.0"})
        c = step("hopr-api", {"hopr-api": "4.4.1"}, todo={"hopr-api": "4.4.1"}, gaps=[gap("hopr-types", "4.1.0", "5.0.0")],
                 requires=["hopr-types"])
        ops = rc.operations(plan([a, b, c]))
        layer = {o["id"]: o["layer"] for o in ops}
        self.assertEqual([layer["op-1"], layer["op-2"]], [1, 1])      # types, bindings
        self.assertEqual(layer["op-3"], 2)                            # repin api after types
        self.assertEqual(layer["op-4"], 3)                            # release api after its repin
        self.assertEqual([o["concurrency_group"] for o in ops], ["layer-1", "layer-1", "layer-2", "layer-3"])

    def test_report_lists_the_operation_ids_of_each_layer(self):
        a = step("hopr-types", {"hopr-types": "5.0.0"}, todo={"hopr-types": "5.0.0"})
        b = step("hopr-bindings", {"hopr-bindings": "5.3.0"}, todo={"hopr-bindings": "5.3.0"})
        r = rc.status(plan([a, b]))
        self.assertEqual(r["layers"], [["op-1", "op-2"]])


class Output(unittest.TestCase):
    def report(self):
        a = step("hopr-types", {"hopr-types": "5.0.0"}, todo={"hopr-types": "5.0.0"})
        api = step("hopr-api", {"hopr-api": "4.4.1"}, todo={"hopr-api": "4.4.1"}, gaps=[gap("hopr-types", "4.1.0", "5.0.0")],
                   requires=["hopr-types"])
        return rc.status(plan([a, api]))

    def test_json_is_indented_and_mjson_is_one_line_with_the_same_content(self):
        r = self.report()
        pretty, mini = rc.render(r, "json"), rc.render(r, "mjson")
        self.assertGreater(pretty.count("\n"), 10)
        self.assertEqual(mini.count("\n"), 0)
        self.assertEqual(rc.json.loads(pretty), rc.json.loads(mini))
        self.assertNotIn(", ", mini.split('"schema"')[0])

    def test_plain_shows_state_next_operations_and_resume(self):
        text = rc.render(self.report(), "plain")
        for needle in ("state: release_ready", "next: op-1", "op-1", "release", "layer-1", "repin", "hopr-types 4.1.0 -> 5.0.0", "resume:"):
            self.assertIn(needle, text)

    def test_plain_for_an_error_report(self):
        text = rc.render(rc.error_report(("edge-client", "main"), "boom"), "plain")
        self.assertIn("state: error", text)
        self.assertIn("boom", text)

    def test_unknown_format_is_rejected(self):
        with self.assertRaises(rc.Stop):
            rc.render(self.report(), "xml")


class FakeHttp:
    """Records requests and returns canned (status, json) answers keyed by (method, path suffix)."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, method, url, body=None):
        self.calls.append((method, url, body))
        for (m, suffix), out in self.answers.items():
            if m == method and url.split("?")[0].endswith(suffix):
                return out
        raise AssertionError(f"unexpected {method} {url}")


class Forge(unittest.TestCase):
    def test_gh_is_used_when_installed_and_logged_in(self):
        self.assertEqual(rc.choose_forge(which=lambda n: "/bin/gh", gh_ok=lambda: True, env={}), "gh")

    def test_token_is_used_when_gh_is_missing(self):
        self.assertEqual(rc.choose_forge(which=lambda n: None, gh_ok=lambda: False, env={"GH_TOKEN": "t"}), "api")
        self.assertEqual(rc.choose_forge(which=lambda n: None, gh_ok=lambda: False, env={"GITHUB_TOKEN": "t"}), "api")

    def test_token_is_used_when_gh_is_not_logged_in(self):
        self.assertEqual(rc.choose_forge(which=lambda n: "/bin/gh", gh_ok=lambda: False, env={"GH_TOKEN": "t"}), "api")

    def test_neither_gh_nor_token_is_an_error_that_names_the_fix(self):
        with self.assertRaises(rc.Stop) as e:
            rc.choose_forge(which=lambda n: None, gh_ok=lambda: False, env={})
        self.assertIn("GH_TOKEN", str(e.exception))

    def test_forced_choice_is_honoured(self):
        self.assertEqual(rc.choose_forge(which=lambda n: "/bin/gh", gh_ok=lambda: True, env={"GH_TOKEN": "t"}, force="api"), "api")
        with self.assertRaises(rc.Stop):
            rc.choose_forge(which=lambda n: None, gh_ok=lambda: False, env={}, force="gh")

    def test_api_dispatch_posts_the_ref_and_inputs(self):
        http = FakeHttp({("POST", "/repos/hoprnet/hopr-types/actions/workflows/release.yaml/dispatches"): (204, None)})
        rc.ApiForge("tok", http).dispatch("hopr-types", "release.yaml", "compat/v4", {"release_type": "patch"})
        method, url, body = http.calls[0]
        self.assertEqual((method, body), ("POST", {"ref": "compat/v4", "inputs": {"release_type": "patch"}}))
        self.assertTrue(url.startswith("https://api.github.com/repos/hoprnet/hopr-types/"))

    def test_api_run_ids_reads_workflow_runs(self):
        http = FakeHttp({("GET", "/repos/hoprnet/contracts/actions/workflows/release.yaml/runs"):
                         (200, {"workflow_runs": [{"id": 7}, {"id": 9}]})})
        self.assertEqual(rc.ApiForge("tok", http).run_ids("contracts", "release.yaml", "release/v4"), {7, 9})
        self.assertIn("branch=release%2Fv4", http.calls[0][1])
        self.assertIn("event=workflow_dispatch", http.calls[0][1])

    def test_api_run_info_joins_the_run_and_its_jobs(self):
        http = FakeHttp({("GET", "/repos/hoprnet/contracts/actions/runs/5"): (200, {"status": "in_progress", "conclusion": None}),
                         ("GET", "/repos/hoprnet/contracts/actions/runs/5/jobs"):
                         (200, {"jobs": [{"name": "Build Library / Build", "conclusion": None,
                                          "steps": [{"name": "Publish library", "conclusion": "success"}]}]})})
        info = rc.ApiForge("tok", http).run_info("contracts", 5)
        self.assertEqual(info["status"], "in_progress")
        self.assertEqual(info["jobs"][0]["steps"][0], {"name": "Publish library", "conclusion": "success"})

    def test_api_token_never_appears_in_a_git_command_line(self):
        env = rc.ApiForge("sekret", FakeHttp({})).git_env()
        self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
        self.assertIn("basic", env["GIT_CONFIG_VALUE_0"])
        self.assertNotIn("sekret", env["GIT_CONFIG_VALUE_0"])  # base64, not plain


if __name__ == "__main__":
    unittest.main()
