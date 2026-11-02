# Finding where the time goes

The aim is to split a pipeline's time into decoding and work, without a
second implementation to compare against.

## The decode-only baseline

Run the pipeline's own scan, with nothing done to the batches:

```mojo
from std.time import perf_counter_ns

def decode_only_ms(scan: TableScan, opts: ScanOptions) raises -> Int:
    """The same scan as the pipeline's, with every batch thrown away."""
    var t0 = Int(perf_counter_ns())
    var reader = scan.to_batch_reader(opts)
    var rows = 0
    while True:
        var b = reader.next_batch()
        if not b:
            break
        rows += b.value().num_rows  # touch the batch so it isn't skipped
    reader.raise_if_failed()
    return (Int(perf_counter_ns()) - t0) // 1_000_000
```

It has to be the same scan: the same columns (`select`), the same filter,
and the same `ScanOptions` (`num_workers`, `files_at_once`). With fewer
columns it decodes less, and the comparison flatters the pipeline.

## Timing the work

Add up the time spent in the call that consumes each batch:

```mojo
var work_ns = 0
while True:
    var b = reader.next_batch()
    if not b:
        break
    var t = Int(perf_counter_ns())
    acc.add(b.value())
    work_ns += Int(perf_counter_ns()) - t
```

To split the work further, time each phase inside it the same way, keeping
the counters in fields of the accumulator. That is how the shuffle's per-row
loop turned out to be 897 ms of reading values and only 188 ms of the
scatter into memory (see hot-loops.md).

## Reading the numbers

| What you see | What it means |
|---|---|
| Total is close to decode-only | The work is cheap. Look at the reading instead: `files_at_once`, `num_workers`, the storage. |
| Total is well above decode-only on one thread | The per-row work is the bottleneck. Apply the loop rules. |
| More threads speed up decode-only, but not the total | The work runs on one thread. Give each worker its own accumulator. |
| Decode-only doesn't get faster past about 4 threads | That is likely memory bandwidth. More threads won't help. |

## Traps

- **A warm cache flatters whatever runs second.** On a local disk the second
  scan reads from the page cache. Run the decode-only scan first, or run each
  twice and keep the second. On S3 there's no such cache.
- **One run is not a measurement.** Take the median of several, and say so.
- **Removing the work can remove the decode.** If the batches aren't used,
  a lazy reader might skip decoding. Touch every batch, as above.
- **A stopped program still prints PASS.** When you cut code out to time the
  rest, the answer is wrong. Throw those builds away, and never commit them.
- **The same answer is the check.** Before and after a change, compare the
  outputs. If floating-point sums differ, the order of the additions changed,
  and you should know why.
