#!/usr/bin/env python3
"""Inventory pinned Rust source; see references/catalog-maintenance.md for scope.

Python 3.11+, standard library only. This is a lexical source index, not a Rust
compiler or a runtime exporter. Inputs are read-only, already exported snapshots.
"""

import argparse
import collections
import json
from pathlib import Path
import re
import tomllib


METRIC = re.compile(r"\b((?:Simple|Multi)(?:Counter|Gauge|Histogram))\s*::\s*new\s*\(")
EVENT = re.compile(r"\b(?:(?:tracing|log)::)?(error|warn|info|debug|trace|event|(?:error|warn|info|debug|trace)_span|span)!\s*([({\[])")
INSTRUMENT = re.compile(r"#\s*\[\s*(?:tracing::)?instrument\b")
EXCLUDED = {"tests", "test", "testing", "benches", "examples", "target", ".git"}
RAW_STRING = re.compile(r'(?:br|r)(#*)"')
CHAR_LITERAL = re.compile(r"'(?:\\(?:u\{[0-9a-fA-F_]+\}|x[0-9a-fA-F]{2}|.)|[^'\\\n])'")


def mask_noncode(source):
    """Preserve offsets/newlines while hiding comments, strings and char literals."""
    chars = list(source)
    strings = {}
    i = 0
    while i < len(source):
        start = i
        if source.startswith("//", i):
            end = source.find("\n", i)
            i = len(source) if end < 0 else end
        elif source.startswith("/*", i):
            depth = 1
            i += 2
            while i < len(source) and depth:
                if source.startswith("/*", i):
                    depth += 1
                    i += 2
                elif source.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
        else:
            raw = RAW_STRING.match(source, i)
            if raw:
                terminator = '"' + raw[1]
                end = source.find(terminator, raw.end())
                if end < 0:
                    raise ValueError("unterminated raw string")
                strings[start] = source[raw.end():end]
                i = end + len(terminator)
            elif source[i] == '"':
                i += 1
                while i < len(source):
                    if source[i] == "\\":
                        i += 2
                    elif source[i] == '"':
                        i += 1
                        break
                    else:
                        i += 1
                literal = re.sub(r"\\\n\s*", "", source[start:i])
                try:
                    strings[start] = json.loads(literal)
                except ValueError:
                    strings[start] = literal[1:-1]
            elif source[i] == "'" and (char := CHAR_LITERAL.match(source, i)):
                i = char.end()
            else:
                i += 1
                continue
        for p in range(start, i):
            if chars[p] != "\n":
                chars[p] = " "
    return "".join(chars), strings


def closing(mask, start):
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = [pairs[mask[start]]]
    for i in range(start + 1, len(mask)):
        c = mask[i]
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")]}" and stack:
            if c != stack.pop():
                raise ValueError(f"unbalanced delimiter at {i}")
            if not stack:
                return i
    raise ValueError(f"unclosed delimiter at {start}")


def parts(source):
    mask, _ = mask_noncode(source)
    result, start, i = [], 0, 0
    while i < len(mask):
        if mask[i] in "([{":
            i = closing(mask, i)
        elif mask[i] == ",":
            result.append(source[start:i].strip())
            start = i + 1
        i += 1
    if source[start:].strip():
        result.append(source[start:].strip())
    return result


def literal(value):
    mask, strings = mask_noncode(value.strip())
    return next(iter(strings.values())) if len(strings) == 1 and not mask.strip() else None


def cfg_values(expression):
    """Possible cfg values with test=false and unknown features/platforms."""
    expression = expression.strip()
    if expression == "test":
        return {False}
    match = re.fullmatch(r"(all|any|not)\((.*)\)", expression, re.S)
    if not match:
        return {False, True}
    values = [cfg_values(p) for p in parts(match[2])]
    if match[1] == "not":
        return {not v for v in values[0]}
    combined = {match[1] == "all"}
    for options in values:
        combined = {(a and b) if match[1] == "all" else (a or b)
                    for a in combined for b in options}
    return combined


def production_mask(source):
    mask, strings = mask_noncode(source)
    # Skip inline test/testing modules without truncating later production items.
    ranges = []
    for m in re.finditer(r"\bmod\s+(?:tests?|testing)\s*\{", mask):
        ranges.append((m.start(), closing(mask, m.end() - 1) + 1))
    # Test-only modules need not be named `tests` (e.g. coverage_tests).
    for m in re.finditer(r"#\s*\[\s*cfg\s*\(", mask):
        condition_end = closing(mask, m.end() - 1)
        if True in cfg_values(source[m.end():condition_end]):
            continue
        attr_end = closing(mask, mask.index("[", m.start()))
        item_start = attr_end + 1
        while (attr := re.match(r"\s*#\s*\[", mask[item_start:])):
            item_start = closing(mask, item_start + attr.end() - 1) + 1
        boundary = re.search(r"[;{]", mask[item_start:])
        if boundary:
            pos = item_start + boundary.start()
            ranges.append((m.start(), closing(mask, pos) + 1 if mask[pos] == "{" else pos + 1))
    chars = list(mask)
    for start, end in ranges:
        for i in range(start, end):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars), strings


