# Taking a benchmark number you can trust

How to measure data code so the number survives being checked, and how to
make a comparison against a reference implementation fair. Every rule
carries the measurement that produced it.

**Report p50 as the headline, p90 beside it, min only as a clean floor. Never
the mean alone.** One first-call sample of 125 ms moved a pyarrow benchmark's
mean by 30% while its p50 did not move at all. A mean over an unbounded tail
measures how the machine felt, not how the code performs. Quote min only when
you want an explicit "nothing got in the way" floor, and label it as that.

**Measure the clock's tick, not the cost of reading it.** On macOS/arm64
`perf_counter_ns` costs about 13 ns to call but advances in 1000 ns steps.
Per-iteration timing is only honest when one iteration costs more than ~100×
the tick; below that, calibrate a repetition count and time the batch. Find
the tick by spinning until the returned value changes — do not assume the
units in the function's name are the units it resolves.

**Build the benchmark; never JIT it.** Running a multi-benchmark suite through
`mojo run` inflated one benchmark by **1.9×**; the same program compiled ahead
of time did not. The same hazard exists anywhere a warm-up-sensitive runtime
sits under the timer.

**Never compare one configuration under a JIT against another built ahead of
time.** That single error produced two confident and wrong diagnoses in a row
— first "the benchmarks interfere by ordering", then "the machine is busy" —
before anyone checked that the two numbers came from differently built
binaries. When two figures disagree, verify they were produced the same way
before you explain the difference.

**Make the harness state whether the machine held still.** Contention is real
and it is large: the same benchmark ran 31 ms idle, 51 ms against 8 competing
threads, and 89 ms against 16. Time a fixed reference kernel either side of
each benchmark, re-time the first benchmark at the end, read the load average,
and print a verdict. A harness that cannot say the machine was steady cannot
defend its numbers. Run contended ladders — worker-count sweeps especially —
one at a time, or they contend with each other.

**Take numbers per benchmark, not from one pass over a whole suite.**
Neighbouring benchmarks warm caches and allocators for each other.

## Making a reference comparison fair

A comparison against a reference implementation is a claim about two things,
and it is usually wrong about the second one.

**Name the API, not just the library.** `pq.read_table` is pyarrow's *dataset
scanner*, not parquet-cpp's read path. `ParquetFile.read()` is the honest
single-thread comparator, and it is faster: switching to it moved a measured
gap from 1.42× to 1.51× *against us*. Publishing the flattering leg is the
default outcome of not checking.

**Name the thread count on both sides, and set it explicitly.**
`pa.set_cpu_count(1)` matters even with `use_threads=False`: the same file
reads 2.25 ms one way and 0.83 ms the other — 2.9× apart on one file. Quote
"one thread" and "threaded" as separate labelled legs. A comparison that does
not name the leg is not a comparison.

**Confirm the reference materialises what you materialise.** pyarrow returns
`string`, not `dictionary<…>`, from a dictionary-encoded column, so the
dictionary gather is work both sides do and the comparison is sound. Had it
returned dictionary-encoded arrays, the same benchmark would have been
comparing a gather against a pointer copy.

**Do not "fix" a comparison in only the direction that helps you.** Where the
reference's own threaded path is slower than its dataset scanner, quoting the
scanner is the generous choice and should stay. Fairness is a property of the
whole table, not of each row taken separately.
