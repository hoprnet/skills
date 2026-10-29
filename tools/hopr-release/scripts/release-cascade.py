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
its head version is already on crates.io. One run releases only the crates in its first
plan, because CI bumps each head after a release. --max-releases N caps the repos released.

A step is blocked while a repo's own requirements do not admit the versions released below
it. Repin that repo first. A release publishes to crates.io and cannot be undone.

Git-ref pins. A dependency of the start repo pinned to a git ref of an org repo (git = ...,
branch/rev/tag = ...) is planned for a swap to a released version. When crates.io has a
version at or above the locked one, the swap joins the final operation. Otherwise a
`pin_swap` operation is `blocked` and says why. [patch] git entries are only warned about.

--execute-no-release. For each ready repin or final operation: create a branch in a git
worktree (<workspace>/<repo>-worktrees/), edit Cargo.toml (requirements, version bumps, git
pins), refresh Cargo.lock (cargo, inside `nix develop` when the repo has a flake, so hooks
run), commit, push, and open a draft PR. It asks `yes` once (--yes skips it). It never
merges and never releases. A branch name is derived from the edits, so a re-run finds the
open PR and does not push a second one. --execute does the releases first, then the same.

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
  pull_requests    draft PRs opened or found by --execute / --execute-no-release
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
import argparse, base64, datetime, hashlib, json, shlex, os, re, shutil, subprocess, sys, time, tomllib, urllib.parse, urllib.request, urllib.error
from pathlib import Path

ORG = "hoprnet"
UA = "hoprnet-release-cascade"
PUBLISH_STEP = "Publish library"  # step name in hoprnet/hopr-workflows build-library.yaml
SKIP_DIRS = ("target/", "vendor/", "node_modules/", "/lib/")
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


def run(*a, check=True):
    env = {**os.environ, **FORGE.git_env()} if FORGE is not None and FORGE.git_env() else None
    r = subprocess.run(a, capture_output=True, text=True, env=env)
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


def git_pins(ms):
    """Dependencies pinned to a git ref of an org repo: {file, key, crate, git, ref}."""
    out = []

    def table(file, t):
        for key, spec in (t or {}).items():
            if isinstance(spec, dict) and f"github.com/{ORG}/" in spec.get("git", "").lower():
                ref = next((f"{k} {spec[k]}" for k in ("branch", "rev", "tag") if k in spec), "default branch")
                out.append(dict(file=file, key=key, crate=spec.get("package", key), git=spec["git"], ref=ref))

    for f, m in ms.items():
        for t in DEP_TABLES:
            table(f, m.get(t))
        for tgt in m.get("target", {}).values():
            for t in DEP_TABLES:
                table(f, tgt.get(t))
        table(f, m.get("workspace", {}).get("dependencies"))
    return out


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

    owned, pinned, seen = {}, {}, set()
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


def build_plan(start_repo, start_branch, planned=None):
    """`planned`: crates of the run's first plan. A later plan treats other unpublished heads, the version bumps CI
    pushes after a release, as the newest published version, so lock updates target versions that exist."""
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
        if planned is not None:
            for c in [c for c in s["todo"] if c not in planned]:
                del s["todo"][c]
                target[c] = pick_latest_compatible(published_versions(c), s["head"][c]) or s["head"][c]
        s["gaps"] = gaps(s["entries"], target, owned)
        # a repo whose head is published but whose requirements lag must be bumped and released again
        s["republish"] = bool(s["gaps"]) and not s["todo"]
        s["release_versions"] = {c: bump_patch(v) for c, v in s["head"].items()} if s["republish"] else s["todo"]
        s["hard_requires"] = hard_needs(s["entries"], set(s["crates"]), releasing)
        releasing |= set(s["release_versions"])
        target.update(s["release_versions"])  # leaf first, so downstream repos see the version that will exist
    start_ms = manifests(start_repo, f"origin/{start_branch}")
    start_entries = dep_entries(start_ms)
    pins = []
    for pin in git_pins(start_ms):
        locked = pinned.get(pin["crate"])
        swap_to = pick_latest_compatible(published_versions(pin["crate"]), locked) if locked else None
        if swap_to and ver(swap_to) < ver(locked):
            swap_to = None
        pins.append(dict(pin, line=line_of(start_repo, f"origin/{start_branch}", pin["file"], pin["key"]), locked=locked, target=swap_to))
    return dict(start=(start_repo, start_branch), steps=steps, fixed=fixed, pinned=sorted(pinned), pins=pins,
                patched=patch_pins(start_ms),
                repin=gaps(start_entries, target, owned), locked={c: v for c, (_, v) in owned.items()}, target=target)


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

    def find_pr(self, repo, head):
        rows = json.loads(run("gh", "pr", "list", "--repo", f"{ORG}/{repo}", "--head", head, "--state", "open", "--json", "url")[1] or "[]")
        return rows[0]["url"] if rows else None

    def dispatch(self, repo, workflow, ref, inputs):
        run("gh", "workflow", "run", workflow, "--repo", f"{ORG}/{repo}", "--ref", ref,
            *[a for k, v in inputs.items() for a in ("-f", f"{k}={v}")])

    def run_ids(self, repo, workflow, branch):
        rows = json.loads(run("gh", "run", "list", "--repo", f"{ORG}/{repo}", "--workflow", workflow, "--branch", branch,
                              "--event", "workflow_dispatch", "--limit", "20", "--json", "databaseId")[1] or "[]")
        return {r["databaseId"] for r in rows}

    def run_info(self, repo, run_id):
        return json.loads(run("gh", "run", "view", str(run_id), "--repo", f"{ORG}/{repo}", "--json", "status,conclusion,jobs")[1])

    def create_pr(self, repo, head, base, title, body, draft=True):
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

    def find_pr(self, repo, head):
        rows = self._call("GET", f"/repos/{ORG}/{repo}/pulls?head={ORG}:{urllib.parse.quote(head, safe='')}&state=open")
        return rows[0]["html_url"] if rows else None

    def dispatch(self, repo, workflow, ref, inputs):
        self._call("POST", f"/repos/{ORG}/{repo}/actions/workflows/{workflow}/dispatches", {"ref": ref, "inputs": inputs}, ok=(204,))

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

    def create_pr(self, repo, head, base, title, body, draft=True):
        return self._call("POST", f"/repos/{ORG}/{repo}/pulls",
                          dict(head=head, base=base, title=title, body=body, draft=draft), ok=(201,))["html_url"]


