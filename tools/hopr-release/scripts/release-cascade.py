#!/usr/bin/env python3
"""Release the unpublished HOPR crates leaf-to-root, per release line.

Each release line has its own config in ../configs/release-cascade.<line>.yaml (v5 is the
`main` branches, v4 the `compat/v4` branches). Run from the directory that holds the repo
clones (hoprd, edge-client, hopr-api, ...), or pass --workspace. Regenerate the configs from the
Cargo.lock files of the top repos (edge-client and hoprd, which sit on top of the whole graph):

  release-cascade.py --regenerate                 v5 from the default branches, v4 from release/X.Y
  release-cascade.py --regenerate --default-only  v5 only

Then plan or run it. Default is a DRY RUN: it reads branch heads from the local clones
(after `git fetch`), asks crates.io which versions exist, and prints the steps.

  release-cascade.py                  plan for every release-cascade.*.yaml
  release-cascade.py --line v4        one line
  release-cascade.py --execute        trigger release workflows, one step at a time
  release-cascade.py --execute --yes  same, without the per-step prompt

A release publishes to crates.io and cannot be undone. Each step runs the repo's release
workflow with `gh workflow run`, then waits for the run's "Publish library" step to succeed
(later steps of the run may still fail) and for the crates to show on crates.io. Repinning
the root repos is not automated: the plan prints which requirements are behind.
"""
import argparse, json, re, subprocess, sys, time, tomllib, urllib.request, urllib.error
from pathlib import Path

CONFIGS = Path(__file__).resolve().parent.parent / "configs"
WORKSPACE = Path.cwd()  # directory holding the repo clones; set with --workspace
ORG = "hoprnet"
UA = "hoprnet-release-cascade"
TOP_REPOS = ("edge-client", "hoprd")
LIB_BRANCH = {"v5": "main", "v4": "compat/v4"}  # library branch per line


def config_path(line):
    return CONFIGS / f"release-cascade.{line}.yaml"
OWNED_PREFIXES = ("hopr-", "blokli-")
PUBLISH_STEP = "Publish library"  # step name in hoprnet/hopr-workflows build-library.yaml


def run(*a, cwd=None, check=True):
    r = subprocess.run(a, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"command failed: {' '.join(a)}\n{r.stderr.strip()}")
    return r.returncode, r.stdout


def git(repo, *a, check=True):
    return run("git", "-C", str(WORKSPACE / repo), *a, check=check)[1]


def has_ref(repo, branch):
    return run("git", "-C", str(WORKSPACE / repo), "rev-parse", "--verify", "-q", f"origin/{branch}", check=False)[0] == 0


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


def ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s.split("-")[0])[:3])


def tree_files(repo, ref):
    return [f for f in git(repo, "ls-tree", "-r", "--name-only", ref).split("\n") if f and "target/" not in f]


def packages(repo, ref):
    """{crate: version} of every publishable package at <ref>."""
    out = {}
    for f in tree_files(repo, ref):
        if not f.endswith("Cargo.toml"):
            continue
        try:
            p = tomllib.loads(git(repo, "show", f"{ref}:{f}")).get("package")
        except tomllib.TOMLDecodeError:
            continue
        if p and p.get("publish", True) is not False and isinstance(p.get("version"), str):
            out[p["name"]] = p["version"]
    return out


# ---------- config: read ----------

def load_config(path):
    text = path.read_text()
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        pass
    for cmd in (["yq", ".", str(path)], ["yq", "-o=json", ".", str(path)]):  # python-yq, then mikefarah yq
        rc, out = run(*cmd, check=False)
        if rc == 0:
            try:
                return json.loads(out)
            except json.JSONDecodeError:
                continue
    sys.exit("cannot read the YAML config: install PyYAML or yq")


# ---------- config: regenerate from the root repos' Cargo.lock ----------

