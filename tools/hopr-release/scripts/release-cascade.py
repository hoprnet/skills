#!/usr/bin/env python3
"""Release the unpublished org crates a repo depends on, leaf to root, then report the repin.

  release-cascade.py --from edge-client@main                         dry run, JSON
  release-cascade.py --from edge-client@main -o plain                dry run, text
  release-cascade.py --from hoprd@release/4.1 --execute              release, then open repin PRs
  release-cascade.py --from hoprd@release/4.1 --execute-no-release   open repin PRs only

The start repo and branch choose the release line. The script reads that branch's
Cargo.lock, walks the dependency graph, and asks crates.io which crates have their
`repository` under github.com/hoprnet. Those crates are the ones to release. Each one is
mapped to its repo and branch:

  start branch is the default branch  -> every repo's default branch
  start branch is release/X.Y         -> compat/vX, else release/vX, in every repo
  repo has no such branch             -> fixed input: only checked to be on crates.io

GitHub access: gh when it is installed and logged in, else the REST API with GH_TOKEN or
GITHUB_TOKEN (--forge gh|api forces one). The token goes to git through an environment header,
never into a command line or a file. crates.io needs no token.

Run from the directory that holds the repo clones, or pass --workspace. Missing org repos
are cloned there (partial clone). Default is a DRY RUN: it prints the operations and
triggers nothing. --execute runs the ready release workflows with `gh workflow run`, all
at the same time, after one `yes` (--yes skips it). It continues for a repo once its
versions show on crates.io; later jobs of the run do not matter. A crate is released unless
its head version is already on crates.io, or the head is only CI's post-release bump (set by
a `ci: Bump to version X` commit, with nothing but Cargo.toml and Cargo.lock changed since).
Such a bump head is listed in `bump_heads`, not released, and the newest published version
stands in for it. One run also releases only the crates in its first plan.
--max-releases N caps the repos released.

A step is blocked while a repo's own requirements do not admit the versions released below
it. Repin that repo first. A release publishes to crates.io and cannot be undone.

Repos pinned by git (hoprnet). The library crates reach the start repo through the repo behind
each git pin (hoprnet: hopr-lib and its siblings), so that repo is a step: its branch comes
from the pin (`branch = X`, or the default branch when a `rev` pin is on it), it gets a repin
for requirements that do not admit the released versions plus a refresh of its own Cargo.lock,
and it is never released. The start repo's final operation waits for it and moves the git
revision (`cargo update -p hopr-lib`; a `rev` pin is edited to the branch head).

Git-ref pins. A dependency of the start repo pinned to a git ref of an org repo (git = ...,
branch/rev/tag = ...) is planned for a swap to a released version. When crates.io has a
version at or above the locked one, the swap joins the final operation. Otherwise a
`pin_swap` operation is `blocked` and says why, unless the repo behind the pin is a step
(hoprnet), which is never released: then the pin is a note in `git_pinned_swaps`. [patch] git
entries are only warned about.

--execute-no-release. For each ready repin or final operation: create a branch in a git
worktree (<workspace>/<repo>-worktrees/), edit Cargo.toml (requirements, version bumps, git
pins), refresh Cargo.lock (cargo, inside `nix develop` when the repo has a flake, so hooks
run), commit, push, and open a PR. It asks `yes` once (--yes skips it). It never
merges and never releases. A branch name is derived from the edits, so a re-run finds the
open PR and does not push a second one. --execute does the releases first, then the same.

Bump level. A published crate whose requirements must be raised is bumped and released again.
When the raised dependency moves to another compatibility line (its major, or its minor for
0.x) and the crate re-exports it (`pub use`), the crate's own public API changes too, so the
plan proposes the next breaking version (major, or minor for 0.x) instead of a patch, and marks
the bump `level` and `reason`. Its dependents are then re-checked against that version, which
carries the change up the tree. A bump is only a proposal: it is a version edit in the repin PR.
A repo's re-export check covers all its crates, and a gap in a virtual workspace root counts
for all of them, so a multi-crate repo may get more bumps than it needs; review the PR.

Order rule. The start repo's PR is never opened while the PR of the repo it waits for (hoprnet)
is open: its operation stays `pending` until that PR is merged. --wait-merge MINUTES makes the
script wait for the PRs it opened (it never merges them), then re-plan at once and open the PRs
that were waiting. A PR closed without a merge, or the time limit, stops the run (state paused).

Output. -o json (default) prints one indented JSON document, -o mjson the same minified on
one line, -o plain a text rendering. Schema "hopr-release-cascade/v2"; progress goes to stderr. The dry run lists every operation to do, so a bot can propagate the repins,
turn the list into a workflow, or run the releases. Fields:
  state            release_ready | repin_required | final_repin_required
                   | manual_release_required | blocked | complete | paused | error
  summary          one sentence on where the cascade stands
  repos_to_update  repo, branch, its operation ids, needs_pr, crates it releases
  crates_to_change crate, version to publish, repo, branch, republish, operation id
  operations       the full ordered list, leaf first. Each has id, kind, repo, branch, status
                   (ready | pending | blocked), depends_on, description, and per kind:
                   release / release_manual: crates, workflow, command, republish
                   repin: edits (file, line, key, current, required), version_bumps, then
                   final_repin: edits (also git-pin swaps), lock_updates (crate, locked, target), then
                   pin_swap: blocked_reason (a git pin with no published version yet)
  git_pinned_swaps git pins kept because their repo is a step that is never released
  bump_heads       heads that are only CI's version bump: crate, head, newest published
  pull_requests    PRs opened or found by --execute / --execute-no-release
                   (operation, repo, base, branch, url, state opened | exists)
  ready_operations ids that can run now (no unfinished dependency)
  layers           operation ids per layer. An operation's layer is 1 + the highest layer of its
                   depends_on; operations in one layer are independent (concurrency_group
                   "layer-N" on each operation)
  next             the first ready operation, or {"kind": "none"}
  conditions       what must hold before the cascade continues, each with a verify command
  resume           the command to run once the conditions hold (null when complete)
  fixed_inputs, git_pinned_out_of_scope, warnings
Exit code: 0 for every state except error (2).
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse, base64, datetime, hashlib, json, shlex, os, re, shutil, subprocess, sys, threading, time, tomllib, urllib.parse, urllib.request, urllib.error
from pathlib import Path

ORG = "hoprnet"
UA = "hoprnet-release-cascade"
PUBLISH_STEP = "Publish library"  # step name in hoprnet/hopr-workflows build-library.yaml
SKIP_SEGMENTS = ("target", "vendor", "node_modules")  # directories whose manifests are not part of the repo's crates
DEP_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "hopr-release" / "crates-repository.json"
WORKSPACE = Path.cwd()
SCHEMA = "hopr-release-cascade/v2"
FORGE = None  # GhForge or ApiForge, chosen in main()
RESUME_FLAGS = ""  # flags the resume command repeats, set in main()


class Stop(Exception):
    """End the run with a final status report instead of a stack trace."""

    def __init__(self, msg, state="error"):
        super().__init__(msg)
        self.state = state


def die(msg, state="error"):
    raise Stop(msg, state)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def run(*a, check=True, cwd=None):
    env = {**os.environ, **FORGE.git_env()} if FORGE is not None and FORGE.git_env() else None
    r = subprocess.run(a, capture_output=True, text=True, env=env, cwd=cwd)
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
        FORGE.clone(repo, WORKSPACE / repo)
    git(repo, "fetch", "-q", "origin")


def default_branch(repo):
    return git(repo, "symbolic-ref", "refs/remotes/origin/HEAD").strip().removeprefix("refs/remotes/origin/")


def ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s.split("-")[0])[:3])


def prerelease(s):
    """The prerelease tag of a version or of a comparator such as `=1.0.0-rc.1`, without build metadata."""
    return s.split("+")[0].partition("-")[2]


def vkey(s):
    """Sort key: numeric version first, a stable release above its prereleases, prereleases by their parts."""
    pre = prerelease(s)
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")) if pre else ()
    return (ver(s), 0 if pre else 1, parts)


# ---------- crates.io ----------

class Throttle:
    """At most one call per `interval` seconds, across threads."""

    def __init__(self, interval, clock=time.monotonic, sleep=time.sleep):
        self.interval, self.clock, self.sleep, self.last, self.lock = interval, clock, sleep, None, threading.Lock()

    def wait(self):
        with self.lock:
            if self.last is not None and self.clock() - self.last < self.interval:
                self.sleep(self.interval - (self.clock() - self.last))
            self.last = self.clock()


CRATES_THROTTLE = Throttle(1.0)  # crates.io asks for at most one API request per second


def crates_io(name, version=None):
    url = f"https://crates.io/api/v1/crates/{name}" + (f"/{version}" if version else "")
    CRATES_THROTTLE.wait()
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
    if not _repo_cache.get(crate):  # a crate with no repository yet is asked again next time
        d = crates_io(crate)
        _repo_cache[crate] = (d or {}).get("crate", {}).get("repository")
        if _repo_cache[crate]:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(_repo_cache, indent=1))
    url = _repo_cache.get(crate) or ""
    m = re.match(r"https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+)", url, re.I)
    if m and m.group(1).lower() == ORG:
        return m.group(2).removesuffix(".git")
    return None


# ---------- manifests ----------

def skipped_manifest(path):
    """Vendored and build directories anywhere in the path, and the top-level lib/ (vendored libraries)."""
    return path.startswith("lib/") or any(seg in SKIP_SEGMENTS for seg in path.split("/")[:-1])


def tree_files(repo, ref):
    return [f for f in git(repo, "ls-tree", "-r", "--name-only", ref).split("\n")
            if f.endswith("Cargo.toml") and not skipped_manifest(f)]


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


def find_bump(commits, version):
    """Newest commit hash whose subject is CI's `ci: Bump to version <version>`. commits: (hash, subject), newest first."""
    want = f"ci: Bump to version {version}"
    return next((h for h, subject in commits if subject.strip() == want), None)


