# Making readers, writers and codecs fast

Rules for columnar readers, writers, decoders and codecs, mostly from making
parquet.mojo match parquet-cpp and arrow-rs. Most come down to not doing
work that gets thrown away.

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

## Parallelism has a shape

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