def lock_graph(repo, branch):
    pk = tomllib.loads(git(repo, "show", f"origin/{branch}:Cargo.lock"))["package"]
    byname = {}
    for p in pk:
        byname.setdefault(p["name"], []).append(p)

    def resolve(dep):
        x = dep.split()
        c = byname[x[0]]
        return ([p for p in c if p["version"] == x[1]] if len(x) > 1 else c)[0]

    return pk, resolve


def is_release_candidate(p):
    return p["name"].startswith(OWNED_PREFIXES) and p.get("source", "").startswith("registry+")


def crate_repo_map(skip_repos, lib_branch):
    """crate -> repo, scanned from the sibling clones (library branch, else main/master)."""
    out = {}
    for d in sorted(WORKSPACE.iterdir()):
        if not (d / ".git").exists() or d.name in skip_repos:
            continue
        ref = next((f"origin/{b}" for b in (lib_branch, "main", "master") if has_ref(d.name, b)), None)
        if ref:
            for name in packages(d.name, ref):
                out.setdefault(name, d.name)
    return out


def release_workflow(repo, branch):
    names = [Path(f).name for f in tree_files(repo, f"origin/{branch}")
             if f.startswith(".github/workflows/release") and f.endswith((".yaml", ".yml"))]
    if len(names) != 1:
        sys.exit(f"{repo}@{branch}: expected one release*.yaml workflow, found {names}")
    return names[0]


def build_line(line, roots, lib_branch):
    for repo, branch in roots:
        git(repo, "fetch", "-q", "origin")
    owned = {}  # crate -> highest locked version across the roots
    edges = set()  # (crate, crate it depends on), release candidates only
    for repo, branch in roots:
        pk, resolve = lock_graph(repo, f"{branch}")
        for p in pk:
            if is_release_candidate(p):
                owned[p["name"]] = max(owned.get(p["name"], "0"), p["version"], key=ver)
                seen, stack = set(), [p]
                while stack:
                    n = stack.pop()
                    for dep in n.get("dependencies", []):
                        c = resolve(dep)
                        k = (c["name"], c["version"])
                        if is_release_candidate(c):
                            edges.add((p["name"], c["name"]))
                        elif k not in seen:
                            seen.add(k)
                            stack.append(c)
    repo_of = crate_repo_map({r for r, _ in roots}, lib_branch)
    steps, fixed = {}, []
    for crate, version in sorted(owned.items()):
        repo = repo_of.get(crate)
        if repo and has_ref(repo, lib_branch):
            steps.setdefault(repo, []).append(crate)
        else:
            fixed.append((crate, version, repo))
    # repo-level order: a repo comes after every repo whose crates it depends on
    crate_step = {c: r for r, cs in steps.items() for c in cs}
    after = {r: {crate_step[b] for a, b in edges if crate_step.get(a) == r and b in crate_step} - {r} for r in steps}
    order = []
    while len(order) < len(steps):
        ready = sorted(r for r in steps if r not in order and after[r] <= set(order))
        if not ready:
            sys.exit(f"line {line}: dependency cycle between repos {sorted(set(steps) - set(order))}")
        order.append(ready[0])
    return dict(
        steps=[dict(repo=r, branch=lib_branch, workflow=release_workflow(r, lib_branch), crates=sorted(steps[r]))
               for r in order],
        fixed=[dict(crate=c, version=v, repo=r) for c, v, r in fixed],
        downstream=[dict(repo=r, branch=b) for r, b in roots],
    )


def emit(name, c):
    o = [f"# Generated by release-cascade.py --regenerate from the top repos' Cargo.lock files.",
         "# Re-run it to refresh; manual edits are overwritten.",
         f"name: {name}", "steps:"]
    for s in c["steps"]:
        o += [f"  - repo: {s['repo']}", f"    branch: {s['branch']}",
              f"    workflow: {s['workflow']}", f"    crates: [{', '.join(s['crates'])}]"]
    o.append("# published elsewhere: the plan only checks they are on crates.io (not yanked)")
    o.append("fixed:" if c["fixed"] else "fixed: []")
    for f in c["fixed"]:
        o.append(f"  - {{crate: {f['crate']}, version: \"{f['version']}\", repo: {f['repo'] or 'unknown'}}}")
    o.append("# repinned by hand once the steps are published")
    o.append("downstream:")
    for d in c["downstream"]:
        o.append(f"  - {{repo: {d['repo']}, branch: {d['branch']}}}")
    return "\n".join(o) + "\n"


