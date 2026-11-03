---
name: orca
description: Write, run and schedule data pipelines in Mojo with orca, magmalake's orchestrator, whose steps, partitions and DAG the compiler checks. Use when writing or changing an orca pipeline (Step, Pipeline, Mapped, Gather, Monthly, Daily, Keyed partitions, Cron schedules, sensors, reads= and writes= datasets, retries), when a step calls dista tasks, reads or writes Iceberg tables (orca.tables scan, append, ensure_table), when registering, backfilling, rerunning or rebuilding with the `orca` command, when running the scheduler or `orca ui`, choosing a runner (local, kube, ec2), autoscaling a run's workers, giving runs secrets, or when an orca build or run fails. Pairs with the dista and magmalake-apps skills and Modular's mojo-syntax.
license: Apache-2.0
metadata:
  author: magmalake
  version: "0.1"
---

<!-- EDITORIAL GUIDELINES FOR THIS SKILL FILE
Every line here is loaded into an agent's context, so stay brief, but write
whole phrases a newcomer can follow: a list of bare nouns is not brief, it is
cryptic. Every API shown must exist in orca as published (orca-mojo 0.3.0),
copied from its source or examples. Put long material in references/.
-->

orca runs pipelines written in Mojo. A pipeline is a set of **steps**, each
a Mojo function, over **partitions** (months, days, or names read from a
dataset). The compiler checks the DAG: a step's input type must be what
the step before it returns. Runs are recorded in Postgres and resume where
they stopped. Steps run on [dista](../dista/SKILL.md) clusters and read and
write Iceberg tables. A pipeline is its own binary; the `orca` command is
the scheduler, the UI and registration.

## Install

orca is the mojoshelf tin `orca-mojo` (0.3.0, Mojo 1.2.0). A new project
needs Modular's channel, pixi's build preview and the compiler first:

```sh
pixi init lake -c https://conda.modular.com/max -c conda-forge && cd lake
sed -i.bak 's/^\[workspace\]/[workspace]\npreview = ["pixi-build"]/' pixi.toml
pixi add "mojo-compiler==1.2.0"
pixi workspace platform add linux-aarch64   # for images on AWS Graviton
pixi shelf add orca-mojo                     # brings dista-mojo, iceberg-mojo, ...
```

The project's `orca` command is two lines of its own, in a file **not**
named `orca.mojo` (that file would be the module `orca` and hide the
package):

```mojo
# bin/scheduler.mojo
from orca.command import orca_command

def main() raises:
    orca_command()
```

## A pipeline

```mojo
# pipelines/sizes.mojo
from dista import Seq
from orca import Cron, Ctx, Gather, Mapped, Month, Monthly, Pipeline, Step, orca_main


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
    orca_main[Sizes]()
```

- A step function is a top-level `def f(mut ctx: Ctx, a: A) raises -> R`.
  `Step[name, A, R, f, kind, after=..., retries=0, reads=[], writes=[]]`.
- **`Mapped`** runs once per partition, taking the partition key (`Month`,
  `Day` or `Name`) or the result of the mapped step it comes `after`.
- **`Gather`** runs once per run over every partition's result, as a
  `Seq[R]` of the step before it.
- Results are encoded with dista's codec: scalars, `String`, `List`, `Seq`,
  `RecordBatch`, and `@fieldwise_init` structs of those (`Copyable,
  Movable`).
- `Pipeline[name, *steps, tasks=, partitions=, schedule=, fills=Previous,
  catchup=True, max_runs=1, run_retries=3, sensor=False, keys_per_run=1,
  workers=2]`. `Daily["2024-01-01"]` partitions by day; `Cron[expr, tz]`
  takes any IANA zone, e.g. `Cron["30 2 * * *", "America/New_York"]`.
  `catchup=False` starts only the latest fire a scheduler missed.
- `ctx.log(msg)` writes to the attempt's log, which the UI shows.

Every pipeline binary has orca's command line:

```sh
mojo build pipelines/sizes.mojo -o build/sizes
./build/sizes describe                        # the DAG as JSON
./build/sizes run --partitions 2024-01..2024-03
./build/sizes status                          # with ORCA_DATABASE_URL
./build/sizes fires 3 --after 2024-03-09T12:00Z
```

Without `ORCA_DATABASE_URL` a run keeps its state in memory; with it, in
Postgres, and `resume <run>` continues one that stopped.

## Steps that call dista

A pipeline declares its tasks; a step gets the run's runtime, started once
before the first step, from `ctx`:

```mojo
from dista import ObjectRef, Task
from orca import Ctx, Mapped, Month, Pipeline, Step, Tasks, orca_main


def work(day: Int) -> Int:
    return day * 7


comptime Work = Task[Int, Int, work]
comptime MyTasks = Tasks[Work]


def month_total(mut ctx: Ctx, m: Month) raises -> Int:
    ref rt = ctx.runtime[MyTasks]()
    var refs = List[ObjectRef[Int]]()
    for d in range(1, 29):
        refs.append(rt.remote[Work](d))
    var t = 0
    for r in refs:
        t += rt.get(r)
    return t


comptime MonthTotal = Step["month_total", Month, Int, month_total, Mapped]
comptime Totals = Pipeline["totals", MonthTotal, tasks=MyTasks, workers=3]


def main() raises:
    orca_main[Totals]()
```

