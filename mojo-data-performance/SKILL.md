---
name: mojo-data-performance
description: Write fast Mojo data pipelines, and find out where their time goes without a second implementation to compare against. Use when writing or reviewing Mojo code that loops over Arrow record batches, Parquet or Iceberg scans (arrow-mlake, parquet.mojo, iceberg.mojo, dista, orca), or that filters, groups, joins or aggregates rows; when a pipeline seems slow; or before claiming a speedup. Pairs with Modular's mojo-syntax skill; for readers, writers, codecs and benchmark discipline, use writing-performant-data-code as well.
license: Apache-2.0
metadata:
  author: magmalake
  version: "0.1"
---

<!-- EDITORIAL GUIDELINES FOR THIS SKILL FILE
Every line here is loaded into an agent's context, so stay brief, but write
whole phrases a newcomer can follow: a list of bare nouns is not brief, it is
cryptic. Every rule carries the measurement that justifies it, from a real
magmalake run, with where it came from. Put code and long stories in
references/, and keep this file to what an agent must know before writing
the first loop.
-->

A data pipeline's time goes to three places: reading and decoding the data,
the work done on each row, and waiting (on storage, on a lock, or on one
busy thread). Decoding is usually already tuned in the libraries. The work on
each row is what you write, and it is where a careless loop costs 2–3×.

This skill is about the code that consumes a scan's batches. For making
the reader, writer or codec itself faster, and for how to take a benchmark
number you can trust, see
[`writing-performant-data-code`](../writing-performant-data-code/SKILL.md)
in this repository.

## First, measure where the time goes

You rarely have a second implementation (Ray, DuckDB, pyarrow) to compare
against. Use the scan itself as the yardstick instead:

1. **Time the scan alone.** Run the same scan, with the same columns, filter
   and `ScanOptions`, and throw the batches away. This is the decode-only
   baseline: no pipeline can be faster than it.
2. **Time your work on its own.** Wrap the per-batch call (`fold`, `add`,
   whatever consumes the batch) in `perf_counter_ns()` and add the times up.
3. **Compare them.** On one thread, total time within about 1.5× of the
   decode-only time means the work is cheap; above that, the work is the
   bottleneck and worth optimizing. On many threads, also check whether the
   work runs on one thread while the decoding runs on many.

The measured example: a group-by over 77M trips took 4.6 s on one thread, of
which 2.1 s was the per-row work (45%). On 8 threads it took 2.6 s, of which
the same 2.1 s was the work (82%), because one thread consumed every batch.
[references/profiling.md](references/profiling.md) has the recipe and its
traps.

## Rules for the per-row loop

Each rule comes with what it saved in the dista shuffle benchmark (77M rows,
local table, one thread). The code is in
[references/hot-loops.md](references/hot-loops.md).

| Rule | Measured effect |
|---|---|
| Take each column's raw pointer once per batch, then index the pointer. Don't index `List` or `Tuple[List, List]` inside the loop. | 1.40 → 0.78 s for the whole per-row work |
| Keep everything one key accumulates side by side in one slot (array of structs), not in parallel arrays, so one update touches one cache line. | 2.09 → 1.40 s, together with the next rule |
| Filter without branches: compute every row, write it at a `kept` index, and advance `kept` by `Int(keep)`. Then add the kept rows in a second, tight loop. | measured together with the rule above |
| When keys are small integers (zone × zone × hour is 1.7M), index a dense array by key instead of using a `Dict`. | not measured on its own; both versions used a dense array |
| Don't change the order of floating-point additions while optimizing. Then the answer must match the old one bit for bit, which is a free correctness check. | every step above matched exactly |

## Rules for the whole pipeline

| Rule | Measured effect |
|---|---|
| Don't let one thread consume what many threads decode. Give each worker its own accumulator and merge them at the end, as dista's map tasks do. | 16 threads only took a one-process group-by from 202 s to 126 s on AWS |
| Keep several reads in flight, even with one decoding thread: `ScanOptions.files_at_once` (and `overlap_io`) in iceberg.mojo. | 6.6–6.9× on one thread reading from S3 |
| Load a shared C library once per process and keep the handle, never once per call. | with zstd opened on every call, a scan spent 52% of its time in `dyld`; fixed, 2,687 → 262 ms |
| Cache anything you parse that repeats byte for byte, such as Avro schemas in manifests. | manifest planning's fixed cost fell 3.6× (113.7 → 31.9 µs) |
| Don't allocate per record: decode into reused slots. | the Avro reader allocated about 60 times a record before its cursor |
| Know the ceiling: past a few cores a scan is limited by memory bandwidth, and more threads won't help. Read fewer bytes instead. | an Iceberg scan took 35.8 ms on one core and 12.1 ms on four, about 3× |

[references/case-studies.md](references/case-studies.md) tells each of these
stories, with what gave the problem away.

## Before claiming a speedup

- **Compare the answers** before and after, exactly where you can (see the
  last loop rule). A faster wrong answer is worse than a slow right one.
- **Report both numbers** and the setup: rows, threads, local disk or S3, and
  whether the cache was warm.
- **Say what's left.** After the shuffle fix, 325 ms of the 777 ms is still
  copying columns, and on many threads the work is still on one thread.