def make_forge(force=None):
    kind = choose_forge(force=force)
    return GhForge() if kind == "gh" else ApiForge(os.environ.get("GH_TOKEN") or os.environ["GITHUB_TOKEN"])


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
    pins = plan.get("pins", [])
    swaps = [dict(file=p["file"], line=p["line"], key=p["key"], crate=p["crate"], current=f"git {p['git']} {p['ref']}",
                  required=p["target"], swap="git_to_version") for p in pins if p["target"]]
    if plan["repin"] or lock_updates or swaps:
        sr, sb = plan["start"]
        named = {g["crate"] for g in plan["repin"]} | {u["crate"] for u in lock_updates}
        new("final_repin", repo=sr, branch=sb, edits=edits(sr, sb, plan["repin"]) + swaps, lock_updates=lock_updates,
            version_bumps=[], depends_on=sorted({rel[c] for c in named if c in rel}),
            description=f"update {sr}@{sb} to the released versions"
                        + (f" (cargo update -p {' -p '.join(u['crate'] for u in lock_updates)})" if lock_updates else ""),
            then="open a PR with the requirement edits and a refreshed Cargo.lock, then merge it")
    for p in pins:
        if not p["target"]:
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
                pull_requests=[],
                next=nxt, conditions=conditions,
                fixed_inputs=fixed, git_pinned_out_of_scope=plan["pinned"], warnings=warnings,
                resume=resume if state != "complete" else None)