`workers=` is the default cluster size; registration can override it. Run
it locally with `ORCA_WORKERS=8 ./build/totals run --partitions 2024-01`.
See the dista skill for tasks, refs and actors.

## Datasets and Iceberg tables

`ORCA_WAREHOUSE` is the root of a filesystem catalog: a directory, or
`s3://bucket/warehouse`. Dataset `lake.events` is the table at
`<warehouse>/lake/events`. A step declares what it reads and writes, and
orca refuses anything else; the declarations also give the lineage and
the cycle check at registration.

```mojo
from orca.tables import append, ensure_table, scan, table

comptime Load = Step["load", Month, Int64, load, Mapped, writes=["lake.events"]]
comptime Report = Step["report", Int64, Int, report, Mapped, after=Load,
                       reads=["lake.events"]]
```

- `ensure_table(ctx, "lake.events", schema)` loads the table, creating it
  with an iceberg `Schema` (and optional `PartitionSpec`) if missing.
- `append(ctx, table, batches)` commits `List[RecordBatch]` as one snapshot
  and returns its ID. A retry after a crash finds the snapshot its attempt
  already made instead of writing the data twice.
- `scan(ctx, table(ctx, "lake.events"))` is a `TableScan` at a **pinned**
  snapshot: a retry or resume sees the same data. Then `.to_batches()`.

These import iceberg, so only pipelines that use tables pay for it.

## Keyed partitions and sensors

Partitions can be names read from a column of a dataset, for things that
aren't dates and grow (repositories, devices, logs):

```mojo
from orca.keyed import Keyed

comptime Repos = Pipeline[
    "repos", Fetch, Index,
    partitions=Keyed["lake.repos", "repo"],   # the keys: distinct `repo` values
    keys_per_run=50,                          # one run handles 50 keys
    sensor=True,                              # new snapshots start runs
]
```

The partition type is `Name` (`String(name)` for its text). `--partitions`
takes names, `@all`, or `@file:<path>`; a range or an unknown name is
refused. A keyed pipeline has no calendar, so `schedule=` doesn't compile
with it. With `sensor=True`, the scheduler starts runs when a dataset the
pipeline reads gets a new snapshot, for the keys that are new.

## The `orca` command

Needs `ORCA_DATABASE_URL` (a Postgres the scheduler and runs share):

```sh
./build/orca locate build/sizes --root .      # step → source line, for the UI's code links
./build/orca register ./build/sizes           # records it and its version
./build/orca backfill sizes 2023-01..2024-12  # queue runs (keyed: NAME,... | @all)
./build/orca scheduler                        # fires schedules, sensors, retries; starts runs
./build/orca ui                               # http://127.0.0.1:8270, pages and JSON
./build/orca user add alice                   # a login for the UI's actions (password on stdin)
./build/orca lineage lake.events              # what made a dataset's snapshot, and what it made
./build/orca rebuild lake.events 2024-03      # rerun everything downstream, in order
```

Build pipelines with `-D ORCA_SOURCE=$(git remote get-url origin) -D
ORCA_COMMIT=$(git rev-parse HEAD)` and each run links the exact commit it
ran on GitHub.

`register` flags, for the runners that start instances or pods:
`--image IMAGE`, `--head-type T`, `--worker-type T`, `--workers N` or
`--workers MIN..MAX` (autoscaling; MIN may be 0), `--spot`, and
`--secret NAME=orca/ID` (repeatable).

## Runners

- **local** (default): the scheduler starts each run as a process on its
  machine; dista workers are forked there.
- **ec2** (`ORCA_RUNNER=ec2`): each run gets an on-demand head instance and
  worker instances from the pipeline's image; the scheduler terminates them
  when the run ends. With `--workers MIN..MAX` the scheduler reads the
  head's demand every second, starts workers when tasks wait and drains idle
  ones (`ORCA_SCALE_UP_AFTER`, `ORCA_SCALE_IDLE`, `ORCA_SCALE_STEP` tune
  it). `ORCA_VCPU_BUDGET` caps the vCPUs of runs at once. Deploy it with
  `orca aws` (see the magmalake-apps skill).
- **kube** (`ORCA_RUNNER=kube`): each run is a dista cluster of pods.

Secrets: put the value in Secrets Manager under `orca/` and register with
`--secret GITHUB_TOKEN=orca/github-token`; the run's processes get
`GITHUB_TOKEN`, and every log orca writes masks its value. Postgres keeps
only the ID.

Logs: with `ORCA_LOGS` (a directory or `s3://...`), each attempt, the
driver, and each instance of an ec2 run keep their logs there, and the UI
shows them with the run's timeline.

## Common mistakes

- **`bin/orca.mojo`**: the file becomes the module `orca` and every
  `from orca import ...` fails. Name it `bin/scheduler.mojo`.
- **A step's types don't line up**: `after=` a step returning `R` means the
  next step takes `R` (mapped) or `Seq[R]` (gather). The error is at the
  `Pipeline[...]` line.
- **Writing a dataset not in `writes=`** (or reading one not in `reads=`):
  the step fails with a message naming it. Declare it.
- **Re-registering after a change**: rebuild the binary and `register`
  again; queued runs move to the new code, running ones finish on the old.
- **A keyed pipeline with `schedule=`** doesn't compile; use `sensor=True`
  or backfills.
- **`pixi shelf add` fails in a new project**: it needs Modular's channel,
  `preview = ["pixi-build"]` and `mojo-compiler==1.2.0` first.