def only_cargo_files(files):
    return all(Path(f).name in ("Cargo.toml", "Cargo.lock") for f in files)


def bump_only(repo, ref, version):
    """True when `version` is only CI's post-release bump: set by a `ci: Bump to version` commit, with nothing
    but Cargo.toml and Cargo.lock changed since. Such a head has no new code and is not a release."""
    rows = git(repo, "log", "--format=%H%x09%s", "-n", "300", ref).splitlines()
    h = find_bump([tuple(r.split("\t", 1)) for r in rows if "\t" in r], version)
    return bool(h) and only_cargo_files(git(repo, "diff", "--name-only", h, ref).split())


def pick_latest_compatible(versions, head):
    """Highest release in `versions` on the same breaking line as `head` (same major, or same minor for 0.x)."""
    a, b, _ = (ver(head) + (0, 0, 0))[:3]
    same = [v for v in versions if "-" not in v and (ver(v) + (0, 0, 0))[:1] == (a,) and (a > 0 or (ver(v) + (0, 0, 0))[1] == b)]
    return max(same, key=ver) if same else None


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


def all_versions(crate):
    """Every version crates.io has seen for a crate, yanked ones included."""
    return [v["num"] for v in (crates_io(crate) or {"versions": []})["versions"]]


def next_free_patch(seen, v):
    """The next patch version after `v` that is not in `seen`."""
    v = bump_patch(v)
    while v in set(seen):
        v = bump_patch(v)
    return v


def bump_patch(v):
    a, b, c = (ver(v) + (0, 0, 0))[:3]
    return f"{a}.{b}.{c + 1}"


def dep_entries(ms):
    """Every versioned dependency: {file, table, key, crate, req}. Resolves `package =` renames."""
    out = []

    def table(file, t, label):
        for key, spec in (t or {}).items():
            crate, req = key, None
            if isinstance(spec, str):
                req = spec
            elif isinstance(spec, dict):
                crate, req = spec.get("package", key), spec.get("version")
            if req:
                out.append(dict(file=file, table=label, key=key, crate=crate, req=req))

    for f, m in ms.items():
        for t in DEP_TABLES:
            table(f, m.get(t), t)
        for cfg, tgt in m.get("target", {}).items():
            for t in DEP_TABLES:
                table(f, tgt.get(t), f"target.{cfg}.{t}")
        table(f, m.get("workspace", {}).get("dependencies"), "workspace.dependencies")
    return out


def git_pins(ms):
    """Dependencies pinned to a git ref of an org repo: {file, table, key, crate, git, ref}."""
    out = []

    def table(file, t, label):
        for key, spec in (t or {}).items():
            if isinstance(spec, dict) and f"github.com/{ORG}/" in spec.get("git", "").lower():
                ref = next((f"{k} {spec[k]}" for k in ("branch", "rev", "tag") if k in spec), "default branch")
                out.append(dict(file=file, table=label, key=key, crate=spec.get("package", key), git=spec["git"], ref=ref))

    for f, m in ms.items():
        for t in DEP_TABLES:
            table(f, m.get(t), t)
        for cfg, tgt in m.get("target", {}).items():
            for t in DEP_TABLES:
                table(f, tgt.get(t), f"target.{cfg}.{t}")
        table(f, m.get("workspace", {}).get("dependencies"), "workspace.dependencies")
    return out


def group_git_pins(pins, sources, rev_branch):
    """Group the start repo's git pins by (org repo, branch). A branch pin gives its branch; a rev pin gives the
    branch the rev is on (rev_branch(repo, rev)), or is left out; tags are left out. -> [{repo, branch, crates, locked, style}]"""
    groups = {}
    for p in pins:
        m = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", p["git"])
        if not m or m.group(1).lower() != ORG:
            continue
        repo = m.group(2).removesuffix(".git")
        kind, _, val = p["ref"].partition(" ")
        branch = val if kind == "branch" else rev_branch(repo, val) if kind == "rev" else None
        if not branch:
            continue
        g = groups.setdefault((repo, branch), dict(repo=repo, branch=branch, crates=[], locked=None, style=kind))
        g["crates"].append(p["crate"])
        g["locked"] = g["locked"] or (sources.get(p["crate"]) or (None, None))[1]
    return [dict(g, crates=sorted(set(g["crates"]))) for _, g in sorted(groups.items())]


def patch_pins(ms):
    """Crates with a git entry under [patch.*]. The script does not edit those."""
    return sorted({spec.get("package", key) for m in ms.values() for reg in m.get("patch", {}).values()
                   for key, spec in reg.items() if isinstance(spec, dict) and f"github.com/{ORG}/" in spec.get("git", "").lower()})


