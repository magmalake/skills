# Steps and pipelines in full

Everything `Step` and `Pipeline` take, as `src/morca/step.mojo` and
`src/morca/pipeline.mojo` declare them in morca-mojo 0.30.0, and what a step
can ask of morca while it runs.

## `Step`

```mojo
Step[
    name, A, R, f, kind,          # kind is Mapped or Gather
    after=NoStep,                 # the step whose output this one takes
    retries=0,                    # tries again in the same run
    backoff_seconds=0,            # wait before a retry, doubling each time
    timeout_seconds=0,            # an attempt still going after this fails
    reads=[], writes=[],          # datasets, as "namespace.table"
    replaces=[],                  # written, and a new run replaces its partitions
    version=0,                    # raise it to make the step's outputs again
]
```

What a step's input must be:

| Step | Its input |
|---|---|
| first, mapped | the partition key: `Month`, `Day`, `Hour` or `Name` |
| first, gather | `Seq` of the key: every partition of the run |
| mapped, after a mapped step | that step's output |
| gather, after a mapped step | `Seq` of that step's output, in run order |
| gather, after a gather step | that step's output |

A mapped step can't follow a gather step. Steps are listed in the
`Pipeline` after the step they follow.

- **`retries`** retry the step within its run. A step that fails past them
  fails its partition: the steps after it for that partition, and any
  gather over it, are skipped, and other partitions carry on.
- **`timeout_seconds`** stops an attempt by ending the run's process; the
  run's own retry (`run_retries`) takes it from there.
- **`replaces=`** makes `append` and `append_files` to that dataset replace
  the partitions the run writes into instead of adding to them, so
  `morca backfill --rerun` leaves one copy of a key's rows. It counts as
  written. Use it when a step recomputes whole partitions; keep `writes=`
  when a key's rows land in a partition other keys also write (an hourly
  key into a daily partition), or a rerun would remove its siblings' rows.
- **`version=`**: raising it marks every partition the step made as to be
  made again; a pipeline with `fills = "all"` then fills them again.

## `Pipeline`

```mojo
Pipeline[
    name, *steps,
    tasks=NoTasks,                # Tasks[...] of the dista tasks its steps call
    partitions=Monthly[],         # Monthly["2024-01"], Daily[...], Hourly[...], Keyed[ds, col]
    schedule=NoSchedule,          # Cron["0 3 * * *"] or Cron[expr, "Europe/Berlin"]
    fills=Previous,               # a fire runs the partition before it; Current: the one it's in
    catchup=True,                 # False: after downtime, only the latest missed fire
    max_runs=1,                   # runs at once (morca.toml overrides, "auto" there)
    run_retries=3,                # the scheduler starts a failed run again
    retry_seconds=0,              # its backoff, doubling (0: MORCA_RETRY_SECONDS)
    max_retry_seconds=0,          # the backoff's ceiling
    sensor=False,                 # new snapshots of what it reads start runs
    keys_per_run=1,               # keys per backfill, fill or sensor run
    workers=2,                    # workers per run on EC2 or Kubernetes (morca.toml overrides)
]
```

`run_retries=72, retry_seconds=3600, max_retry_seconds=3600` tries hourly
for three days. A run over many keys fails as a whole when one key fails,
and its retry skips the steps already done.

## What a step can ask

- **`ctx.log(msg)`** writes to the attempt's log, which the UI shows.
  `ctx.pipeline`, `ctx.run`, `ctx.step`, `ctx.partition` and `ctx.attempt`
  say where it is.
- **"Not yet":** `raise defer(seconds, why)` ends the attempt `deferred`,
  spending no retry, and the scheduler starts the run again that many
  seconds later. For a download that isn't published yet, or an API out of
  requests.
- **`outdated()`** is true once a newer version of the pipeline leads (a
  deploy registered other code, or its scheduler drains). Morca does
  nothing about it; a long step checks it between units of work and ends
  early, so a deploy doesn't wait for it. `outdated_why()` says why.
