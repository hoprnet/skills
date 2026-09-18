# Benchmarking HOPR components

Read this when profiling or benchmarking a HOPR component. These are domain gotchas on top of the
general `performance` skill's benchmarking methodology — apply that skill's method, then correct for
the HOPR-specific traps below, or a benchmark measures the wrong code path and reads as fast for the
wrong reason.

- Use realistic parameters — e.g. a winning probability around 1%, not 100%. An always-win ticket
  changes the code path under test (PoR, redemption) and doesn't represent production load.
- Use bounded channels matching production capacity, not unbounded ones.
- Include the mixer adapter so its per-hop delay counts as real cost instead of being stripped out
  as noise. (§4)
- n-hop terminology: `n` = number of relayers (intermediate hops), not the total number of hops in
  the path; `n=0` means point-to-point (direct, no relay).