def admits(req, v):
    """Minimal semver requirement check: ^ ~ = > >= < <= * and comma lists."""
    if "-" in v and "-" not in req:
        return False
    vt = ver(v)
    for part in (p.strip() for p in req.split(",")):
        if part in ("", "*"):
            continue
        if part.endswith(".*"):
            part = "~" + part[:-2]  # Cargo: 1.2.* is ~1.2 and 1.* is ~1
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
            ok = (vt == (a, b, c) if n == 3 else vt[:n] == (a, b, c)[:n]) and prerelease(v) == prerelease(part)
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

    owned, pinned, git_sources, seen = {}, {}, {}, set()
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
                    pinned[c["name"]] = c["version"]
                    m = re.match(r"git\+([^?#]+)(?:\?[^#]*)?#(.+)", src)
                    if m:
                        git_sources[c["name"]] = (m.group(1), m.group(2))
                stack.append(c)
            elif src.startswith("registry+"):
                r = org_repo_of(c["name"])
                if r:
                    old = owned.get(c["name"])
                    owned[c["name"]] = (r, max(c["version"], old[1], key=vkey) if old else c["version"])
                    stack.append(c)
            else:
                stack.append(c)
    return owned, pinned, git_sources


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


def build_plan(start_repo, start_branch, planned=None):
    """`planned`: crates of the run's first plan. A later plan treats other unpublished heads, the version bumps CI
    pushes after a release, as the newest published version, so lock updates target versions that exist."""
    ensure(start_repo)
    if not has_ref(start_repo, start_branch):
        die(f"{start_repo}: no branch origin/{start_branch}")
    log(f"discovering {ORG} crates from {start_repo}@{start_branch} (crates.io lookups are cached) ...")
    owned, pinned, git_sources = discover(start_repo, start_branch)
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
    releasing, bump_heads = set(), []
    for k in order:
        s = plan[k]
        s["requires"] = sorted({e["crate"] for e in s["entries"] if e["crate"] in crate_step and crate_step[e["crate"]] != k})
        s["todo"] = {c: v for c, v in s["head"].items() if not is_published(c, v)}
        # a head that is only CI's post-release bump (or, in a later plan of one run, any head outside the first
        # plan) is not a release; the newest published version stands in for it
        for c in [c for c, v in s["todo"].items()
                  if (planned is not None and (c, v) not in planned) or bump_only(s["repo"], f"origin/{s['branch']}", v)]:
            bump_heads.append(dict(crate=c, head=s["todo"].pop(c), published=pick_latest_compatible(published_versions(c), s["head"][c])))
            target[c] = bump_heads[-1]["published"] or s["head"][c]
        s["gaps"] = gaps(s["entries"], target, owned)
        # a published crate whose requirements lag must be bumped and released again, also next to an unpublished sibling
        pb = plan_bumps(s, all_versions, lambda dep, key: reexports_dep(s["repo"], f"origin/{s['branch']}", dep, key))
        s["todo"], s["bump_crates"], s["bump_info"], s["release_versions"] = pb["todo"], pb["bump_crates"], pb["bump_info"], pb["release_versions"]
        s["republish"] = bool(s["bump_crates"])
        s["hard_requires"] = hard_needs(s["entries"], set(s["crates"]), releasing)
        releasing |= set(s["release_versions"])
        target.update(s["release_versions"])  # leaf first, so downstream repos see the version that will exist
    start_ms = manifests(start_repo, f"origin/{start_branch}")
    start_entries = dep_entries(start_ms)
    pin_groups = group_git_pins(git_pins(start_ms), git_sources, rev_branch=branch_of_rev)
    step_crates = {c for g in pin_groups for c in g["crates"]}
    pins = []
    for pin in git_pins(start_ms):
        locked = pinned.get(pin["crate"])
        swap_to = pick_latest_compatible(published_versions(pin["crate"]), locked) if locked else None
        if swap_to and vkey(swap_to) < vkey(locked):
            swap_to = None
        pins.append(dict(pin, line=line_of(start_repo, f"origin/{start_branch}", pin["file"], pin["key"], pin.get("table")),
                         locked=locked, target=swap_to, step=pin["crate"] in step_crates))
    upstream, git_updates = [], []
    for g in pin_groups:
        try:
            ensure(g["repo"])
        except Stop:
            continue
        ref = f"origin/{g['branch']}"
        if not has_ref(g["repo"], g["branch"]):
            continue
        in_lock = {}
        for pk_ in tomllib.loads(git(g["repo"], "show", f"{ref}:Cargo.lock"))["package"]:
            if pk_["name"] in owned and vkey(pk_["version"]) > vkey(in_lock.get(pk_["name"], "0.0.0")):
                in_lock[pk_["name"]] = pk_["version"]
        upstream.append(dict(repo=g["repo"], branch=g["branch"], gaps=gaps(dep_entries(manifests(g["repo"], ref)), target, owned),
                             lock_updates=[dict(crate=c, locked=in_lock[c], target=target[c]) for c in sorted(in_lock)
                                           if c in target and vkey(target[c]) > vkey(in_lock[c])]))
        head = git(g["repo"], "rev-parse", ref).strip()
        if g["locked"] and head != g["locked"]:
            rev_edits = [dict(file=p["file"], line=line_of(start_repo, f"origin/{start_branch}", p["file"], p["key"], p.get("table")),
                              key=p["key"], crate=p["crate"], current=p["ref"], required=head, swap="git_rev")
                         for p in git_pins(start_ms) if p["crate"] in g["crates"] and p["ref"].startswith("rev ")]
            git_updates.append(dict(repo=g["repo"], branch=g["branch"], crates=g["crates"], locked=g["locked"][:9], head=head[:9],
                                    edits=rev_edits))
    return dict(start=(start_repo, start_branch), steps=steps, fixed=fixed, pinned=sorted(pinned), pins=pins,
                upstream=upstream, git_updates=git_updates,
                patched=patch_pins(start_ms), bump_heads=bump_heads,
                repin=gaps(start_entries, target, owned), locked={c: v for c, (_, v) in owned.items()}, target=target)


def breaking_line(v):
    """The part of a version that Cargo treats as compatible: the major, or the minor for 0.x, or the patch for 0.0.x."""
    a, b, c = (ver(v) + (0, 0, 0))[:3]
    return (a,) if a > 0 else (0, b) if b > 0 else (0, 0, c)


def is_breaking(req, want):
    """True when `want` is on another compatibility line than the version `req` asks for."""
    m = re.search(r"\d+(?:\.\d+){0,2}", req)
    return bool(m) and breaking_line(m.group(0)) != breaking_line(want)


def bump_breaking(v):
    a, b, c = (ver(v) + (0, 0, 0))[:3]
    return f"{a + 1}.0.0" if a > 0 else f"0.{b + 1}.0" if b > 0 else f"0.0.{c + 1}"


def reexports_dep(repo, ref, dep, key):
    """True when the crate has `pub use <dep>::...` (or `pub use <dep>;`) in a .rs file at `ref`. `key` is the dependency's name in Cargo.toml."""
    idents = "|".join(sorted({dep.replace("-", "_"), key.replace("-", "_")}))
    return run("git", "-C", str(WORKSPACE / repo), "grep", "-q", "-E", rf"pub use ({idents})(::|;| as )", ref, "--", "*.rs", check=False)[0] == 0


