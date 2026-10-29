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
are cloned there (partial clone). Default is a DRY RUN: it prints the operations and
triggers nothing. --execute runs the ready release workflows with `gh workflow run`, all
at the same time, after one `yes` (--yes skips it). It continues for a repo once its
versions show on crates.io; later jobs of the run do not matter. A crate is released unless
its head version is already on crates.io. One run releases only the crates in its first
plan, because CI bumps each head after a release. --max-releases N caps the repos released.

A step is blocked while a repo's own requirements do not admit the versions released below
it. Repin that repo first. A release publishes to crates.io and cannot be undone.

Output. stdout is always one JSON document (schema "hopr-release-cascade/v2"); progress
goes to stderr. The dry run lists every operation to do, so a bot can propagate the repins,
turn the list into a workflow, or run the releases. Fields:
  state            release_ready | repin_required | final_repin_required
                   | manual_release_required | complete | paused | error
  summary          one sentence on where the cascade stands
  repos_to_update  repo, branch, its operation ids, needs_pr, crates it releases
  crates_to_change crate, version to publish, repo, branch, republish, operation id
  operations       the full ordered list, leaf first. Each has id, kind, repo, branch, status
                   (ready | pending), depends_on, description, and per kind:
                   release / release_manual: crates, workflow, command, republish
                   repin: edits (file, line, key, current, required), version_bumps, then
                   final_repin: edits, lock_updates (crate, locked, target), then
  ready_operations ids that can run now (no unfinished dependency)
  next             the first ready operation, or {"kind": "none"}
  conditions       what must hold before the cascade continues, each with a verify command
  resume           the command to run once the conditions hold (null when complete)
  fixed_inputs, git_pinned_out_of_scope, warnings
Exit code: 0 for every state except error (2).
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse, datetime, json, os, re, subprocess, sys, time, tomllib, urllib.request, urllib.error
from pathlib import Path

ORG = "hoprnet"
UA = "hoprnet-release-cascade"
PUBLISH_STEP = "Publish library"  # step name in hoprnet/hopr-workflows build-library.yaml
SKIP_DIRS = ("target/", "vendor/", "node_modules/", "/lib/")
DEP_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "hopr-release" / "crates-repository.json"
WORKSPACE = Path.cwd()
SCHEMA = "hopr-release-cascade/v2"


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
    """{crate: {version, file}} of every publishable package. `file` holds the version string."""
    ws = next(((f, m["workspace"]["package"]["version"]) for f, m in ms.items()
               if isinstance(m.get("workspace", {}).get("package", {}).get("version"), str)), (None, None))
    out = {}
    for f, m in ms.items():
        p = m.get("package")
        if not p or p.get("publish", True) is False:
            continue
        v = p.get("version")
        file, v = (ws if isinstance(v, dict) and v.get("workspace") else (f, v))
        if isinstance(v, str):
            out[p["name"]] = dict(version=v, file=file)
    return out


def published_versions(crate):
    """Non-yanked versions of a crate on crates.io."""
    return [v["num"] for v in (crates_io(crate) or {"versions": []})["versions"] if not v["yanked"]]


def hard_needs(entries, own, releasing, published=published_versions):
    """Crates this repo must wait for: ones being released whose requirement no published version meets."""
    cache, out = {}, set()
    for e in entries:
        c = e["crate"]
        if c in releasing and c not in own and c not in out:
            if c not in cache:
                cache[c] = published(c)
            if not any(admits(e["req"], v) for v in cache[c]):
                out.add(c)
    return sorted(out)