def error_report(start, msg, state="error"):
    return dict(schema=SCHEMA, generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                start=dict(repo=start[0], branch=start[1]), state=state, summary=msg, repos_to_update=[],
                crates_to_change=[], operations=[], ready_operations=[], layers=[], pull_requests=[], next=dict(kind="none"), conditions=[],
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


def edit_text(text, e):
    if e.get("swap") == "git_to_version":
        return swap_git_text(text, e)
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
    return cmds


def branch_name(op):
    digest = hashlib.sha1(json.dumps([op.get("edits"), op.get("version_bumps"), op.get("lock_updates")], sort_keys=True).encode()).hexdigest()[:8]
    return f"chore/release-cascade-{op['kind']}-{digest}"


def pr_text(op, start):
    crates = list(dict.fromkeys([e["crate"] for e in op.get("edits", [])] + [u["crate"] for u in op.get("lock_updates", [])]))
    title = f"chore(deps): {'repin' if op.get('edits') else 'update'} {', '.join(crates)} to the released versions"
    lines = [f"Part of the release cascade from {start[0]}@{start[1]}. Merge after the releases below are on crates.io.", ""]
    lines += [f"- {e['file']}:{e['line']}: {e['key']} {e['current']} -> {e['required']}" for e in op.get("edits", [])]
    lines += [f"- bump {b['crate']} {b['current']} -> {b['suggested']} ({b['file']})" for b in op.get("version_bumps", [])]
    lines += [f"- lock: {u['crate']} {u['locked']} -> {u['target']}" for u in op.get("lock_updates", [])]
    lines += ["", f"Operation {op['id']} ({op['kind']}). Generated by hopr-release's release-cascade.py."]
    return title, "\n".join(lines)


def in_shell(wt, cmds):
    """Run commands in the worktree, inside `nix develop` when the repo has a flake (toolchain and hooks)."""
    script = f"cd {shlex.quote(str(wt))} && " + " && ".join(cmds)
    if (Path(wt) / "flake.nix").exists():
        run("nix", "develop", str(wt), "-c", "bash", "-c", script)
    else:
        run("bash", "-c", script)


def hooked_git(wt, *args):
    """git in the worktree. With a flake it runs in `nix develop`, so the repo's pre-commit and pre-push hooks work."""
    cmd = ["git", "-C", str(wt), *args]
    run(*(["nix", "develop", str(wt), "-c", *cmd] if (Path(wt) / "flake.nix").exists() else cmd))


def apply_op(op, start, shell=None):
    """Apply a repin or final repin in a new worktree branch, commit, push, open a draft PR. Never merges."""
    shell = shell or in_shell
    repo, base, branch = op["repo"], op["branch"], branch_name(op)
    existing = FORGE.find_pr(repo, branch)
    if existing:
        return dict(operation=op["id"], repo=repo, base=base, branch=branch, url=existing, state="exists")
    wt = WORKSPACE / f"{repo}-worktrees" / branch.replace("/", "-")
    git(repo, "fetch", "-q", "origin")
    git(repo, "worktree", "add", "-q", "-b", branch, str(wt), f"origin/{base}")
    files = {}
    for kind, items, fn in (("edits", op.get("edits", []), edit_text), ("bumps", op.get("version_bumps", []), bump_text)):
        for item in items:
            path = wt / item["file"]
            files[item["file"]] = fn(files.get(item["file"]) or path.read_text(), item)
    for f, text in files.items():
        (wt / f).write_text(text)
    shell(wt, lock_commands(op))
    title, body = pr_text(op, start)
    hooked_git(wt, "add", "--", *files, *(["Cargo.lock"] if (wt / "Cargo.lock").exists() else []))
    hooked_git(wt, "commit", "-q", "-m", title, "-m", f"{body}\n\n{CO_AUTHOR}")
    hooked_git(wt, "push", "-q", "-u", "origin", branch)
    url = FORGE.create_pr(repo, branch, base, title, body, draft=True)
    return dict(operation=op["id"], repo=repo, base=base, branch=branch, url=url, state="opened", worktree=str(wt))


def op_detail(o):
    if o.get("blocked_reason"):
        return f"blocked: {o['blocked_reason']}"
    if o["kind"].startswith("release"):
        return ", ".join(f"{c['name']} {c['version']}" for c in o["crates"])
    parts = [f"{e['key']} {e['current']} -> {e['required']}" for e in o.get("edits", [])]
    parts += [f"bump {b['crate']} {b['current']} -> {b['suggested']}" for b in o.get("version_bumps", [])]
    parts += [f"lock {u['crate']} {u['locked']} -> {u['target']}" for u in o.get("lock_updates", [])]
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
    FORGE.dispatch(s["repo"], s["workflow"], s["branch"], {"release_type": release_type})
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
                    help="no releases: apply each ready repin or lock update in a git worktree, commit, push and open a draft PR")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompts (with --execute or --execute-no-release)")
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
    RESUME_FLAGS = " ".join(filter(None, [f"--workspace {WORKSPACE}" if a.workspace else "",
                                          f"--forge {a.forge}" if a.forge != "auto" else "",
                                          f"-o {a.output}" if a.output != "json" else ""]))
    report = None
    try:
        FORGE = make_forge(None if a.forge == "auto" else a.forge)
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
            plan = build_plan(repo, branch, planned)  # re-read heads and crates.io
            report = status(plan)
        if a.execute or a.execute_no_release:
            todo = [o for o in report["operations"] if o["status"] == "ready" and o["kind"] in ("repin", "final_repin")]
            if todo and not a.yes:
                names = "; ".join(f"{o['repo']}@{o['branch']} ({o['kind']})" for o in todo)
                try:
                    ok = input(f"open {len(todo)} draft PR(s): {names}? type 'yes': ") == "yes"
                except EOFError:
                    ok = False
                if not ok:
                    die(f"confirmation needed to open PRs for {names}; nothing done. Re-run interactively or pass --yes", "paused")
            for o in todo:
                log(f"applying {o['id']} ({o['kind']}) in {o['repo']}@{o['branch']}")
                result = apply_op(o, start)
                report["pull_requests"].append(result)
                o["pull_request"] = result["url"]
    except Stop as e:
        report = error_report(start, str(e), e.state)
    print(render(report, a.output))
    sys.exit(2 if report["state"] == "error" else 0)


if __name__ == "__main__":
    main()
