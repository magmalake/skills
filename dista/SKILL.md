---
name: dista
description: Write and run distributed Mojo programs with dista, magmalake's Ray-style task framework, where one compiled binary is the driver, the head and every worker. Use when writing or reviewing Mojo code that imports dista or mentions distributed Mojo, a remote task, `Task`/`Task2`/`TaskMany`/`TaskWith`, `ObjectRef`, `rt.remote`, `rt.get`, `Seq`, an actor with methods or checkpoints, a cluster, a head, workers, `DISTA_ROLE`/`DISTA_HEAD`, the `dista` command (status, drain, scale, timeline, stop), draining, autoscaling, or a dista runtime inside an orca pipeline step; and when a dista job hangs, is refused with a registry mismatch, or fails with a lost result. Pairs with Modular's mojo-syntax skill and magmalake's mojo-data-performance skill.
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

dista (the `dista-mojo` tin, 0.2.0, Mojo 1.2.0) lets you mark a Mojo
function remote, call it, and have it run on other processes or Kubernetes
pods. Every process runs **the same compiled binary**, so a task message is
a function ID plus encoded arguments: no code is shipped, nothing is pickled,
and columnar data travels as Arrow.

What dista is **not**: a cluster manager. It creates no pods or machines.
It uses every worker that joins, drains the ones it is told to stop, and
says how many it wants. Kubernetes, a shell loop or orca starts and stops
them. It also doesn't rebuild results lost with a dead worker (no lineage
reconstruction): the job fails, naming the result, and an orchestrator runs
it again.