def owner(path, root):
    for directory in (path.parent, *path.parents):
        if not directory.is_relative_to(root):
            break
        manifest = directory / "Cargo.toml"
        if manifest.exists():
            data = tomllib.loads(manifest.read_text())
            if "package" in data:
                return data["package"]["name"]
    return None


def location(source, start, end, path, spec, snapshot):
    line = source.count("\n", 0, start) + 1
    end_line = source.count("\n", 0, end) + 1
    return dict(snapshot=snapshot, repo=spec["repo"], path=path, line=line,
                end_line=end_line, source_commit=spec["commit"],
                source_url=f'https://github.com/{spec["repo"]}/blob/{spec["commit"]}/{path}#L{line}-L{end_line}')


def scan(snapshot, spec):
    root = Path(spec["root"])
    if not root.is_absolute() or not root.is_dir():
        raise ValueError(f"Snapshot root must be an existing absolute directory: {root}")
    if not re.fullmatch(r"[0-9a-f]{40}", spec["commit"]):
        raise ValueError(f"Pin {snapshot} to an immutable full commit SHA")
    metrics, events, scanned = [], [], []
    for path in sorted(root.rglob("*.rs")):
        rel = path.relative_to(root)
        if EXCLUDED.intersection(rel.parts) or path.stem in EXCLUDED or "src" not in rel.parts:
            continue
        # Auxiliary CLIs are not emitted by the daemon/library.
        if "bin" in rel.parts:
            continue
        source = path.read_text()
        mask, _ = production_mask(source)
        scanned.append(rel.as_posix())
        crate = owner(path, root)
        for m in METRIC.finditer(mask):
            end = closing(mask, m.end() - 1)
            args = parts(source[m.end():end])
            name, meaning = literal(args[0]), literal(args[1])
            if name is None or meaning is None:
                raise ValueError(f"Dynamic metric needs manual inventory: {snapshot}/{rel}:{m.start()}")
            kind = m[1]
            labels = []
            if kind.startswith("Multi"):
                label_arg = args[3 if kind.endswith("Histogram") else 2]
                _, label_strings = mask_noncode(label_arg)
                labels = list(label_strings.values())
            item = location(source, m.start(), end, rel.as_posix(), spec, snapshot)
            item.update(crate=crate, name=name, type=kind, labels=labels, meaning=meaning,
                        otel_kind="u64_counter" if kind.endswith("Counter") else "f64_up_down_counter" if kind.endswith("Gauge") else "f64_histogram",
                        buckets_expression=args[2] if kind.endswith("Histogram") else None,
                        availability="source_verified_feature_and_producer_dependent; deployed_release_not_verified",
                        surface="otlp_or_embedding_only; excluded_from_hoprd_rest_metrics" if name.startswith("hopr_session_") else "metric_exporter_subject_to_configuration")
            metrics.append(item)
        calls = [(m, m.end() - 1, m[1]) for m in EVENT.finditer(mask)]
        for m in INSTRUMENT.finditer(mask):
            calls.append((m, mask.index("[", m.start()), "instrument"))
        for m, start, macro in sorted(calls, key=lambda row: row[0].start()):
            end = closing(mask, start)
            body = source[start + 1:end]
            kind = "span" if macro.endswith("span") or macro == "instrument" else "event"
            if macro == "instrument":
                attr_start = body.find("(")
                body = body[attr_start + 1:-1] if attr_start >= 0 else ""
            args = parts(body)
            level = macro.removesuffix("_span").upper() if macro not in {"event", "span", "instrument"} else None
            name, fields, target, parent = None, {}, None, None
            for arg in args:
                if (value := literal(arg)) is not None and name is None:
                    name = value
                elif match := re.match(r"(?:tracing::)?Level::(\w+)$", arg):
                    level = match[1]
                elif match := re.match(r"(target|parent)\s*:\s*(.*)", arg, re.S):
                    if match[1] == "target":
                        target = literal(match[2]) or match[2]
                    else:
                        parent = match[2]
                elif match := re.match(r"([\w.]+)\s*=\s*(.*)", arg, re.S):
                    key, value = match.groups()
                    if macro == "instrument" and key in {"name", "level", "target"}:
                        parsed = literal(value)
                        if key == "name":
                            name = parsed
                        elif key == "level":
                            level = parsed.upper() if parsed else value
                        else:
                            target = parsed or value
                    else:
                        fields[key] = value
                elif re.fullmatch(r"[?%]?[A-Za-z_]\w*(?:\.\w+)*", arg):
                    fields[arg.lstrip("?%")] = arg
            if macro == "instrument":
                fn = re.search(r"\bfn\s+(\w+)", mask[end + 1:])
                name = name or (fn[1] if fn else None)
                level = level or "INFO"
            item = location(source, m.start(), end, rel.as_posix(), spec, snapshot)
            item.update(crate=crate, kind=kind, macro=macro, level=level, name=name,
                        explicit_target=target, explicit_parent=parent, fields=fields,
                        declaration=source[m.start():end + 1])
            if macro == "instrument":
                item["field_note"] = "Read declaration fields()/skip()/skip_all() and function signature for implicit argument capture; fields below are lexical only."
            events.append(item)
    if not scanned:
        raise ValueError(f"No eligible Rust sources found in {snapshot}")
    return metrics, events, scanned


