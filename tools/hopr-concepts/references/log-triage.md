# Log triage recipes

Use these on a large hoprd log (100k+ lines) before reading any of it. Do not grep the
same file again for each new question: count first, narrow to a window, then read lines.

Assumed format: `tracing` text, one line per event, an ISO 8601 timestamp first
(`2026-11-08T10:00:00.123456Z  WARN target: message`). Check the first line with
`head -n 3 LOG` and adjust the patterns if the format differs.

| Question | Command |
| --- | --- |
| Which events dominate? | `awk '{print $2, $3}' LOG \| sort \| uniq -c \| sort -rn \| head -20` |
| Count one event | `grep -c 'PATTERN' LOG` |
| Lines in a time window | `awk -v a='FROM' -v b='TO' '$1 >= a && $1 <= b' LOG` (FROM and TO are ISO prefixes, for example `2026-11-08T10:00`) |
| Events per minute | `awk '{print substr($1,1,16)}' LOG \| uniq -c` |
| Event counts inside a window | the window filter, then the count-by-event command |
| Peer connection summary | `grep -o 'connected=\(true\|false\)' LOG \| sort \| uniq -c` |

Read order for a timeout or stall report: count by event, find the minute where the count
jumps, filter to that window, then read a sample of the first lines of the window. Report the
total and the window, not only the sample.
