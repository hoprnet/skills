# Catalog scope, format and regeneration

Verified 2026-11-18. [source-manifest.json](source-manifest.json) pins the source
revisions and PR states; [inventory-coverage.json](inventory-coverage.json) records
per-snapshot file, instrument, event and span counts. Source availability and
availability in a deployed binary are separate facts.

## Look up a signal

The catalog is a dictionary of definitions extracted from source. It does not
contain a node's measurements. From the repository root, find a metric by its
exact name:

```sh
jq '.metrics[] | select(.name == "hopr_packets_count") |
  {name, type, labels, meaning, source_url, availability}' \
  tools/hopr-debug/references/metric-catalog.json
```

For example, this reports `hopr_packets_count` as a `MultiCounter` with a `type`
label for packet categories. To determine what happened on a node, compare the
captured series for each actual label value over the same process lifetime and
account for counter resets. Check the running export's HELP/TYPE and version first.

To search for all PIX metrics by name and display their descriptions:

```sh
jq -r '.metrics[] | select(.name | startswith("hopr_pix_")) |
  [.name, .type, .meaning] | @tsv' \
  tools/hopr-debug/references/metric-catalog.json
```

For logs and spans, use the JSONL queries in [logs and traces](logs-and-traces.md).
The [source manifest](source-manifest.json) identifies the commits and PR heads
used to build both indexes. The [coverage report](inventory-coverage.json) records
what the scan included; neither establishes runtime availability.

## Files for consumers

| File | Contract | Load when |
|---|---|---|
| [telemetry-inventory.md](telemetry-inventory.md) | Collection surfaces and readable metric table | Discovering available telemetry or selecting collection |
| [metric-catalog.json](metric-catalog.json) | Version 2; `metrics[]` with exact name, type, OTLP kind, labels, HELP description, bucket expression, crate, source location and availability | Matching a metric or building queries |
| [log-catalog.jsonl](log-catalog.jsonl) | One JSON object per lexical event/span call site in the pinned baseline | Finding exact messages, fields, targets and levels |
| [logs-and-traces.md](logs-and-traces.md) | Export formats, filters, trace limits, common event families and JSONL queries | Parsing logs or tracing a request |
| [state-snapshots.md](state-snapshots.md) | Component schemas, host-only counters, session hooks and profiler outputs | Reading state dumps or diagnosing missing exported signals |
| [pending-features.md](pending-features.md) | Merged PR changes and version boundaries | Interpreting captures from older or newer builds |

The metric count is instruments, not exported time series. Histogram bucket/sum/count
series and each label combination multiply the number of time series. The six
default-branch sources include the four requested repositories plus `hoprd` for
the daemon/exporter and `hopr-utilities` for CPU/UDP instruments. `hopr-types`
provides the metric implementation and log sites, with **zero production named
metric declarations**; its `otel_my_*` constructors are tests.

The source scan includes feature-conditional production Rust under `src`, including
legacy/helper crates identified by `crate`. It excludes test-only items, test and
testing modules/files, examples, benchmarks, auxiliary `src/bin` CLIs and non-`src`
files. `hoprd-localcluster` entries describe the support harness, not daemon output.
Third-party libp2p/Tokio/SDK events and host/container/node-exporter metrics depend on
their own versions and configuration; they are not HOPR instrument declarations.

## JSONL interpretation

`snapshot` is the short repository name. Both previously tracked PRs are merged,
so their final changes are included in the current hoprnet baseline; this edition
has no separate PR overlays. When a future open PR is scanned, an overlay can use
`change=added_or_modified` or `change=baseline_call_removed_or_modified`; removed
records retain their baseline source URL and `comparison_commit` identifies the PR.

`kind` is `event` or `span`; `name` is a message template or span name, not a rendered
event. `fields` maps explicitly parsed names to **Rust expressions**, not captured
values. `declaration` retains the entire invocation to resolve formatting arguments,
`fields(...)`, `skip(...)`, `ret`/`err` and implicit function-argument capture in
`#[instrument]`. `explicit_target=null` means use the defining module's default
target; `explicit_parent=null` means no explicit parent expression was declared.
It does not mean a root span. Consult `crate`/`path` and the actual captured target.

The extractor masks strings/comments and balances delimiters. It does not expand
Rust macros, resolve imports, evaluate all Cargo features, infer runtime values or
prove emitted trace linkage. Macro-template sites remain templates; formatted
message helpers such as jemalloc's `info!("{}", stats_str)` need the accompanying
prose/source. Spans may have fields populated later by `.record`. Treat raw source
and a compatible actual capture as authoritative when lexical metadata is partial.

## Reproduce or update

Use Python 3.11+ and immutable source exports. Export each named commit with
`git archive <sha>` into a temporary directory, or download that commit's public
source archive. Keep existing checkouts and uncommitted edits intact.

Create a temporary input JSON mapping each snapshot to `repo`, `commit`, `root`
(absolute snapshot directory), and `status` (`default_branch` or `open_pr`). Put
default branches before PRs. Use IDs matching `inventory-coverage.json`, for example:

```json
{
  "hoprnet": {
    "repo": "hoprnet/hoprnet",
    "commit": "ff0076efaa58655e282ee7e2fa6b685ecd330a5d",
    "root": "/tmp/hopr-sources/hoprnet",
    "status": "default_branch"
  }
}
```

Supply all six baselines when regenerating this catalog. Add independent PR
snapshots only for unmerged changes that need a separate overlay:

```sh
python3.12 tools/hopr-debug/scripts/inventory_telemetry.py \
  /tmp/hopr-sources/sources.json /tmp/hopr-generated --verified-on YYYY-MM-DD
```

Review the output before replacing the three generated reference files. The script
fails on a nonliteral metric name/description rather than silently dropping it.
Compare instrument sets, types and labels with the previous inventory; separately
review helper implementations, recording sites, feature gates, REST routing,
exporter setup and relevant PR diffs. Search alternate telemetry APIs and new macros
that a lexical constructor scan would miss. Refresh the Markdown table and source
manifest from the reviewed JSON, and reconcile pending-versus-merged prose.

Check every source location against its exported file, JSON validity, duplicate
metric names, test-only exclusions, reference links and the generated output's
reproducibility. Validate the skill frontmatter with skill-creator's validator.
Run the extractor's parsing/coverage regression checks with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest discover \
  -s tools/hopr-debug/scripts -p 'test_*.py' -v
```

A real capture is still needed to establish runtime availability, exporter shape,
trace propagation and performance; documentation checks do not establish those.
