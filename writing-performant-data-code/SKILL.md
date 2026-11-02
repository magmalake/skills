---
name: writing-performant-data-code
description: Measure and optimise data-processing code — columnar readers and writers, decoders, codecs, scan engines — without fooling yourself. Use when asked to make a parser, reader, writer, encoder or query path faster; when writing or fixing a benchmark harness; when comparing against a reference implementation such as pyarrow, parquet-cpp or arrow-rs; when a performance number looks too good or moves between runs; or when writing high-throughput Mojo. Covers measurement discipline, optimisation rules with the evidence attached, tests that catch a silently-broken fast path, and Mojo-specific ownership and threading hazards. For a pipeline's per-row loops over record batches, use mojo-data-performance as well.
license: Apache-2.0
compatibility: Language-independent except for the final section, which is Mojo 1.0 specific
metadata:
  author: magmalake
---

# Writing performant data code

Rules for making columnar and streaming data code fast, and for producing
numbers about it that survive being checked. Every rule carries the
measurement that produced it — a rule without its number is folklore, and you
should treat any rule here whose number you cannot reproduce on your own
machine as untested on your workload.

The order is deliberate. Measurement first, because every optimisation below
was found or refuted by it, and because most wrong performance claims are
measurement bugs rather than code bugs. Then the technique. Then the tests
that stop an optimisation from silently doing nothing. Then Mojo.

## 1. Measure first, and measure honestly

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

### Making a reference comparison fair

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

## 2. Do not do work you throw away

**Check that a fast-path gate can actually be satisfied.** A vectorised path
was gated on `require_def == 0 or (all_present and …)`. A list child is always
called with `require_def > 0`, and any null makes `all_present` false — so the
fast path was unreachable for exactly the data that needed it. One 9 KiB
column was 43% of a read. Read every gate against the callers that reach it,
not against the case you had in mind when you wrote it.

**Prove the fast path is taken, by instrumentation.** A change that silently
never fires passes every test while doing nothing, and then gets credited with
someone else's improvement.

**Discard the work, not just the result.** 67% of a plain-int64 column write
was building a dictionary and then deciding not to use it. Removing the build
produced byte-identical output **3.3× faster**. Look for anything computed
speculatively before a decision that usually goes the other way.

**A heuristic can be wrong as well as slow.** Allowing a dictionary to grow to
half the row group made fixed-width chunks **30% larger** than plain encoding.
Check that the heuristic's chosen branch is actually better on the data it
chooses it for, not merely cheaper to decide.

**Prefer an absolute cap to prefix sampling.** Cyclic and sorted
low-cardinality columns have all-distinct prefixes: `str-{i % 5000}` has 5,000
distinct values and a perfectly distinct first 5,000. Any "sample the first N
and extrapolate" rule reads that as unbounded cardinality. Cap on the real
budget instead.

**Copy nothing you can borrow.** An uncompressed page was deep-copied for no
reason: 9.4% of a read and a third of its total allocation. Both parquet-cpp
and arrow-rs return the page buffer untouched when there is no codec, and
decompress into one scratch buffer reused per chunk — and they *document* that
the previous page is invalidated by advancing. Buy the speed with a stated
lifetime rule rather than with a copy.

**Decode into the destination representation.** arrow-rs decodes definition
levels straight into the validity bitmap when `max_def == 1 && max_rep == 0`;
at bit width 1 a literal run is a shifted memcpy rather than N decodes. The
naive shape made three linear passes over 2 bytes per slot to produce 1 bit
per slot. Ask what the final byte layout is and decode into it once.

**Work in runs where the data is run-structured.** At 1% nulls, spacing values
by runs turns ~25,000 per-slot branches into ~250 `memmove`s. Null masks,
RLE runs and sorted keys are all run-structured; per-element loops over them
are the default mistake.

**Fuse the passes that share a traversal.** Decoding indices into a
materialised array, bounds-checking that array in a second pass, and gathering
in a third is three passes and an intermediate allocation where the references
do one pass into a reused 4 KB scratch, bounds-checking a whole unpacked block
with a min/max scan rather than per element. Note that a short-circuiting
`.all()` blocks autovectorisation — arrow-rs writes its bounds check as a
`chunks_exact(16)` fold on purpose.

**Know when you are already at memcpy speed.** An assembly stage moving
18.3 MB in and 18.3 MB out in 1.20 ms is running at ~30 GB/s. It cannot be
made faster; it can only be *not done*. Compute the bandwidth before you try
to optimise a copy.

