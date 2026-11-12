"""Cooperative release ownership through an existing, CAS-protected org registry."""
import argparse
import copy
import contextlib
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

REGISTRY_PATH = "experts/rust-dep-propagator/state.json"
TERMINAL = {"merged", "skipped"}


class CoordinationError(Exception):
    """An error code safe for JSON output; never include subprocess diagnostics."""


class Conflict(CoordinationError):
    """The store positively identifies a conditional-write conflict."""


def require(condition, code):
    if not condition:
        raise CoordinationError(code)


def text(value):
    return isinstance(value, str) and bool(value) and not any(ord(c) < 32 for c in value)


def resource_key(value):
    require(text(value), "invalid_resource")
    match = re.fullmatch(r"([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+)@([^\s]+)", value)
    require(match is not None and match[1].split("/")[1] not in (".", ".."), "invalid_resource")
    return match[1].lower() + "@" + match[2]


def timestamp(value):
    return (type(value) is int and value >= 0) or (type(value) is float and math.isfinite(value) and value >= 0)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "invalid_registry")
        result[key] = value
    return result


def validate(state):
    """Reject malformed or inconsistent registries before using any reservation."""
    try:
        require(type(state) is dict and set(state) == {"version", "runs", "routes", "events", "resources"}
                and type(state["version"]) is int and state["version"] == 1, "invalid_registry")
        for key in ("runs", "routes", "events", "resources"):
            require(type(state[key]) is dict, "invalid_registry")
        runs = state["runs"]
        operation_ids = set()
        for rid, run in runs.items():
            require(text(rid) and type(run) is dict and set(run) == {"router_session", "owner_session", "generation", "status",
                    "lease_until", "targets", "pending", "operations", "mutation"}, "invalid_registry")
            require(text(run["router_session"]) and (run["owner_session"] is None or text(run["owner_session"])), "invalid_registry")
            require(type(run["generation"]) is int and run["generation"] > 0, "invalid_registry")
            require(run["status"] in ("active", "complete") and timestamp(run["lease_until"]), "invalid_registry")
            for key in ("targets", "pending"):
                require(type(run[key]) is list and all(text(v) for v in run[key]) and len(set(run[key])) == len(run[key]), "invalid_registry")
            require(bool(run["targets"]) and type(run["operations"]) is dict, "invalid_registry")
            for target in run["targets"]:
                require(resource_key(target) == target, "invalid_registry")
            for event in run["pending"]:
                require(state["events"].get(event) == rid, "invalid_registry")
            for resource, op in run["operations"].items():
                require(resource_key(resource) == resource and type(op) is dict
                        and set(op) == {"operation_id", "status", "pr", "mutation_done", "completed_actions"}, "invalid_registry")
                require(text(op["operation_id"]) and op["operation_id"] not in operation_ids, "invalid_registry")
                operation_ids.add(op["operation_id"])
                require(op["status"] in TERMINAL | {"claimed"} and type(op["mutation_done"]) is bool, "invalid_registry")
                require(type(op["completed_actions"]) is list and all(text(a) for a in op["completed_actions"])
                        and len(set(op["completed_actions"])) == len(op["completed_actions"]), "invalid_registry")
                if op["pr"] is not None:
                    validate_pr(resource, op["pr"])
            mutation = run["mutation"]
            if mutation is not None:
                require(type(mutation) is dict and set(mutation) == {"mutation_id", "resource", "started_at", "state", "action_id"}
                        and text(mutation["mutation_id"]) and timestamp(mutation["started_at"]), "invalid_registry")
                op = run["operations"][mutation["resource"]]
                require(mutation["state"] == "uncertain" and op["status"] == "claimed" and text(mutation["action_id"])
                        and mutation["action_id"] not in op["completed_actions"], "invalid_registry")
            if run["status"] == "complete":
                require(mutation is None and not run["pending"] and all(t in run["operations"] for t in run["targets"]), "invalid_registry")
                require(all(o["status"] in TERMINAL for o in run["operations"].values()), "invalid_registry")
            else:
                for resource in set(run["targets"]) | set(run["operations"]):
                    require(state["resources"].get(resource) == rid, "invalid_registry")
        for key in ("routes", "events", "resources"):
            for name, rid in state[key].items():
                require(text(name) and isinstance(rid, str) and rid in runs, "invalid_registry")
                if key == "resources":
                    require(resource_key(name) == name and name in set(runs[rid]["targets"]) | set(runs[rid]["operations"]), "invalid_registry")
    except (KeyError, TypeError, ValueError, CoordinationError):
        raise CoordinationError("invalid_registry") from None