def plan_bumps(s, versions_of, reexports):
    """Which crates of a step need a new version, and which. A published crate with a stale requirement is bumped; a
    crate that re-exports a dependency whose version moved to another compatibility line gets the next breaking
    version (its public API changes with it), not a patch. -> {todo, bump_crates, release_versions, bump_info}"""
    todo, info = dict(s["todo"]), {}
    bump = set(crates_with_gaps([c for c in s["head"] if c not in todo], s["files"], s["gaps"]))
    breaking = [g for g in s["gaps"] if is_breaking(g["req"], g["want"]) and reexports(g["crate"], g["key"])]
    proposed = {}
    for c in crates_with_gaps(sorted(s["head"]), s["files"], breaking):
        head, latest = s["head"][c], max(versions_of(c), key=vkey, default=s["head"][c])
        if c in todo and breaking_line(head) != breaking_line(latest):
            continue  # the head is already on a newer line than anything published
        bump.add(c)
        todo.pop(c, None)
        base = max(head, latest, key=vkey)
        proposed[c] = bump_breaking(base)
        deps = ", ".join(f"{g['crate']} {g['req']} -> {g['want']}" for g in breaking)
        info[c] = dict(level="major" if breaking_line(base)[0] > 0 else "minor", reason=f"re-exports a dependency that moves line: {deps}")
    release = {**todo, **{c: proposed.get(c) or next_free_patch(versions_of(c), s["head"][c]) for c in sorted(bump)}}
    for c in bump:
        info.setdefault(c, dict(level="patch", reason="requirements raised to the released versions"))
    return dict(todo=todo, bump_crates=sorted(bump), release_versions=release, bump_info=info)


def crates_with_gaps(crates, files, gap_list):
    """Published crates whose manifest holds a stale requirement. A gap in the root manifest of a virtual
    workspace (workspace.dependencies) may touch every crate, so it counts for all of them."""
    gap_files = {g["file"] for g in gap_list}
    direct = [c for c in crates if files[c] in gap_files]
    return sorted(crates) if "Cargo.toml" in gap_files and "Cargo.toml" not in files.values() else direct


def branch_of_rev(repo, rev):
    """The repo's default branch when `rev` is on it, else None."""
    b = default_branch(repo)
    ok = run("git", "-C", str(WORKSPACE / repo), "merge-base", "--is-ancestor", rev, f"origin/{b}", check=False)[0] == 0
    return b if ok else None


def gaps(entries, target, owned):
    """Requirements on org crates that do not admit the version released below them."""
    return [dict(e, want=target[e["crate"]]) for e in entries
            if e["crate"] in owned and e["crate"] in target and not admits(e["req"], target[e["crate"]])]


# ---------- GitHub access: gh when it works, else the REST API with a token ----------

def choose_forge(which=shutil.which, gh_ok=None, env=os.environ, force=None):
    """'gh' when gh is installed and logged in, else 'api' when GH_TOKEN or GITHUB_TOKEN is set."""
    gh_ok = gh_ok or (lambda: run("gh", "auth", "status", check=False)[0] == 0)
    gh_ready = bool(which("gh")) and gh_ok()
    token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
    if force == "gh":
        return "gh" if gh_ready else die("--forge gh: gh is missing or not logged in (gh auth login)")
    if force == "api":
        return "api" if token else die("--forge api: set GH_TOKEN or GITHUB_TOKEN")
    if gh_ready:
        return "gh"
    if token:
        return "api"
    return die("no GitHub access: install gh and log in (gh auth login), or set GH_TOKEN or GITHUB_TOKEN")


class GhForge:
    """GitHub through the gh CLI."""

    def git_env(self):
        return {}

    def clone(self, repo, dest):
        run("gh", "repo", "clone", f"{ORG}/{repo}", str(dest), "--", "--filter=blob:none", "--no-checkout", "--quiet")

    def pr_state(self, repo, number):
        d = json.loads(run("gh", "api", f"repos/{ORG}/{repo}/pulls/{number}")[1])
        return "merged" if d.get("merged") else d["state"]

    def find_pr(self, repo, head):
        rows = json.loads(run("gh", "pr", "list", "--repo", f"{ORG}/{repo}", "--head", head, "--state", "open", "--json", "url")[1] or "[]")
        return rows[0]["url"] if rows else None

    def dispatch(self, repo, workflow, ref, inputs):
        """Triggers the workflow. Returns the run id when gh prints the run URL (gh 2.87+), else None."""
        return run_id_from_output(run("gh", "workflow", "run", workflow, "--repo", f"{ORG}/{repo}", "--ref", ref,
                                      *[a for k, v in inputs.items() for a in ("-f", f"{k}={v}")])[1])

    def run_ids(self, repo, workflow, branch):
        rows = json.loads(run("gh", "run", "list", "--repo", f"{ORG}/{repo}", "--workflow", workflow, "--branch", branch,
                              "--event", "workflow_dispatch", "--limit", "20", "--json", "databaseId")[1] or "[]")
        return {r["databaseId"] for r in rows}

    def run_info(self, repo, run_id):
        return json.loads(run("gh", "run", "view", str(run_id), "--repo", f"{ORG}/{repo}", "--json", "status,conclusion,jobs")[1])

    def create_pr(self, repo, head, base, title, body, draft=False):
        out = run("gh", "pr", "create", "--repo", f"{ORG}/{repo}", "--head", head, "--base", base, "--title", title,
                  "--body", body, *(["--draft"] if draft else []))[1]
        return out.strip().splitlines()[-1]


def _http(token):
    def call(method, url, body=None):
        req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": UA,
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw else None)
    return call


class ApiForge:
    """GitHub through the REST API with a token. Git uses the same token through an environment header, so the
    token is in no command line and in no file."""

    API = "https://api.github.com"

    def __init__(self, token, http=None):
        self.token, self.http = token, http or _http(token)

    def git_env(self):
        basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
        return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"}

    def _call(self, method, path, body=None, ok=(200,)):
        status, data = self.http(method, self.API + path, body)
        if status not in ok:
            die(f"GitHub API {method} {path} returned {status}: {(data or {}).get('message', '')}")
        return data

    def clone(self, repo, dest):
        run("git", "clone", "--filter=blob:none", "--no-checkout", "--quiet", f"https://github.com/{ORG}/{repo}", str(dest))

    def pr_state(self, repo, number):
        d = self._call("GET", f"/repos/{ORG}/{repo}/pulls/{number}")
        return "merged" if d.get("merged") else d["state"]

    def find_pr(self, repo, head):
        rows = self._call("GET", f"/repos/{ORG}/{repo}/pulls?head={ORG}:{urllib.parse.quote(head, safe='')}&state=open")
        return rows[0]["html_url"] if rows else None

    def dispatch(self, repo, workflow, ref, inputs):
        """Triggers the workflow. Returns the run id when the API reports it (return_run_details), else None."""
        url = f"{self.API}/repos/{ORG}/{repo}/actions/workflows/{workflow}/dispatches"
        status, data = self.http("POST", url, {"ref": ref, "inputs": inputs, "return_run_details": True})
        if status == 422:  # an API version that does not know return_run_details
            status, data = self.http("POST", url, {"ref": ref, "inputs": inputs})
        if status not in (200, 204):
            die(f"GitHub API POST dispatches for {repo} returned {status}: {(data or {}).get('message', '')}")
        return (data or {}).get("workflow_run_id")

    def run_ids(self, repo, workflow, branch):
        q = f"branch={urllib.parse.quote(branch, safe='')}&event=workflow_dispatch&per_page=20"
        return {r["id"] for r in self._call("GET", f"/repos/{ORG}/{repo}/actions/workflows/{workflow}/runs?{q}")["workflow_runs"]}

    def run_info(self, repo, run_id):
        run_ = self._call("GET", f"/repos/{ORG}/{repo}/actions/runs/{run_id}")
        jobs = self._call("GET", f"/repos/{ORG}/{repo}/actions/runs/{run_id}/jobs?per_page=100")["jobs"]
        return dict(status=run_["status"], conclusion=run_["conclusion"],
                    jobs=[dict(name=j["name"], conclusion=j["conclusion"],
                               steps=[dict(name=st["name"], conclusion=st["conclusion"]) for st in j.get("steps", [])])
                          for j in jobs])

    def create_pr(self, repo, head, base, title, body, draft=False):
        return self._call("POST", f"/repos/{ORG}/{repo}/pulls",
                          dict(head=head, base=base, title=title, body=body, draft=draft), ok=(201,))["html_url"]