### Parallelism has a shape

**A per-item parallel decomposition is bounded by its slowest item.** Splitting
work per column chunk capped a read at **1.39×** because one fat dictionary
column ran the whole time. Find the critical item before you add workers.

**Flatten every axis into one work list rather than splitting the budget
between axes.** Row groups and columns are two axes; giving each half the
workers deletes the first axis entirely on a one-row-group file. Flattening
(row group, column) pairs into a single list beat splitting.

**Longest-task-first is not free.** It recovered one fixture's gap and cost
22% on another, by starting every fat column at once and contending for
bandwidth. Measure both orderings on more than one fixture.

**Fit Amdahl's law to a scaling ladder and it will tell you where to look.**
Fitting the serial fraction from a 1/2/4/8/10-worker ladder predicted the
observed bend to within a few percent and named the right stage as the
ceiling. Also expect the bend where the *performance* core count is, not where
the total core count is, and watch the p90 degrade past it even while the p50
improves slightly.

**Check the allocator you are being compared against.** A conda or pip build
of a reference library typically ships jemalloc or mimalloc; a stock build of
yours is on the system allocator. That is not unfair, but it means an
allocation-count gap costs you more than the same count costs them — one more
reason to remove allocations rather than to make them faster.

## 3. Test the optimisation, not the happy path

**Run negative controls: break your own optimisation deliberately and prove a
test fails.** Twice in one session a test passed for the wrong reason. If
breaking the code does not break the test, you do not have a test.

**Fold structure, not only values.** A fingerprint that walks a tree from its
roots folds a *consistently permuted* arena to an identical value: every value
correct, the layout wrong, every test green. Zero tests would have caught it.
Include positions, offsets, and traversal order in the fingerprint, not just
the payload.

**Mutation-test the tests.** Introduce five deliberate breakages and check
which the suite catches. In one round, the new test caught all five and the
existing suite caught only some — which is the only evidence that the new test
was worth adding.

**Assert the compiler's silence as well as its errors.** A file that must
compile without warning and then print the wrong answer is a regression test
against the *toolchain*: it tells you when the compiler gets good enough to
catch what it currently accepts.

## 4. Mojo-specific hazards

Everything above applies to any language. These do not.

**Values die at their last textual use, not at the end of scope.** A benchmark
that reads a buffer through a span must keep the owner alive — `keep(data)`
after the timed region — or it times a use-after-free that returns
plausible-looking garbage rather than crashing.

**`MutUntrackedOrigin` is a contract, not a bug.** Every wrong answer worth
chasing in this codebase followed a line that told the compiler to stop
tracking an origin. When something is inexplicably wrong, grep for the
untracked conversions first.

**A guard holding the last share of shared state can free that state inside
its own critical section.** A pool lease took a mutex inline; the owning
`ArcPointer` reached its last use mid-section, freeing the `pthread_mutex_t`
before `unlock()` wrote into freed heap. Fix: every critical section takes the
state as a **borrowed argument**, which cannot be destroyed during the call.
macOS was green throughout; only Linux crashed — so a green macOS CI leg is
not evidence here.

**ThreadSanitizer reports on any allocating threaded Mojo program.** The 1.0.0
runtime allocator is invisible to TSan's intercepts. Before believing *or*
dismissing a report, build a control: the same harness with task bodies that
only read, only write, and only allocate. Compare against that baseline.

**`.mojoc` packages are stamped with the compiler that built them** and are
refused by any other, so a package built with stable cannot be loaded by
nightly. Build the package with the toolchain that will consume it.

**`-I <path>` does not override a package already on the environment's import
path.** Testing a modified library against a consumer requires shadowing the
packaged copy; otherwise you silently test the old one and conclude your
change did nothing.

## Applying this

When asked to make data code faster, in order:

1. Get a trustworthy number first — built, percentile-reported, steadiness-checked.
2. Profile to stages and print allocation counts and bytes alongside times.
3. Attack the largest stage that is not already at memory bandwidth.
4. Check every fast-path gate is reachable, and instrument to prove it fires.
5. Write the negative control before you write the optimisation.
6. Re-measure the same way you measured before, and say which leg of any
   reference comparison you used.

For the code that consumes a reader's batches, a pipeline's filters,
group-bys and aggregations, the
[`mojo-data-performance`](../mojo-data-performance/SKILL.md) skill in this
repository has the per-row loop rules and the decode-only baseline.
