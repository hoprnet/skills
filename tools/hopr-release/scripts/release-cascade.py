#!/usr/bin/env python3
"""Release the unpublished org crates a repo depends on, leaf to root, then report the repin.

  release-cascade.py --from edge-client@main
  release-cascade.py --from hoprd@release/4.1 --execute

The start repo and branch choose the release line. The script reads that branch's
Cargo.lock, walks the dependency graph, and asks crates.io which crates have their
`repository` under github.com/hoprnet. Those crates are the ones to release. Each one is
mapped to its repo and branch:

  start branch is the default branch  -> every repo's default branch
  start branch is release/X.Y         -> compat/vX, else release/vX, in every repo
  repo has no such branch             -> fixed input: only checked to be on crates.io

Run from the directory that holds the repo clones, or pass --workspace. Missing org repos
are cloned there (partial clone). Default is a DRY RUN: it prints the steps and triggers
nothing. --execute runs each repo's release workflow with `gh workflow run`, asks `yes`
before each step (--yes skips it), and continues once the run's "Publish library" step
succeeds and the crates show on crates.io. Later steps of the run may still fail.

A step is blocked while a repo's own requirements do not admit the versions released below
it. Repin that repo first. A release publishes to crates.io and cannot be undone.

Output. stdout is always one JSON document (schema "hopr-release-cascade/v1"); progress
goes to stderr. --execute releases every ready step in order, then stops and reports where
it stands. Fields:
  state       release_ready | repin_required | final_repin_required | complete
              | manual_release_required (repo has no release workflow)
              | paused (waiting for confirmation) | error
  summary     one sentence on the current state
  steps       per repo in release order: status published | ready | blocked_repin,
              crates with on_crates_io, repin_edits (file, line, key, current, required)
  next        the single next action: release (command, crates), repin (edits), or none
  conditions  what must hold before the cascade continues, each with a verify command
  resume      the command to run once the conditions hold (null when complete)
  fixed_inputs, git_pinned_out_of_scope, warnings
Exit code: 0 for every state except error (2).
"""
import argparse, datetime, json, os, re, subprocess, sys, time, tomllib, urllib.request, urllib.error
from pathlib import Path

ORG = "hoprnet"
UA = "hoprnet-release-cascade"
PUBLISH_STEP = "Publish library"  # step name in hoprnet/hopr-workflows build-library.yaml
SKIP_DIRS = ("target/", "vendor/", "node_modules/", "/lib/")
DEP_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "hopr-release" / "crates-repository.json"
WORKSPACE = Path.cwd()
SCHEMA = "hopr-release-cascade/v1"


class Stop(Exception):
    """End the run with a final status report instead of a stack trace."""

    def __init__(self, msg, state="error"):
        super().__init__(msg)
        self.state = state


def die(msg, state="error"):
    raise Stop(msg, state)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def run(*a, check=True):
    r = subprocess.run(a, capture_output=True, text=True)
    if check and r.returncode:
        die(f"command failed: {' '.join(a)}\n{r.stderr.strip()}")
    return r.returncode, r.stdout


def git(repo, *a, check=True):
    return run("git", "-C", str(WORKSPACE / repo), *a, check=check)[1]


def has_ref(repo, branch):
    return run("git", "-C", str(WORKSPACE / repo), "rev-parse", "--verify", "-q", f"origin/{branch}", check=False)[0] == 0


def ensure(repo):
    if not (WORKSPACE / repo / ".git").exists():
        log(f"  cloning {ORG}/{repo} into {WORKSPACE / repo} (partial clone)")
        run("gh", "repo", "clone", f"{ORG}/{repo}", str(WORKSPACE / repo), "--", "--filter=blob:none", "--no-checkout", "--quiet")
    git(repo, "fetch", "-q", "origin")


def default_branch(repo):
    return git(repo, "symbolic-ref", "refs/remotes/origin/HEAD").strip().removeprefix("refs/remotes/origin/")


def ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s.split("-")[0])[:3])


# ---------- crates.io ----------

def crates_io(name, version=None):
    url = f"https://crates.io/api/v1/crates/{name}" + (f"/{version}" if version else "")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def is_published(name, version):
    d = crates_io(name, version)
    return bool(d) and not d["version"]["yanked"]


_repo_cache = None