def run_id_from_output(text):
    m = re.search(r"/actions/runs/(\d+)", text or "")
    return int(m.group(1)) if m else None


class AnonForge:
    """No gh and no token: enough for a dry run, which only clones public repos."""

    def git_env(self):
        return {}

    def clone(self, repo, dest):
        run("git", "clone", "--filter=blob:none", "--no-checkout", "--quiet", f"https://github.com/{ORG}/{repo}", str(dest))

    def _need(self, what):
        die(f"{what} needs GitHub access: install gh and log in (gh auth login), or set GH_TOKEN or GITHUB_TOKEN")

    def dispatch(self, *a, **kw):
        self._need("releasing")

    def run_ids(self, *a, **kw):
        self._need("releasing")

    def run_info(self, *a, **kw):
        self._need("releasing")

    def find_pr(self, *a, **kw):
        self._need("opening a pull request")

    def pr_state(self, *a, **kw):
        self._need("waiting for a merge")

    def create_pr(self, *a, **kw):
        self._need("opening a pull request")


def make_forge(force=None):
    kind = choose_forge(force=force)
    return GhForge() if kind == "gh" else ApiForge(os.environ.get("GH_TOKEN") or os.environ["GITHUB_TOKEN"])


# ---------- status report: the single output format ----------

def find_line(text, key, table=None):
    """1-based line of `key = ...` inside the named table (any table when None); 0 when absent."""
    cur = None
    for i, line in enumerate(text.split("\n"), 1):
        h = re.match(r"\s*\[+([^\]]+)\]+\s*$", line)
        if h:
            cur = re.sub(r"[\"']", "", h.group(1)).strip()
            if table and cur == f"{table}.{key}":  # [dependencies.key]
                return i
        elif (table is None or cur == table) and re.match(rf'\s*"?{re.escape(key)}"?\s*=', line):
            return i
    return 0


def line_of(repo, ref, file, key, table=None):
    return find_line(git(repo, "show", f"{ref}:{file}"), key, table)


def edits(repo, branch, gs):
    return [dict(file=g["file"], line=line_of(repo, f"origin/{branch}", g["file"], g["key"], g.get("table")), key=g["key"],
                 crate=g["crate"], current=g["req"], required=g["want"]) for g in gs]


def resume_flags(a):
    """The run settings a resume command repeats, shell-quoted."""
    parts = []
    if a.workspace:
        parts += ["--workspace", str(a.workspace)]
    if a.forge != "auto":
        parts += ["--forge", a.forge]
    if a.output != "json":
        parts += ["-o", a.output]
    if a.release_type != "patch":
        parts += ["--release-type", a.release_type]
    if a.max_releases is not None:
        parts += ["--max-releases", str(a.max_releases)]
    if getattr(a, "wait_merge", 0):
        parts += ["--wait-merge", str(a.wait_merge)]
    return shlex.join(parts)


def parse_start(text):
    repo, _, branch = text.partition("@")
    if not repo or not branch:
        die("--from needs REPO@BRANCH, for example edge-client@main")
    return repo, branch


def ask(prompt):
    """Prompt on stderr, so stdout holds only the report."""
    print(prompt, end="", file=sys.stderr, flush=True)
    return input()


def resume_command(start):
    return f"python3 {Path(sys.argv[0]).resolve()} --from {start[0]}@{start[1]}" + (f" {RESUME_FLAGS}" if RESUME_FLAGS else "") + " --execute"


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
            bumps = [dict(crate=c, file=s["files"][c], current=s["head"][c], suggested=s["release_versions"][c],
                          **s.get("bump_info", {}).get(c, dict(level="patch", reason="requirements raised to the released versions")))
                     for c in s.get("bump_crates", [])]
            repin = new("repin", repo=s["repo"], branch=s["branch"], edits=edits(s["repo"], s["branch"], s["gaps"]),
                        version_bumps=bumps, depends_on=sorted({rel[g["crate"]] for g in s["gaps"] if g["crate"] in rel}),
                        description=f"repin {s['repo']}@{s['branch']} to the released versions"
                                    + (" and bump its version" if bumps else ""),
                        then="open a PR with these edits and a refreshed Cargo.lock, then merge it")
        if s["release_versions"]:
            crates = s["release_versions"]
            deps = {rel[c] for c in s.get("hard_requires", s["requires"]) if c in rel} | ({repin["id"]} if repin else set())
            op = new("release" if s["workflow"] else "release_manual", repo=s["repo"], branch=s["branch"],
                     workflow=s["workflow"], republish=s["republish"], depends_on=sorted(deps),
                     crates=[dict(name=c, version=v) for c, v in crates.items()],
                     description=f"release {', '.join(f'{c} {v}' for c, v in crates.items())} from {s['repo']}@{s['branch']}",
                     command=(f"gh workflow run {s['workflow']} --repo {ORG}/{s['repo']} --ref {s['branch']} -f release_type=patch"
                              if s["workflow"] else None))
            rel.update({c: op["id"] for c in crates})
    # a repo the start repo pins by git (hoprnet) takes the released versions before the start repo moves to it
    up_ids = []
    for u in plan.get("upstream", []):
        ed = edits(u["repo"], u["branch"], u["gaps"])
        if ed or u["lock_updates"]:
            named = {g["crate"] for g in u["gaps"]} | {x["crate"] for x in u["lock_updates"]}
            op = new("repin", repo=u["repo"], branch=u["branch"], edits=ed, lock_updates=u["lock_updates"], version_bumps=[],
                     depends_on=sorted({rel[c] for c in named if c in rel}),
                     description=f"update {u['repo']}@{u['branch']}, which the start repo pins by git, to the released versions",
                     then="open a PR with these edits and a refreshed Cargo.lock, then merge it")
            up_ids.append(op["id"])
    # the start repo's lock pins older versions even when its requirements already admit the new ones
    lock_updates = [dict(crate=c, locked=v, target=plan["target"][c]) for c, v in sorted(plan.get("locked", {}).items())
                    if c in plan.get("target", {}) and vkey(plan["target"][c]) > vkey(v)]
    pins = plan.get("pins", [])
    swaps = [dict(file=p["file"], line=p["line"], key=p["key"], crate=p["crate"], current=f"git {p['git']} {p['ref']}",
                  required=p["target"], swap="git_to_version") for p in pins if p["target"]]
    # a crate swapped to a published version no longer has a git revision to move
    swapped = {p["crate"] for p in pins if p["target"]}
    git_updates = [dict(g, crates=[c for c in g["crates"] if c not in swapped],
                        **({"edits": [e for e in g["edits"] if e["crate"] not in swapped]} if "edits" in g else {}))
                   for g in plan.get("git_updates", [])]
    git_updates = [g for g in git_updates if g["crates"]]
    if plan["repin"] or lock_updates or swaps or git_updates:
        sr, sb = plan["start"]
        named = {g["crate"] for g in plan["repin"]} | {u["crate"] for u in lock_updates}
        rev_edits = [e for g in git_updates for e in g.get("edits", [])]
        new("final_repin", repo=sr, branch=sb, edits=edits(sr, sb, plan["repin"]) + swaps + rev_edits, lock_updates=lock_updates,
            git_updates=git_updates, version_bumps=[], depends_on=sorted({rel[c] for c in named if c in rel} | set(up_ids)),
            description=f"update {sr}@{sb} to the released versions"
                        + (f" (cargo update -p {' -p '.join(u['crate'] for u in lock_updates)})" if lock_updates else "")
                        + (f" and to the current {', '.join(sorted({g['repo'] + '@' + g['branch'] for g in git_updates}))}" if git_updates else ""),
            then="open a PR with the requirement edits and a refreshed Cargo.lock, then merge it")
    for p in pins:
        if not p["target"] and not p.get("step"):  # a pin to a repo that is a step is only a note (git_pinned_swaps)
            sr, sb = plan["start"]
            new("pin_swap", repo=sr, branch=sb, edits=[], crates=[p["crate"]], depends_on=[],
                description=f"swap the git pin of {p['crate']} ({p['ref']}) for a released version",
                blocked_reason=f"{p['crate']} has no published version >= {p['locked']} on crates.io; release it first, "
                               f"then this pin can become a version requirement")
    layer = {}
    for op in ops:  # dependencies come before their dependents in the list
        op["status"] = "blocked" if op["kind"] == "pin_swap" else "pending" if op["depends_on"] else "ready"
        layer[op["id"]] = 1 + max((layer[d] for d in op["depends_on"]), default=0)
        op["layer"], op["concurrency_group"] = layer[op["id"]], f"layer-{layer[op['id']]}"
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
                 verify=f"{resume.replace(' --execute', '')} (dry run); operation {op['id']} must be gone from the list")]