def default_branch(repo):
    ref = git(repo, "symbolic-ref", "refs/remotes/origin/HEAD").strip()
    return ref.removeprefix("refs/remotes/origin/")


def release_branch(repos, override):
    """Highest release/X.Y branch that every top repo has."""
    if override:
        return override
    common = None
    for r in repos:
        found = {b.strip().removeprefix("origin/") for b in git(r, "branch", "--no-color", "-r", "--list", "origin/release/*").split("\n")
                 if re.fullmatch(r"origin/release/\d+\.\d+", b.strip())}
        common = found if common is None else common & found
    if not common:
        sys.exit(f"no release/X.Y branch common to {repos}; pass --release-branch")
    return max(common, key=lambda b: ver(b.split("/", 1)[1]))


def regenerate(top_repos, default_only, release_override):
    """Build the v5 line from the top repos' default branches and the v4 line from their release branch."""
    for r in top_repos:
        git(r, "fetch", "-q", "origin")
    lines = {"v5": [(r, default_branch(r)) for r in top_repos]}
    if not default_only:
        rb = release_branch(top_repos, release_override)
        lines["v4"] = [(r, rb) for r in top_repos]
    for name, roots in lines.items():
        cfg = build_line(name, roots, LIB_BRANCH[name])
        config_path(name).write_text(emit(name, cfg))
        print(f"wrote {config_path(name)}\n  {name} from " + ", ".join(f"{r}@{b}" for r, b in roots)
              + ":\n    " + " -> ".join(s["repo"] for s in cfg["steps"]))


# ---------- plan / execute ----------

def requirements(repo, branch, crate):
    hits = []
    for f in tree_files(repo, f"origin/{branch}"):
        if f.endswith("Cargo.toml"):
            for line in git(repo, "show", f"origin/{branch}:{f}").split("\n"):
                if re.match(rf"\s*{re.escape(crate)}\s*=", line):
                    hits.append(f"{f}: {line.strip()}")
    return hits


def plan_line(cfg):
    steps = []
    for s in cfg["steps"]:
        git(s["repo"], "fetch", "-q", "origin")
        head = packages(s["repo"], f"origin/{s['branch']}")
        miss = [c for c in s["crates"] if c not in head]
        if miss:
            sys.exit(f"{s['repo']}@{s['branch']}: config lists {miss} but the branch has no such crate; regenerate")
        todo = {c: head[c] for c in s["crates"] if not is_published(c, head[c])}
        steps.append(dict(s, todo=todo, done={c: head[c] for c in s["crates"] if c not in todo}))
    return steps


def show(line, cfg, steps):
    print(f"\n=== line: {line} ===")
    for f in cfg.get("fixed") or []:
        ok = is_published(f["crate"], f["version"])
        print(f"  fixed input {f['crate']} {f['version']} (repo: {f.get('repo')}): "
              f"{'on crates.io' if ok else 'MISSING OR YANKED on crates.io'}")
    for i, s in enumerate(steps, 1):
        head = f"{i}. {s['repo']} @ {s['branch']}"
        if not s["todo"]:
            print(f"  {head}: nothing to release ({len(s['done'])} crates already on crates.io)")
            continue
        print(f"  {head}: release {', '.join(f'{n} {v}' for n, v in s['todo'].items())}")
        print(f"      gh workflow run {s['workflow']} --repo {ORG}/{s['repo']} --ref {s['branch']} -f release_type=patch")
    print("  then repin by hand (requirements still behind the new versions):")
    current = {n: v for s in steps for n, v in {**s["todo"], **s["done"]}.items()}
    for d in cfg["downstream"]:
        git(d["repo"], "fetch", "-q", "origin")
        for crate, v in sorted(current.items()):
            for h in requirements(d["repo"], d["branch"], crate):
                m = re.search(r'version\s*=\s*"=?\^?([\d.]+[^"]*)"|=\s*"=?\^?([\d.]+[^"]*)"', h)
                if m and ver(m.group(1) or m.group(2)) < ver(v):
                    print(f"      {d['repo']}@{d['branch']}  {h}   -> {v}")


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
            sys.exit(f"{repo}: step '{PUBLISH_STEP}' did not succeed ({state or 'not reached'}); "
                     f"see https://github.com/{ORG}/{repo}/actions/runs/{run_id}. Nothing further triggered")
        time.sleep(20)
    sys.exit(f"{repo}: timeout waiting for step '{PUBLISH_STEP}' of run {run_id}; nothing further triggered")