def org_repo_of(crate):
    """Repo name under github.com/hoprnet that crates.io lists for the crate, else None. Cached on disk."""
    global _repo_cache
    if _repo_cache is None:
        try:
            _repo_cache = json.loads(CACHE.read_text())
        except (OSError, json.JSONDecodeError):
            _repo_cache = {}
    if crate not in _repo_cache:
        time.sleep(1)  # crates.io asks for at most one request per second
        d = crates_io(crate)
        _repo_cache[crate] = (d or {}).get("crate", {}).get("repository")
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(_repo_cache, indent=1))
    url = _repo_cache[crate] or ""
    m = re.match(r"https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+)", url, re.I)
    if m and m.group(1).lower() == ORG:
        return m.group(2).removesuffix(".git")
    return None


# ---------- manifests ----------

def tree_files(repo, ref):
    return [f for f in git(repo, "ls-tree", "-r", "--name-only", ref).split("\n")
            if f.endswith("Cargo.toml") and not any(d in f for d in SKIP_DIRS)]


def manifests(repo, ref):
    out = {}
    for f in tree_files(repo, ref):
        try:
            out[f] = tomllib.loads(git(repo, "show", f"{ref}:{f}"))
        except tomllib.TOMLDecodeError:
            pass
    return out


def packages(ms):
    """{crate: version} of every publishable package in the manifests."""
    ws_ver = next((m["workspace"]["package"]["version"] for m in ms.values()
                   if isinstance(m.get("workspace", {}).get("package", {}).get("version"), str)), None)
    out = {}
    for m in ms.values():
        p = m.get("package")
        if not p or p.get("publish", True) is False:
            continue
        v = p.get("version")
        v = ws_ver if isinstance(v, dict) and v.get("workspace") else v
        if isinstance(v, str):
            out[p["name"]] = v
    return out


def dep_entries(ms):
    """Every versioned dependency: {file, key, crate, req}. Resolves `package =` renames."""
    out = []

    def table(file, t):
        for key, spec in (t or {}).items():
            crate, req = key, None
            if isinstance(spec, str):
                req = spec
            elif isinstance(spec, dict):
                crate, req = spec.get("package", key), spec.get("version")
            if req:
                out.append(dict(file=file, key=key, crate=crate, req=req))

    for f, m in ms.items():
        for t in DEP_TABLES:
            table(f, m.get(t))
        for tgt in m.get("target", {}).values():
            for t in DEP_TABLES:
                table(f, tgt.get(t))
        table(f, m.get("workspace", {}).get("dependencies"))
    return out


def admits(req, v):
    """Minimal semver requirement check: ^ ~ = > >= < <= * and comma lists."""
    if "-" in v and "-" not in req:
        return False
    vt = ver(v)
    for part in (p.strip() for p in req.split(",")):
        if part in ("", "*"):
            continue
        m = re.match(r"(\^|~|=|>=|<=|>|<)?\s*(\d+(?:\.\d+){0,2})", part)
        if not m:
            return False
        op, n = m.group(1) or "^", m.group(2).count(".") + 1
        a, b, c = (ver(m.group(2)) + (0, 0, 0))[:3]
        if op == "^":
            up = (a + 1, 0, 0) if (a > 0 or n == 1) else (0, b + 1, 0) if (n == 2 or b > 0) else (0, 0, c + 1)
            ok = (a, b, c) <= vt < up
        elif op == "~":
            ok = (a, b, c) <= vt < ((a + 1, 0, 0) if n == 1 else (a, b + 1, 0))
        elif op == "=":
            ok = vt == (a, b, c) if n == 3 else vt[:n] == (a, b, c)[:n]
        else:
            ok = {">=": vt >= (a, b, c), ">": vt > (a, b, c), "<=": vt <= (a, b, c), "<": vt < (a, b, c)}[op]
        if not ok:
            return False
    return True


# ---------- discovery ----------

def discover(repo, branch):
    """Org crates in the start branch's dependency graph: ({crate: (repo, locked version)}, git-pinned org crates)."""
    pk = tomllib.loads(git(repo, "show", f"origin/{branch}:Cargo.lock"))["package"]
    byname = {}
    for p in pk:
        byname.setdefault(p["name"], []).append(p)

    def resolve(dep):
        x = dep.split()
        c = byname[x[0]]
        return ([p for p in c if p["version"] == x[1]] if len(x) > 1 else c)[0]

    owned, pinned, seen = {}, set(), set()
    stack = [p for p in pk if "source" not in p]  # the start repo's own packages
    while stack:
        n = stack.pop()
        for dep in n.get("dependencies", []):
            c = resolve(dep)
            k = (c["name"], c["version"])
            if k in seen:
                continue
            seen.add(k)
            src = c.get("source", "")
            if src.startswith("git+"):
                if f"github.com/{ORG}/" in src.lower():
                    pinned.add(c["name"])
                stack.append(c)
            elif src.startswith("registry+"):
                r = org_repo_of(c["name"])
                if r:
                    old = owned.get(c["name"])
                    owned[c["name"]] = (r, max(c["version"], old[1], key=ver) if old else c["version"])
                    stack.append(c)
            else:
                stack.append(c)
    return owned, pinned