def status(plan):
    """The single report. `operations` is the full ordered list; `next` is the first one that can run now."""
    start = plan["start"]
    ops = operations(plan)
    fixed = [dict(f, on_crates_io=is_published(f["crate"], f["version"])) for f in plan["fixed"]]
    warnings = [f"fixed input {f['crate']} {f['version']} is missing or yanked on crates.io" for f in fixed if not f["on_crates_io"]]
    warnings += [f"[patch] entry for {c} is a git pin the script does not edit; remove it by hand once {c} is released"
                 for c in plan.get("patched", [])]
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
    elif any(o["status"] == "blocked" for o in ops):
        blocked = [o for o in ops if o["status"] == "blocked"]
        state = "blocked"
        summary = f"{len(blocked)} operation(s) blocked: " + "; ".join(o["blocked_reason"] for o in blocked[:3])
        nxt, conditions = dict(kind="none"), []
    else:
        state, summary = "complete", "every crate is released and the start repo requirements are current"
        nxt, conditions = dict(kind="none"), []
    return dict(schema=SCHEMA, generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                start=dict(repo=start[0], branch=start[1]), state=state, summary=summary,
                repos_to_update=repos_to_update, crates_to_change=crates_to_change, operations=ops,
                ready_operations=[o["id"] for o in ready],
                layers=[[o["id"] for o in ops if o["layer"] == n] for n in sorted({o["layer"] for o in ops})],
                pull_requests=[], bump_heads=plan.get("bump_heads", []),
                git_pinned_swaps=[dict(crate=p["crate"], ref=p["ref"], locked=p["locked"],
                                       note="the repo behind this pin is a step and is never released; the pin can become a "
                                            "version requirement once its crates are on crates.io")
                                  for p in plan.get("pins", []) if p.get("step") and not p["target"]],
                next=nxt, conditions=conditions,
                fixed_inputs=fixed, git_pinned_out_of_scope=plan["pinned"], warnings=warnings,
                resume=resume if state != "complete" else None)


def error_report(start, msg, state="error"):
    return dict(schema=SCHEMA, generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                start=dict(repo=start[0], branch=start[1]), state=state, summary=msg, repos_to_update=[],
                crates_to_change=[], operations=[], ready_operations=[], layers=[], pull_requests=[], bump_heads=[], git_pinned_swaps=[], next=dict(kind="none"), conditions=[],
                fixed_inputs=[], git_pinned_out_of_scope=[], warnings=[], resume=resume_command(start))


# ---------- applying repins in a git worktree (--execute-no-release) ----------

CO_AUTHOR = "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"


def new_req(current, want):
    """The requirement text for `want`, keeping a leading = ~ ^ and replacing a range with the plain version."""
    m = re.fullmatch(r"\s*(=|~|\^)?\s*\d[\w.+-]*\s*", current)
    return (m.group(1) or "") + want if m else want


def swap_git_text(text, e):
    """Replace a git pin on one line with a version requirement, keeping features and the other keys."""
    lines = text.split("\n")
    i = e["line"] - 1
    found = lines[i] if 0 <= i < len(lines) else "(no such line)"
    if "git" not in found or e["key"] not in found or "{" not in found:
        die(f"{e['file']}:{e['line']}: expected an inline git pin for {e['key']}, found: {found.strip()}")
    out = re.sub(r'\bgit\s*=\s*"[^"]*"\s*,?\s*', "", found, count=1)
    out = re.sub(r'\b(?:branch|rev|tag)\s*=\s*"[^"]*"\s*,?\s*', "", out, count=1)
    ver_kv = f'version = "{e["required"]}"'
    out = re.sub(r"\{\s*\}", "{ " + ver_kv + " }", out, count=1) if re.search(r"\{\s*\}", out) else \
        re.sub(r"\{\s*", "{ " + ver_kv + ", ", out, count=1)
    lines[i] = re.sub(r",\s*,", ",", out)
    return "\n".join(lines)


def rev_text(text, e):
    """Point a git pin's rev at a new commit, on the pin's line."""
    lines = text.split("\n")
    i = e["line"] - 1
    found = lines[i] if 0 <= i < len(lines) else "(no such line)"
    old = e["current"].partition(" ")[2]
    if f'rev = "{old}"' not in found or e["key"] not in found:
        die(f"{e['file']}:{e['line']}: expected {e['key']} with rev \"{old}\", found: {found.strip()}")
    lines[i] = found.replace(f'rev = "{old}"', f'rev = "{e["required"]}"', 1)
    return "\n".join(lines)


def edit_text(text, e):
    if e.get("swap") == "git_to_version":
        return swap_git_text(text, e)
    if e.get("swap") == "git_rev":
        return rev_text(text, e)
    lines = text.split("\n")
    i = e["line"] - 1
    found = lines[i] if 0 <= i < len(lines) else "(no such line)"
    if f'"{e["current"]}"' not in found or e["key"] not in found:
        die(f"{e['file']}:{e['line']}: expected {e['key']} with \"{e['current']}\", found: {found.strip()}")
    lines[i] = found.replace(f'"{e["current"]}"', f'"{new_req(e["current"], e["required"])}"', 1)
    return "\n".join(lines)


def bump_text(text, b):
    """Set the package version, in [package] or [workspace.package] only."""
    lines, table = text.split("\n"), None
    for i, line in enumerate(lines):
        h = re.match(r"\s*\[([^\]]+)\]\s*$", line)
        if h:
            table = h.group(1)
        elif table in ("package", "workspace.package") and re.match(rf'\s*version\s*=\s*"{re.escape(b["current"])}"', line):
            lines[i] = line.replace(f'"{b["current"]}"', f'"{b["suggested"]}"', 1)
            return "\n".join(lines)
    return die(f"{b['file']}: no package version \"{b['current']}\" to bump for {b['crate']}")


def lock_commands(op):
    cmds = ["cargo metadata --format-version 1 >/dev/null"]  # rewrites the lock for the edited requirements and versions
    cmds += [f"cargo update -p {c}" for c in dict.fromkeys(e["crate"] for e in op.get("edits", []))]
    cmds += [f"cargo update -p {u['crate']}@{u['locked']} --precise {u['target']}" for u in op.get("lock_updates", [])]
    cmds += [f"cargo update -p {c}" for c in dict.fromkeys(c for g in op.get("git_updates", []) for c in g["crates"])]
    return list(dict.fromkeys(cmds))