def execute(line, steps, assume_yes, release_type):
    for i, s in enumerate(steps, 1):
        if not s["todo"]:
            continue
        label = f"{s['repo']}@{s['branch']} ({', '.join(f'{n} {v}' for n, v in s['todo'].items())})"
        if not assume_yes and input(f"[{line} {i}] trigger {s['workflow']} for {label}? type 'yes': ") != "yes":
            sys.exit("stopped by user; nothing further triggered")
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
            sys.exit(f"{s['repo']}: cannot identify the triggered run (new runs: {sorted(new)}); nothing further triggered")
        rid = new.pop()
        print(f"  triggered run {rid}: https://github.com/{ORG}/{s['repo']}/actions/runs/{rid}\n"
              f"  waiting for step '{PUBLISH_STEP}' ...")
        status = wait_publish_step(s["repo"], rid)
        print(f"  published by run {rid} (run status: {status}); checking crates.io ...")
        pending, deadline = dict(s["todo"]), time.time() + 10 * 60  # index lag, downstream steps resolve against it
        while pending and time.time() < deadline:
            pending = {n: v for n, v in pending.items() if not is_published(n, v)}
            if pending:
                time.sleep(15)
        if pending:
            sys.exit(f"step succeeded but {pending} not visible on crates.io after 10 min; nothing further triggered")
        print(f"  on crates.io: {label}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regenerate", action="store_true",
                    help="rewrite the configs from the top repos' Cargo.lock files")
    ap.add_argument("--default-only", action="store_true", help="with --regenerate: build only v5 (default branches)")
    ap.add_argument("--top-repos", default=",".join(TOP_REPOS), help="with --regenerate: comma-separated top repos")
    ap.add_argument("--release-branch", help="with --regenerate: release branch for v4 (default: highest common release/X.Y)")
    ap.add_argument("--line", action="append", help="default: every release-cascade.*.yaml")
    ap.add_argument("--execute", action="store_true", help="trigger the release workflows")
    ap.add_argument("--yes", action="store_true", help="skip the per-step prompt (with --execute)")
    ap.add_argument("--release-type", default="patch", choices=["rc", "patch", "minor", "major"],
                    help="version type after release, passed to the workflow")
    ap.add_argument("--workspace", type=Path, help="directory holding the repo clones (default: current directory)")
    a = ap.parse_args()
    if a.workspace:
        global WORKSPACE
        WORKSPACE = a.workspace.resolve()
    if a.regenerate:
        return regenerate(a.top_repos.split(","), a.default_only, a.release_branch)
    run("gh", "auth", "status")
    paths = [config_path(l) for l in a.line] if a.line else sorted(CONFIGS.glob("release-cascade.*.yaml"))
    if not paths:
        sys.exit("no release-cascade.*.yaml found; run --regenerate first")
    for path in paths:
        cfg = load_config(path)
        steps = plan_line(cfg)
        show(cfg["name"], cfg, steps)
        if a.execute:
            execute(cfg["name"], steps, a.yes, a.release_type)
    if not a.execute:
        print("\ndry run only; add --execute to trigger")


if __name__ == "__main__":
    main()