- **`auto_count(name, default, memory_mb_each=0)`** reads a count from the
  variable `name`: a number as given, `default` if unset, and for `"auto"`
  the run's granted CPUs, at most its granted memory over
  `memory_mb_each`. Pair it with `env = { NAME = "auto" }` in morca.toml.

```mojo compile
from std.os import getenv

from morca import (
    Ctx,
    Daily,
    Day,
    Mapped,
    Pipeline,
    Step,
    auto_count,
    defer,
    morca_main,
    outdated,
)


def fetch(mut ctx: Ctx, d: Day) raises -> Int:
    if not getenv("SOURCE_READY"):
        raise defer(1800, "the day's file isn't published yet")
    var parallel = auto_count("FETCH_PARALLEL", 4, memory_mb_each=1500)
    var done = 0
    for _hour in range(24):
        if outdated():          # a newer version leads: stop between hours
            ctx.log(String("stopping after ", done, " hours"))
            break
        done += 1
    ctx.log(String(done, " hours, ", parallel, " at a time"))
    return done


comptime Fetch = Step[
    "fetch", Day, Int, fetch, Mapped, retries=3, backoff_seconds=30
]
comptime Days = Pipeline[
    "days",
    Fetch,
    partitions=Daily["2024-01-01"],
    run_retries=72,
    retry_seconds=3600,
    max_retry_seconds=3600,
]


def main() raises:
    morca_main[Days]()
```

## A keyed run's keys in parallel

A mapped step takes its keys one at a time. To work on a run's keys at
once, make the first step a `Gather` over `Seq[Name]` and send each key to
a dista task:

```mojo compile
from dista import ObjectRef, Seq, Task
from morca import Ctx, Gather, Name, Pipeline, Step, Tasks, morca_main
from morca.keyed import Keyed


@fieldwise_init
struct History(Copyable, Movable):
    var repo: String
    var commits: Int


def read_history(repo: String) raises -> History:
    return History(repo, repo.byte_length())  # stand-in for the real work


comptime ReadHistory = Task[String, History, read_history]
comptime HistoryTasks = Tasks[ReadHistory]


def histories(mut ctx: Ctx, keys: Seq[Name]) raises -> Int:
    ref rt = ctx.runtime[HistoryTasks]()
    var refs = List[ObjectRef[History]]()
    for i in range(len(keys)):
        refs.append(rt.remote[ReadHistory](keys[i].text))
    var commits = 0
    for r in refs:
        commits += rt.take(r).commits   # one key's error raises here
    ctx.log(String(len(refs), " repositories, ", commits, " commits"))
    return commits


comptime Histories = Step["histories", Seq[Name], Int, histories, Gather]
comptime RepoHistory = Pipeline[
    "history",
    Histories,
    tasks=HistoryTasks,
    partitions=Keyed["lake.repos", "repo"],
    keys_per_run=50,
    sensor=True,
]


def main() raises:
    morca_main[RepoHistory]()
```

One key's failure is then the step's to log or raise: morca sees one step.

## The pipeline binary's commands

```text
<pipeline> describe                                   the DAG, datasets and settings as JSON
<pipeline> run --partitions KEY[..KEY][,...]          2024-01, 2024-01-01 or 2024-01-01T05
<pipeline> run --partitions NAME[,...]|@all|@file:<path>   (keyed)
<pipeline> resume <run> [--allow-new-code]
<pipeline> status [<run> | --before <run>] [--status <s>,...]
<pipeline> keys                                       a keyed pipeline's keys now
<pipeline> sense                                      what a sensor would start
<pipeline> fires [<count>] [--after YYYY-MM-DDTHH:MM[:SS]Z]
```

`run` against a database refuses code that isn't registered, unless given
`--allow-new-code`. Build with `-D MORCA_SOURCE=$(git remote get-url
origin) -D MORCA_COMMIT=$(git rev-parse HEAD)`, or set the two in the
environment (the project images do), and each run links its commit.