def signature(item):
    # Preserve message/string content while ignoring indentation outside strings.
    mask, strings = mask_noncode(item["declaration"])
    return item["path"], re.sub(r"\s+", "", mask), tuple(strings.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=Path, help="JSON mapping snapshot IDs to repo, commit, root and status")
    parser.add_argument("output", type=Path)
    parser.add_argument("--verified-on", required=True)
    args = parser.parse_args()
    sources = json.loads(args.sources.read_text())
    all_metrics, all_events, reports, baseline = [], [], {}, {}
    for snapshot, spec in sources.items():
        if spec["status"] not in {"default_branch", "open_pr"}:
            raise ValueError(f"Unsupported snapshot status: {spec['status']}")
        metrics, events, scanned = scan(snapshot, spec)
        reports[snapshot] = dict(repo=spec["repo"], commit=spec["commit"], status=spec["status"],
                                 files_scanned=len(scanned), metric_declarations=len(metrics),
                                 event_calls=sum(e["kind"] == "event" for e in events),
                                 span_calls=sum(e["kind"] == "span" for e in events))
        if spec["status"] == "default_branch":
            all_metrics.extend(metrics)
            all_events.extend(events)
            baseline[spec["repo"]] = (metrics, events)
        else:
            base_metrics, base_events = baseline[spec["repo"]]
            key = lambda m: (m["name"], m["type"], tuple(m["labels"]), m["meaning"], m["buckets_expression"])
            old = {key(m) for m in base_metrics}
            reports[snapshot]["metric_additions_or_changes"] = [m for m in metrics if key(m) not in old]
            reports[snapshot]["metric_names_removed"] = sorted({m["name"] for m in base_metrics} - {m["name"] for m in metrics})
            # Multiset comparison retains repeated call sites, but avoids copying
            # every unchanged baseline call into both PR overlays.
            old_counts = collections.Counter(signature(e) for e in base_events)
            new_counts = collections.Counter(signature(e) for e in events)
            for e in events:
                sig = signature(e)
                if old_counts[sig]:
                    old_counts[sig] -= 1
                else:
                    all_events.append(dict(e, change="added_or_modified"))
            for e in base_events:
                sig = signature(e)
                if new_counts[sig]:
                    new_counts[sig] -= 1
                else:
                    all_events.append(dict(e, snapshot=snapshot, change="baseline_call_removed_or_modified",
                                           comparison_commit=spec["commit"]))
    args.output.mkdir(parents=True, exist_ok=True)
    catalog = dict(schema_version=2, verified_on=args.verified_on,
                   scope="Statically named production metric declarations in the six default-branch snapshots; requested PRs independently compared in inventory-coverage.json. Feature/dependency/producer availability is not a live scrape guarantee.",
                   count=len(all_metrics), metrics=sorted(all_metrics, key=lambda m: (m["repo"], m["name"])))
    (args.output / "metric-catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")
    (args.output / "log-catalog.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in all_events))
    (args.output / "inventory-coverage.json").write_text(json.dumps(dict(
        schema_version=1, verified_on=args.verified_on, snapshots=reports,
        exclusions="Items requiring cfg(test); inline modules test/tests/testing; files or directories test/tests/testing/benches/examples/target; auxiliary src/bin CLIs; non-src Rust files. No macro expansion, dependency traversal or runtime observation. Feature-conditional production source is retained; helper crates are identified by crate field.",
        log_catalog_records=len(all_events)), indent=2) + "\n")
    print(json.dumps({"metrics": len(all_metrics), "log_records": len(all_events), "snapshots": reports}, indent=2))


if __name__ == "__main__":
    main()