def branch_name(op, base_sha=None):
    """Deterministic, so a re-run finds its own branch. Everything the PR holds goes into the hash, including the git
    revision move and the base commit: a branch from an earlier run with other content must not be reused."""
    content = [op.get("edits"), op.get("version_bumps"), op.get("lock_updates"),
               [(g["repo"], g["branch"], sorted(g["crates"]), g["head"]) for g in op.get("git_updates", [])], base_sha]
    return f"chore/release-cascade-{op['kind']}-{hashlib.sha1(json.dumps(content, sort_keys=True).encode()).hexdigest()[:8]}"


def pr_text(op, start):
    crates = list(dict.fromkeys([e["crate"] for e in op.get("edits", [])] + [u["crate"] for u in op.get("lock_updates", [])]))
    title = f"chore(deps): {'repin' if op.get('edits') else 'update'} {', '.join(crates)} to the released versions"
    lines = [f"Part of the release cascade from {start[0]}@{start[1]}. Merge after the releases below are on crates.io.", ""]
    lines += [f"- {e['file']}:{e['line']}: {e['key']} {e['current']} -> {e['required']}" for e in op.get("edits", [])]
    lines += [f"- bump {b['crate']} {b['current']} -> {b['suggested']} ({b['file']})" for b in op.get("version_bumps", [])]
    lines += [f"- lock: {u['crate']} {u['locked']} -> {u['target']}" for u in op.get("lock_updates", [])]
    lines += [f"- git: {c} {g['locked']} -> {g['head']} ({g['repo']}@{g['branch']})" for g in op.get("git_updates", []) for c in g["crates"]]
    lines += ["", f"Operation {op['id']} ({op['kind']}). Generated by hopr-release's release-cascade.py."]
    return title, "\n".join(lines)


def in_shell(wt, cmds):
    """Run commands in the worktree, inside `nix develop` when the repo has a flake (toolchain and hooks)."""
    script = f"cd {shlex.quote(str(wt))} && " + " && ".join(cmds)
    if (Path(wt) / "flake.nix").exists():  # cwd matters: the shell hook (pre-commit setup) runs in the current directory
        run("nix", "develop", str(wt), "-c", "bash", "-c", script, cwd=wt)
    else:
        run("bash", "-c", script)


def hooked_git(wt, *args):
    """git in the worktree. With a flake it runs in `nix develop`, so the repo's pre-commit and pre-push hooks work."""
    cmd = ["git", "-C", str(wt), *args]
    if (Path(wt) / "flake.nix").exists():
        run("nix", "develop", str(wt), "-c", *cmd, cwd=wt)
    else:
        run(*cmd)


EDIT_KINDS = ("repin", "final_repin")


def op_key(o):
    return (o["repo"], o["branch"], o["kind"])


def ready_edit_ops(report, done=()):
    """Operations that become a PR now. Order rule: an operation that waits for another (the start repo waits for
    the hoprnet PR) is `pending`, so it never appears here while that PR is open."""
    return [o for o in report["operations"] if o["status"] == "ready" and o["kind"] in EDIT_KINDS and op_key(o) not in done]


def pr_number(url):
    return int(re.search(r"/pull/(\d+)", url).group(1))


def wait_for_merge(prs, minutes, poll=60, state_of=None, clock=time.monotonic, sleep=time.sleep):
    """Wait until every PR is merged. -> 'merged', 'closed' (one was closed without a merge) or 'timeout'. Never merges."""
    state_of = state_of or (lambda repo, n: FORGE.pr_state(repo, n))
    deadline = clock() + minutes * 60
    waiting = {pr["url"]: (pr["repo"], pr_number(pr["url"])) for pr in prs}
    while True:
        for url, (repo, n) in list(waiting.items()):
            st = state_of(repo, n)
            if st == "merged":
                del waiting[url]
            elif st == "closed":
                return "closed"
        if not waiting:
            return "merged"
        if clock() >= deadline:
            return "timeout"
        sleep(poll)