def branch_for(repo, start_repo, start_branch):
    if start_branch == default_branch(start_repo):
        cands = [default_branch(repo)]
    else:
        m = re.search(r"(\d+)", start_branch)
        cands = [f"compat/v{m.group(1)}", f"release/v{m.group(1)}"] if m else []
    return next((b for b in cands if has_ref(repo, b)), None)


def release_workflow(repo, branch):
    names = [Path(f).name for f in git(repo, "ls-tree", "-r", "--name-only", f"origin/{branch}").split("\n")
             if f.startswith(".github/workflows/release") and f.endswith((".yaml", ".yml"))]
    if len(names) > 1:
        die(f"{repo}@{branch}: expected one release*.yaml workflow, found {names}")
    return names[0] if names else None  # None: no automated release, the plan reports a manual step


def build_plan(start_repo, start_branch):
    ensure(start_repo)
    if not has_ref(start_repo, start_branch):
        die(f"{start_repo}: no branch origin/{start_branch}")
    log(f"discovering {ORG} crates from {start_repo}@{start_branch} (crates.io lookups are cached) ...")
    owned, pinned = discover(start_repo, start_branch)
    steps, fixed = {}, []
    for crate, (repo, locked) in sorted(owned.items()):
        try:
            ensure(repo)
        except Stop as e:
            fixed.append(dict(crate=crate, version=locked, repo=repo, reason="repo_not_accessible", detail=str(e).splitlines()[-1]))
            continue
        b = branch_for(repo, start_repo, start_branch)
        if not b:
            fixed.append(dict(crate=crate, version=locked, repo=repo, reason="no_matching_branch"))
            continue
        steps.setdefault((repo, b), []).append(crate)
    plan = {}
    for (repo, b), crates in steps.items():
        ms = manifests(repo, f"origin/{b}")
        head = packages(ms)
        miss = [c for c in crates if c not in head]
        if miss:
            die(f"{repo}@{b}: crates.io lists {repo} as the repository of {miss}, but the branch has no such package")
        plan[(repo, b)] = dict(repo=repo, branch=b, crates=sorted(crates), head={c: head[c] for c in crates},
                               entries=dep_entries(ms), workflow=release_workflow(repo, b))
    # order: a repo comes after every repo whose crates it requires, read from the branch heads
    crate_step = {c: k for k, s in plan.items() for c in s["crates"]}
    after = {k: {crate_step[e["crate"]] for e in s["entries"] if e["crate"] in crate_step} - {k} for k, s in plan.items()}
    order = []
    while len(order) < len(plan):
        ready = sorted(k for k in plan if k not in order and after[k] <= set(order))
        if not ready:
            die(f"dependency cycle between {sorted(set(plan) - set(order))}")
        order.append(ready[0])
    target = {c: v for s in plan.values() for c, v in s["head"].items()}
    target.update({f["crate"]: f["version"] for f in fixed})
    steps = [plan[k] for k in order]
    for s in steps:
        s["todo"] = {c: v for c, v in s["head"].items() if not is_published(c, v)}
        s["gaps"] = gaps(s["entries"], target, owned)
    start_entries = dep_entries(manifests(start_repo, f"origin/{start_branch}"))
    return dict(start=(start_repo, start_branch), steps=steps, fixed=fixed, pinned=sorted(pinned),
                repin=gaps(start_entries, target, owned))


def gaps(entries, target, owned):
    """Requirements on org crates that do not admit the version released below them."""
    return [dict(e, want=target[e["crate"]]) for e in entries
            if e["crate"] in owned and e["crate"] in target and not admits(e["req"], target[e["crate"]])]


# ---------- status report: the single output format ----------

def line_of(repo, ref, file, key):
    for i, line in enumerate(git(repo, "show", f"{ref}:{file}").split("\n"), 1):
        if re.match(rf'\s*"?{re.escape(key)}"?\s*=', line):
            return i
    return 0


def edits(repo, branch, gs):
    return [dict(file=g["file"], line=line_of(repo, f"origin/{branch}", g["file"], g["key"]), key=g["key"],
                 crate=g["crate"], current=g["req"], required=g["want"]) for g in gs]


def resume_command(start):
    return f"python3 {Path(sys.argv[0]).resolve()} --from {start[0]}@{start[1]} --execute"