def validate_pr(resource, url):
    repo = resource.split("@", 1)[0]
    require(isinstance(url, str) and re.fullmatch(r"https://github\.com/" + re.escape(repo) + r"/pull/[1-9][0-9]*", url, re.IGNORECASE), "invalid_pr_url")


class VfsStore:
    """Remote-first store. read() returns (JSON state, sequence); write uses CAS."""

    def __init__(self, scope_prefix=None, *, runner=subprocess.run, timeout=30):
        if scope_prefix is None:
            root = os.environ.get("AUGMENT_VFS_DIR", "")
            scope_prefix = root.rstrip("/") + "/org" if root else ""
        require(isinstance(scope_prefix, str) and scope_prefix.startswith("/") and scope_prefix.endswith("/org")
                and ".." not in scope_prefix.split("/") and text(scope_prefix), "invalid_scope_prefix")
        self.scope_prefix, self.runner, self.timeout = scope_prefix, runner, timeout

    def _call(self, args, writing=False):
        try:
            result = self.runner(["auggie", "cloud", "vfs", *args], capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.SubprocessError):
            raise CoordinationError("uncertain_write" if writing else "store_read_failed") from None
        if result.returncode:
            # These explicit diagnostics indicate rejected CAS, not uncertain delivery.
            uncertain = re.search(r"\b(timeout|timed out|network|connection|transport)\b", result.stderr, re.IGNORECASE)
            if writing and not uncertain and re.search(r"\b(sequence conflict|sequence mismatch|base[-_ ]seq mismatch|precondition failed|409\s+Conflict:\s+VFS upload-file failed)\b", result.stderr, re.IGNORECASE):
                raise Conflict("sequence_conflict")
            raise CoordinationError("uncertain_write" if writing else "store_read_failed")
        return result

    def read(self):
        with tempfile.TemporaryDirectory(prefix="cascade-state-") as scratch:
            result = self._call(["get-url", self.scope_prefix + "/" + REGISTRY_PATH])
            url = result.stdout.strip()
            try:
                parsed = urlsplit(url)
                require(parsed.scheme == "https" and bool(parsed.hostname) and parsed.path.startswith("/files/")
                        and not parsed.query and not parsed.fragment and parsed.username is None and parsed.password is None
                        and not any(c.isspace() for c in url), "store_read_failed")
            except ValueError:
                raise CoordinationError("store_read_failed") from None
            path = Path(scratch) / "state.json"
            result = self._call(["get", url, "--output", str(path)])
            sequences = set(re.findall(r"\bseq=([0-9]+)(?![\w.])", result.stdout + "\n" + result.stderr))
            require(len(sequences) == 1, "missing_or_ambiguous_sequence")
            try:
                state = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
            except (OSError, ValueError, RecursionError):
                raise CoordinationError("invalid_registry") from None
            validate(state)
            return state, int(sequences.pop())

    def write(self, state, sequence):
        validate(state)
        require(type(sequence) is int and sequence >= 0, "invalid_sequence")
        with tempfile.TemporaryDirectory(prefix="cascade-state-") as scratch:
            path = Path(scratch) / "state.json"
            path.write_text(json.dumps(state, allow_nan=False), encoding="utf-8")
            self._call(["put", str(path), REGISTRY_PATH, "--filesystem-id", "org", "--base-seq", str(sequence)], writing=True)