def apply_op(op, start, shell=None):
    """Apply a repin or final repin in a new worktree branch, commit, push, open a PR. Never merges."""
    shell = shell or in_shell
    repo, base = op["repo"], op["branch"]
    git(repo, "fetch", "-q", "origin")
    branch = branch_name(op, git(repo, "rev-parse", f"origin/{base}").strip())
    existing = FORGE.find_pr(repo, branch)
    if existing:
        return dict(operation=op["id"], repo=repo, base=base, branch=branch, url=existing, state="exists")
    wt = WORKSPACE / f"{repo}-worktrees" / branch.replace("/", "-")
    title, body = pr_text(op, start)
    if git(repo, "ls-remote", "--heads", "origin", branch).strip():  # pushed earlier, the PR step failed
        url = FORGE.create_pr(repo, branch, base, title, body)
        return dict(operation=op["id"], repo=repo, base=base, branch=branch, url=url, state="opened")
    if wt.exists() or git(repo, "branch", "--list", branch).strip():  # a failed attempt left this behind; nothing was pushed
        git(repo, "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(wt, ignore_errors=True)
        git(repo, "worktree", "prune")
        git(repo, "branch", "-D", branch, check=False)
    git(repo, "worktree", "add", "-q", "-b", branch, str(wt), f"origin/{base}")
    files = {}
    for kind, items, fn in (("edits", op.get("edits", []), edit_text), ("bumps", op.get("version_bumps", []), bump_text)):
        for item in items:
            path = wt / item["file"]
            files[item["file"]] = fn(files.get(item["file"]) or path.read_text(), item)
    for f, text in files.items():
        (wt / f).write_text(text)
    shell(wt, lock_commands(op))
    hooked_git(wt, "add", "--", *files, *(["Cargo.lock"] if (wt / "Cargo.lock").exists() else []))
    hooked_git(wt, "commit", "-q", "-m", title, "-m", f"{body}\n\n{CO_AUTHOR}")
    hooked_git(wt, "push", "-q", "-u", "origin", branch)
    url = FORGE.create_pr(repo, branch, base, title, body)
    return dict(operation=op["id"], repo=repo, base=base, branch=branch, url=url, state="opened", worktree=str(wt))


def op_detail(o):
    if o.get("blocked_reason"):
        return f"blocked: {o['blocked_reason']}"
    if o["kind"].startswith("release"):
        return ", ".join(f"{c['name']} {c['version']}" for c in o["crates"])
    parts = [f"{e['key']} {e['current']} -> {e['required']}" for e in o.get("edits", [])]
    parts += [f"bump {b['crate']} {b['current']} -> {b['suggested']}"
              + (f" ({b['level']}: {b['reason']})" if b.get("level", "patch") != "patch" else "") for b in o.get("version_bumps", [])]
    parts += [f"lock {u['crate']} {u['locked']} -> {u['target']}" for u in o.get("lock_updates", [])]
    parts += [f"git {c} {g['locked']} -> {g['head']} ({g['repo']}@{g['branch']})" for g in o.get("git_updates", []) for c in g["crates"]]
    return "; ".join(parts)


def render_plain(r):
    out = [f"release cascade from {r['start']['repo']}@{r['start']['branch']} ({r['schema']}, {r['generated_at']})",
           f"state: {r['state']}", f"summary: {r['summary']}"]
    n = r["next"]
    out.append("next: none" if n["kind"] == "none" else f"next: {n['id']} {n['kind']} {n['repo']}@{n['branch']}: {n['description']}")
    if r["operations"]:
        out.append(f"operations ({len(r['operations'])}):")
        for o in r["operations"]:
            deps = f" after {','.join(o['depends_on'])}" if o["depends_on"] else ""
            out.append(f"  {o['id']:<6}{o['concurrency_group']:<9}{o['status']:<8}{o['kind']:<15}"
                       f"{o['repo']}@{o['branch']}{deps}: {op_detail(o)}")
    for pr in r.get("pull_requests", []):
        out.append(f"pull request ({pr['state']}): {pr['url']} {pr['repo']} {pr['branch']} -> {pr['base']}")
    for c in r["conditions"]:
        out += [f"condition {c['id']}: {c['description']}", f"  verify: {c['verify']}"]
    for f in r["fixed_inputs"]:
        out.append(f"fixed input: {f['crate']} {f['version']} ({f.get('reason', '')}, on crates.io: {f['on_crates_io']})")
    out += [f"git pin kept: {n['crate']} ({n['ref']}, locked {n['locked']}): {n['note']}" for n in r.get("git_pinned_swaps", [])]
    out += [f"bump head, not released: {b['crate']} {b['head']} (CI bump after {b['published']})" for b in r.get("bump_heads", [])]
    if r["git_pinned_out_of_scope"]:
        out.append("git-pinned: " + ", ".join(r["git_pinned_out_of_scope"]))
    out += [f"warning: {w}" for w in r["warnings"]]
    if r["resume"]:
        out.append(f"resume: {r['resume']}")
    return "\n".join(out)


def render(report, fmt):
    """json: indented. mjson: minified, one line. plain: text for people."""
    if fmt == "json":
        return json.dumps(report, indent=2)
    if fmt == "mjson":
        return json.dumps(report, separators=(",", ":"))
    if fmt == "plain":
        return render_plain(report)
    return die(f"unknown output format {fmt!r}: use json, mjson or plain")


def run_ids(repo, wf, branch):
    return FORGE.run_ids(repo, wf, branch)


def wait_publish_step(repo, run_id, todo, timeout=45 * 60):
    """Block until the publish step succeeds. crates.io decides: if the step reports a failure
    but every version is on crates.io, carry on. Later steps of the run do not matter."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = FORGE.run_info(repo, run_id)
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
    rid = FORGE.dispatch(s["repo"], s["workflow"], s["branch"], {"release_type": release_type})
    if rid is None:  # no run id from the dispatch: take the one new run of this workflow and branch
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
    """(name, version) of every release crate in the first plan of a run. A later plan can hold the same crate at a
    new head, for example after a real commit follows a release; that version stays out of this run."""
    return {(c["name"], c["version"]) for o in report["operations"] if o["kind"].startswith("release") for c in o["crates"]}


def next_batch(report, planned, remaining):
    """Ready release operations that belong to the run's first plan, capped by `remaining` (None: no cap)."""
    batch = [o for o in report["operations"] if o["status"] == "ready" and o["kind"] == "release"
             and all((c["name"], c["version"]) in planned for c in o["crates"])]
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
    global WORKSPACE, FORGE, RESUME_FLAGS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", required=True, metavar="REPO@BRANCH", help="repo and branch to release for")
    ap.add_argument("-o", "--output", default="json", choices=["json", "mjson", "plain"],
                    help="json: indented (default); mjson: minified, one line; plain: text for people")
    ap.add_argument("--forge", default="auto", choices=["auto", "gh", "api"],
                    help="auto: gh when installed and logged in, else the REST API with GH_TOKEN or GITHUB_TOKEN")
    ap.add_argument("--workspace", type=Path, help="directory holding the repo clones (default: current directory)")
    ap.add_argument("--execute", action="store_true", help="trigger the release workflows")
    ap.add_argument("--execute-no-release", action="store_true",
                    help="no releases: apply each ready repin or lock update in a git worktree, commit, push and open a PR")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompts (with --execute or --execute-no-release)")
    ap.add_argument("--wait-merge", type=int, default=0, metavar="MINUTES",
                    help="after opening PRs, wait up to MINUTES for them to merge (the script never merges), then re-plan "
                         "and open the PRs that were waiting for them")
    ap.add_argument("--max-releases", type=int, metavar="N", help="with --execute: release at most N repos in this run")
    ap.add_argument("--release-type", default="patch", choices=["rc", "patch", "minor", "major"],
                    help="version type after release, passed to the workflow")
    a = ap.parse_args()
    if a.workspace:
        WORKSPACE = a.workspace.resolve()
    RESUME_FLAGS = resume_flags(a)
    start, report, opened_all = (a.start, ""), None, []
    try:
        start = parse_start(a.start)
        repo, branch = start
        try:
            FORGE = make_forge(None if a.forge == "auto" else a.forge)
        except Stop:
            if a.execute or a.execute_no_release or a.forge != "auto":
                raise
            FORGE = AnonForge()  # a dry run only reads public repos and crates.io
            log("no gh login and no GH_TOKEN or GITHUB_TOKEN: dry run only, with anonymous clones")
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
                    ok = ask(f"release {len(batch)} at once: {names}? type 'yes': ") == "yes"
                except EOFError:
                    ok = False
                if not ok:
                    die(f"confirmation needed for {names}; nothing triggered. Re-run interactively or pass --yes", "paused")
            log(f"releasing {len(batch)} at once: {names}")
            by_key = {(st["repo"], st["branch"]): st for st in plan["steps"]}
            release_batch([by_key[(o["repo"], o["branch"])] for o in batch], a.release_type)
            released += len(batch)
            plan = build_plan(repo, branch, planned)  # re-read heads and crates.io
            report = status(plan)
        if a.execute or a.execute_no_release:
            done = set()
            while True:
                todo = ready_edit_ops(report, done)
                if not todo:
                    break
                if not a.yes:
                    names = "; ".join(f"{o['repo']}@{o['branch']} ({o['kind']})" for o in todo)
                    try:
                        ok = ask(f"open {len(todo)} PR(s): {names}? type 'yes': ") == "yes"
                    except EOFError:
                        ok = False
                    if not ok:
                        die(f"confirmation needed to open PRs for {names}; nothing done. Re-run interactively or pass --yes", "paused")
                opened = []
                for o in todo:
                    log(f"applying {o['id']} ({o['kind']}) in {o['repo']}@{o['branch']}")
                    result = apply_op(o, start)
                    opened.append(result)
                    opened_all.append(result)
                    report["pull_requests"].append(result)
                    o["pull_request"] = result["url"]
                    done.add(op_key(o))
                if not a.wait_merge:
                    break
                log(f"waiting up to {a.wait_merge} min for {len(opened)} PR(s) to merge: " + ", ".join(r["url"] for r in opened))
                outcome = wait_for_merge(opened, a.wait_merge)
                if outcome != "merged":
                    die(f"stopped waiting: {outcome}. Open: " + ", ".join(r["url"] for r in opened), "paused")
                plan = build_plan(repo, branch, planned)  # the merged PRs change what is ready
                report = status(plan)
                report["pull_requests"] = list(opened_all)
    except Stop as e:
        report = error_report(start, str(e), e.state)
        report["pull_requests"] = list(opened_all)
    except Exception as e:  # a network error or a missing binary still ends in the documented error report
        report = error_report(start, f"unexpected {type(e).__name__}: {e}")
    print(render(report, a.output))
    sys.exit(2 if report["state"] == "error" else 0)


if __name__ == "__main__":
    main()