def status(plan):
    """One report for every run. `state` says where the cascade stands; `next` and `conditions` say how it goes on."""
    start = plan["start"]
    steps = []
    for i, s in enumerate(plan["steps"], 1):
        st = "published" if not s["todo"] else "blocked_repin" if s["gaps"] else "ready"
        steps.append(dict(index=i, repo=s["repo"], branch=s["branch"], workflow=s["workflow"], status=st,
                          crates=[dict(name=c, version=v, on_crates_io=c not in s["todo"]) for c, v in s["head"].items()],
                          repin_edits=edits(s["repo"], s["branch"], s["gaps"])))
    fixed = [dict(f, on_crates_io=is_published(f["crate"], f["version"])) for f in plan["fixed"]]
    warnings = [f"fixed input {f['crate']} {f['version']} is missing or yanked on crates.io" for f in fixed if not f["on_crates_io"]]
    warnings += [f"{s['repo']}@{s['branch']} does not consume {g['crate']} {g['want']} ({g['file']}: {g['key']} = \"{g['req']}\")"
                 for s in plan["steps"] if not s["todo"] for g in s["gaps"]]
    first = next((s for s in plan["steps"] if s["todo"]), None)
    resume = resume_command(start)
    if first and first["gaps"]:
        state = "repin_required"
        summary = f"{first['repo']}@{first['branch']} must repin before its crates can be released"
        nxt = dict(action="repin", repo=first["repo"], branch=first["branch"],
                   edits=edits(first["repo"], first["branch"], first["gaps"]),
                   then="open a PR with these edits and a refreshed Cargo.lock, then merge it")
        conditions = [dict(id="repin_merged", description=f"the repin PR is merged into {first['repo']}@{first['branch']}",
                           verify=f"{resume} (dry run first: drop --execute); step {steps[plan['steps'].index(first)]['index']} must show status ready")]
    elif first and not first["workflow"]:
        state = "manual_release_required"
        summary = f"{first['repo']}@{first['branch']} has no release workflow; release {', '.join(first['todo'])} by hand"
        nxt = dict(action="release_manual", repo=first["repo"], branch=first["branch"],
                   crates=[dict(name=c, version=v) for c, v in first["todo"].items()],
                   then="publish with the repo's own process (see its README)")
        conditions = [dict(id="on_crates_io", description="every listed crate version shows on crates.io",
                           verify="; ".join(f"curl -s https://crates.io/api/v1/crates/{c}/{v}" for c, v in first["todo"].items()))]
    elif first:
        state = "release_ready"
        summary = f"{first['repo']}@{first['branch']} is ready to release {', '.join(first['todo'])}"
        nxt = dict(action="release", repo=first["repo"], branch=first["branch"], workflow=first["workflow"],
                   crates=[dict(name=c, version=v) for c, v in first["todo"].items()],
                   command=f"gh workflow run {first['workflow']} --repo {ORG}/{first['repo']} --ref {first['branch']} -f release_type=patch")
        conditions = [dict(id="publish_step", description=f"the run's '{PUBLISH_STEP}' step succeeds",
                           verify=f"gh run list --repo {ORG}/{first['repo']} --workflow {first['workflow']} --limit 1"),
                      dict(id="on_crates_io", description="every listed crate version shows on crates.io",
                           verify="; ".join(f"curl -s https://crates.io/api/v1/crates/{c}/{v}" for c, v in first["todo"].items()))]
    elif plan["repin"]:
        state = "final_repin_required"
        summary = f"every crate is released; {start[0]}@{start[1]} must repin"
        nxt = dict(action="repin", repo=start[0], branch=start[1], edits=edits(start[0], start[1], plan["repin"]),
                   then="open a PR with these edits and a refreshed Cargo.lock, then merge it")
        conditions = [dict(id="repin_merged", description=f"the repin PR is merged into {start[0]}@{start[1]}",
                           verify=f"{resume} (dry run first: drop --execute) must report state complete")]
    else:
        state, summary, nxt, conditions = "complete", "every crate is released and the start repo requirements are current", dict(action="none"), []
    return dict(schema=SCHEMA, generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                start=dict(repo=start[0], branch=start[1]), state=state, summary=summary, steps=steps,
                fixed_inputs=fixed, git_pinned_out_of_scope=plan["pinned"], warnings=warnings,
                next=nxt, conditions=conditions, resume=resume if state != "complete" else None)


def error_report(start, msg, state="error"):
    return dict(schema=SCHEMA, generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                start=dict(repo=start[0], branch=start[1]), state=state, summary=msg, steps=[], fixed_inputs=[],
                git_pinned_out_of_scope=[], warnings=[], next=dict(action="none"),
                conditions=[], resume=resume_command(start))


