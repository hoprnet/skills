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
                bump_crates=sorted(head) if republish else [],
                release_versions=({c: rc.bump_patch(v) for c, v in head.items()} if republish else todo))


def plan(steps, repin=(), fixed=(), locked=None, target=None, pins=(), upstream=(), git_updates=()):
    return dict(start=("edge-client", "main"), steps=steps, fixed=list(fixed), pinned=[], repin=list(repin),
                locked=locked or {}, target=target or {}, pins=list(pins), upstream=list(upstream),
                git_updates=list(git_updates))


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
        got = rc.next_batch(report, planned={("hopr-types", "1.0.0")}, remaining=None)
        self.assertEqual([o["id"] for o in got], ["op-1"])

    def test_pending_and_non_release_operations_are_skipped(self):
        report = dict(operations=[self.op("op-1", "a", status="pending"), self.op("op-2", "b", kind="repin"),
                                  self.op("op-3", "c", kind="release_manual"), self.op("op-4", "d")])
        got = rc.next_batch(report, planned={(n, "1.0.0") for n in "abcd"}, remaining=None)
        self.assertEqual([o["id"] for o in got], ["op-4"])

    def test_remaining_caps_the_batch(self):
        report = dict(operations=[self.op("op-1", "a"), self.op("op-2", "b"), self.op("op-3", "c")])
        planned = {(n, "1.0.0") for n in "abc"}
        self.assertEqual([o["id"] for o in rc.next_batch(report, planned=planned, remaining=2)], ["op-1", "op-2"])
        self.assertEqual(rc.next_batch(report, planned=planned, remaining=0), [])

    def test_planned_crates_are_every_release_crate_of_the_first_plan(self):
        report = dict(operations=[self.op("op-1", "a"), self.op("op-2", "b", status="pending"), self.op("op-3", "c", kind="repin")])
        self.assertEqual(rc.planned_crates(report), {("a", "1.0.0"), ("b", "1.0.0")})

    def test_the_same_crate_at_a_new_version_is_not_in_the_plan(self):
        report = dict(operations=[self.op("op-1", "a")])
        report["operations"][0]["crates"][0]["version"] = "1.0.1"  # CI's bump landed a new head
        self.assertEqual(rc.next_batch(report, planned={("a", "1.0.0")}, remaining=None), [])


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
        bump = repin["version_bumps"][0]
        self.assertEqual((bump["crate"], bump["file"], bump["current"], bump["suggested"], bump["level"]),
                         ("hopr-chain-connector", "Cargo.toml", "0.28.0", "0.28.1", "patch"))
        self.assertEqual(len(repin["version_bumps"]), 1)
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

    def test_branch_name_changes_with_the_git_update_and_with_the_base_commit(self):
        a = self.op()
        b = self.op(git_updates=[dict(repo="hoprnet", branch="release/4.0", crates=["hopr-lib"], locked="9e7372b04", head="88b935877")])
        c = self.op(git_updates=[dict(repo="hoprnet", branch="release/4.0", crates=["hopr-lib"], locked="9e7372b04", head="77ac01da0")])
        self.assertEqual(rc.branch_name(a, "base1"), rc.branch_name(self.op(), "base1"))
        self.assertNotEqual(rc.branch_name(a, "base1"), rc.branch_name(b, "base1"))   # the git update counts
        self.assertNotEqual(rc.branch_name(b, "base1"), rc.branch_name(c, "base1"))   # so does the new head
        self.assertNotEqual(rc.branch_name(a, "base1"), rc.branch_name(a, "base2"))   # and the base commit

    def test_pr_text_lists_the_git_revision_move(self):
        op = self.op(git_updates=[dict(repo="hoprnet", branch="release/4.0", crates=["hopr-lib", "hopr-utils-session"],
                                       locked="9e7372b04", head="77ac01da0")])
        title, body = rc.pr_text(op, ("hoprd", "release/4.1"))
        self.assertIn("git: hopr-lib 9e7372b04 -> 77ac01da0 (hoprnet@release/4.0)", body)
        self.assertIn("git: hopr-utils-session 9e7372b04 -> 77ac01da0 (hoprnet@release/4.0)", body)

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
        # every command runs inside the temporary directory, never in the repository the tests live in
        def sh(*a, cwd=None):
            where = cwd or (self.tmp / "hopr-api" if (self.tmp / "hopr-api").exists() else self.tmp)
            assert str(where).startswith(str(self.tmp)), f"test command outside the temp dir: {where}"
            return subprocess.run(a, cwd=where, env=env, check=True, capture_output=True, text=True)
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
            def find_pr(self, repo, head, base=None): return None
            def create_pr(self, repo, head, base, title, body, draft=False):
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

    def base_sha(self):
        return self.sh("git", "rev-parse", "origin/main").stdout.strip()

    def stub_shell(self, wt, cmds):
        self.cmds.append(cmds)  # a real run would update Cargo.lock here
        (Path(wt) / "Cargo.lock").write_text("# lock updated\n")

    def test_edits_are_committed_on_a_branch_pushed_and_a_draft_pr_is_opened(self):
        op = ApplyOps().op()
        result = rc.apply_op(op, ("edge-client", "release/4.1"), shell=self.stub_shell)
        branch = rc.branch_name(op, self.base_sha())
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
        self.assertEqual(self.forge.prs[0][:3] + self.forge.prs[0][4:], ("hopr-api", branch, "main", False))  # ready, not draft
        self.assertTrue(self.cmds and self.cmds[0][0].startswith("cargo metadata"))
        # the main branch is untouched
        main = self.sh("git", "--git-dir", str(self.tmp / "origin.git"), "show", "main:Cargo.toml").stdout
        self.assertIn('version = "4.1.0"', main)

    def test_a_leftover_local_branch_and_worktree_from_a_failed_attempt_are_replaced(self):
        op = ApplyOps().op()
        branch = rc.branch_name(op, self.base_sha())
        wt = self.tmp / "hopr-api-worktrees" / branch.replace("/", "-")
        self.sh("git", "worktree", "add", "-q", "-b", branch, str(wt), "origin/main")
        (wt / "Cargo.lock").write_text("# half-finished lock")      # what a failed attempt leaves behind
        result = rc.apply_op(op, ("edge-client", "release/4.1"), shell=self.stub_shell)
        self.assertEqual(result["state"], "opened")
        shown = self.sh("git", "--git-dir", str(self.tmp / "origin.git"), "show", f"{branch}:Cargo.toml").stdout
        self.assertIn('hopr-types = { version = "5.0.0"', shown)

    def test_a_leftover_worktree_with_other_changes_is_never_deleted(self):
        op = ApplyOps().op()
        branch = rc.branch_name(op, self.base_sha())
        wt = self.tmp / "hopr-api-worktrees" / branch.replace("/", "-")
        self.sh("git", "worktree", "add", "-q", "-b", branch, str(wt), "origin/main")
        (wt / "notes.txt").write_text("someone's work")
        with self.assertRaises(rc.Stop) as e:
            rc.apply_op(op, ("edge-client", "release/4.1"), shell=self.stub_shell)
        self.assertIn("notes.txt", str(e.exception))
        self.assertTrue((wt / "notes.txt").exists())

    def test_a_pushed_branch_with_no_pr_only_opens_the_pr(self):
        op = ApplyOps().op()
        branch = rc.branch_name(op, self.base_sha())
        self.sh("git", "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        result = rc.apply_op(op, ("edge-client", "release/4.1"), shell=self.stub_shell)
        self.assertEqual((result["state"], result["url"]), ("opened", "https://github.com/hoprnet/hopr-api/pull/1"))
        self.assertEqual(self.cmds, [])  # no edits, no cargo: the branch was already pushed
        self.assertEqual(self.forge.prs[0][1], branch)

    def test_a_branch_pushed_by_an_earlier_run_with_other_content_is_not_reused(self):
        old = ApplyOps().op()                                   # the earlier run had no git update
        old_branch = rc.branch_name(old, self.base_sha())
        self.sh("git", "push", "-q", "origin", f"HEAD:refs/heads/{old_branch}")
        new = ApplyOps().op(git_updates=[dict(repo="hoprnet", branch="release/4.0", crates=["hopr-lib"], locked="9e7372b04", head="77ac01da")])
        result = rc.apply_op(new, ("edge-client", "release/4.1"), shell=self.stub_shell)
        self.assertNotEqual(result["branch"], old_branch)
        self.assertEqual(result["state"], "opened")
        self.assertTrue(any("cargo update -p hopr-lib" in c for c in self.cmds[0]))   # the new content was applied

    def test_an_existing_open_pr_is_reported_and_nothing_is_pushed_again(self):
        self.forge.find_pr = lambda repo, head, base=None: "https://github.com/hoprnet/hopr-api/pull/9"
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
        self.write("src/lib.rs", "a")
        self.write("Cargo.toml", self.manifest("1.0.0"))
        self.commit("feat: something")

    def tearDown(self):
        import shutil
        rc.WORKSPACE = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def manifest(self, version, deps=""):
        return f'[package]\nname = "x"\nversion = "{version}"\n\n[dependencies]\n{deps}'

    def write(self, path, text):
        f = self.tmp / "r" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    def commit(self, subject):
        self.sh("git", "add", "-A")
        self.sh("git", "commit", "-q", "-m", subject)

    def ref(self):
        self.sh("git", "update-ref", "refs/remotes/origin/main", "HEAD")
        return "origin/main"

    def bump(self, version="1.0.1"):
        self.write("Cargo.toml", self.manifest(version))
        self.commit(f"ci: Bump to version {version}")

    def test_head_set_by_a_ci_bump_with_nothing_after_it_is_bump_only(self):
        self.bump()
        self.assertTrue(rc.bump_only("r", self.ref(), "1.0.1"))

    def test_a_lock_only_commit_after_the_bump_is_still_bump_only(self):
        self.bump()
        self.write("Cargo.lock", "l")
        self.commit("fix(ci): sync Cargo.lock")
        self.assertTrue(rc.bump_only("r", self.ref(), "1.0.1"))

    def test_a_source_change_after_the_bump_is_real_work(self):
        self.bump()
        self.write("src/lib.rs", "new")
        self.commit("fix: something real")
        self.assertFalse(rc.bump_only("r", self.ref(), "1.0.1"))

    def test_a_version_not_set_by_a_ci_bump_is_not_bump_only(self):
        self.write("Cargo.toml", self.manifest("1.1.0"))
        self.commit("feat: add a thing and bump to 1.1.0")
        self.assertFalse(rc.bump_only("r", self.ref(), "1.1.0"))

    def test_a_bump_commit_that_also_changes_source_is_not_a_bump(self):
        self.write("src/lib.rs", "sneaked in")
        self.bump()
        self.assertFalse(rc.bump_only("r", self.ref(), "1.0.1"))

    def test_a_manifest_edit_that_is_not_a_version_after_the_bump_is_real_work(self):
        self.bump()
        self.write("Cargo.toml", self.manifest("1.0.1", deps='serde = "1"\n'))
        self.commit("feat: add a dependency")
        self.assertFalse(rc.bump_only("r", self.ref(), "1.0.1"))

    def test_a_manifest_edit_that_is_only_a_version_after_the_bump_is_still_a_bump(self):
        self.bump()
        self.write("Cargo.toml", self.manifest("1.0.1").replace('version = "1.0.1"', 'version = "1.0.1"  # synced'))
        self.commit("chore: tidy")
        self.assertTrue(rc.bump_only("r", self.ref(), "1.0.1"))


class VersionChanges(unittest.TestCase):
    def test_only_version_lines_count(self):
        ok = '--- a/Cargo.toml\n+++ b/Cargo.toml\n@@ -3 +3 @@\n-version = "1.0.0"\n+version = "1.0.1"\n'
        self.assertTrue(rc.only_version_changes(ok))
        self.assertFalse(rc.only_version_changes(ok + '+serde = "1"\n'))
        self.assertFalse(rc.only_version_changes('-hopr-api = { version = "1.0.0" }\n+hopr-api = { version = "1.0.1" }\n'))
        self.assertTrue(rc.only_version_changes(""))


class ClassifyHeads(unittest.TestCase):
    def s(self, todo):
        return dict(repo="r", branch="main", todo=dict(todo), head=dict(todo))

    def published(self, c):
        return {"a": ["1.0.0", "1.0.1"], "b": ["2.0.0"]}.get(c, [])

    def test_a_bump_head_with_a_published_stand_in_is_not_released(self):
        got = rc.classify_heads(self.s({"a": "1.0.2"}), None, lambda c, v: True, self.published)
        self.assertEqual(got["todo"], {})
        self.assertEqual(got["bump_heads"], [dict(crate="a", head="1.0.2", published="1.0.1")])
        self.assertEqual(got["stand_in"], {"a": "1.0.1"})

    def test_a_bump_head_with_no_published_stand_in_stays_a_release(self):
        got = rc.classify_heads(self.s({"new": "0.1.1"}), None, lambda c, v: True, self.published)
        self.assertEqual(got["todo"], {"new": "0.1.1"})
        self.assertEqual(got["bump_heads"], [])

    def test_a_real_head_outside_the_first_plan_is_reported_as_a_later_release(self):
        got = rc.classify_heads(self.s({"a": "1.0.2"}), {("b", "2.0.0")}, lambda c, v: False, self.published)
        self.assertEqual(got["todo"], {})
        self.assertEqual(got["bump_heads"], [])
        self.assertEqual(got["later_heads"], [dict(crate="a", head="1.0.2", published="1.0.1")])

    def test_a_real_head_inside_the_first_plan_or_with_no_plan_is_released(self):
        self.assertEqual(rc.classify_heads(self.s({"a": "1.0.2"}), {("a", "1.0.2")}, lambda c, v: False, self.published)["todo"], {"a": "1.0.2"})
        self.assertEqual(rc.classify_heads(self.s({"a": "1.0.2"}), None, lambda c, v: False, self.published)["todo"], {"a": "1.0.2"})

    def test_later_heads_show_in_the_report_as_warnings(self):
        r = rc.status(dict(plan([]), later_heads=[dict(crate="a", head="1.0.2", published="1.0.1")]))
        self.assertEqual(r["later_heads"], [dict(crate="a", head="1.0.2", published="1.0.1")])
        self.assertTrue(any("a 1.0.2" in w and "run again" in w for w in r["warnings"]))


class ZeroZeroVersions(unittest.TestCase):
    def test_a_0_0_x_head_matches_only_its_own_patch(self):
        self.assertEqual(rc.pick_latest_compatible(["0.0.3", "0.0.4"], "0.0.3"), "0.0.3")
        self.assertIsNone(rc.pick_latest_compatible(["0.0.4"], "0.0.3"))


def tomllib_loads(text):
    import tomllib
    return tomllib.loads(text)


class TableFormPins(unittest.TestCase):
    swap = dict(file="Cargo.toml", key="hopr-lib", crate="hopr-lib", current="git x", required="5.0.0", swap="git_to_version")

    def text(self):
        return ('[package]\nname = "x"\n\n[dependencies.hopr-lib]\ngit = "https://github.com/hoprnet/hoprnet"\n'
                'branch = "master"\nfeatures = ["serde"]\n\n[dev-dependencies]\nfoo = "1"\n')

    def test_swap_in_a_table_keeps_the_other_keys_and_the_next_table(self):
        out = rc.swap_git_text(self.text(), dict(self.swap, line=4))
        self.assertIn('[dependencies.hopr-lib]\nversion = "5.0.0"\nfeatures = ["serde"]\n\n[dev-dependencies]\nfoo = "1"', out)
        self.assertEqual(tomllib_loads(out)["dependencies"]["hopr-lib"], {"version": "5.0.0", "features": ["serde"]})

    def test_rev_in_a_table(self):
        text = '[dependencies.hopr-lib]\ngit = "https://github.com/hoprnet/hoprnet"\nrev = "af80993c"\n'
        out = rc.rev_text(text, dict(self.swap, line=1, current="rev af80993c", required="88b93587", swap="git_rev"))
        self.assertEqual(tomllib_loads(out)["dependencies"]["hopr-lib"]["rev"], "88b93587")

    def test_swap_replaces_an_existing_version_key_and_leaves_valid_toml(self):
        text = 'hopr-lib = { git = "https://github.com/hoprnet/hoprnet", version = "4.0", branch = "master" }\n'
        out = rc.swap_git_text(text, dict(self.swap, line=1))
        self.assertEqual(tomllib_loads(out)["hopr-lib"], {"version": "5.0.0"})

    def test_edited_manifests_stay_valid_toml(self):
        for text, e in [('a = { git = "https://github.com/hoprnet/hoprnet", branch = "x", features = ["f"] }\n', dict(self.swap, key="a", line=1)),
                        ('a = { git = "https://github.com/hoprnet/hoprnet", rev = "abc", default-features = false }\n',
                         dict(self.swap, key="a", line=1, current="rev abc", required="def", swap="git_rev"))]:
            tomllib_loads(rc.edit_text(text, e))


class CargoAccepts(unittest.TestCase):
    """The edited manifest still works with cargo (skipped where cargo is missing)."""

    def test_a_version_bump_gives_a_manifest_cargo_resolves_and_a_lock_with_the_new_version(self):
        import shutil, subprocess, tempfile
        if not shutil.which("cargo"):
            self.skipTest("cargo not installed")
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "src").mkdir()
            (tmp / "src" / "lib.rs").write_text("")
            (tmp / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "1.0.0"\nedition = "2021"\n')
            bumped = rc.bump_text((tmp / "Cargo.toml").read_text(), dict(crate="x", file="Cargo.toml", current="1.0.0", suggested="1.0.1"))
            (tmp / "Cargo.toml").write_text(bumped)
            r = subprocess.run(["cargo", "metadata", "--format-version", "1", "--offline"], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('version = "1.0.1"', (tmp / "Cargo.lock").read_text())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class FindPrByBase(unittest.TestCase):
    def test_api_find_pr_filters_by_base(self):
        http = FakeHttp({("GET", "/pulls"): (200, [{"html_url": "https://github.com/hoprnet/r/pull/3"}])})
        self.assertEqual(rc.ApiForge("t", http).find_pr("r", "head", "release/4.1"), "https://github.com/hoprnet/r/pull/3")
        self.assertIn("base=release%2F4.1", http.calls[0][1])

    def test_gh_find_pr_passes_the_base(self):
        calls = []
        saved = rc.run
        rc.run = lambda *a, **kw: calls.append(a) or (0, "[]")
        try:
            rc.GhForge().find_pr("r", "head", "release/4.1")
        finally:
            rc.run = saved
        self.assertIn("--base", calls[0])
        self.assertEqual(calls[0][calls[0].index("--base") + 1], "release/4.1")


class ErrorReportKeepsPullRequests(unittest.TestCase):
    def test_pull_requests_opened_before_the_error_stay_in_the_report(self):
        prs = [dict(url="https://github.com/hoprnet/x/pull/1")]
        self.assertEqual(rc.error_report(("a", "b"), "boom", pull_requests=prs)["pull_requests"], prs)
        self.assertEqual(rc.error_report(("a", "b"), "boom")["pull_requests"], [])


class RevPinRepoIsCloned(unittest.TestCase):
    def test_the_repo_is_cloned_before_its_default_branch_is_read(self):
        order, saved = [], (rc.ensure, rc.default_branch, rc.run)
        rc.ensure = lambda repo: order.append("ensure")
        rc.default_branch = lambda repo: order.append("default_branch") or "master"
        rc.run = lambda *a, **kw: (0, "")
        try:
            self.assertEqual(rc.branch_of_rev("hoprnet", "af80993c"), "master")
            rc.ensure = lambda repo: (_ for _ in ()).throw(rc.Stop("no access"))
            self.assertIsNone(rc.branch_of_rev("hoprnet", "af80993c"))
        finally:
            rc.ensure, rc.default_branch, rc.run = saved
        self.assertEqual(order, ["ensure", "default_branch"])


class LockOfAGitPinnedRepo(unittest.TestCase):
    def test_a_repo_without_a_committed_lock_gives_no_versions(self):
        import os, subprocess, tempfile, shutil
        tmp = Path(tempfile.mkdtemp())
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        sh = lambda *a: subprocess.run(a, cwd=tmp / "r", env=env, check=True, capture_output=True, text=True)
        (tmp / "r").mkdir()
        sh("git", "init", "-q", "-b", "main")
        (tmp / "r" / "README").write_text("x")
        sh("git", "add", "-A"); sh("git", "commit", "-q", "-m", "init"); sh("git", "update-ref", "refs/remotes/origin/main", "HEAD")
        saved = rc.WORKSPACE
        rc.WORKSPACE = tmp
        try:
            self.assertEqual(rc.locked_org_versions("r", "origin/main", {"hopr-types"}), {})
            (tmp / "r" / "Cargo.lock").write_text('[[package]]\nname = "hopr-types"\nversion = "2.3.2"\n[[package]]\nname = "serde"\nversion = "1.0.0"\n')
            sh("git", "add", "-A"); sh("git", "commit", "-q", "-m", "lock"); sh("git", "update-ref", "refs/remotes/origin/main", "HEAD")
            self.assertEqual(rc.locked_org_versions("r", "origin/main", {"hopr-types"}), {"hopr-types": "2.3.2"})
        finally:
            rc.WORKSPACE = saved
            shutil.rmtree(tmp, ignore_errors=True)


class InheritedRequirements(unittest.TestCase):
    manifests = {"a": "a/Cargo.toml", "b": "b/Cargo.toml"}

    def gap(self, file, table, key="hopr-types"):
        return dict(file=file, table=table, key=key, crate=key, req="4.1.0", want="5.0.0")

    def test_a_gap_in_a_members_own_manifest_selects_that_member(self):
        got = rc.crates_with_gaps(["a", "b"], self.manifests, [self.gap("b/Cargo.toml", "dependencies")], {})
        self.assertEqual(got, ["b"])

    def test_a_stale_workspace_requirement_selects_only_the_members_that_inherit_it(self):
        gaps_ = [self.gap("Cargo.toml", "workspace.dependencies")]
        got = rc.crates_with_gaps(["a", "b"], self.manifests, gaps_, {"a/Cargo.toml": {"hopr-types"}, "b/Cargo.toml": {"serde"}})
        self.assertEqual(got, ["a"])

    def test_a_root_package_and_an_inheriting_member_are_both_selected(self):
        manifests = {"root": "Cargo.toml", "m": "m/Cargo.toml"}
        gaps_ = [self.gap("Cargo.toml", "workspace.dependencies")]
        got = rc.crates_with_gaps(["root", "m"], manifests, gaps_, {"Cargo.toml": {"hopr-types"}, "m/Cargo.toml": {"hopr-types"}})
        self.assertEqual(got, ["m", "root"])

    def test_inherited_keys_are_read_from_the_manifests(self):
        ms = {"a/Cargo.toml": {"package": {"name": "a"}, "dependencies": {"hopr-types": {"workspace": True}, "x": "1"},
                               "dev-dependencies": {"y": {"workspace": True, "features": ["f"]}}},
              "b/Cargo.toml": {"package": {"name": "b"}}}
        self.assertEqual(rc.inherited_keys(ms), {"a/Cargo.toml": {"hopr-types", "y"}, "b/Cargo.toml": set()})


class GitSourceSpec(unittest.TestCase):
    def test_a_git_pinned_crate_is_named_by_its_source_in_cargo_update(self):
        op = dict(kind="final_repin", repo="hoprd", branch="release/4.1", edits=[], lock_updates=[], version_bumps=[],
                  git_updates=[dict(repo="hoprnet", branch="release/4.0", crates=["hopr-lib"], locked="9e7372b04", head="77ac01dad",
                                    specs={"hopr-lib": "https://github.com/hoprnet/hoprnet#hopr-lib@4.0.5"})])
        self.assertIn("cargo update -p 'https://github.com/hoprnet/hoprnet#hopr-lib@4.0.5'", rc.lock_commands(op))
        self.assertNotIn("cargo update -p hopr-lib", rc.lock_commands(op))


class DirtyWorktree(unittest.TestCase):
    def test_edits_in_the_files_the_script_writes_are_expected(self):
        self.assertEqual(rc.unexpected_changes(" M Cargo.lock\n M Cargo.toml\n", {"Cargo.toml"}), [])

    def test_any_other_change_is_reported(self):
        self.assertEqual(rc.unexpected_changes(" M Cargo.lock\n?? notes.txt\n M src/lib.rs\n", {"Cargo.toml"}), ["notes.txt", "src/lib.rs"])


class BumpHeads(unittest.TestCase):
    def test_report_lists_the_bump_heads_that_were_skipped(self):
        r = rc.status(dict(plan([]), bump_heads=[dict(crate="hopr-types", head="2.3.3", published="2.3.2")]))
        self.assertEqual(r["bump_heads"], [dict(crate="hopr-types", head="2.3.3", published="2.3.2")])
        self.assertIn("hopr-types 2.3.3", rc.render(r, "plain"))


class NixCwd(unittest.TestCase):
    """`nix develop <path>` runs its shell hook in the current directory, so it must be the worktree."""

    def worktree(self, flake=True):
        import tempfile
        wt = Path(tempfile.mkdtemp())
        if flake:
            (wt / "flake.nix").write_text("{}")
        return wt

    def run_with(self, nix_installed, fn):
        calls, saved_run, saved_which = [], rc.run, rc.shutil.which
        rc.run = lambda *a, **kw: calls.append((a, kw)) or (0, "")
        rc.shutil.which = lambda name: "/bin/nix" if (nix_installed and name == "nix") else None
        try:
            fn()
        finally:
            rc.run, rc.shutil.which = saved_run, saved_which
        return calls

    def test_commands_in_a_flake_worktree_run_with_the_worktree_as_cwd(self):
        wt = self.worktree()
        calls = self.run_with(True, lambda: (rc.in_shell(wt, ["cargo metadata"]), rc.hooked_git(wt, "status")))
        self.assertEqual(len(calls), 2)
        for args, kw in calls:
            self.assertEqual(args[0], "nix")
            self.assertEqual(kw.get("cwd"), wt)

    def test_a_flake_worktree_without_nix_installed_runs_cargo_and_git_directly(self):
        wt = self.worktree()
        calls = self.run_with(False, lambda: (rc.in_shell(wt, ["cargo metadata"]), rc.hooked_git(wt, "status")))
        self.assertEqual([a[0] for a, _ in calls], ["bash", "git"])
        self.assertNotIn("nix", [x for a, _ in calls for x in a])

    def test_commands_without_a_flake_do_not_use_nix(self):
        wt = self.worktree(flake=False)
        calls = self.run_with(True, lambda: rc.hooked_git(wt, "status"))
        self.assertEqual(calls[0][0][0], "git")


class Versions(unittest.TestCase):
    def test_a_stable_release_ranks_above_its_prereleases(self):
        self.assertGreater(rc.vkey("5.2.0"), rc.vkey("5.2.0-rc.1"))
        self.assertGreater(rc.vkey("5.2.0-rc.2"), rc.vkey("5.2.0-rc.1"))
        self.assertGreater(rc.vkey("5.2.0-rc.10"), rc.vkey("5.2.0-rc.2"))
        self.assertGreater(rc.vkey("5.2.1-rc.1"), rc.vkey("5.2.0"))

    def test_a_lock_on_a_prerelease_is_updated_to_the_stable_release(self):
        ops = rc.operations(plan([], locked={"x": "5.2.0-rc.1"}, target={"x": "5.2.0"}))
        self.assertEqual(ops[0]["lock_updates"], [dict(crate="x", locked="5.2.0-rc.1", target="5.2.0")])

    def test_wildcards_are_tilde_ranges(self):
        self.assertTrue(rc.admits("1.2.*", "1.2.9"))
        self.assertFalse(rc.admits("1.2.*", "1.3.0"))
        self.assertTrue(rc.admits("1.*", "1.9.0"))
        self.assertFalse(rc.admits("1.*", "2.0.0"))

    def test_an_exact_prerelease_requirement_matches_only_that_prerelease(self):
        self.assertFalse(rc.admits("=2.3.0-rc.2", "2.3.0-rc.1"))
        self.assertTrue(rc.admits("=2.3.0-rc.2", "2.3.0-rc.2"))

    def test_next_free_patch_skips_every_version_crates_io_has_seen(self):
        self.assertEqual(rc.next_free_patch(["0.28.0", "0.28.1", "0.28.2"], "0.28.0"), "0.28.3")
        self.assertEqual(rc.next_free_patch([], "1.0.0"), "1.0.1")


class Throttle(unittest.TestCase):
    def test_requests_are_spaced_by_the_interval(self):
        now, slept = [100.0], []
        t = rc.Throttle(1.0, clock=lambda: now[0], sleep=lambda d: (slept.append(round(d, 3)), now.__setitem__(0, now[0] + d)))
        t.wait()                 # first request: no wait
        now[0] += 0.25
        t.wait()                 # 0.25 s later: waits the rest of the interval
        now[0] += 2.0
        t.wait()                 # long after: no wait
        self.assertEqual(slept, [0.75])


class RepositoryCache(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (rc.CACHE, rc._repo_cache, rc.crates_io)
        rc.CACHE, rc._repo_cache = self.tmp / "cache.json", None
        self.calls = []

    def tearDown(self):
        import shutil
        rc.CACHE, rc._repo_cache, rc.crates_io = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def answers(self, repo):
        def fake(name, version=None):
            self.calls.append(name)
            return {"crate": {"repository": repo}}
        rc.crates_io = fake

    def test_a_crate_without_a_repository_is_asked_again_next_time(self):
        self.answers(None)
        self.assertIsNone(rc.org_repo_of("x"))
        self.assertIsNone(rc.org_repo_of("x"))
        self.assertEqual(self.calls, ["x", "x"])

    def test_a_found_repository_is_cached(self):
        self.answers("https://github.com/hoprnet/hopr-api")
        self.assertEqual(rc.org_repo_of("x"), "hopr-api")
        self.assertEqual(rc.org_repo_of("x"), "hopr-api")
        self.assertEqual(self.calls, ["x"])

    def test_a_third_party_repository_is_cached_too(self):
        self.answers("https://github.com/serde-rs/serde")
        self.assertIsNone(rc.org_repo_of("serde"))
        self.assertIsNone(rc.org_repo_of("serde"))
        self.assertEqual(self.calls, ["serde"])


class ManifestScan(unittest.TestCase):
    def test_only_vendored_and_build_directories_are_skipped(self):
        for path in ("crates/lib/example/Cargo.toml", "Cargo.toml", "ethereum/bindings/Cargo.toml"):
            self.assertFalse(rc.skipped_manifest(path), path)
        for path in ("lib/forge-std/Cargo.toml", "vendor/x/Cargo.toml", "a/target/debug/Cargo.toml", "node_modules/x/Cargo.toml"):
            self.assertTrue(rc.skipped_manifest(path), path)


class TableLines(unittest.TestCase):
    text = ('[package]\nname = "x"\n\n[dependencies]\nhopr-types = "4.1.0"\n\n[dev-dependencies]\nhopr-types = "4.0.0"\n'
            "\n[target.'cfg(unix)'.dependencies]\nhopr-types = \"3.0.0\"\n\n[workspace.dependencies]\nhopr-types = \"2.0.0\"\n"
            '\n[dependencies.serde]\nversion = "1"\n')

    def test_the_line_is_found_inside_the_named_table(self):
        self.assertEqual(rc.find_line(self.text, "hopr-types", "dependencies"), 5)
        self.assertEqual(rc.find_line(self.text, "hopr-types", "dev-dependencies"), 8)
        self.assertEqual(rc.find_line(self.text, "hopr-types", "target.cfg(unix).dependencies"), 11)
        self.assertEqual(rc.find_line(self.text, "hopr-types", "workspace.dependencies"), 14)

    def test_without_a_table_the_first_assignment_is_used(self):
        self.assertEqual(rc.find_line(self.text, "hopr-types", None), 5)

    def test_entries_record_their_table(self):
        ms = {"Cargo.toml": {"dependencies": {"a": "1.0"}, "dev-dependencies": {"a": "0.9"},
                             "target": {"cfg(unix)": {"dependencies": {"a": "1.1"}}}, "workspace": {"dependencies": {"a": "1.2"}}}}
        tables = sorted((e["table"], e["req"]) for e in rc.dep_entries(ms))
        self.assertEqual(tables, [("dependencies", "1.0"), ("dev-dependencies", "0.9"),
                                  ("target.cfg(unix).dependencies", "1.1"), ("workspace.dependencies", "1.2")])


class MixedRepos(unittest.TestCase):
    def test_a_published_crate_with_a_stale_requirement_is_bumped_with_its_unpublished_sibling(self):
        s = step("hopr-impls", {"a": "1.0.1", "b": "2.0.0"}, todo={"a": "1.0.1"},
                 gaps=[dict(gap("hopr-types", "4.1.0", "5.0.0"), file="b/Cargo.toml")])
        s["files"] = {"a": "a/Cargo.toml", "b": "b/Cargo.toml"}
        s["bump_crates"], s["release_versions"], s["republish"] = ["b"], {"a": "1.0.1", "b": "2.0.1"}, True
        ops = rc.operations(plan([step("hopr-types", {"hopr-types": "5.0.0"}, todo={"hopr-types": "5.0.0"}), s]))
        repin = next(o for o in ops if o["kind"] == "repin")
        self.assertEqual([b["crate"] for b in repin["version_bumps"]], ["b"])
        release = next(o for o in ops if o["kind"] == "release" and o["repo"] == "hopr-impls")
        self.assertEqual(sorted((c["name"], c["version"]) for c in release["crates"]), [("a", "1.0.1"), ("b", "2.0.1")])

    def test_which_published_crates_a_gap_touches(self):
        files = {"a": "a/Cargo.toml", "b": "b/Cargo.toml"}
        self.assertEqual(rc.crates_with_gaps(["a", "b"], files, [dict(file="b/Cargo.toml")], {}), ["b"])
        self.assertEqual(rc.crates_with_gaps(["a"], {"a": "Cargo.toml"}, [dict(file="Cargo.toml")], {}), ["a"])
        self.assertEqual(rc.crates_with_gaps(["a", "b"], files, [], {}), [])


class RunSettings(unittest.TestCase):
    def ns(self, **kw):
        from types import SimpleNamespace
        base = dict(workspace=None, forge="auto", output="json", release_type="patch", max_releases=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_defaults_add_nothing(self):
        self.assertEqual(rc.resume_flags(self.ns()), "")

    def test_run_settings_are_kept_and_quoted(self):
        flags = rc.resume_flags(self.ns(workspace=Path("/tmp/my ws"), forge="api", output="mjson", release_type="minor", max_releases=2))
        self.assertIn("--workspace '/tmp/my ws'", flags)
        for part in ("--forge api", "-o mjson", "--release-type minor", "--max-releases 2"):
            self.assertIn(part, flags)

    def test_the_start_argument_needs_a_branch(self):
        self.assertEqual(rc.parse_start("edge-client@release/4.1"), ("edge-client", "release/4.1"))
        with self.assertRaises(rc.Stop):
            rc.parse_start("edge-client")


class Prompts(unittest.TestCase):
    def test_the_prompt_goes_to_stderr_and_stdout_stays_clean(self):
        import io, contextlib, builtins
        out, err = io.StringIO(), io.StringIO()
        saved = builtins.input
        builtins.input = lambda *a: "yes"
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                answer = rc.ask("release 1 at once? ")
        finally:
            builtins.input = saved
        self.assertEqual((answer, out.getvalue()), ("yes", ""))
        self.assertIn("release 1 at once?", err.getvalue())


class DispatchRunId(unittest.TestCase):
    def test_the_run_id_comes_from_the_dispatch_response(self):
        http = FakeHttp({("POST", "/dispatches"): (200, {"workflow_run_id": 99})})
        self.assertEqual(rc.ApiForge("t", http).dispatch("r", "release.yaml", "main", {"release_type": "patch"}), 99)
        self.assertTrue(http.calls[0][2]["return_run_details"])

    def test_an_empty_204_gives_no_id_so_the_run_list_is_used(self):
        http = FakeHttp({("POST", "/dispatches"): (204, None)})
        self.assertIsNone(rc.ApiForge("t", http).dispatch("r", "release.yaml", "main", {}))

    def test_a_422_for_the_new_parameter_retries_without_it(self):
        answers = iter([(422, {"message": "Unexpected inputs"}), (204, None)])
        calls = []
        def http(method, url, body=None):
            calls.append(body)
            return next(answers)
        self.assertIsNone(rc.ApiForge("t", http).dispatch("r", "release.yaml", "main", {}))
        self.assertIn("return_run_details", calls[0])
        self.assertNotIn("return_run_details", calls[1])

    def test_gh_output_with_a_run_url_gives_the_id(self):
        self.assertEqual(rc.run_id_from_output("Created workflow_dispatch event\nhttps://github.com/hoprnet/hopr-types/actions/runs/40874170666\n"), 40874170666)
        self.assertIsNone(rc.run_id_from_output("Created workflow_dispatch event for release.yaml at main\n"))


class AnonymousForge(unittest.TestCase):
    def test_clones_over_https_without_credentials(self):
        calls = []
        saved = rc.run
        rc.run = lambda *a, **kw: calls.append(a) or (0, "")
        try:
            rc.AnonForge().clone("hopr-api", Path("/w/hopr-api"))
        finally:
            rc.run = saved
        self.assertIn("https://github.com/hoprnet/hopr-api", calls[0])

    def test_anything_that_changes_github_needs_a_login(self):
        with self.assertRaises(rc.Stop) as e:
            rc.AnonForge().dispatch("r", "w", "main", {})
        self.assertIn("GH_TOKEN", str(e.exception))
        with self.assertRaises(rc.Stop):
            rc.AnonForge().create_pr("r", "h", "b", "t", "x")


class GitRefs(unittest.TestCase):
    def pin(self, crate, ref, git="https://github.com/hoprnet/hoprnet"):
        return dict(file="Cargo.toml", line=97, table="dependencies", key=crate, crate=crate, git=git, ref=ref)

    sources = {"hopr-lib": ("https://github.com/hoprnet/hoprnet", "9e7372b04aaa"),
               "hopr-utils-session": ("https://github.com/hoprnet/hoprnet", "9e7372b04aaa")}

    def test_branch_pins_of_one_repo_are_grouped_with_the_branch_from_the_pin(self):
        pins = [self.pin("hopr-lib", "branch release/4.0"), self.pin("hopr-utils-session", "branch release/4.0")]
        got = rc.group_git_pins(pins, self.sources, rev_branch=lambda repo, rev: None)
        self.assertEqual(got, [dict(repo="hoprnet", branch="release/4.0", crates=["hopr-lib", "hopr-utils-session"],
                                    locked="9e7372b04aaa", style="branch")])

    def test_a_rev_pin_uses_the_branch_the_rev_is_on(self):
        got = rc.group_git_pins([self.pin("hopr-lib", "rev af80993c")], self.sources, rev_branch=lambda repo, rev: "master")
        self.assertEqual((got[0]["branch"], got[0]["style"]), ("master", "rev"))

    def test_a_rev_that_is_on_no_known_branch_and_a_tag_are_left_out(self):
        pins = [self.pin("hopr-lib", "rev deadbeef"), self.pin("hopr-utils-session", "tag v1.0.0")]
        self.assertEqual(rc.group_git_pins(pins, self.sources, rev_branch=lambda repo, rev: None), [])

    def test_pins_of_other_owners_are_ignored(self):
        got = rc.group_git_pins([self.pin("x", "branch main", git="https://github.com/somebody/else")], self.sources,
                                rev_branch=lambda repo, rev: None)
        self.assertEqual(got, [])

    def test_rev_text_replaces_the_pinned_revision_on_its_line(self):
        text = 'a = 1\nhopr-lib = { git = "https://github.com/hoprnet/hoprnet", rev = "af80993c", default-features = false }\n'
        e = dict(file="Cargo.toml", line=2, key="hopr-lib", crate="hopr-lib", current="rev af80993c", required="88b93587", swap="git_rev")
        self.assertIn('rev = "88b93587"', rc.edit_text(text, e))
        self.assertNotIn("af80993c", rc.edit_text(text, e))
        with self.assertRaises(rc.Stop):
            rc.edit_text(text, dict(e, current="rev 11111111"))


class UpstreamOps(unittest.TestCase):
    def upstream(self, **kw):
        base = dict(repo="hoprnet", branch="release/4.0",
                    gaps=[dict(file="Cargo.toml", table="workspace.dependencies", key="hopr-types", crate="hopr-types",
                               req="2.3.0", want="2.4.0")],
                    lock_updates=[dict(crate="hopr-types", locked="2.3.0", target="2.4.0")])
        base.update(kw)
        return base

    def test_the_repo_behind_a_git_pin_is_updated_after_the_releases_it_needs(self):
        types = step("hopr-types", {"hopr-types": "2.4.0"}, todo={"hopr-types": "2.4.0"})
        ops = rc.operations(plan([types], upstream=[self.upstream()]))
        self.assertEqual([(o["kind"], o["repo"]) for o in ops], [("release", "hopr-types"), ("repin", "hoprnet")])
        up = ops[1]
        self.assertEqual(up["depends_on"], ["op-1"])
        self.assertEqual(up["edits"][0]["required"], "2.4.0")
        self.assertEqual(up["lock_updates"], [dict(crate="hopr-types", locked="2.3.0", target="2.4.0")])
        self.assertEqual(up["version_bumps"], [])
        self.assertNotIn("release", [o["kind"] for o in ops if o["repo"] == "hoprnet"])  # hoprnet crates are not released

    def test_only_a_stale_lock_is_still_an_operation(self):
        ops = rc.operations(plan([], upstream=[self.upstream(gaps=[])]))
        self.assertEqual([(o["kind"], o["repo"]) for o in ops], [("repin", "hoprnet")])
        self.assertEqual(ops[0]["edits"], [])

    def test_nothing_stale_means_no_operation(self):
        self.assertEqual(rc.operations(plan([], upstream=[self.upstream(gaps=[], lock_updates=[])])), [])

    def test_the_start_repo_waits_for_the_upstream_repo_and_refreshes_the_git_revision(self):
        types = step("hopr-types", {"hopr-types": "2.4.0"}, todo={"hopr-types": "2.4.0"})
        git = dict(repo="hoprnet", branch="release/4.0", crates=["hopr-lib", "hopr-utils-session"], locked="9e7372b04", head="88b935877")
        ops = rc.operations(plan([types], upstream=[self.upstream()], git_updates=[git]))
        final = ops[-1]
        self.assertEqual(final["kind"], "final_repin")
        self.assertEqual(final["depends_on"], ["op-2"])
        self.assertEqual(final["git_updates"], [git])
        cmds = rc.lock_commands(final)
        self.assertIn("cargo update -p hopr-lib", cmds)
        self.assertIn("cargo update -p hopr-utils-session", cmds)

    def test_a_branch_ahead_of_the_locked_revision_alone_makes_a_ready_final_operation(self):
        git = dict(repo="hoprnet", branch="release/4.0", crates=["hopr-lib"], locked="9e7372b04", head="88b935877")
        ops = rc.operations(plan([], git_updates=[git]))
        self.assertEqual([(o["kind"], o["status"]) for o in ops], [("final_repin", "ready")])
        self.assertIn("git hopr-lib 9e7372b04 -> 88b935877", rc.op_detail(ops[0]))


class GitPinnedSwaps(unittest.TestCase):
    def pin(self, target=None, step=True):
        return dict(file="Cargo.toml", line=97, key="hopr-lib", crate="hopr-lib", git="https://github.com/hoprnet/hoprnet",
                    ref="branch release/4.0", locked="4.0.5", target=target, step=step)

    def test_a_pin_to_a_repo_that_is_a_step_is_a_note_not_an_operation(self):
        self.assertEqual(rc.operations(plan([], pins=[self.pin()])), [])
        r = rc.status(plan([], pins=[self.pin()]))
        self.assertEqual(r["state"], "complete")
        self.assertEqual(r["git_pinned_swaps"], [dict(crate="hopr-lib", ref="branch release/4.0", locked="4.0.5",
                                                      note=r["git_pinned_swaps"][0]["note"])])
        self.assertIn("crates.io", r["git_pinned_swaps"][0]["note"])

    def test_a_pin_to_a_repo_that_is_not_a_step_stays_a_blocked_operation(self):
        ops = rc.operations(plan([], pins=[self.pin(step=False)]))
        self.assertEqual([(o["kind"], o["status"]) for o in ops], [("pin_swap", "blocked")])
        self.assertEqual(rc.status(plan([], pins=[self.pin(step=False)]))["git_pinned_swaps"], [])

    def test_a_pin_with_a_published_version_is_still_swapped(self):
        ops = rc.operations(plan([], pins=[self.pin(target="5.0.0")]))
        self.assertEqual([o["kind"] for o in ops], ["final_repin"])

    def test_the_notes_show_in_the_plain_report(self):
        self.assertIn("git pin kept: hopr-lib", rc.render(rc.status(plan([], pins=[self.pin()])), "plain"))


class ReadyPullRequests(unittest.TestCase):
    def test_gh_opens_a_ready_pr_unless_a_draft_is_asked_for(self):
        calls = []
        saved = rc.run
        rc.run = lambda *a, **kw: calls.append(a) or (0, "https://github.com/hoprnet/x/pull/1\n")
        try:
            rc.GhForge().create_pr("x", "head", "main", "t", "b")
            rc.GhForge().create_pr("x", "head", "main", "t", "b", draft=True)
        finally:
            rc.run = saved
        self.assertNotIn("--draft", calls[0])
        self.assertIn("--draft", calls[1])

    def test_the_api_opens_a_ready_pr_unless_a_draft_is_asked_for(self):
        http = FakeHttp({("POST", "/pulls"): (201, {"html_url": "https://github.com/hoprnet/x/pull/1"})})
        rc.ApiForge("t", http).create_pr("x", "head", "main", "t", "b")
        rc.ApiForge("t", http).create_pr("x", "head", "main", "t", "b", draft=True)
        self.assertFalse(http.calls[0][2]["draft"])
        self.assertTrue(http.calls[1][2]["draft"])


class OrderRule(unittest.TestCase):
    """The start repo's PR is never opened while the PR of the repo it waits for (hoprnet) is open."""

    def upstream(self):
        return dict(repo="hoprnet", branch="release/4.0", gaps=[],
                    lock_updates=[dict(crate="hopr-types", locked="2.3.0", target="2.3.2")])

    def git(self):
        return dict(repo="hoprnet", branch="release/4.0", crates=["hopr-lib"], locked="9e7372b04", head="88b935877")

    def test_only_the_upstream_operation_can_be_applied_while_its_pr_is_open(self):
        report = rc.status(plan([], upstream=[self.upstream()], git_updates=[self.git()]))
        self.assertEqual([(o["kind"], o["repo"]) for o in rc.ready_edit_ops(report)], [("repin", "hoprnet")])
        final = next(o for o in report["operations"] if o["kind"] == "final_repin")
        self.assertEqual(final["status"], "pending")

    def test_once_the_upstream_pr_is_merged_the_start_repo_operation_is_ready(self):
        report = rc.status(plan([], git_updates=[self.git()]))  # hoprnet has nothing left to change
        self.assertEqual([(o["kind"], o["repo"]) for o in rc.ready_edit_ops(report)], [("final_repin", "edge-client")])

    def test_blocked_and_release_operations_are_never_applied_as_prs(self):
        report = rc.status(plan([step("hopr-types", {"hopr-types": "5.0.0"}, todo={"hopr-types": "5.0.0"})],
                                pins=[dict(file="Cargo.toml", line=1, key="x", crate="x", git="g", ref="branch b", locked="1.0.0",
                                           target=None, step=False)]))
        self.assertEqual(rc.ready_edit_ops(report), [])

    def test_applied_operations_are_not_applied_twice_in_one_run(self):
        report = rc.status(plan([], upstream=[self.upstream()]))
        key = rc.op_key(report["operations"][0])
        self.assertEqual(rc.ready_edit_ops(report, done={key}), [])


class WaitForMerge(unittest.TestCase):
    def prs(self):
        return [dict(repo="hoprnet", url="https://github.com/hoprnet/hoprnet/pull/8593"),
                dict(repo="edge-client", url="https://github.com/hoprnet/edge-client/pull/234")]

    def run_wait(self, states, minutes=10):
        calls, now = [], [0.0]
        def state_of(repo, number):
            calls.append((repo, number))
            return states.pop(0) if isinstance(states, list) else states[(repo, number)]
        result = rc.wait_for_merge(self.prs(), minutes, poll=60, state_of=state_of, clock=lambda: now[0],
                                   sleep=lambda d: now.__setitem__(0, now[0] + d))
        return result, calls

    def test_pr_number_comes_from_the_url(self):
        self.assertEqual(rc.pr_number("https://github.com/hoprnet/hoprnet/pull/8593"), 8593)

    def test_returns_merged_when_every_pr_merged(self):
        result, _ = self.run_wait({("hoprnet", 8593): "merged", ("edge-client", 234): "merged"})
        self.assertEqual(result, "merged")

    def test_waits_until_a_pr_merges(self):
        result, calls = self.run_wait(["open", "open", "merged", "merged"])
        self.assertEqual(result, "merged")
        self.assertGreater(len(calls), 2)

    def test_a_pr_closed_without_a_merge_stops_the_wait(self):
        result, _ = self.run_wait({("hoprnet", 8593): "closed", ("edge-client", 234): "open"})
        self.assertEqual(result, "closed")

    def test_gives_up_after_the_time_limit(self):
        result, calls = self.run_wait({("hoprnet", 8593): "open", ("edge-client", 234): "open"}, minutes=3)
        self.assertEqual(result, "timeout")
        self.assertLess(len(calls), 20)


class SwapVersusRevision(unittest.TestCase):
    """Once a pinned crate is on crates.io, the pin becomes a version; the git revision is no longer moved."""

    def pin(self):
        return dict(file="Cargo.toml", line=18, key="hopr-lib", crate="hopr-lib", git="https://github.com/hoprnet/hoprnet",
                    ref="rev af80993c", locked="5.0.0-rc.5", target="5.0.0", step=True)

    def git(self):
        return dict(repo="hoprnet", branch="master", crates=["hopr-lib", "hopr-transport-mixer"], locked="af80993cc", head="88b935877",
                    edits=[dict(file="Cargo.toml", line=18, key="hopr-lib", crate="hopr-lib", current="rev af80993c",
                                required="88b935877aaa", swap="git_rev"),
                           dict(file="Cargo.toml", line=20, key="hopr-transport-mixer", crate="hopr-transport-mixer",
                                current="rev af80993c", required="88b935877aaa", swap="git_rev")])

    def test_a_swapped_crate_gets_no_rev_edit_and_no_git_update(self):
        ops = rc.operations(plan([], pins=[self.pin()], git_updates=[self.git()]))
        final = ops[-1]
        by_key = {e["key"]: e["swap"] for e in final["edits"]}
        self.assertEqual(by_key["hopr-lib"], "git_to_version")           # one edit per line, the version one
        self.assertEqual(by_key["hopr-transport-mixer"], "git_rev")      # still pinned: the revision moves
        self.assertEqual(final["git_updates"][0]["crates"], ["hopr-transport-mixer"])
        self.assertEqual([e["key"] for e in final["git_updates"][0]["edits"]], ["hopr-transport-mixer"])

    def test_lock_commands_update_the_swapped_crate_once(self):
        ops = rc.operations(plan([], pins=[self.pin()], git_updates=[self.git()]))
        cmds = rc.lock_commands(ops[-1])
        self.assertEqual(cmds.count("cargo update -p hopr-lib"), 1)      # from the version edit
        self.assertEqual(cmds.count("cargo update -p hopr-transport-mixer"), 1)

    def test_no_git_update_is_left_when_every_crate_of_the_group_is_swapped(self):
        git = dict(self.git(), crates=["hopr-lib"], edits=self.git()["edits"][:1])
        ops = rc.operations(plan([], pins=[self.pin()], git_updates=[git]))
        self.assertEqual(ops[-1].get("git_updates"), [])


class BreakingBumps(unittest.TestCase):
    def test_a_gap_is_breaking_when_the_required_version_is_on_another_line(self):
        self.assertTrue(rc.is_breaking("4.1.0", "5.0.0"))
        self.assertTrue(rc.is_breaking("0.35.0", "0.36.1"))     # 0.x: the minor is the breaking digit
        self.assertFalse(rc.is_breaking("=4.4.0", "4.4.1"))     # an exact pin moving within a line
        self.assertFalse(rc.is_breaking("0.35.0", "0.35.4"))
        self.assertFalse(rc.is_breaking("2.3.0", "2.4.0"))

    def test_the_next_breaking_version(self):
        self.assertEqual(rc.bump_breaking("4.4.1"), "5.0.0")
        self.assertEqual(rc.bump_breaking("0.29.0"), "0.30.0")
        self.assertEqual(rc.bump_breaking("0.0.3"), "0.0.4")

    def gap(self, crate="hopr-types", req="4.1.0", want="5.0.0", file="Cargo.toml"):
        return dict(file=file, table="dependencies", key=crate, crate=crate, req=req, want=want)

    def versions(self, c):
        return {"hopr-api": ["4.3.2", "4.4.0"]}.get(c, [])

    def test_a_published_crate_that_re_exports_a_breaking_dependency_gets_a_major_bump(self):
        s = step("hopr-api", {"hopr-api": "4.4.0"}, gaps=[self.gap()])
        got = rc.plan_bumps(s, self.versions, reexports=lambda dep, key: dep == "hopr-types")
        self.assertEqual(got["bump_crates"], ["hopr-api"])
        self.assertEqual(got["release_versions"], {"hopr-api": "5.0.0"})
        self.assertEqual(got["bump_info"]["hopr-api"]["level"], "major")
        self.assertIn("hopr-types", got["bump_info"]["hopr-api"]["reason"])

    def test_without_a_re_export_the_bump_stays_a_patch(self):
        s = step("hopr-api", {"hopr-api": "4.4.0"}, gaps=[self.gap()])
        got = rc.plan_bumps(s, self.versions, reexports=lambda dep, key: False)
        self.assertEqual(got["release_versions"], {"hopr-api": "4.4.1"})
        self.assertEqual(got["bump_info"]["hopr-api"]["level"], "patch")

    def test_a_non_breaking_gap_stays_a_patch_even_with_a_re_export(self):
        s = step("hopr-api", {"hopr-api": "4.4.0"}, gaps=[self.gap(req="=4.1.0", want="4.1.1")])
        got = rc.plan_bumps(s, self.versions, reexports=lambda dep, key: True)
        self.assertEqual(got["release_versions"], {"hopr-api": "4.4.1"})

    def test_an_unpublished_head_on_the_published_line_is_raised_to_the_next_major(self):
        s = step("hopr-api", {"hopr-api": "4.4.1"}, todo={"hopr-api": "4.4.1"}, gaps=[self.gap()])
        got = rc.plan_bumps(s, self.versions, reexports=lambda dep, key: True)
        self.assertEqual(got["release_versions"], {"hopr-api": "5.0.0"})
        self.assertNotIn("hopr-api", got["todo"])          # no longer released as it is
        self.assertEqual(got["bump_crates"], ["hopr-api"])

    def test_a_head_already_on_a_newer_line_is_left_alone(self):
        s = step("hopr-api", {"hopr-api": "5.0.0"}, todo={"hopr-api": "5.0.0"}, gaps=[self.gap()])
        got = rc.plan_bumps(s, self.versions, reexports=lambda dep, key: True)
        self.assertEqual(got["release_versions"], {"hopr-api": "5.0.0"})
        self.assertEqual(got["bump_crates"], [])
        self.assertEqual(got["todo"], {"hopr-api": "5.0.0"})

    def test_the_bump_level_is_in_the_repin_operation(self):
        types = step("hopr-types", {"hopr-types": "5.0.0"}, todo={"hopr-types": "5.0.0"})
        api = step("hopr-api", {"hopr-api": "4.4.0"}, gaps=[self.gap()], requires=["hopr-types"])
        api["bump_crates"], api["release_versions"] = ["hopr-api"], {"hopr-api": "5.0.0"}
        api["bump_info"] = {"hopr-api": dict(level="major", reason="re-exports hopr-types, which moves 4.1.0 -> 5.0.0")}
        ops = rc.operations(plan([types, api]))
        repin = next(o for o in ops if o["kind"] == "repin")
        self.assertEqual((repin["version_bumps"][0]["level"], repin["version_bumps"][0]["suggested"]), ("major", "5.0.0"))
        self.assertIn("major", rc.op_detail(repin))


class ReExports(unittest.TestCase):
    def setUp(self):
        import os, subprocess, tempfile
        self.tmp = Path(tempfile.mkdtemp())
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.sh = lambda *a: subprocess.run(a, cwd=self.tmp / "r", env=env, check=True, capture_output=True, text=True)
        (self.tmp / "r" / "src").mkdir(parents=True)
        self.saved = rc.WORKSPACE
        rc.WORKSPACE = self.tmp
        self.sh("git", "init", "-q", "-b", "main")

    def tearDown(self):
        import shutil
        rc.WORKSPACE = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, text):
        (self.tmp / "r" / "src" / "lib.rs").write_text(text)
        self.sh("git", "add", "-A")
        self.sh("git", "commit", "-q", "-m", "x")
        self.sh("git", "update-ref", "refs/remotes/origin/main", "HEAD")

    def test_pub_use_of_the_dependency_is_a_re_export(self):
        self.commit("pub use hopr_types::{Address, Hash};\n")
        self.assertTrue(rc.reexports_dep("r", "origin/main", "hopr-types", "hopr-types"))

    def test_a_plain_use_is_not(self):
        self.commit("use hopr_types::Address;\nfn f(_: Address) {}\n")
        self.assertFalse(rc.reexports_dep("r", "origin/main", "hopr-types", "hopr-types"))

    def test_a_renamed_dependency_is_found_by_its_key(self):
        self.commit("pub use hopr_utils::thing;\n")
        self.assertTrue(rc.reexports_dep("r", "origin/main", "hopr-utilities", "hopr-utils"))


class TokenSources(unittest.TestCase):
    def source(self, **kw):
        calls = kw.pop("calls", [])
        outputs = kw.pop("outputs", [(0, "tok-1\n", "")])
        now = kw.pop("now", [0.0])

        def run_cmd(cmd):
            calls.append(cmd)
            return outputs[min(len(calls) - 1, len(outputs) - 1)]
        return rc.TokenSource(clock=lambda: now[0], run_cmd=run_cmd, **kw), calls, now

    def test_the_env_token_wins_and_the_command_is_not_run(self):
        src, calls, _ = self.source(env_token="env-tok", cmd="print-token")
        self.assertEqual(src.get(), "env-tok")
        self.assertEqual(calls, [])

    def test_the_command_output_is_the_token_and_is_reused_until_it_is_old(self):
        src, calls, now = self.source(cmd="print-token", outputs=[(0, "tok-1\n", ""), (0, "tok-2\n", "")])
        self.assertEqual((src.get(), src.get()), ("tok-1", "tok-1"))
        self.assertEqual(len(calls), 1)
        now[0] += 49 * 60
        self.assertEqual(src.get(), "tok-1")
        now[0] += 2 * 60                       # past 50 minutes: installation tokens last an hour
        self.assertEqual(src.get(), "tok-2")
        self.assertEqual(len(calls), 2)

    def test_refresh_after_a_401_asks_the_command_again_and_drops_the_env_token(self):
        src, calls, _ = self.source(env_token="old", cmd="print-token", outputs=[(0, "new\n", "")])
        self.assertEqual(src.get(), "old")
        self.assertEqual(src.refresh(), "new")
        self.assertEqual(src.get(), "new")

    def test_refresh_with_only_an_env_token_says_what_is_missing(self):
        src, _, _ = self.source(env_token="old")
        with self.assertRaises(rc.Stop) as e:
            src.refresh()
        self.assertIn("--token-cmd", str(e.exception))

    def test_a_token_file_is_read_every_time(self):
        import tempfile
        f = Path(tempfile.mkdtemp()) / "token"
        f.write_text("file-1\n")
        src = rc.TokenSource(file=str(f))
        self.assertEqual(src.get(), "file-1")
        f.write_text("file-2\n")
        self.assertEqual(src.get(), "file-2")
        f.write_text("\n")
        with self.assertRaises(rc.Stop):
            src.get()

    def test_a_failing_command_reports_its_stderr_and_never_its_output(self):
        src, _, _ = self.source(cmd="print-token", outputs=[(3, "SECRET-OUTPUT", "helper: not logged in\nmore")])
        with self.assertRaises(rc.Stop) as e:
            src.get()
        self.assertIn("exit 3", str(e.exception))
        self.assertIn("helper: not logged in", str(e.exception))
        self.assertNotIn("SECRET-OUTPUT", str(e.exception))

    def test_no_token_or_more_than_one_line_is_an_error(self):
        for out in ("", "\n", "line one\nline two\n"):
            src, _, _ = self.source(cmd="print-token", outputs=[(0, out, "")])
            with self.assertRaises(rc.Stop):
                src.get()

    def test_no_source_at_all_names_every_option(self):
        with self.assertRaises(rc.Stop) as e:
            rc.TokenSource().get()
        for name in ("GH_TOKEN", "--token-cmd", "--token-file"):
            self.assertIn(name, str(e.exception))


class ApiForgeTokens(unittest.TestCase):
    def test_a_401_refreshes_the_token_and_retries_once(self):
        answers = iter([(401, {"message": "Bad credentials"}), (200, {"workflow_runs": [{"id": 5}]})])
        calls = []

        def http(method, url, body=None):
            calls.append(url)
            return next(answers)
        src = rc.TokenSource(env_token="old", cmd="print-token", run_cmd=lambda c: (0, "new\n", ""))
        self.assertEqual(rc.ApiForge(src, http).run_ids("r", "release.yaml", "main"), {5})
        self.assertEqual(len(calls), 2)
        self.assertEqual(src.get(), "new")

    def test_a_401_with_nothing_to_refresh_it_with_ends_the_run_with_a_clear_message(self):
        http = FakeHttp({("GET", "/runs"): (401, {"message": "Bad credentials"})})
        with self.assertRaises(rc.Stop) as e:
            rc.ApiForge(rc.TokenSource(env_token="old"), http).run_ids("r", "w", "main")
        self.assertIn("--token-cmd", str(e.exception))

    def test_git_gets_the_current_token_each_time(self):
        import base64
        out = iter([(0, "t1\n", ""), (0, "t2\n", "")])
        src = rc.TokenSource(cmd="print-token", run_cmd=lambda c: next(out))
        forge = rc.ApiForge(src, FakeHttp({}))

        def token_in(env):
            return base64.b64decode(env["GIT_CONFIG_VALUE_0"].split()[-1]).decode().split(":", 1)[1]
        self.assertEqual(token_in(forge.git_env()), "t1")
        src.refresh()
        self.assertEqual(token_in(forge.git_env()), "t2")


class ForgeChoiceWithTokenSources(unittest.TestCase):
    def test_an_explicit_token_command_means_the_api_even_when_gh_is_there(self):
        self.assertEqual(rc.choose_forge(which=lambda n: "/bin/gh", gh_ok=lambda: True, env={}, token_cmd="print-token"), "api")
        self.assertEqual(rc.choose_forge(which=lambda n: None, gh_ok=lambda: False, env={}, token_file="/run/token"), "api")

    def test_forcing_gh_still_needs_gh(self):
        with self.assertRaises(rc.Stop):
            rc.choose_forge(which=lambda n: None, gh_ok=lambda: False, env={}, force="gh", token_cmd="print-token")

    def test_the_error_without_any_access_names_the_new_options(self):
        with self.assertRaises(rc.Stop) as e:
            rc.choose_forge(which=lambda n: None, gh_ok=lambda: False, env={})
        self.assertIn("--token-cmd", str(e.exception))

    def test_resume_keeps_the_token_options(self):
        from types import SimpleNamespace
        a = SimpleNamespace(workspace=None, forge="auto", output="json", release_type="patch", max_releases=None,
                            token_cmd="printf 'x' | cat", token_file="/run/token")
        flags = rc.resume_flags(a)
        self.assertIn("--token-cmd 'printf '\"'\"'x'\"'\"' | cat'", flags)
        self.assertIn("--token-file /run/token", flags)


class TokenCommandRobustness(unittest.TestCase):
    def test_a_token_command_that_hangs_is_stopped(self):
        import time
        t0 = time.monotonic()
        with self.assertRaises(rc.Stop) as e:
            rc.run_token_command("sleep 5", timeout=0.3)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertIn("did not finish", str(e.exception))

    def test_a_token_command_gets_no_input_and_its_output_is_returned(self):
        code, out, err = rc.run_token_command("cat >/dev/null; echo tok", timeout=5)  # cat would block on a terminal
        self.assertEqual((code, out.strip()), (0, "tok"))

    def test_an_empty_token_file_is_read_again_before_the_run_stops(self):
        reads = iter(["", "", "tok-file\n"])
        slept = []
        src = rc.TokenSource(file="/run/token", read_file=lambda p: next(reads), sleep=slept.append)
        self.assertEqual(src.get(), "tok-file")
        self.assertEqual(len(slept), 2)

    def test_a_token_file_that_stays_empty_still_stops_the_run(self):
        src = rc.TokenSource(file="/run/token", read_file=lambda p: "", sleep=lambda d: None)
        with self.assertRaises(rc.Stop):
            src.get()

    def test_a_gh_that_cannot_launch_does_not_block_a_token_source(self):
        def broken(*a, **kw):
            raise OSError("exec format error")
        saved = rc.run
        rc.run = broken
        try:
            self.assertEqual(rc.choose_forge(which=lambda n: "/bin/gh", env={}, token_cmd="print-token"), "api")
            self.assertEqual(rc.choose_forge(which=lambda n: "/bin/gh", env={}, force="api", token_cmd="print-token"), "api")
        finally:
            rc.run = saved

    def test_a_token_file_path_is_made_absolute_for_the_resume_command(self):
        from types import SimpleNamespace
        a = SimpleNamespace(workspace=None, forge="auto", output="json", release_type="patch", max_releases=None,
                            token_cmd=None, token_file="rel/token")
        import os
        self.assertIn(f"--token-file {os.path.abspath('rel/token')}", rc.resume_flags(a))

    def test_forge_gh_cannot_be_combined_with_a_token_option(self):
        with self.assertRaises(rc.Stop) as e:
            rc.choose_forge(which=lambda n: "/bin/gh", gh_ok=lambda: True, env={}, force="gh", token_cmd="print-token")
        self.assertIn("--forge gh", str(e.exception))
        self.assertIn("--token-cmd", str(e.exception))


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
        self.assertEqual((method, body["ref"], body["inputs"]), ("POST", "compat/v4", {"release_type": "patch"}))
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
