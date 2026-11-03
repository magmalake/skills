---
name: mojo-data-performance
description: Write fast Mojo data code and find out where its time goes, from the per-row loops of a pipeline down to the readers, writers and codecs under it, and take benchmark numbers that survive being checked. Use when writing or reviewing Mojo code that loops over Arrow record batches, Parquet or Iceberg scans (arrow-mlake, parquet.mojo, iceberg.mojo, dista, orca), or that filters, groups, joins or aggregates rows; when making a parser, reader, writer, encoder or codec faster; when writing a benchmark or comparing against pyarrow, parquet-cpp, arrow-rs or Ray; when a number looks too good or moves between runs; or before claiming a speedup. Pairs with Modular's mojo-syntax skill.
license: Apache-2.0
metadata:
  author: magmalake
  version: "0.2"
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

Most of this file is about the code that consumes a scan's batches. For
the reader, writer or codec underneath, and for taking a number you can
trust, see the last two sections and their references.

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

## Readers, writers and codecs

When the decoding itself is the slow part, the rules are mostly about not
doing work that gets thrown away. The details and their numbers are in
[references/readers-and-codecs.md](references/readers-and-codecs.md).

- **Check that each fast path's gate can be met** by the callers that reach
  it, and prove by instrumentation that it runs. A vectorized path whose
  gate no list column could meet left one 9 KiB column at 43% of a read.
- **Don't compute what a later decision usually discards.** Building a
  dictionary only to choose plain encoding was 67% of an int64 column write;
  removing it made the write 3.3× faster with byte-identical output.
- **Borrow instead of copying,** and decode straight into the final layout,
  such as definition levels into the validity bitmap.
- **Work in runs** where the data has them: at 1% nulls, about 250
  `memmove`s replace about 25,000 per-slot branches.
- **Know when a stage is already at memory bandwidth:** one that moved
  18.3 MB in and out in 1.2 ms, about 30 GB/s, could only be skipped.
- **Find the slowest item before adding threads.** Splitting by column chunk
  capped one read at 1.39×, because one fat column ran the whole time.

Test the optimization as well as the answer: break it on purpose and check
that a test fails ([references/testing-optimizations.md](references/testing-optimizations.md)).
Mojo's own traps, such as values dying at their last use and
`MutUntrackedOrigin`, are in [references/mojo-hazards.md](references/mojo-hazards.md).

## Taking a number you can trust

[references/measurement.md](references/measurement.md) has the full rules.
The ones that changed conclusions in magmalake:

- **Report the median (p50) with p90 beside it, never the mean alone.** One
  slow first call moved a mean by 30% and the p50 not at all.
- **Build the benchmark ahead of time; never time it under `mojo run`.** The
  JIT inflated one benchmark 1.9×.
- **Check the machine held still.** The same benchmark took 31 ms idle and
  89 ms beside 16 busy threads.
- **Name the reference's API and thread count.** `pq.read_table` is
  pyarrow's dataset scanner, not parquet-cpp's read path, and pyarrow's
  thread setting moved one file's read 2.9×.
- **When two numbers disagree, check they were produced the same way**
  before explaining the difference.

## Before claiming a speedup

- **Compare the answers** before and after, exactly where you can (see the
  last loop rule). A faster wrong answer is worse than a slow right one.
- **Report both numbers** and the setup: rows, threads, local disk or S3, and
  whether the cache was warm.
- **Say what's left.** After the shuffle fix, 325 ms of the 777 ms is still
  copying columns, and on many threads the work is still on one thread.
