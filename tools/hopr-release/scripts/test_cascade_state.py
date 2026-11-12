"""Offline coordination tests: python3 -I tools/hopr-release/scripts/test_cascade_state.py."""
import contextlib
import copy
import importlib.util
import io
import json
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("cascade_state", Path(__file__).with_name("cascade_state.py"))
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)
A, B, C = "hoprnet/a@main", "hoprnet/b@main", "hoprnet/c@main"


def empty_state():
    return dict(version=1, runs={}, routes={}, events={}, resources={})


class SharedStore:
    def __init__(self, barrier=None):
        self.state, self.sequence = empty_state(), 0
        self.lock, self.barrier = threading.Lock(), barrier
        self.reads, self.conflicts, self.writes = 0, 0, 0

    def read(self):
        with self.lock:
            result = copy.deepcopy(self.state), self.sequence
            self.reads += 1
            wait = self.barrier is not None and self.reads <= 2
        if wait:
            self.barrier.wait(timeout=5)
        return result

    def write(self, state, sequence):
        with self.lock:
            if sequence != self.sequence:
                self.conflicts += 1
                raise cs.Conflict("sequence_conflict")
            self.state, self.sequence = copy.deepcopy(state), self.sequence + 1
            self.writes += 1


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.store = SharedStore()
        self.registry = cs.Registry(self.store, clock=lambda: self.now)

    def reserve(self, targets=(A,), topic="topic", event="1", router="router"):
        return self.registry.route(topic, router, event, targets)

    def own(self, targets=(A,), topic="topic", event="1", owner="owner"):
        result = self.reserve(targets, topic, event)
        self.registry.bind(result["run_id"], "router", owner)
        return result["run_id"], owner, 1

    def error(self, code, function, *args, **kwargs):
        with self.assertRaisesRegex(cs.CoordinationError, "^" + code + "$"):
            function(*args, **kwargs)

    def test_routers_race_one_launch_and_persistent_inbox(self):
        store = SharedStore(threading.Barrier(2))
        registries = [cs.Registry(store, clock=lambda: 1000) for _ in range(2)]
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(r.route, "topic", f"router-{i}", str(i), [A]) for i, r in enumerate(registries)]
            results = [f.result(timeout=10) for f in futures]
        self.assertEqual(sorted(r["action"] for r in results), ["launch", "pending"])
        self.assertEqual(len({r["run_id"] for r in results}), 1)
        self.assertEqual(store.conflicts, 1)
        self.assertEqual(store.reads, 3)
        winner = next(r for r in results if r["action"] == "launch")
        registry = cs.Registry(store, clock=lambda: 1000)
        registry.bind(winner["run_id"], winner["router_session"], "owner")
        self.assertEqual(set(registry.inbox(winner["run_id"], "owner", 1)["event_ids"]), {"0", "1"})

    def test_same_event_race_never_launches_twice(self):
        store = SharedStore(threading.Barrier(2))
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(cs.Registry(store).route, "topic", f"r{i}", "same", [A]) for i in range(2)]
            results = [f.result(timeout=10) for f in futures]
        self.assertEqual(sorted(r["action"] for r in results), ["duplicate", "launch"])

    def test_overlapping_targets_unify_and_reserve_entire_union(self):
        first = self.reserve([A, B])
        second = self.reserve([B, C], topic="other", event="2", router="other")
        self.assertEqual((first["run_id"], "pending"), (second["run_id"], second["action"]))
        self.assertEqual(self.store.state["runs"][first["run_id"]]["targets"], [A, B, C])
        self.assertEqual(set(self.store.state["resources"].values()), {first["run_id"]})

    def test_racing_overlapping_target_sets_recompute_union(self):
        store = SharedStore(threading.Barrier(2))
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(cs.Registry(store).route, f"topic-{i}", f"r{i}", str(i), targets)
                       for i, targets in enumerate(([A, B], [B, C]))]
            results = [f.result(timeout=10) for f in futures]
        self.assertEqual(sorted(r["action"] for r in results), ["launch", "pending"])
        self.assertEqual(len(store.state["runs"]), 1)
        self.assertEqual(next(iter(store.state["runs"].values()))["targets"], [A, B, C])
        self.assertEqual(store.conflicts, 1)

    def test_overlap_across_runs_rejects_without_partial_write(self):
        self.own([A])
        self.own([B], topic="other", event="2", owner="other")
        before = copy.deepcopy(self.store.state)
        self.error("overlapping_runs", self.reserve, [A, B, C], topic="third", event="3")
        self.assertEqual(before, self.store.state)

    def test_route_conflicts_with_resource_owner_even_for_same_owner_session(self):
        self.own([A])
        self.own([B], topic="other", event="2")
        self.error("overlapping_runs", self.reserve, [B], event="3")

    def test_alias_routes_followups_and_ack_is_idempotent(self):
        owner = self.own()
        self.registry.alias(*owner, "AI Automation/output")
        result = self.reserve([B], topic="AI Automation/output", event="2", router="new-router")
        self.assertEqual((result["action"], result["owner_session"]), ("forward", "owner"))
        self.assertEqual(self.registry.inbox(*owner), {"event_ids": ["1", "2"]})
        self.registry.ack(*owner, "1")
        self.registry.ack(*owner, "1")
        self.assertEqual(self.registry.inbox(*owner), {"event_ids": ["2"]})
        self.error("unknown_event", self.registry.ack, *owner, "unknown")
        self.error("pending_events", self.registry.complete, *owner)

    def test_alias_cannot_replace_active_route(self):
        owner = self.own()
        self.own([B], topic="other", event="2")
        self.error("route_conflict", self.registry.alias, *owner, "other")

    def test_bind_only_winning_router_and_once(self):
        rid = self.reserve()["run_id"]
        self.error("not_router", self.registry.bind, rid, "loser", "owner")
        first = self.registry.bind(rid, "router", "owner")
        self.assertEqual(first, self.registry.bind(rid, "router", "owner"))
        self.error("already_bound", self.registry.bind, rid, "router", "other")

    def test_handover_invalidates_stale_owner_and_generation(self):
        owner = self.own()
        result = self.registry.handover(*owner, "next")
        self.assertEqual(result["generation"], 2)
        self.error("not_owner", self.registry.renew, *owner)
        self.error("stale_generation", self.registry.claim_operation, owner[0], "next", 1, A)
        self.error("already_bound", self.registry.bind, owner[0], "router", "owner")
        self.registry.claim_operation(owner[0], "next", 2, A)
        self.registry.handover(owner[0], "next", 2, "owner")
        self.error("stale_generation", self.registry.claim_operation, *owner, A)
        self.assertEqual(self.registry.inbox(owner[0], "owner", 3), {"event_ids": ["1"]})

    def test_one_operation_one_pr_without_revision_or_kind_keys(self):
        owner = self.own()
        initial = self.registry.claim_operation(*owner, "HoprNet/A@main")
        recorded = self.registry.record_pr(*owner, A, "https://github.com/hoprnet/a/pull/42")
        self.assertEqual(initial["operation_id"], recorded["operation_id"])
        self.assertEqual(recorded, self.registry.claim_operation(*owner, A))
        self.assertEqual(recorded, self.registry.record_pr(*owner, A, "https://github.com/HoprNet/A/pull/42"))
        self.error("pr_conflict", self.registry.record_pr, *owner, A, "https://github.com/hoprnet/a/pull/43")
        for url in ("https://github.com/other/a/pull/42", "http://github.com/hoprnet/a/pull/42",
                    "https://github.com/hoprnet/a/issues/42", "https://github.com/hoprnet/a/pull/42?secret=x"):
            self.error("invalid_pr_url", self.registry.record_pr, *owner, A, url)

    def test_claim_exclusive_until_parent_complete(self):
        first = self.own()
        second = self.own([B], topic="other", event="2", owner="other")
        self.registry.claim_operation(*first, A)
        self.registry.finish_operation(*first, A, "skipped")
        self.error("resource_reserved", self.registry.claim_operation, *second, A)
        self.registry.ack(*first, "1")
        self.registry.complete(*first)
        self.registry.claim_operation(*second, A)

    def test_claim_race_across_runs_has_one_global_winner(self):
        first = self.own()
        second = self.own([B], topic="other", event="2", owner="other")
        self.store.reads = 0
        self.store.barrier = threading.Barrier(2)
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.registry.claim_operation, *owner, C) for owner in (first, second)]
            successes, failures = [], []
            for future in futures:
                try:
                    successes.append(future.result(timeout=10))
                except cs.CoordinationError as error:
                    failures.append(str(error))
        self.assertEqual(len(successes), 1)
        self.assertEqual(failures, ["resource_reserved"])
        self.assertEqual(self.store.conflicts, 1)

    def test_complete_requires_every_target_and_extra_claimed_operation(self):
        owner = self.own([A, B])
        self.registry.ack(*owner, "1")
        self.registry.claim_operation(*owner, A)
        self.registry.finish_operation(*owner, A, "skipped")
        self.error("unfinished_operations", self.registry.complete, *owner)
        self.registry.claim_operation(*owner, B)
        self.registry.claim_operation(*owner, C)
        self.registry.finish_operation(*owner, B, "merged")
        self.error("unfinished_operations", self.registry.complete, *owner)
        self.registry.finish_operation(*owner, C, "skipped")
        result = self.registry.complete(*owner)
        self.now += 10000
        self.assertEqual(result, self.registry.complete(*owner))
        replay = self.reserve(event="1", targets=[C], topic="other")
        self.assertEqual((replay["action"], replay["run_id"]), ("duplicate", owner[0]))
        self.assertEqual(self.reserve(event="2")["action"], "launch")

    def test_terminal_outcome_is_idempotent_but_cannot_change(self):
        owner = self.own()
        self.registry.claim_operation(*owner, A)
        result = self.registry.finish_operation(*owner, A, "skipped")
        self.assertEqual(result, self.registry.finish_operation(*owner, A, "skipped"))
        self.error("outcome_conflict", self.registry.finish_operation, *owner, A, "merged")

    def test_expiry_cannot_renew_bind_steal_or_handover(self):
        owner = self.own()
        self.now += 900
        self.error("lease_expired", self.registry.renew, *owner)
        self.error("lease_expired", self.registry.check, *owner)
        self.error("lease_expired", self.registry.handover, *owner, "replacement")
        self.error("lease_expired", self.reserve, event="2", router="replacement")
        self.assertEqual(len(self.store.state["runs"]), 1)
        self.assertNotIn("2", self.store.state["events"])
        self.error("lease_expired", self.registry.bind, owner[0], "router", "owner")
        self.assertEqual(self.reserve(event="1")["action"], "duplicate")

    def test_renew_before_expiry_extends_same_lease(self):
        owner = self.own()
        self.now += 899
        self.assertEqual(self.registry.renew(*owner)["lease_until"], self.now + 900)

    def test_mutation_blocks_handover_complete_finish_and_second_begin(self):
        owner = self.own()
        self.registry.ack(*owner, "1")
        self.registry.claim_operation(*owner, A)
        marker = self.registry.begin_mutation(*owner, A)
        self.error("mutation_blocked", self.registry.handover, *owner, "other")
        self.error("mutation_blocked", self.registry.complete, *owner)
        self.error("mutation_blocked", self.registry.finish_operation, *owner, A, "skipped")
        self.error("mutation_blocked", self.registry.begin_mutation, *owner, A)
        self.error("mutation_mismatch", self.registry.end_mutation, *owner, A, "wrong")
        self.registry.end_mutation(*owner, A, marker["mutation_id"])
        self.error("mutation_already_succeeded", self.registry.begin_mutation, *owner, A)
        self.registry.handover(*owner, "other")

    def test_mutation_and_handover_race_never_both_succeed(self):
        owner = self.own()
        self.registry.claim_operation(*owner, A)
        self.store.reads = 0
        self.store.barrier = threading.Barrier(2)
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.registry.begin_mutation, *owner, A),
                       pool.submit(self.registry.handover, *owner, "next")]
            successes, failures = [], []
            for future in futures:
                try:
                    successes.append(future.result(timeout=10))
                except cs.CoordinationError as error:
                    failures.append(str(error))
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn(failures[0], ("not_owner", "mutation_blocked"))
        self.assertEqual(self.store.conflicts, 1)

    def test_failed_exec_exception_timeout_and_nonzero_leave_marker(self):
        for failure in (subprocess.CompletedProcess([], 2), OSError("sensitive diagnostic"), subprocess.TimeoutExpired("sensitive argv", 1)):
            with self.subTest(failure=type(failure).__name__):
                self.setUp()
                owner = self.own()
                self.registry.claim_operation(*owner, A)
                options = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch.object(cs.subprocess, "run", **options) as run:
                    self.error("mutation_uncertain", self.registry.exec, *owner, A, ["sensitive-command"])
                    self.error("mutation_blocked", self.registry.exec, *owner, A, ["sensitive-command"])
                    self.assertEqual(run.call_count, 1)
                self.assertEqual(self.registry.check(*owner)["mutation"]["state"], "uncertain")
                self.now += 10000
                self.error("lease_expired", self.registry.renew, *owner)
                self.assertIsNotNone(self.store.state["runs"][owner[0]]["mutation"])

    def test_process_crash_marker_survives_a_new_registry_instance(self):
        owner = self.own()
        self.registry.claim_operation(*owner, A)
        self.registry.begin_mutation(*owner, A)
        replacement = cs.Registry(self.store, clock=lambda: self.now)
        self.error("mutation_blocked", replacement.exec, *owner, A, ["command"])
        self.error("mutation_blocked", replacement.handover, *owner, "next")

    def test_successful_exec_no_shell_output_and_no_duplicate_effect(self):
        owner = self.own()
        self.registry.claim_operation(*owner, A)
        with patch.object(cs.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            result = self.registry.exec(*owner, A, ["command", "private-argument"], timeout=3)
            self.assertEqual(result["status"], "succeeded")
            self.assertIsNone(self.registry.check(*owner)["mutation"])
            self.error("mutation_already_succeeded", self.registry.exec, *owner, A, ["command"])
            self.assertEqual(run.call_count, 1)
            self.assertFalse(run.call_args.kwargs["shell"])
            self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_real_exec_runs_standard_library_child(self):
        owner = self.own()
        self.registry.claim_operation(*owner, A)
        result = self.registry.exec(*owner, A, [sys.executable, "-I", "-c", "print('suppressed')"])
        self.assertEqual(result["status"], "succeeded")

    def test_uncertain_write_does_not_retry_or_execute_command(self):
        owner = self.own()
        self.registry.claim_operation(*owner, A)
        with patch.object(self.store, "write", side_effect=cs.CoordinationError("uncertain_write")) as write:
            with patch.object(cs.subprocess, "run") as run:
                self.error("uncertain_write", self.registry.exec, *owner, A, ["command"])
            self.assertEqual(write.call_count, 1)
            run.assert_not_called()

    def test_uncertain_begin_commit_never_starts_child_and_remains_blocked(self):
        owner = self.own()
        self.registry.claim_operation(*owner, A)
        write = self.store.write

        def delivered_but_uncertain(state, sequence):
            write(state, sequence)
            raise cs.CoordinationError("uncertain_write")

        with patch.object(self.store, "write", side_effect=delivered_but_uncertain) as attempted:
            with patch.object(cs.subprocess, "run") as run:
                self.error("uncertain_write", self.registry.exec, *owner, A, ["command"])
                run.assert_not_called()
            self.assertEqual(attempted.call_count, 1)
        self.error("mutation_blocked", self.registry.exec, *owner, A, ["command"])

    def test_uncertain_end_write_never_repeats_child(self):
        owner = self.own()
        self.registry.claim_operation(*owner, A)
        write = self.store.write
        attempts = []

        def uncertain_success(state, sequence):
            attempts.append(sequence)
            if len(attempts) == 2:
                raise cs.CoordinationError("uncertain_write")
            write(state, sequence)

        with patch.object(self.store, "write", side_effect=uncertain_success):
            with patch.object(cs.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                self.error("uncertain_write", self.registry.exec, *owner, A, ["command"])
                self.error("mutation_blocked", self.registry.exec, *owner, A, ["command"])
                self.assertEqual(run.call_count, 1)
        self.assertEqual(len(attempts), 2)
        self.assertIsNotNone(self.registry.check(*owner)["mutation"])

    def test_bounded_conflicts_recompute_and_do_not_execute(self):
        owner = self.own()
        self.registry.claim_operation(*owner, A)
        registry = cs.Registry(self.store, clock=lambda: self.now, retries=2)
        with patch.object(self.store, "write", side_effect=cs.Conflict("sequence_conflict")) as write:
            with patch.object(cs.subprocess, "run") as run:
                self.error("cas_retries_exhausted", registry.exec, *owner, A, ["command"])
            self.assertEqual(write.call_count, 2)
            run.assert_not_called()

    def test_missing_and_invalid_state_never_auto_initialize(self):
        with patch.object(self.store, "read", side_effect=cs.CoordinationError("store_read_failed")):
            self.error("store_read_failed", self.reserve)
        malformed = [None, {}, {**empty_state(), "version": 2}, {**empty_state(), "runs": []},
                     {**empty_state(), "unknown": float("nan")},
                     {**empty_state(), "resources": {A: "missing"}}]
        owner = self.own()
        broken = copy.deepcopy(self.store.state)
        broken["resources"].clear()
        malformed.append(broken)
        broken = copy.deepcopy(self.store.state)
        broken["runs"][owner[0]]["lease_until"] = float("nan")
        malformed.append(broken)
        with patch.object(self.store, "write") as write:
            for state in malformed:
                with self.subTest(state=type(state).__name__):
                    self.store.state = state
                    self.error("invalid_registry", self.reserve)
            write.assert_not_called()

    def test_distinct_repairs_are_allowed_but_repeated_actions_are_not(self):
        owner = self.own()
        self.registry.claim_operation(*owner, A)
        with patch.object(cs.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as execute:
            self.registry.exec(*owner, A, ["command"], action_id="create-pr")
            self.registry.exec(*owner, A, ["command"], action_id="format-at-reviewed-head")
            self.error("mutation_already_succeeded", self.registry.exec, *owner, A, ["command"], action_id="create-pr")
            self.assertEqual(execute.call_count, 2)

    def test_long_guard_renews_ownership(self):
        import time
        registry = cs.Registry(self.store, lease_ttl=0.3)
        run_id = registry.route("topic", "router", "1", [A])["run_id"]
        owner = (run_id, "owner", 1)
        registry.bind(run_id, "router", "owner")
        registry.claim_operation(*owner, A)
        before = self.store.writes
        with registry.guard(*owner, A, "build"):
            time.sleep(0.45)
        self.assertGreater(self.store.writes, before + 2)
        self.assertIsNone(registry.check(*owner)["mutation"])

    def test_guard_renewal_failure_does_not_clear_uncertainty(self):
        registry = cs.Registry(self.store, lease_ttl=0.3)
        run_id = registry.route("topic", "router", "1", [A])["run_id"]
        owner = (run_id, "owner", 1)
        registry.bind(run_id, "router", "owner")
        registry.claim_operation(*owner, A)
        failed = threading.Event()

        def renewal_failure(*args):
            failed.set()
            raise cs.CoordinationError("store_read_failed")

        with patch.object(registry, "renew", side_effect=renewal_failure):
            with self.assertRaisesRegex(cs.CoordinationError, "lease_renewal_failed"):
                with registry.guard(*owner, A, "push"):
                    self.assertTrue(failed.wait(timeout=1))
        self.assertIsNotNone(self.store.state["runs"][run_id]["mutation"])

    def test_push_preserves_remote_commit_without_force(self):
        owner = self.own()
        self.registry.claim_operation(*owner, A)
        expected = "a" * 40
        branch = "chore/release-cascade-fix"
        results = [subprocess.CompletedProcess([], 0, "", ""),
                   subprocess.CompletedProcess([], 0, "https://github.com/hoprnet/a.git\n", ""),
                   subprocess.CompletedProcess([], 0, expected + "\trefs/heads/" + branch, ""),
                   subprocess.CompletedProcess([], 0, "", ""), subprocess.CompletedProcess([], 0, "", "")]
        with patch.object(cs.subprocess, "run", side_effect=results) as git:
            self.registry.push(*owner, A, "/scratch/repo", branch, expected, "format-" + expected)
        push = git.call_args_list[-1].args[0]
        self.assertEqual(push[-3:], ["push", "origin", "HEAD:refs/heads/" + branch])
        self.assertNotIn("--force", push)
        self.assertIsNone(self.registry.check(*owner)["mutation"])

    def test_push_stops_when_remote_changes_or_remote_commit_is_missing(self):
        for changed in (True, False):
            self.setUp()
            owner = self.own()
            self.registry.claim_operation(*owner, A)
            expected = "a" * 40
            results = [subprocess.CompletedProcess([], 0, "", ""),
                       subprocess.CompletedProcess([], 0, "https://github.com/hoprnet/a", ""),
                       subprocess.CompletedProcess([], 0, ("b" if changed else "a") * 40 + "\trefs/heads/x", ""),
                       subprocess.CompletedProcess([], 1, "", "")]
            with patch.object(cs.subprocess, "run", side_effect=results) as git:
                self.error("remote_head_changed" if changed else "remote_commit_not_preserved", self.registry.push,
                           *owner, A, "/scratch/repo", "chore/release-cascade-fix", expected, "format")
                self.assertFalse(any("push" in call.args[0] for call in git.call_args_list))


class VfsTests(unittest.TestCase):
    def fake_runner(self, *, stdout="downloaded seq=7", stderr="", failure=None, content=None,
                    url="https://vfs.example/files/state"):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            self.assertEqual(argv[:3], ["auggie", "cloud", "vfs"])
            self.assertTrue(kwargs["capture_output"])
            if argv[3] == "get-url":
                return subprocess.CompletedProcess(argv, 0, url + "\n", "")
            if argv[3] == "get":
                Path(argv[-1]).write_text(json.dumps(empty_state()) if content is None else content)
                return subprocess.CompletedProcess(argv, 0, stdout, stderr)
            self.assertEqual(argv[3], "put")
            self.assertTrue(Path(argv[4]).exists())
            if isinstance(failure, Exception):
                raise failure
            return subprocess.CompletedProcess(argv, 1 if failure else 0, "", failure or "")
        return calls, run

    def test_remote_first_read_and_conditional_org_put(self):
        calls, runner = self.fake_runner(stdout="", stderr="Downloaded state seq=7")
        store = cs.VfsStore("/vfs/tenant/org", runner=runner)
        state, sequence = store.read()
        self.assertEqual(sequence, 7)
        self.assertEqual(calls[0][4], "/vfs/tenant/org/" + cs.REGISTRY_PATH)
        self.assertEqual(calls[1][4], "https://vfs.example/files/state")
        store.write(state, sequence)
        self.assertEqual(calls[2][5:], [cs.REGISTRY_PATH, "--filesystem-id", "org", "--base-seq", "7"])
        self.assertFalse(Path(calls[1][-1]).exists())
        self.assertFalse(Path(calls[2][4]).exists())

    def test_signed_or_credential_urls_never_enter_subprocess_argv(self):
        for url in ("https://vfs.example/files/state?signature=not-a-real-secret",
                    "https://user:dummy@vfs.example/files/state", "https://vfs.example/files/state#private",
                    "https://vfs.example/not-a-durable-file"):
            calls, runner = self.fake_runner(url=url)
            with self.assertRaisesRegex(cs.CoordinationError, "store_read_failed"):
                cs.VfsStore("/vfs/org", runner=runner).read()
            self.assertEqual(len(calls), 1)

    def test_missing_ambiguous_sequence_and_invalid_json_fail_closed(self):
        for options in ({"stdout": "downloaded"}, {"stdout": "seq=7invalid"}, {"stdout": "seq=7", "stderr": "seq=8"}, {"content": "not json"},
                        {"content": "{}"}, {"content": '{"version": 1, "version": 1, "runs": {}, "events": {}, "resources": {}, "routes": {}}'}):
            with self.subTest(options=options):
                calls, runner = self.fake_runner(**options)
                with self.assertRaises(cs.CoordinationError):
                    cs.VfsStore("/vfs/org", runner=runner).read()
                self.assertEqual(len(calls), 2)

    def test_explicit_conflict_fixtures_only(self):
        for diagnostic in ("Error: sequence conflict", "Error: base-seq mismatch", "Precondition failed", "sequence mismatch",
                           "VFS command failed: API call failed: 409 Conflict: VFS upload-file failed"):
            with self.subTest(diagnostic=diagnostic):
                _, runner = self.fake_runner(failure=diagnostic)
                with self.assertRaises(cs.Conflict):
                    cs.VfsStore("/vfs/org", runner=runner).write(empty_state(), 7)
        for diagnostic in ("network timeout", "network timeout after sequence conflict", "conflict in remote transport", "permission denied: private-value",
                           subprocess.TimeoutExpired("private command", 30), OSError("private-value")):
            with self.subTest(diagnostic=type(diagnostic).__name__):
                _, runner = self.fake_runner(failure=diagnostic)
                with self.assertRaisesRegex(cs.CoordinationError, "^uncertain_write$") as caught:
                    cs.VfsStore("/vfs/org", runner=runner).write(empty_state(), 7)
                self.assertNotIsInstance(caught.exception, cs.Conflict)

    def test_environment_scope_and_no_local_path_fallback(self):
        with patch.dict(cs.os.environ, {"AUGMENT_VFS_DIR": "/vfs/tenant/"}, clear=True):
            self.assertEqual(cs.VfsStore().scope_prefix, "/vfs/tenant/org")
        for prefix in ("localpath/org", "/vfs/org/../org", "/vfs/tenant", ""):
            with self.assertRaisesRegex(cs.CoordinationError, "invalid_scope_prefix"):
                cs.VfsStore(prefix)
        with patch.dict(cs.os.environ, {}, clear=True):
            with self.assertRaisesRegex(cs.CoordinationError, "invalid_scope_prefix"):
                cs.VfsStore()

    def test_read_subprocess_errors_and_diagnostics_are_sanitized(self):
        for failure in (OSError("private-value"), subprocess.TimeoutExpired("private command", 30)):
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaisesRegex(cs.CoordinationError, "^store_read_failed$"):
                    cs.VfsStore("/vfs/org", runner=lambda *a, **k: (_ for _ in ()).throw(failure)).read()
        with self.assertRaisesRegex(cs.CoordinationError, "^store_read_failed$"):
            cs.VfsStore("/vfs/org", runner=lambda *a, **k: subprocess.CompletedProcess([], 1, "private-value", "private-value")).read()


class CliTests(unittest.TestCase):
    def invoke(self, argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = cs.main(argv)
        return status, json.loads(output.getvalue())

    def test_route_and_exec_cli_arguments(self):
        registry = cs.Registry(SharedStore(), clock=lambda: 1000)
        with patch.object(cs, "VfsStore"), patch.object(cs, "Registry", return_value=registry):
            status, result = self.invoke(["--scope-prefix", "/vfs/org", "route", "--topic-key", "realm/stream/topic",
                                          "--router-session", "router", "--event-id", "123", "--target", A, "--target", B])
            self.assertEqual((status, result["action"]), (0, "launch"))
            with patch.object(registry, "exec", return_value={"status": "succeeded"}) as execute:
                status, _ = self.invoke(["exec", "--run-id", result["run_id"], "--owner-session", "owner", "--generation", "1",
                                         "--resource", A, "--timeout", "2", "--", "command", "--private-flag"])
                self.assertEqual(status, 0)
                self.assertEqual(execute.call_args.kwargs["command"], ["command", "--private-flag"])
                self.assertEqual(execute.call_args.kwargs["timeout"], 2)

    def test_errors_are_json_without_subprocess_or_argument_details(self):
        with patch.object(cs, "VfsStore", side_effect=cs.CoordinationError("store_read_failed")):
            status, result = self.invoke(["check", "--run-id", "run", "--owner-session", "owner", "--generation", "1"])
        self.assertEqual((status, result), (1, {"error": "store_read_failed"}))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(SystemExit) as caught:
                cs.main(["private-invalid-command"])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue()), {"error": "invalid_arguments"})


if __name__ == "__main__":
    unittest.main()
