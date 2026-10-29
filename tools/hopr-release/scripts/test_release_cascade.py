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


def plan(steps, repin=(), fixed=(), locked=None, target=None):
    return dict(start=("edge-client", "main"), steps=steps, fixed=list(fixed), pinned=[], repin=list(repin),
                locked=locked or {}, target=target or {})


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