def gh_json(*a):
    return json.loads(run("gh", *a)[1] or "null")


def run_ids(repo, wf, branch):
    rows = gh_json("run", "list", "--repo", f"{ORG}/{repo}", "--workflow", wf, "--branch", branch,
                   "--event", "workflow_dispatch", "--limit", "20", "--json", "databaseId")
    return {r["databaseId"] for r in rows}


def wait_publish_step(repo, run_id, timeout=45 * 60):
    """Block until the crates.io publish step of the run succeeds. Later steps may still fail."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = gh_json("run", "view", str(run_id), "--repo", f"{ORG}/{repo}", "--json", "status,conclusion,jobs")
        steps = [st for j in d["jobs"] for st in j["steps"] if st["name"] == PUBLISH_STEP]
        state = steps[0]["conclusion"] if steps else None
        if state == "success":
            return d["status"]
        if state in ("failure", "cancelled", "timed_out") or (d["status"] == "completed" and state != "success"):
            die(f"{repo}: step '{PUBLISH_STEP}' did not succeed ({state or 'not reached'}); "
                     f"see https://github.com/{ORG}/{repo}/actions/runs/{run_id}. Nothing further triggered")
        time.sleep(20)
    die(f"{repo}: timeout waiting for step '{PUBLISH_STEP}' of run {run_id}; nothing further triggered")


def release_step(i, s, assume_yes, release_type):
    label = f"{s['repo']}@{s['branch']} ({', '.join(f'{n} {v}' for n, v in s['todo'].items())})"
    if not assume_yes:
        try:
            ok = input(f"[{i}] trigger {s['workflow']} for {label}? type 'yes': ") == "yes"
        except EOFError:
            ok = False
        if not ok:
            die(f"confirmation needed for {label}; nothing triggered. Re-run interactively or pass --yes", "paused")
    before = run_ids(s["repo"], s["workflow"], s["branch"])
    run("gh", "workflow", "run", s["workflow"], "--repo", f"{ORG}/{s['repo']}", "--ref", s["branch"],
        "-f", f"release_type={release_type}")
    new = set()
    for _ in range(12):  # the run takes a few seconds to appear
        time.sleep(5)
        new = run_ids(s["repo"], s["workflow"], s["branch"]) - before
        if new:
            break
    if len(new) != 1:
        die(f"{s['repo']}: cannot identify the triggered run (new runs: {sorted(new)}); nothing further triggered")
    rid = new.pop()
    log(f"  triggered run {rid}: https://github.com/{ORG}/{s['repo']}/actions/runs/{rid}\n"
        f"  waiting for step '{PUBLISH_STEP}' ...")
    log(f"  published by run {rid} (run status: {wait_publish_step(s['repo'], rid)}); checking crates.io ...")
    pending, deadline = dict(s["todo"]), time.time() + 10 * 60  # index lag; later steps resolve against it
    while pending and time.time() < deadline:
        pending = {n: v for n, v in pending.items() if not is_published(n, v)}
        if pending:
            time.sleep(15)
    if pending:
        die(f"step succeeded but {pending} not visible on crates.io after 10 min; nothing further triggered")
    log(f"  on crates.io: {label}")


def main():
    global WORKSPACE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", required=True, metavar="REPO@BRANCH", help="repo and branch to release for")
    ap.add_argument("--workspace", type=Path, help="directory holding the repo clones (default: current directory)")
    ap.add_argument("--execute", action="store_true", help="trigger the release workflows")
    ap.add_argument("--yes", action="store_true", help="skip the per-step prompt (with --execute)")
    ap.add_argument("--release-type", default="patch", choices=["rc", "patch", "minor", "major"],
                    help="version type after release, passed to the workflow")
    a = ap.parse_args()
    if a.workspace:
        WORKSPACE = a.workspace.resolve()
    repo, _, branch = a.start.partition("@")
    if not branch:
        die("--from needs REPO@BRANCH, for example edge-client@main")
    start = (repo, branch)
    report = None
    try:
        run("gh", "auth", "status")
        plan = build_plan(repo, branch)
        report = status(plan)
        while a.execute and report["state"] == "release_ready":
            i = next(k for k, st in enumerate(plan["steps"], 1) if st["todo"])
            release_step(i, plan["steps"][i - 1], a.yes, a.release_type)
            plan = build_plan(repo, branch)  # re-read heads and crates.io
            report = status(plan)
    except Stop as e:
        report = error_report(start, str(e), e.state)
    print(json.dumps(report, indent=2))
    sys.exit(2 if report["state"] == "error" else 0)


if __name__ == "__main__":
    main()
