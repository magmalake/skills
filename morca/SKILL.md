---
name: morca
description: Write, run and schedule data pipelines in Mojo with morca (formerly orca), magmalake's orchestrator, whose steps, partitions and DAG the compiler checks. Use when writing or changing a morca pipeline (Step, Pipeline, Mapped, Gather, Monthly, Daily, Hourly or Keyed partitions, Cron schedules, sensors, reads=, writes= and replaces= datasets, retries, defer, version=), when a step calls dista tasks or a Rust worker (rust_step), reads or writes Iceberg tables through morca.tables (BatchBuilder, scan, to_batch_reader, append, append_files, FileWriter, ensure_table), when using the `morca` command (register, backfill with --rerun, --as-fill or --cancel, retire, status, config check, show and export), writing morca.toml (requests and grants, max_runs, fills, priority, keys_per_run, worker pools, overlays), running the scheduler or UI, or when a morca build, registration or run fails. Pairs with the dista and magmalake-apps skills and Modular's mojo-syntax.
license: Apache-2.0
metadata:
  author: magmalake
  version: "0.3.1"
---

<!-- EDITORIAL GUIDELINES FOR THIS SKILL FILE
Every line here is loaded into an agent's context, so stay brief, but write
whole phrases a newcomer can follow: a list of bare nouns is not brief, it is
cryptic. Every API shown must exist in morca as published (morca-mojo
0.30.0), copied from its source or examples. Blocks fenced as `mojo compile`
are built by CI against that version; keep them whole programs. Put long
material in references/.
-->

morca runs pipelines written in Mojo. A pipeline is a set of **steps**, each
a Mojo function, over **partitions** (months, days, hours, or names read
from a dataset). The compiler checks the DAG: a step's input type must be
what the step before it returns. Runs are recorded in Postgres (or SQLite)
and resume where they stopped. Steps can fan out over a
[dista](../dista/SKILL.md) cluster and read and write Iceberg tables. Each
pipeline is its own binary; the `morca` command is the scheduler, the UI and
registration, and `morca.toml` says where and how big each pipeline's runs
are.

morca was called orca until 0.25.0. Code, commands and settings are all
`morca` now (`morca.toml`, `MORCA_*`, `/opt/morca`), but **stored names
stayed `orca`**: the Postgres schema (`orca.runs`, `orca.pipelines`), the
Iceberg snapshot summary keys (`orca.run`, `orca.pipeline`), secret IDs
(`orca/github-token`) and server paths (`/etc/orca`). Use those as they are.

## Install

morca is the mojoshelf tin `morca-mojo` (0.30.0, Mojo 1.2.0). A new project
needs Modular's channel, pixi's build preview and the compiler first:

```sh
pixi init lake -c https://conda.modular.com/max -c conda-forge \
    -c https://repo.prefix.dev/modular-community && cd lake
# In pixi.toml's [workspace], add:   preview = ["pixi-build"]
pixi add "mojo-compiler==1.2.0"
pixi workspace platform add linux-aarch64   # Hetzner CAX and Graviton images
pixi shelf add morca-mojo                    # brings dista-mojo, arrow-mlake-mojo, ...
```