The installed environment holds dista only as a compiled `dista.mojoc`.
Read its source on GitHub,
[magmalake/dista.mojo](https://github.com/magmalake/dista.mojo), at the
`rev` your `pixi.toml` pins, or in pixi's git cache
(`ls -d ~/Library/Caches/rattler/cache/git-v0/checkouts/*/<rev's first 7>*`
on macOS, `~/.cache/rattler/...` on Linux). File paths cited here are in
that checkout.

```sh
pixi shelf add dista-mojo    # brings arrow-mlake, memory-region and objectstore
```

Write the Mojo itself with the `mojo-syntax` skill loaded; for fast per-row
work inside tasks, see `mojo-data-performance`.

## Declare tasks, then call them

A task is a **top-level `def`** declared once with its argument and result
types, because Mojo 1.2 can't infer a function's result type. The registry
is the `Runtime`'s parameter list. Every complete sample in this file was
built and run against dista 0.2.0.

```mojo
from dista import ObjectRef, Runtime, Seq, Task, Task2

def square(x: Int) -> Int:
    return x * x

def total(xs: Seq[Int]) -> Int:
    var t = 0
    for i in range(len(xs)):
        t += xs[i]
    return t

def scale(x: Int, factor: Float64) -> Float64:
    return Float64(x) * factor

comptime Square = Task[Int, Int, square]
comptime Total = Task[Seq[Int], Int, total]
comptime Scale = Task2[Int, Float64, Float64, scale]

def main() raises:
    var rt = Runtime[Square, Total, Scale](workers=4)
    var r = rt.remote[Square](7)              # an ObjectRef[Int], at once
    print(rt.get(r))                          # 49: waits for the result
    print(rt.get(rt.remote[Scale](5, 2.5)))   # 12.5
    var maps = List[ObjectRef[Int]]()
    for i in range(1000):
        maps.append(rt.remote[Square](i))
    print(rt.get(rt.remote[Total](maps)))     # 332833500
    print(rt.get(rt.remote[Square](r)))       # 2401: a ref as the argument
    rt.release(maps)
```

- **Arity:** `Task[A, R, f]` takes one argument, `Task2[A, B, R, f]` two,
  `Task3[A, B, C, R, f]` three. Pass more as one struct. Calling a `Task2`
  with one argument raises `takes 2 arguments` at the call.
- **The function may raise or not.** A task that raises re-raises its
  message in `rt.get`, and the worker carries on.
- **Refs feed tasks without the driver fetching anything.** Pass an
  `ObjectRef[A]` where the task takes an `A`, or a `List[ObjectRef[X]]`
  where it takes a `Seq[X]` (gathered in order). The task runs once every
  input exists, and fails with the input's error if one failed.
- **`rt.release(ref)` (or a list) frees a result** at the head and on its
  worker once no pending task needs it; `get` on it raises afterwards.
  Results are otherwise kept until the runtime ends.
- Also on `Runtime`: `rt.ready(r)` (finished yet, without waiting),
  `rt.location(r)` (the holding worker's `ip:port` and node-store file, or
  `""` if the result came back inline), `rt.stats()`, `rt.bytes_fetched()`
  and `rt.timeline(path)`, which writes a Chrome trace for Perfetto.

## Shuffles: several results, or a value beside refs

A `TaskMany[A, X, f]`, where `f` returns a `Seq[X]`, is a map task:
`rt.remote_many[T](arg, n)` returns `n` refs, one per partition, each
stored, fetched and freed on its own; `f` must return exactly `n` items. A
`TaskWith[A, X, R, f]`, where `f` takes an `A` and a `Seq[X]`, is a reducer:
`rt.remote[T](j, refs)`. A sample that compiles, and the details, are in
[references/shuffles.md](references/shuffles.md).

## What can be an argument or a result

The codec picks an encoding at compile time:

- `Int`, `IntN`, `UIntN`, `Float32`, `Float64`, `Bool` and `String`;
- `Seq[T]`, a list that goes through the codec (`List` does **not**:
  reflection can't recover its element type), also as a struct field;
- any plain struct of the above, field by field by reflection (fields must
  be `Movable`; argument and result types need `Movable & Deinitable`);
- a type implementing the `Codec` trait (its own `encode` and `decode`);
- arrow-mlake's `RecordBatch`, sent as an Arrow IPC stream. Use it for
  anything large; one batch per Parquet row group is the intended grain.

An `ObjectRef` **inside** a struct or a `Seq` is a compile error that names
the three shapes that work: the ref as the argument, a list of refs, or a
value beside refs with `TaskWith`. Results of 64 KiB or less come back
through the head; larger ones stay on the worker that made them and are
mapped from shared memory on the same host or fetched across hosts.

## Actors

An actor is state on a worker of its own. Its calls run one at a time, in
the order they were made, and return `ObjectRef`s like tasks do.

```mojo
from dista import Actor, ActorHandle, Client, CtxTask, Method, ObjectRef, Runtime

@fieldwise_init
struct Counter(Movable):
    var total: Int

def new_counter(start: Int) -> Counter:
    return Counter(start)

def add(mut c: Counter, n: Int) -> Int:
    c.total += n
    return c.total

def bump(mut c: Client, h: ActorHandle[CounterActor]) raises -> Int:
    var r = c.call[Add](h, 1)    # a task calling the actor it was handed
    var n = c.get(r)
    c.release(r)
    return n

comptime CounterActor = Actor[Counter, Int, new_counter]
comptime Add = Method[CounterActor, Int, Int, add]
comptime Bump = CtxTask[ActorHandle[CounterActor], Int, bump]

def main() raises:
    var rt = Runtime[CounterActor, Add, Bump](workers=3)
    var c = rt.create[CounterActor](0, checkpoint_every=100)
    var refs = List[ObjectRef[Int]]()
    for _ in range(10):
        refs.append(rt.call[Add](c, 5))
    print(rt.get(refs[9]))           # 50
    print(rt.get(rt.remote[Bump](c)))  # 51
    rt.kill(c)
```

- **One actor takes one whole worker,** and tasks never run on it. An actor
  created when every worker hosts one waits for a worker to join, so give
  local mode more workers than actors.
- **Handles are values:** pass an `ActorHandle` to a `CtxTask`
  (`def f(mut c: Client, a: A) raises -> R`) or keep it in another actor's
  state, and call through the `Client`. A `CtxMethod`
  (`def f(mut s: S, mut c: Client, a: A) raises -> R`) lets an actor call
  others; a cycle of actor calls deadlocks.
- **Checkpoints:** `checkpoint_every=N` on `create`, or `rt.checkpoint(c)`,
  whose `ObjectRef[Int]` is the number of calls covered. They go through
  the codec to `DISTA_CHECKPOINT_URI`, a directory or `s3://`; without it
  `rt.checkpoint` fails. A state type implementing `Codec` chooses what to
  save. When an actor's worker dies, it comes back on another worker from
  its last checkpoint (or its constructor) and **the calls since are run
  again**, so methods should be safe to repeat.

## Local mode and cluster mode

`Runtime(...)` reads the environment to choose the process's role:

| Environment | Role |
|---|---|
| neither variable | local mode: forks a head and `workers` workers, then drives them |
| `DISTA_ROLE=head` | runs the head on `DISTA_PORT` (default 6390; 0 picks one and prints it), never returns |
| `DISTA_ROLE=worker`, `DISTA_HEAD=host:port` | serves that head, never returns |
| `DISTA_HEAD=host:port` | a driver submitting to that head |

So **create the `Runtime` first in `main`**: in a head or worker the
constructor never returns, and anything before it runs in every role. In
local mode the workers are forked inside the constructor and inherit the
environment set before it. To run a cluster by hand, as `tests/cluster.sh`
does:

```sh
DISTA_ROLE=head DISTA_PORT=6390 ./app               # the head
DISTA_ROLE=worker DISTA_HEAD=headhost:6390 ./app    # once per worker
DISTA_HEAD=headhost:6390 ./app                      # the driver
```

Workers and drivers retry reaching the head for `DISTA_CONNECT_TIMEOUT`
seconds (default 60), so start order doesn't matter. A head started with
`dista head` instead is generic: it serves several programs at once and
gives each driver's tasks only to workers built from the same binary.

## The `dista` command

Built from the dista repository with `pixi run cli` (to `build/dista`). Each
command finds its head by `--head`, else `DISTA_HEAD`, else
`localhost:6390`.

| Command | What it does |
|---|---|
| `dista head [--port 6390] [--pipeline 2]` | a generic head for any program |
| `dista status` | workers with their registry hash, drivers, actors |
| `dista status --json` | the demand, on one line, for an autoscaler |
| `dista drain <ip:port>` | drains that worker, as SIGTERM does; refuses the last one while work is left |
| `dista scale --once` | prints the policy's decision for the current demand |
| `dista timeline -o trace.json` | every driver's finished tasks, as a Chrome trace |
| `dista stop` | shuts the head and its workers down |

With `DISTA_METRICS_PORT` set, the head and every worker serve Prometheus
metrics at `/metrics`. Every variable, metric name and the Kubernetes
manifests are in [references/operations.md](references/operations.md).

## Draining and autoscaling

A worker sent SIGTERM (how Kubernetes stops a pod) **drains** instead of
dying: it takes no new work, finishes what it has, its results move to
other workers and its actor moves through a checkpoint, then it exits 0.
Nothing fails or runs twice (`tests/drain.sh`). `DISTA_DRAIN_SECONDS`
(default 25) bounds it; a second SIGTERM ends it at once.

`dista status --json` gives `tasks_ready`, `tasks_waiting`,
`tasks_running`, `actors_waiting`, `pipeline`, `checkpoints`, and per
worker `address`, `node`, `running`, `idle_seconds`, `held_bytes`, `actor`
and `draining`. `dista.scale` (import it from `dista.scale`; it is not
re-exported from `dista`) turns that into a decision, with no clock or I/O
of its own:

```mojo
from dista.scale import Demand, Policy, decide

def main() raises:
    var d = Demand.parse('{"tasks_ready":100,"pipeline":2,"workers":[]}')
    var got = decide(d, Policy(min_workers=0, max_workers=6), now=10)
    print(got.want, got.start, len(got.drain))  # 6 4 0
```

`Policy` fields and defaults: `min_workers=1` (0 is allowed: the last
worker drains once no task is left and it holds nothing),
`max_workers=4`, `tasks_per_worker=0` (the head's pipeline depth),
`up_after=5` seconds of ready tasks with no free slot, `idle_before_down=60`
seconds, and `max_step=4` workers started per decision. `decide(demand,
policy, now, pending=0, short_since=0)` returns `start`, `drain` (addresses,
fewest held bytes first), `want` and `short_since`, which the provider
passes back next time with `pending`, the workers started but not yet
joined. `dista scale --once --min 0 --max 6 ...` prints it as lines;
`tests/autoscale.sh` drives a job from 0 workers to 6 and back to 0 with it.

## Under orca

An orca pipeline declares its tasks and workers, and orca starts the
runtime once per run. A step never builds its own `Runtime`; it borrows
the run's (an excerpt):

```mojo
comptime Trips = Task[Day, Int, trips]
comptime FanoutTasks = Tasks[Trips]                   # from orca

def month_trips(mut ctx: Ctx, m: Month) raises -> Int:
    ref rt = ctx.runtime[FanoutTasks]()
    var r = rt.remote[Trips](Day(m.copy(), 1))   # then rt.get, as usual
    ...

comptime Fanout = Pipeline["fanout", MonthTrips, AllTrips, tasks=FanoutTasks, workers=3]
```

The full program is `examples/fanout.mojo` in
[orca.mojo](https://github.com/magmalake/orca.mojo).

orca runs a run's steps in order and a mapped step's partitions one after
another, so a step that calls one task and waits for it is serial. Send
all of a step's work with `rt.remote` first, then `rt.get` each ref.

## Common mistakes

- **`registry mismatch: this peer was built with different tasks`.** The
  driver and workers came from different builds, or list different tasks
  in `Runtime[...]`. The registry hash covers the task names in order, so
  rebuild once and run that one binary in every role.
- **`... is not registered with this runtime`.** Every task, actor and
  method you call must be in `Runtime[...]`. Calls through a `CtxTask`'s
  `Client` aren't checked at the call, so an unregistered one fails on the
  worker instead, with `unknown task ID` or `unknown method ID`.
- **A nested function or closure as a task.** Use a top-level `def`.
- **Assuming a task runs once.** A dead worker's tasks run again, up to
  `DISTA_TASK_RETRIES` (default 3): keep tasks and methods safe to repeat.
- **Expecting a lost result to come back.** A result held only by a dead
  worker fails with `result <id> lost: its worker went away`, as does every
  task waiting on it. Rerun the job, or let orca retry the step.
- **Unreleased refs** keep small results in the head's memory. And
  `remote` blocking past `DISTA_MAX_QUEUED` tasks (default 100,000) is
  backpressure, not a hang.
