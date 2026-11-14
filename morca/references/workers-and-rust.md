# Remote workers, pools and Rust steps

From morca's `docs/workers.md` and `docs/rust-workers.md` (morca-mojo
0.30.0), shortened.

## Remote workers

A remote worker runs a pool's runs on another machine (a second server, a
laptop) and talks only to `morca ui`'s API over HTTPS: it holds no
database credentials, and the scheduler stays the only one deciding.

```toml
[pipelines.mojo_releases]
placement = "worker:bgent"      # pool names: 1 to 32 of a-z 0-9 - _
```

The scheduler never starts these runs; they stay queued, "waiting for a
worker of pool bgent", until a worker claims one. `max_runs`, `pause` and
priority apply as usual, and a worker admits what it claims against its
own host's room. Start a worker with a token of its own:

```sh
# on the server, once per machine
docker exec scheduler morca worker-token create --pool bgent --label mac

# on the machine, with MORCA_WORKER_TOKEN (or --token-file) in worker.env
docker run -d --name orca-worker --restart always --env-file worker.env \
    ghcr.io/<owner>/<image>:<tag> worker --pool bgent --max-runs 1 \
    --name mac --remote https://morca.example.com
```

It runs `<binary> resume <run>` from the same image, and reads and writes
the warehouse and logs directly. When a worker goes silent its runs are
queued again for the pool, within `run_retries`.

- **Fills on a pool:** `fills = "all"` works; `fill_max_runs` caps the live
  fill runs across the whole pool, each worker's `--max-runs` what one
  machine takes. A pool's fills need no budget.
- **One run over several machines:** `workers = N` on a pool pipeline lets
  a run's dista cluster add up to N machines of the pool. Each worker must
  advertise an address the others reach (`--advertise`, set for you on a
  pool morca grows), and the cluster is guarded by a token
  (`DISTA_TOKEN`), which authenticates but doesn't encrypt: keep the
  machines on a private network.
- **Pools morca grows:** a `[pools.<name>]` table
  ([morca-toml.md](morca-toml.md#worker-pools)) makes and removes Hetzner
  or EC2 machines as runs wait. Each boots with a token of its own, asks
  the UI for its image, and runs `morca worker` in Docker; a deploy drains
  machines of the old image and new ones replace them. Ops > Pools in the
  UI shows them.

## Steps in Rust

A step can run a Rust worker library in the run's process. A small Mojo
**proxy pipeline** declares the pipeline, its partitions and the dataset
the step writes, as for any step, and calls `rust_step`:

```mojo compile
from morca import Ctx, Mapped, Month, Monthly, Pipeline, Step, morca_main
from morca.rust_step import rust_step
from morca.tables import Schema, ensure_table

comptime NUMBERS = "demo.numbers"
comptime SCHEMA = (
    '{"type":"struct","schema-id":0,"fields":['
    '{"id":1,"name":"id","required":true,"type":"long"}]}'
)


def numbers(mut ctx: Ctx, m: Month) raises -> Int64:
    var t = ensure_table(ctx, NUMBERS, Schema.parse(SCHEMA))
    var r = rust_step(ctx, "numbers_worker", t, params='{"rows": 100}')
    ctx.log(String(r.rows, " rows, ", r.files, " files, stopped=", r.stopped))
    return r.snapshot_id


comptime Numbers = Step["numbers", Month, Int64, numbers, Mapped, writes=[NUMBERS]]
comptime NumbersPipeline = Pipeline[
    "numbers", Numbers, partitions=Monthly["2024-01"]
]


def main() raises:
    morca_main[NumbersPipeline]()
```

`rust_step(ctx, library, table, params="{}", step_version=0,
stop_when=outdated, flush_bytes=64 MiB)`:

1. If an earlier attempt of the step committed in this run, it returns that
   commit and the worker doesn't run (`RustRun.resumed`).
2. It loads the library then, never at program start: `library` is a path,
   a file name, or a bare name `x`, found as `libx.so` (`libx.dylib` on
   macOS) in `$CONDA_PREFIX/lib`, beside the binary or in its `../lib`.
3. It checks the worker's stream schema against the table's (columns by
   name at any depth; types the table's or ones Iceberg widens), writes
   Parquet as batches arrive, takes any Parquet files the worker wrote
   itself, and **commits once**, with `append_files` (or a replace, for a
   dataset in `replaces=`).
4. While the worker runs, it polls `stop_when()` (by default
   `morca.outdated()`) and sets the worker's stop flag; what came is
   committed and `RustRun.stopped` says so.

`RustRun` has `snapshot_id`, `rows`, `files`, `worker_files`, `stopped` and
`resumed`. `morca.rust_step` compiles iceberg into the program, unlike
`morca.tables`.

**The Rust side** uses the `morca-worker` crate from the morca repository,
at the commit the pipeline's morca pins (the ABI version is checked when
the step runs):

```toml
# Cargo.toml
[lib]
crate-type = ["cdylib", "rlib"]

[dependencies]
morca-worker = { git = "https://github.com/morca-dev/morca", rev = "<morca's commit>" }
```

```rust
use std::sync::Arc;
use morca_worker::arrow_array::{Int64Array, RecordBatch};
use morca_worker::{export_worker, schema, Input, Output, Result, RunContext, Worker};

const SCHEMA: &str = include_str!("../schemas/numbers.json");

struct Numbers;

impl Worker for Numbers {
    const NAME: &'static str = "numbers";
    const VERSION: &'static str = env!("CARGO_PKG_VERSION");

    fn run(ctx: &RunContext, _input: Option<Input>) -> Result<Output> {
        let schema = Arc::new(schema::to_arrow(SCHEMA)?);
        let (s, c) = (schema.clone(), ctx.clone());
        let batches = (0..10).map_while(move |i| {
            if c.stopping() { return None; }
            let ids = Int64Array::from_iter_values(i * 100..(i + 1) * 100);
            Some(RecordBatch::try_new(s.clone(), vec![Arc::new(ids)]).map_err(Into::into))
        });
        Ok(Output::from_iter(schema, batches))
    }
}

export_worker!(Numbers);
```

- `RunContext` gives the pipeline, step, partition, run, attempt,
  `params()`, `param(name)` and `secret(name)`; `log`, `progress` and
  `metric` write to the step's log, `stopping()` is the stop flag. A
  worker writing Parquet itself writes into `ctx.output_dir()` and reports
  each file with `ctx.add_file(path)` (`Output::from_files`).
- Errors and caught panics fail the attempt; never build with
  `panic = "abort"`. Threads are the worker's own, sized to the run's
  grant; stop and join them before the stream ends.
- **One schema file, read by both sides:** the Mojo side embeds it (as a
  string, or with morca's `scripts/embed-json.sh`) and the Rust side with
  `include_str!`; `morca_worker::schema::check` in a `cargo test` catches
  drift first. `morca_worker::testing::Run` runs a worker through the C ABI
  in a test.
- **Shipping:** the runtime image doesn't carry Rust. Make the worker a
  pixi package that installs `lib/lib<name>.so` into the environment, or
  copy the built library into the image's `lib/`.
- **Not yet:** Rust steps as dista tasks, a step's input as the worker's
  input stream, and several outputs.

`examples/rust-worker` is a complete one and `examples/hindcast` a real
one (NOAA GFS forecasts decoded in Rust, scored by Mojo steps).