`pixi shelf add` takes many minutes and prints every tin's build log; it
hasn't hung. **Reading morca's source:** the environment holds compiled
`.mojoc` packages only, so read a tin on GitHub at the `rev` its
`pixi.toml` line pins (morca is
[morca-dev/morca](https://github.com/morca-dev/morca)), or in pixi's git
cache: `ls -d ~/Library/Caches/rattler/cache/git-v0/checkouts/*/<rev's
first 7>*` on macOS, `~/.cache/rattler/cache/git-v0/checkouts/` on Linux.
Paths such as `examples/taxi/taxi.mojo` and `docs/morca-toml.md` are there.
The public API is what `src/morca/__init__.mojo` lists; never import
`morca._internal`.

The project's `morca` command is two lines, in a file **not** named
`morca.mojo` (that file would be the module `morca` and hide the package):

```mojo compile
from morca.command import morca_command


def main() raises:
    morca_command()
```

## A pipeline

```mojo compile
from dista import Seq
from morca import Cron, Ctx, Gather, Mapped, Month, Monthly, Pipeline, Step, morca_main


def size(mut ctx: Ctx, m: Month) raises -> Int:
    ctx.log(String("sizing ", m))
    return m.month * 100


def total(mut ctx: Ctx, sizes: Seq[Int]) raises -> Int:
    var t = 0
    for i in range(len(sizes)):
        t += sizes[i]
    return t


comptime Size = Step["size", Month, Int, size, Mapped, retries=2]
comptime Total = Step["total", Seq[Int], Int, total, Gather, after=Size]
comptime Sizes = Pipeline[
    "sizes",
    Size,
    Total,
    partitions=Monthly["2024-01"],
    schedule=Cron["0 3 2 * *"],  # 03:00 UTC on the 2nd, for the month before
    max_runs=2,
]


def main() raises:
    morca_main[Sizes]()
```

- A step function is a top-level `def f(mut ctx: Ctx, a: A) raises -> R`.
  **`Mapped`** runs once per partition, taking the partition key (`Month`,
  `Day`, `Hour` or `Name`) or what the mapped step it comes `after`
  returned. **`Gather`** runs once per run, over a `Seq[R]` of every
  partition's result.
- Results cross processes through dista's codec: scalars, `String`, `Seq`,
  `RecordBatch`, and `@fieldwise_init` structs of those (`Copyable,
  Movable`). A `List` field doesn't encode; use `Seq`.
- `Daily["2024-01-01"]` partitions by day, `Hourly["2026-11-11T00"]` by
  hour. `Cron[expr, tz]` takes any IANA zone. `catchup=False` starts only
  the latest fire a scheduler missed.
- **A run is sequential:** its steps run in order, and a mapped step runs
  its partitions one after another. Parallelism comes from dista tasks
  inside a step, or from several runs at once (`max_runs`).

[references/steps.md](references/steps.md) has every `Step` and `Pipeline`
parameter (`retries`, `backoff_seconds`, `timeout_seconds`, `replaces=`,
`version=`, `run_retries`, `keys_per_run`, ...), `defer`, `outdated()`,
`auto_count`, and the pipeline binary's own commands.

Every pipeline binary has a command line of its own:

```sh
mojo build pipelines/sizes.mojo -o build/sizes
./build/sizes describe                         # the DAG as JSON
./build/sizes run --partitions 2024-01..2024-03
./build/sizes fires 3 --after 2024-03-09T12:00Z
```

Without `MORCA_DATABASE_URL` a run keeps its state in memory; with a
Postgres URL (or `sqlite:<path>`) it is recorded, and `resume <run>`
continues one that stopped.

## Steps that call dista

A pipeline declares its tasks; a step borrows the run's runtime, started
once before the first step, from `ctx`:

```mojo compile
from dista import ObjectRef, Task
from morca import Ctx, Mapped, Month, Pipeline, Step, Tasks, days_in, morca_main


def work(day: Int) -> Int:
    return day * 7


comptime Work = Task[Int, Int, work]
comptime MyTasks = Tasks[Work]


def month_total(mut ctx: Ctx, m: Month) raises -> Int:
    ref rt = ctx.runtime[MyTasks]()
    var refs = List[ObjectRef[Int]]()
    for d in range(1, days_in(m.year, m.month) + 1):
        refs.append(rt.remote[Work](d))   # send everything first
    var t = 0
    for r in refs:
        t += rt.take(r)                   # then collect; take frees the result
    return t


comptime MonthTotal = Step["month_total", Month, Int, month_total, Mapped]
comptime Totals = Pipeline["totals", MonthTotal, tasks=MyTasks, workers=3]


def main() raises:
    morca_main[Totals]()
```

A run on one host forks `MORCA_WORKERS` dista workers (default 4); the
pipeline's `workers` is the cluster size on Kubernetes and the extra
machines on a worker pool. On a stack, `DISTA_WORKER_SLOTS = "auto"` in morca.toml sizes
them from the run's grant. See the dista skill for tasks, refs and actors.

## Iceberg tables: `morca.tables`

`MORCA_WAREHOUSE` is the root of a filesystem catalog (a directory or
`s3://bucket/warehouse`): dataset `demo.events` is the table at
`<warehouse>/demo/events`. A step declares what it reads and writes, and
morca refuses anything else. The work runs in **liblake**, a shared library
the package ships, so pipelines don't compile iceberg or parquet; build
batches with **`BatchBuilder`**, not `iceberg.batch`.

```mojo compile
from std.os import getenv, setenv

from dista import Seq
from parquet import RecordBatch

from morca import Ctx, Daily, Day, Gather, Mapped, Pipeline, Step, morca_main
from morca.tables import BatchBuilder, Schema, append, ensure_table, scan, table

comptime EVENTS = "demo.events"
comptime SCHEMA = (
    '{"type":"struct","schema-id":0,"fields":['
    '{"id":1,"name":"day","required":true,"type":"string"},'
    '{"id":2,"name":"n","required":true,"type":"long"}]}'
)


def load(mut ctx: Ctx, d: Day) raises -> Int64:
    var t = ensure_table(ctx, EVENTS, Schema.parse(SCHEMA))
    var b = BatchBuilder(t.metadata.schema())
    var day = b.col("day")
    var n = b.col("n")
    for i in range(3):
        b.add_str(day, String(d))
        b.add_int(n, Int64(d.day * 10 + i))
    var batches: List[RecordBatch] = [b^.build()]
    return append(ctx, t, batches)  # a retry finds this commit, not a second one


def total(mut ctx: Ctx, snapshots: Seq[Int64]) raises -> Int64:
    var reader = scan(ctx, table(ctx, EVENTS)).select(["n"]).to_batch_reader()
    var sum = Int64(0)
    while True:
        var next = reader.next_batch()   # one batch at a time, not the scan
        if not next:
            break
        var batch = next.take()
        var col = batch.column_i64(0)
        for v in col[0]:
            sum += v
    return sum


comptime Load = Step["load", Day, Int64, load, Mapped, writes=[EVENTS]]
comptime Total = Step[
    "total", Seq[Int64], Int64, total, Gather, after=Load, reads=[EVENTS]
]
comptime Events = Pipeline["events", Load, Total, partitions=Daily["2024-01-01"]]


def main() raises:
    if not getenv("MORCA_WAREHOUSE"):   # a stack sets it; locally, a directory
        _ = setenv("MORCA_WAREHOUSE", "build/warehouse")
    morca_main[Events]()
```

- **`scan(ctx, t)` is pinned:** a retry or resume of the step reads the
  snapshot its first attempt read. `.to_batches()` reads it all at once.
- **`append` is recognised:** a retry after a crash finds the snapshot its
  attempt already made. A step that recomputes a partition declares the
  dataset in `replaces=` instead, so a `--rerun` replaces its rows rather
  than adding them twice.
- **Workers can write the files** (`write_files`, `FileWriter` with
  `write_row_group`) and the step commits them with `append_files`.

[references/tables.md](references/tables.md) has these and the rest:
`Schema` lookups, `PartitionSpec`, typed partition values of planned files,
`find_commit` for a step that commits several times, `commit` for a
compaction, and nested columns in `BatchBuilder`.

## Keyed partitions and sensors

Partitions can be names read from a column of a dataset, for things that
aren't dates and grow (repositories, devices, logs). `morca.keyed` isn't
re-exported from `morca`:

```mojo
from morca.keyed import Keyed

comptime Repos = Pipeline[
    "repos", Fetch, Index,
    partitions=Keyed["lake.repos", "repo"],   # the distinct `repo` values
    keys_per_run=50,                          # one run handles 50 keys
    sensor=True,                              # new snapshots start runs
]
```

The key type is `Name` (`String(name)` for its text). `--partitions` takes
names, `@all` or `@file:<path>`. A keyed pipeline has no calendar, so
`schedule=` and `fire=` don't compile with it. With `sensor=True` the
scheduler starts runs when a dataset the pipeline reads gets a new
snapshot. **Register a sensor before the data it senses:** snapshots that
exist when it's registered count as seen; backfill them instead. To work on
a run's keys in parallel, make the first step a `Gather` over `Seq[Name]`
and send each key to a dista task (references/steps.md has the pattern).

## The `morca` command

It needs `MORCA_DATABASE_URL`, a Postgres the scheduler, UI and runs share
(the scheduler and UI don't run on SQLite):

```sh
./build/morca register ./build/sizes          # or no binaries: all of morca.toml
./build/morca backfill sizes 2023-01..2024-12
./build/morca backfill --rerun --as-fill sizes 2023-01..2024-12   # throttled, resumable
./build/morca backfill --cancel sizes         # ends that fill request and its runs
./build/morca status sizes                    # runs, why runs wait, fill progress
./build/morca retire sizes                    # stops it; register brings it back
./build/morca config check                    # morca.toml, in CI, no database
./build/morca config show sizes               # each setting and where it came from
./build/morca config export --out -           # a morca.toml from what's registered
./build/morca scheduler                       # fires schedules, sensors, fills, retries
./build/morca ui                              # http://127.0.0.1:8270
```

`backfill --rerun` refuses a step that appends (it would duplicate rows)
unless given `--allow-append`; declare `replaces=` instead. `morca login
<url>` and `morca --remote <url> ...` run the same commands against a
deployed UI. [references/command.md](references/command.md) has every
command: start, cancel, pause, resume, rerun, rebuild, lineage, expire,
users, workers, watchdog and alerts.

## morca.toml

The code says what a pipeline does; `morca.toml`, at the project's root,
says where its runs run and how much they use. `morca register` reads it,
and a key taken out of it goes back to its default at the next
registration.

```toml
[defaults]
memory_gb = "auto"           # learned from the last 20 runs' peaks

[pipelines.gharchive]
cpus = "1..16"               # a request: start once 1 CPU fits, grant up to 16
memory_gb = "2..24"
max_runs = "auto"            # as many as the host has room for, up to 64
keys_per_run = 4
fills = "all"                # fill the whole range by itself
fill_max_runs = 2
fill_window = "22..6"        # UTC hours fill runs start in
fill_lag = "2h"
priority = -10               # others start first when room is short
env = { DISTA_WORKER_SLOTS = "auto", GHARCHIVE_PARALLEL = "auto" }

[pipelines.mojo_repos]
secrets = { GITHUB_TOKEN = "orca/github-token" }
placement = "worker:lab"     # runs on the `lab` pool's machines
```

A stack's own differences (no fills on a beta stack, one pipeline only a
lab runs) go in an **overlay** laid on this file (`--overlay`,
`MORCA_CONFIG_OVERLAY`), not in a second copy.
[references/morca-toml.md](references/morca-toml.md) has every key, what
"auto" does, grants and cgroups, fills, priority, overlays, apps and worker
pools.

## Where runs run

- **local** (the default, and what a Hetzner stack uses): the scheduler
  starts each run as a process on its host, admitted against the host's
  CPUs and memory by its request, in a cgroup of its own.
- **control** (`placement = "control"`): on the scheduler's host whatever
  the runner.
- **worker pools** (`placement = "worker:<pool>"`): `morca worker`
  processes on other machines claim the runs over the UI's API, and a
  `[pools.<name>]` table lets morca make and remove Hetzner or EC2 machines
  for them.
- **ec2** and **kube** (`MORCA_RUNNER`): instances or pods per run.

**Steps in Rust:** `morca.rust_step` runs a Rust worker library in the
step's process and commits what it streams, plus the Parquet files it
writes, in one commit a retry recognises.
[references/workers-and-rust.md](references/workers-and-rust.md) covers
pools, `morca worker`, and Rust steps with the `morca-worker` crate.

**Deploying** a project (its image, a Hetzner stack with `deploy.sh`,
`promote.sh` and a release's `upgrade.toml` steps) is in the
[magmalake-apps](../magmalake-apps/SKILL.md) skill.

## Common mistakes

- **`bin/morca.mojo`**: the file becomes the module `morca` and every
  `from morca import ...` fails. Name it `bin/scheduler.mojo`.
- **Importing `orca` or `iceberg.batch`**: the package is `morca` since
  0.25.0, and batches come from `morca.tables.BatchBuilder` since 0.29.0.
- **`from morca import ensure_table`**: tables, keyed partitions and
  `rust_step` are only in `morca.tables`, `morca.keyed` and
  `morca.rust_step`.
- **A step's types don't line up**: `after=` a step returning `R` means the
  next step takes `R` (mapped) or `Seq[R]` (gather). The error is at the
  `Pipeline[...]` line.
- **Writing a dataset not in `writes=` or `replaces=`** (or reading one not
  in `reads=`): the step fails, naming it.
- **Changing code without registering**: rebuild and `register` again;
  queued runs move to the new code, and a running one can ask `outdated()`
  whether to stop.
- **A long step that never checks `outdated()`**: a deploy waits for it up
  to `DRAIN_SECONDS`, then kills it. Check between units of work and return
  as soon as it's true (references/steps.md has the census's `mojo_live`
  loop).
- **`backfill --rerun` on a step that appends**: refused, since the rows
  would be there twice. Declare the dataset in `replaces=`.
- **`fills = "all"` on a keyed pipeline or a sensor**: `register` refuses
  it; they follow their keys or inputs instead.