class Registry:
    def __init__(self, store, clock=time.time, *, lease_ttl=900, retries=5):
        require(timestamp(lease_ttl) and lease_ttl > 0 and type(retries) is int and retries > 0, "invalid_configuration")
        self.store, self.clock, self.lease_ttl, self.retries = store, clock, lease_ttl, retries

    def _update(self, change):
        for _ in range(self.retries):
            state, sequence = self.store.read()
            validate(state)
            before = copy.deepcopy(state)
            result = change(state)
            if state == before:
                return copy.deepcopy(result)
            validate(state)
            try:
                self.store.write(state, sequence)
                return copy.deepcopy(result)
            except Conflict:
                continue
        raise CoordinationError("cas_retries_exhausted")

    def _live(self, run):
        require(self.clock() < run["lease_until"], "lease_expired")

    def _owner(self, state, run_id, owner_session, generation, *, completed=False):
        run = state["runs"].get(run_id)
        require(run is not None, "unknown_run")
        require(run["owner_session"] == owner_session and owner_session is not None, "not_owner")
        require(type(generation) is int and run["generation"] == generation, "stale_generation")
        if not (completed and run["status"] == "complete"):
            require(run["status"] == "active", "run_complete")
            self._live(run)
        return run

    @staticmethod
    def _info(run_id, run, **extra):
        return dict(run_id=run_id, generation=run["generation"], owner_session=run["owner_session"],
                    router_session=run["router_session"], status=run["status"], lease_until=run["lease_until"],
                    targets=list(run["targets"]), **extra)

    def lookup(self, topic_key):
        require(text(topic_key), "invalid_topic")
        state, _ = self.store.read()
        validate(state)
        run_id = state["routes"].get(topic_key)
        if run_id is None:
            return {"action": "missing"}
        run = state["runs"][run_id]
        action = "complete" if run["status"] == "complete" else "existing"
        if action == "existing" and self.clock() >= run["lease_until"]:
            action = "blocked"
        return self._info(run_id, run, action=action)

    def route(self, topic_key, router_session, event_id, targets):
        require(all(text(v) for v in (topic_key, router_session, event_id)) and bool(targets), "invalid_route")
        targets = sorted({resource_key(t) for t in targets})

        def change(state):
            if event_id in state["events"]:
                rid = state["events"][event_id]
                return self._info(rid, state["runs"][rid], action="duplicate")
            candidates = {state["routes"].get(topic_key)} | {state["resources"].get(t) for t in targets}
            candidates = {rid for rid in candidates if rid is not None and state["runs"][rid]["status"] == "active"}
            require(len(candidates) <= 1, "overlapping_runs")
            if candidates:
                rid = candidates.pop()
                run = state["runs"][rid]
                self._live(run)
                action = "forward" if run["owner_session"] else "pending"
                run["targets"] = sorted(set(run["targets"]) | set(targets))
            else:
                rid, action = uuid.uuid4().hex, "launch"
                run = dict(generation=1, router_session=router_session, owner_session=None, status="active",
                           lease_until=self.clock() + self.lease_ttl, targets=targets, pending=[], operations={}, mutation=None)
                state["runs"][rid] = run
            state["routes"][topic_key] = rid
            state["events"][event_id] = rid
            run["pending"].append(event_id)
            for target in targets:
                state["resources"][target] = rid
            return self._info(rid, run, action=action)
        return self._update(change)

    def bind(self, run_id, router_session, owner_session):
        require(text(owner_session), "invalid_owner")

        def change(state):
            run = state["runs"].get(run_id)
            require(run is not None, "unknown_run")
            require(run["router_session"] == router_session, "not_router")
            require(run["owner_session"] in (None, owner_session) and run["generation"] == 1, "already_bound")
            require(run["status"] == "active", "run_complete")
            self._live(run)
            run["owner_session"] = owner_session
            return self._info(run_id, run)
        return self._update(change)

    def _owned(self, run_id, owner_session, generation, change, *, completed=False):
        return self._update(lambda state: change(state, self._owner(state, run_id, owner_session, generation, completed=completed)))

    def alias(self, run_id, owner_session, generation, topic_key):
        require(text(topic_key), "invalid_topic")

        def change(state, run):
            old = state["routes"].get(topic_key)
            require(old in (None, run_id) or state["runs"][old]["status"] == "complete", "route_conflict")
            state["routes"][topic_key] = run_id
            return self._info(run_id, run)
        return self._owned(run_id, owner_session, generation, change)

    def inbox(self, run_id, owner_session, generation):
        return self._owned(run_id, owner_session, generation, lambda s, r: {"event_ids": list(r["pending"])})

    def ack(self, run_id, owner_session, generation, event_id):
        def change(state, run):
            require(state["events"].get(event_id) == run_id, "unknown_event")
            if event_id in run["pending"]:
                run["pending"].remove(event_id)
            return {"event_id": event_id, "handled": True}
        return self._owned(run_id, owner_session, generation, change)

    def check(self, run_id, owner_session, generation):
        return self._owned(run_id, owner_session, generation, lambda s, r: self._info(run_id, r, mutation=r["mutation"]))

    def renew(self, run_id, owner_session, generation):
        def change(state, run):
            run["lease_until"] = self.clock() + self.lease_ttl
            return self._info(run_id, run)
        return self._owned(run_id, owner_session, generation, change)

    def claim_operation(self, run_id, owner_session, generation, resource):
        resource = resource_key(resource)

        def change(state, run):
            old = state["resources"].get(resource)
            require(old in (None, run_id) or state["runs"][old]["status"] == "complete", "resource_reserved")
            state["resources"][resource] = run_id
            if resource not in run["operations"]:
                run["operations"][resource] = dict(operation_id=uuid.uuid4().hex, status="claimed", pr=None, mutation_done=False, completed_actions=[])
            return dict(resource=resource, **run["operations"][resource])
        return self._owned(run_id, owner_session, generation, change)

    def _operation(self, run, resource):
        require(resource in run["operations"], "operation_not_claimed")
        return run["operations"][resource]

    def record_pr(self, run_id, owner_session, generation, resource, url):
        resource = resource_key(resource)
        validate_pr(resource, url)
        url = "https://github.com/" + resource.split("@", 1)[0] + "/pull/" + url.rsplit("/", 1)[1]

        def change(state, run):
            op = self._operation(run, resource)
            require(op["pr"] in (None, url), "pr_conflict")
            require(op["status"] == "claimed" or op["pr"] == url, "operation_terminal")
            op["pr"] = url
            return dict(resource=resource, **op)
        return self._owned(run_id, owner_session, generation, change)

    def finish_operation(self, run_id, owner_session, generation, resource, outcome):
        resource = resource_key(resource)
        require(outcome in TERMINAL, "invalid_outcome")

        def change(state, run):
            require(run["mutation"] is None, "mutation_blocked")
            op = self._operation(run, resource)
            require(op["status"] in ("claimed", outcome), "outcome_conflict")
            op["status"] = outcome
            return dict(resource=resource, **op)
        return self._owned(run_id, owner_session, generation, change)

    def complete(self, run_id, owner_session, generation):
        def change(state, run):
            require(run["mutation"] is None, "mutation_blocked")
            require(not run["pending"], "pending_events")
            require(all(t in run["operations"] for t in run["targets"])
                    and all(o["status"] in TERMINAL for o in run["operations"].values()), "unfinished_operations")
            run["status"] = "complete"
            return self._info(run_id, run)
        return self._owned(run_id, owner_session, generation, change, completed=True)

    def handover(self, run_id, owner_session, generation, new_owner_session):
        require(text(new_owner_session) and new_owner_session != owner_session, "invalid_new_owner")

        def change(state, run):
            require(run["mutation"] is None, "mutation_blocked")
            run["owner_session"] = new_owner_session
            run["generation"] += 1
            return self._info(run_id, run)
        return self._owned(run_id, owner_session, generation, change)

    def begin_mutation(self, run_id, owner_session, generation, resource, action_id="initial"):
        resource = resource_key(resource)
        require(text(action_id) and len(action_id) <= 200, "invalid_action")

        def change(state, run):
            require(run["mutation"] is None, "mutation_blocked")
            op = self._operation(run, resource)
            require(op["status"] == "claimed", "operation_terminal")
            require(action_id not in op["completed_actions"], "mutation_already_succeeded")
            run["mutation"] = dict(mutation_id=uuid.uuid4().hex, resource=resource, started_at=self.clock(),
                                   state="uncertain", action_id=action_id)
            return dict(run["mutation"])
        return self._owned(run_id, owner_session, generation, change)

    def end_mutation(self, run_id, owner_session, generation, resource, mutation_id):
        resource = resource_key(resource)

        def change(state, run):
            marker = run["mutation"]
            require(marker is not None and marker["resource"] == resource and marker["mutation_id"] == mutation_id, "mutation_mismatch")
            run["operations"][resource]["mutation_done"] = True
            run["operations"][resource]["completed_actions"].append(marker["action_id"])
            run["mutation"] = None
            return {"mutation_id": mutation_id, "status": "succeeded"}
        return self._owned(run_id, owner_session, generation, change)

    def push(self, run_id, owner_session, generation, resource, repo_path, branch, expected_head, action_id):
        """Push additively only when the remote still contains the reviewed head."""
        require(re.fullmatch(r"[0-9a-f]{40}", expected_head) is not None, "invalid_expected_head")
        require(branch.startswith("chore/release-cascade-") and text(branch), "invalid_branch")
        with self.guard(run_id, owner_session, generation, resource, action_id) as marker:
            def git(*args):
                try:
                    return subprocess.run(["git", "-C", repo_path, *args], stdin=subprocess.DEVNULL,
                                          capture_output=True, text=True, timeout=120)
                except (OSError, subprocess.SubprocessError):
                    raise CoordinationError("push_uncertain") from None
            require(git("check-ref-format", "refs/heads/" + branch).returncode == 0, "invalid_branch")
            remote = git("remote", "get-url", "origin")
            repo = resource_key(resource).split("@", 1)[0]
            require(remote.returncode == 0 and remote.stdout.strip() in
                    (f"https://github.com/{repo}", f"https://github.com/{repo}.git"), "unexpected_remote")
            head = git("ls-remote", "--heads", "origin", "refs/heads/" + branch)
            require(head.returncode == 0 and head.stdout.split()[:1] == [expected_head], "remote_head_changed")
            require(git("merge-base", "--is-ancestor", expected_head, "HEAD").returncode == 0, "remote_commit_not_preserved")
            require(git("push", "origin", "HEAD:refs/heads/" + branch).returncode == 0, "push_rejected_or_uncertain")
        return {"mutation_id": marker["mutation_id"], "status": "succeeded"}

    @contextlib.contextmanager
    def keepalive(self, run_id, owner_session, generation):
        """Renew ownership during local work without authorizing an external mutation."""
        self.check(run_id, owner_session, generation)
        stopped, failed = threading.Event(), threading.Event()

        def heartbeat():
            while not stopped.wait(self.lease_ttl / 3):
                try:
                    self.renew(run_id, owner_session, generation)
                except Exception:
                    failed.set()
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            yield failed
            require(not failed.is_set(), "lease_renewal_failed")
        finally:
            stopped.set()
            thread.join(timeout=35)

    @contextlib.contextmanager
    def guard(self, run_id, owner_session, generation, resource, action_id="initial"):
        """Keep ownership live during a write; uncertainty retains the marker."""
        marker = self.begin_mutation(run_id, owner_session, generation, resource, action_id)
        with self.keepalive(run_id, owner_session, generation) as failed:
            yield marker
            require(not failed.is_set(), "lease_renewal_failed")
            self.end_mutation(run_id, owner_session, generation, resource, marker["mutation_id"])

    def exec(self, run_id, owner_session, generation, resource, command, *, timeout=900, action_id="initial"):
        require(bool(command) and all(isinstance(arg, str) for arg in command) and timestamp(timeout) and timeout > 0, "invalid_command")
        with self.guard(run_id, owner_session, generation, resource, action_id) as marker:
            try:
                result = subprocess.run(command, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=timeout)
            except Exception:
                raise CoordinationError("mutation_uncertain") from None
            require(result.returncode == 0, "mutation_uncertain")
        return {"mutation_id": marker["mutation_id"], "status": "succeeded"}


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"error": "invalid_arguments"}))
        raise SystemExit(2)


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--scope-prefix")
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("lookup", "route", "bind", "alias", "inbox", "ack", "renew", "check", "claim-operation", "record-pr", "finish-operation", "complete", "handover", "prepare", "succeed", "push", "exec"):
        sub = commands.add_parser(name)
        if name == "lookup":
            sub.add_argument("--topic-key", required=True)
            continue
        if name == "route":
            for arg in ("topic-key", "router-session", "event-id"):
                sub.add_argument("--" + arg, required=True)
            sub.add_argument("--target", dest="targets", action="append", required=True)
            continue
        sub.add_argument("--run-id", required=True)
        if name == "bind":
            sub.add_argument("--router-session", required=True)
        else:
            sub.add_argument("--generation", type=int, required=True)
        sub.add_argument("--owner-session", required=True)
        for arg in {"alias": ("topic-key",), "ack": ("event-id",), "claim-operation": ("resource",),
                    "record-pr": ("resource", "url"), "finish-operation": ("resource", "outcome"),
                    "handover": ("new-owner-session",), "prepare": ("resource",),
                    "succeed": ("resource", "mutation-id"),
                    "push": ("resource", "repo-path", "branch", "expected-head", "action-id"), "exec": ("resource",)}.get(name, ()):
            sub.add_argument("--" + arg, required=True)
        if name in ("prepare", "exec"):
            sub.add_argument("--action-id", default="initial")
        if name == "exec":
            sub.add_argument("--timeout", type=float, default=900)
            sub.add_argument("command", nargs=argparse.REMAINDER)
    args = vars(parser.parse_args(argv))
    try:
        registry = Registry(VfsStore(args.pop("scope_prefix")))
        action = args.pop("action").replace("-", "_")
        action = {"prepare": "begin_mutation", "succeed": "end_mutation"}.get(action, action)
        if action == "exec" and args["command"][:1] == ["--"]:
            args["command"] = args["command"][1:]
        result = getattr(registry, action)(**args)
        print(json.dumps(result, allow_nan=False, sort_keys=True))
        return 0
    except CoordinationError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    except (Exception, KeyboardInterrupt):
        print(json.dumps({"error": "coordination_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
