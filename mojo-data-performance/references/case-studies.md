# What magmalake measured, and what gave it away

Each story is a slowdown that was found and fixed in the magmalake Mojo
stack. Where the record says what pointed to the problem, that is given as
the clue. The numbers come from the runs named; later runs may differ.

## One thread consumed what sixteen decoded (dista shuffle, 2026-11)

A group-by over 1.42 billion trips on one AWS c7g.4xlarge took 202 s on one
thread and 126 s on sixteen. **The clue** was that ratio: sixteen threads
should have done far better. The reader decoded on sixteen threads, but one
thread took every batch and did the per-row work. Profiling on a laptop
showed the work was 82% of the run on 8 threads. The per-row loop was then
made 2.7× faster (hot-loops.md); giving each thread its own accumulator is
the remaining fix.

## A shared library opened on every call (zstd.mojo, 2026-08 and 2026-09)

The zstd binding opened its library with `dlopen` on every decompress call,
about 450 µs each. Opening it once per process and keeping the handle fixed
it, and the same rule now holds for every C library magmalake links:
libzstd, liblz4, libcurl and libsqlite3.

It came back in September, because three repos had a pixi git dependency on
zstd.mojo with no pinned `rev`, which stays locked at whatever version was
first resolved. The installed package also wins over an `-I` source path,
so the fixed source was on the include path and ignored. **The clue** was a
profile: one scan of the 79.5M-row taxi table spent 52% of its time in
`dyld` and 0.5% in `ZSTD_decompress`, over 8,218 `dlopen`s. Re-locking took
the scan from 2,687 to 262 ms. It had hidden for two weeks because the
library's own benchmark table has only a few Parquet pages, so the cost
looked like noise there. Measure on data big enough to have many pages.

## Reads waited one at a time (the taxi benchmark on S3, 2026-11)

With one decoding thread reading one file at a time from S3, the taxi
queries took 1,384 s in Rust and 1,306 s in Ray. Keeping 8 files' reads in
flight made them 6.6× and 6.9× faster. iceberg.mojo's `files_at_once` and
`overlap_io` options do the same for Mojo scans.

## Parsing the same bytes again and again (avro.mojo and iceberg.mojo, 2026-08)

Parsing the schema JSON was 72% of reading a manifest, and every manifest of
a table carries the same schema, byte for byte. Caching the decode plan,
keyed on the schema's raw bytes, took the fixed cost of planning from 113.7
to 31.9 µs per manifest (3.6×) over 500 manifests.

## An allocation per field (avro.mojo, 2026-08)

The Avro reader built a boxed value for every field, about 60 allocations a
record. A cursor that decodes into slots, planned once per schema and reused
across records, made it 11–13× faster than fastavro.

## Hashing 45× slower than the hardware (objectstore.mojo, 2026-08)

S3 signs every byte it uploads with SHA-256, and the scalar code ran at 610
MB/s, where the CPU's SHA instructions do 2.7 GB/s. Using them took
multipart uploads from 53 to 409 MB/s. On GitHub's macOS arm64 runners the
compiler can't detect those instructions, so the code checks for them at
run time.

## Where the scan stops scaling (iceberg.mojo, 2026-08)

A scan of a million rows took 35.8 ms on one core and 12.1 ms on four,
about 3×, which was judged to be the memory-bandwidth ceiling. Past that
point, faster code per core doesn't help; reading fewer bytes does, through
column projection and pruning row groups.