def bump_patch(v):
    a, b, c = (ver(v) + (0, 0, 0))[:3]
    return f"{a}.{b}.{c + 1}"


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
        pk = packages(ms)
        head = {c: i["version"] for c, i in pk.items()}
        miss = [c for c in crates if c not in head]
        if miss:
            die(f"{repo}@{b}: crates.io lists {repo} as the repository of {miss}, but the branch has no such package")
        plan[(repo, b)] = dict(repo=repo, branch=b, crates=sorted(crates), head={c: head[c] for c in crates},
                               files={c: pk[c]["file"] for c in crates},
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
    releasing = set()
    for k in order:
        s = plan[k]
        s["requires"] = sorted({e["crate"] for e in s["entries"] if e["crate"] in crate_step and crate_step[e["crate"]] != k})
        s["todo"] = {c: v for c, v in s["head"].items() if not is_published(c, v)}
        s["gaps"] = gaps(s["entries"], target, owned)
        # a repo whose head is published but whose requirements lag must be bumped and released again
        s["republish"] = bool(s["gaps"]) and not s["todo"]
        s["release_versions"] = {c: bump_patch(v) for c, v in s["head"].items()} if s["republish"] else s["todo"]
        s["hard_requires"] = hard_needs(s["entries"], set(s["crates"]), releasing)
        releasing |= set(s["release_versions"])
        target.update(s["release_versions"])  # leaf first, so downstream repos see the version that will exist
    start_entries = dep_entries(manifests(start_repo, f"origin/{start_branch}"))
    return dict(start=(start_repo, start_branch), steps=steps, fixed=fixed, pinned=sorted(pinned),
                repin=gaps(start_entries, target, owned), locked={c: v for c, (_, v) in owned.items()}, target=target)


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


def operations(plan):
    """Every operation still to do, leaf first, with the operations each one waits for."""
    ops, rel = [], {}  # rel: crate -> id of the operation that releases it

    def new(kind, **kw):
        op = dict(id=f"op-{len(ops) + 1}", kind=kind, **kw)
        ops.append(op)
        return op

    for s in plan["steps"]:
        repin = None
        if s["gaps"]:
            bumps = [] if s["todo"] else [dict(crate=c, file=s["files"][c], current=v, suggested=bump_patch(v))
                                           for c, v in s["head"].items()]
            repin = new("repin", repo=s["repo"], branch=s["branch"], edits=edits(s["repo"], s["branch"], s["gaps"]),
                        version_bumps=bumps, depends_on=sorted({rel[g["crate"]] for g in s["gaps"] if g["crate"] in rel}),
                        description=f"repin {s['repo']}@{s['branch']} to the released versions"
                                    + (" and bump its version" if bumps else ""),
                        then="open a PR with these edits and a refreshed Cargo.lock, then merge it")
        if s["todo"] or s["gaps"]:
            crates = s["release_versions"]
            deps = {rel[c] for c in s.get("hard_requires", s["requires"]) if c in rel} | ({repin["id"]} if repin else set())
            op = new("release" if s["workflow"] else "release_manual", repo=s["repo"], branch=s["branch"],
                     workflow=s["workflow"], republish=s["republish"], depends_on=sorted(deps),
                     crates=[dict(name=c, version=v) for c, v in crates.items()],
                     description=f"release {', '.join(f'{c} {v}' for c, v in crates.items())} from {s['repo']}@{s['branch']}",
                     command=(f"gh workflow run {s['workflow']} --repo {ORG}/{s['repo']} --ref {s['branch']} -f release_type=patch"
                              if s["workflow"] else None))
            rel.update({c: op["id"] for c in crates})
    # the start repo's lock pins older versions even when its requirements already admit the new ones
    lock_updates = [dict(crate=c, locked=v, target=plan["target"][c]) for c, v in sorted(plan.get("locked", {}).items())
                    if c in plan.get("target", {}) and ver(plan["target"][c]) > ver(v)]
    if plan["repin"] or lock_updates:
        sr, sb = plan["start"]
        named = {g["crate"] for g in plan["repin"]} | {u["crate"] for u in lock_updates}
        new("final_repin", repo=sr, branch=sb, edits=edits(sr, sb, plan["repin"]), lock_updates=lock_updates,
            version_bumps=[], depends_on=sorted({rel[c] for c in named if c in rel}),
            description=f"update {sr}@{sb} to the released versions"
                        + (f" (cargo update -p {' -p '.join(u['crate'] for u in lock_updates)})" if lock_updates else ""),
            then="open a PR with the requirement edits and a refreshed Cargo.lock, then merge it")
    for op in ops:
        op["status"] = "pending" if op["depends_on"] else "ready"
    return ops


STATE = dict(release="release_ready", release_manual="manual_release_required", repin="repin_required",
             final_repin="final_repin_required")


def conditions_for(op, resume):
    if op["kind"] == "release":
        return [dict(id="publish_step", description=f"the run's '{PUBLISH_STEP}' step succeeds",
                     verify=f"gh run list --repo {ORG}/{op['repo']} --workflow {op['workflow']} --limit 1"),
                dict(id="on_crates_io", description="every listed crate version shows on crates.io",
                     verify="; ".join(f"curl -s https://crates.io/api/v1/crates/{c['name']}/{c['version']}" for c in op["crates"]))]
    if op["kind"] == "release_manual":
        return [dict(id="on_crates_io", description="every listed crate version shows on crates.io",
                     verify="; ".join(f"curl -s https://crates.io/api/v1/crates/{c['name']}/{c['version']}" for c in op["crates"]))]
    return [dict(id="repin_merged", description=f"the repin PR is merged into {op['repo']}@{op['branch']}",
                 verify=f"{resume} without --execute; operation {op['id']} must be gone from the list")]


def status(plan):
    """The single report. `operations` is the full ordered list; `next` is the first one that can run now."""
    start = plan["start"]
    ops = operations(plan)
    fixed = [dict(f, on_crates_io=is_published(f["crate"], f["version"])) for f in plan["fixed"]]
    warnings = [f"fixed input {f['crate']} {f['version']} is missing or yanked on crates.io" for f in fixed if not f["on_crates_io"]]
    repos = {}
    for op in ops:
        repos.setdefault((op["repo"], op["branch"]), []).append(op)
    repos_to_update = [dict(repo=r, branch=b, operations=[o["id"] for o in os_],
                            needs_pr=any(o["kind"] in ("repin", "final_repin") for o in os_),
                            releases=[c["name"] for o in os_ if o["kind"].startswith("release") for c in o["crates"]])
                       for (r, b), os_ in repos.items()]
    crates_to_change = [dict(crate=c["name"], repo=o["repo"], branch=o["branch"], version=c["version"],
                             republish=o["republish"], operation=o["id"])
                        for o in ops if o["kind"].startswith("release") for c in o["crates"]]
    ready = [o for o in ops if o["status"] == "ready"]
    resume = resume_command(start)
    if ready:
        first = ready[0]
        state = STATE[first["kind"]]
        summary = f"{len(ops)} operations across {len(repos_to_update)} repos; next: {first['description']}"
        nxt, conditions = first, conditions_for(first, resume)
    else:
        state, summary = "complete", "every crate is released and the start repo requirements are current"
        nxt, conditions = dict(kind="none"), []
    return dict(schema=SCHEMA, generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                start=dict(repo=start[0], branch=start[1]), state=state, summary=summary,
                repos_to_update=repos_to_update, crates_to_change=crates_to_change, operations=ops,
                ready_operations=[o["id"] for o in ready], next=nxt, conditions=conditions,
                fixed_inputs=fixed, git_pinned_out_of_scope=plan["pinned"], warnings=warnings,
                resume=resume if state != "complete" else None)


def error_report(start, msg, state="error"):
    return dict(schema=SCHEMA, generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                start=dict(repo=start[0], branch=start[1]), state=state, summary=msg, repos_to_update=[],
                crates_to_change=[], operations=[], ready_operations=[], next=dict(kind="none"), conditions=[],
                fixed_inputs=[], git_pinned_out_of_scope=[], warnings=[], resume=resume_command(start))


def gh_json(*a):
    return json.loads(run("gh", *a)[1] or "null")


def run_ids(repo, wf, branch):
    rows = gh_json("run", "list", "--repo", f"{ORG}/{repo}", "--workflow", wf, "--branch", branch,
                   "--event", "workflow_dispatch", "--limit", "20", "--json", "databaseId")
    return {r["databaseId"] for r in rows}


def wait_publish_step(repo, run_id, todo, timeout=45 * 60):
    """Block until the publish step succeeds. crates.io decides: if the step reports a failure
    but every version is on crates.io, carry on. Later steps of the run do not matter."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = gh_json("run", "view", str(run_id), "--repo", f"{ORG}/{repo}", "--json", "status,conclusion,jobs")
        steps = [st for j in d["jobs"] for st in j["steps"] if st["name"] == PUBLISH_STEP]
        state = steps[0]["conclusion"] if steps else None
        if state == "success":
            return d["status"]
        if state in ("failure", "cancelled", "timed_out") or (d["status"] == "completed" and state != "success"):
            if all(is_published(n, v) for n, v in todo.items()):
                return d["status"]
            die(f"{repo}: step '{PUBLISH_STEP}' did not succeed ({state or 'not reached'}); "
                     f"see https://github.com/{ORG}/{repo}/actions/runs/{run_id}. Nothing further triggered")
        time.sleep(20)
    if all(is_published(n, v) for n, v in todo.items()):
        return "timeout"
    die(f"{repo}: timeout waiting for step '{PUBLISH_STEP}' of run {run_id}; nothing further triggered")


def release_step(s, release_type):
    """Trigger one repo's release workflow and wait for its crates. Safe to run in a thread."""
    tag = f"[{s['repo']}]"
    label = f"{s['repo']}@{s['branch']} ({', '.join(f'{n} {v}' for n, v in s['todo'].items())})"
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
        die(f"{s['repo']}: cannot identify the triggered run (new runs: {sorted(new)})")
    rid = new.pop()
    log(f"{tag} triggered run {rid}: https://github.com/{ORG}/{s['repo']}/actions/runs/{rid}; waiting for '{PUBLISH_STEP}'")
    log(f"{tag} published by run {rid} (run status: {wait_publish_step(s['repo'], rid, s['todo'])}); checking crates.io")
    pending, deadline = dict(s["todo"]), time.time() + 10 * 60  # index lag; later steps resolve against it
    while pending and time.time() < deadline:
        pending = {n: v for n, v in pending.items() if not is_published(n, v)}
        if pending:
            time.sleep(15)
    if pending:
        die(f"{s['repo']}: step succeeded but {pending} not visible on crates.io after 10 min")
    log(f"{tag} on crates.io: {label}")


def planned_crates(report):
    """Crates of every release operation in the first plan of a run. Later plans add CI's post-release bumps; those stay out."""
    return {c["name"] for o in report["operations"] if o["kind"].startswith("release") for c in o["crates"]}


def next_batch(report, planned, remaining):
    """Ready release operations that belong to the run's first plan, capped by `remaining` (None: no cap)."""
    batch = [o for o in report["operations"] if o["status"] == "ready" and o["kind"] == "release"
             and all(c["name"] in planned for c in o["crates"])]
    return batch if remaining is None else batch[:max(remaining, 0)]


def release_batch(steps, release_type):
    """Release independent repos at the same time. Wait for all, then report every failure."""
    errors = []
    with ThreadPoolExecutor(max_workers=len(steps)) as pool:
        futures = {pool.submit(release_step, s, release_type): s for s in steps}
        for f in as_completed(futures):
            try:
                f.result()
            except Stop as e:
                errors.append(f"{futures[f]['repo']}: {e}")
    if errors:
        die("; ".join(errors) + ". Other releases in the batch may have finished; re-run the dry run for the current state")


def main():
    global WORKSPACE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", required=True, metavar="REPO@BRANCH", help="repo and branch to release for")
    ap.add_argument("--workspace", type=Path, help="directory holding the repo clones (default: current directory)")
    ap.add_argument("--execute", action="store_true", help="trigger the release workflows")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt (with --execute)")
    ap.add_argument("--max-releases", type=int, metavar="N", help="with --execute: release at most N repos in this run")
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
        released, planned = 0, planned_crates(report)
        while a.execute:
            batch = next_batch(report, planned, None if a.max_releases is None else a.max_releases - released)
            if not batch:
                break
            names = "; ".join(f"{o['repo']}@{o['branch']} ({', '.join(c['name'] + ' ' + c['version'] for c in o['crates'])})"
                              for o in batch)
            if not a.yes:
                try:
                    ok = input(f"release {len(batch)} at once: {names}? type 'yes': ") == "yes"
                except EOFError:
                    ok = False
                if not ok:
                    die(f"confirmation needed for {names}; nothing triggered. Re-run interactively or pass --yes", "paused")
            log(f"releasing {len(batch)} at once: {names}")
            by_key = {(st["repo"], st["branch"]): st for st in plan["steps"]}
            release_batch([by_key[(o["repo"], o["branch"])] for o in batch], a.release_type)
            released += len(batch)
            plan = build_plan(repo, branch)  # re-read heads and crates.io
            report = status(plan)
    except Stop as e:
        report = error_report(start, str(e), e.state)
    print(json.dumps(report, indent=2))
    sys.exit(2 if report["state"] == "error" else 0)


if __name__ == "__main__":
    main()
