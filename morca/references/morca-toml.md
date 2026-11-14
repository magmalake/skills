# morca.toml in full

The short form of morca's `docs/morca-toml.md` (morca-mojo 0.30.0). The file
sits at the project's root, the image copies it to `/opt/morca/morca.toml`,
and `morca register` reads it. `morca config check` checks it with no
database, and refuses unknown keys and wrong values with `file:line`.

## Tables

| Table | For |
|---|---|
| `[defaults]` | values for every pipeline the file names (not for others) |
| `[pipelines.<name>]` | one pipeline, by the name `describe` gives; may be empty |
| `[pools.<name>]` | a pool of worker machines morca makes and removes |
| `[apps.<name>]` | an app beside the UI, and what the stack gives it |
| `[datasets."ns.t"]` | `public = false` hides a dataset's Data tab from visitors |

**Precedence**, highest first: a `register` flag (that registration only),
`[pipelines.<name>]`, `[defaults]`, the environment, the code's value.
`env` and `secrets` merge a name at a time. A key removed from the file
goes back to its default at the next registration.

## Pipeline keys

| Key | Values | What it does |
|---|---|---|
| `placement` | `"default"`, `"control"`, `"ec2"`, `"worker:<pool>"` | where runs run (default: the scheduler's runner) |
| `max_runs` | `N`, `"auto"`, `"auto..N"` | runs at once; `auto` fits as many as the host has room for, up to 64 or N |
| `cpus` | `N`, `"MIN..MAX"`, `"auto"`, `"auto..MAX"` | the request: start once MIN fits, grant up to MAX |
| `memory_gb` | the same, in GB, decimals allowed | the same, for memory |
| `learn` | `true`, `false` | `false` keeps `auto` requests off |
| `keys_per_run` | 1 to 10000 | keys a backfill, fill or sensor run takes (overrides the code) |
| `priority` | -1000 to 1000 | higher starts first when room is short |
| `env` | `{ NAME = "value" }` | variables for this pipeline's runs only |
| `secrets` | `{ NAME = "orca/<id>" }` | secret IDs, never values |
| `fills` | `"since_registration"`, `"all"` | `all`: fill the whole range by itself |
| `fill_order` | `"oldest"`, `"newest"` | which end fills first |
| `fill_max_runs` | `N`, `"auto"`, `"auto..N"` | fill runs at once, on top of `max_runs` |
| `fill_window` | `"A..B"`, UTC hours | fill runs start in it; `"22..6"` wraps |
| `fill_lag` | `"2h"`, `"1d"`, `0` | wait after a partition ends before filling it |
| `fill_budget_per_day` | dollars | needed for fills on EC2 or Kubernetes, which cost money |
| `enabled` | `true`, `false` | `false`: a bare `register` skips it (an overlay turns it on) |
| `workers` | `N`, `"MIN..MAX"` | workers per run on EC2 or Kubernetes; on a pool, machines a run may add |
| `worker_procs`, `head_type`, `worker_type` | | EC2 only |

`env` may not hold secret-looking names (`*TOKEN*`, `*SECRET*`,
`*PASSWORD*`, `*DATABASE_URL*`) or variables morca sets itself
(`MORCA_WORKERS`, `MORCA_GRANT_*`, `DISTA_ROLE`, ...).

## Requests, grants and "auto"

On the local runner (a Hetzner stack), on the control host and on remote
workers, a pipeline with `cpus` or `memory_gb` is **admitted**: its run
starts once its minimum fits in what the host can grant (its CPUs and
memory less `MORCA_RESERVE_CPUS`, 1, and `MORCA_RESERVE_MEMORY_MB`, 1536)
less what running runs were granted. It is then **granted** up to its
maximum of what's left, in `MORCA_GRANT_CPUS` and `MORCA_GRANT_MEMORY_MB`,
fixed for that start. Where morca makes cgroups (on by default on Hetzner)
the grant is the run's `memory.high`; past it by `MORCA_RUN_MEMORY_MARGIN`
(25%) the run alone is killed, fails `out of memory at <limit>`, and its
retry and later runs ask for 1.5 times as much, up to the maximum. A
pipeline with no request isn't admitted or counted, so on a shared host
give every pipeline one.

- **`memory_gb = "auto"`** (or `cpus`) asks for what the last 20 successful
  runs suggest: memory's 95th percentile peak plus 15%, CPUs' 90th
  percentile, at most what the host can grant. With fewer than 3 measured
  runs there is no request yet. `"auto..24"` caps it at 24.
- **`max_runs = "auto"`** starts another run whenever its request fits;
  without a request it starts one at a time and says so.
- **`env` values of `"auto"`** pass through for the run to size itself:
  `DISTA_WORKER_SLOTS = "auto"` makes dista take slots and a memory budget
  from the grant, and a pipeline's own variable reads it with
  `morca.auto_count`.

`morca config show <pipeline>` prints the learned request and whether it
is applied; a waiting run says why in `morca status`.

## Fills

With `fills = "all"` the scheduler runs the pipeline's whole range by
itself: from its first partition to the last one that ended at least
`fill_lag` ago, `keys_per_run` partitions a run, `fill_max_runs` runs at
once, inside `fill_window`. A partition done, live, or ever queued as a
fill isn't run again; a failed fill run is retried up to `run_retries`,
then left to a person. Fill runs start only while none of the pipeline's
other runs are queued, and after the pipelines of higher priority. Keyed
pipelines and sensors can't fill (`register` refuses it): backfill them.
A big re-run goes the same way with `morca backfill --rerun --as-fill`.

## Priority and keys per run

Each scheduler pass takes the pipelines highest `priority` first, then by
name, and within one priority every pipeline's new runs (scheduled,
sensed, backfilled, retries) go before any fill runs. Nothing running is
stopped. `keys_per_run` above 1 batches a run's fixed costs (claiming a
worker, a dista cluster, loading tables) over many keys; a sensor then
tops up its queued runs before making new ones.

## A stack's overlay

One project, several stacks: what differs per stack goes in a small
overlay laid on `morca.toml` (`--overlay F`, or `MORCA_CONFIG_OVERLAY`;
on Hetzner, `CONFIG_OVERLAY_FILE` in `hetzner.env`):

```toml
# beta/morca.overlay.toml
[pipelines.gharchive]
fills = "since_registration"     # no backfill from 2023 here

[pipelines.ballast]
enabled = true                   # in morca.toml with enabled = false
```

A key in the overlay replaces the file's at that path, and tables merge key
by key. An overlay can change the file's pipelines and apps, not add new
ones, and can't remove a key: set it to its default instead. `morca config
check --overlay F` and `config show --file --overlay F` check and print the
merge.

## Worker pools

```toml
[pools.lab]
provider = "hetzner"            # or "ec2"
machines = [
  { type = "cax31", wait = 600 },   # prefer this, try it for 10 minutes
  { type = "cpx32" },               # the last needs no wait
]
locations = ["hel1", "nbg1"]    # Hetzner locations, or EC2 subnet IDs
max_machines = 3
idle_seconds = 900

[pipelines.fanout]
placement = "worker:lab"
workers = 2                     # each run may spread over 2 more machines
```

The leading scheduler makes a machine while runs of the pool's pipelines
wait, removes one idle for `idle_seconds`, and drains machines of an old
image after a deploy. It needs `MORCA_HCLOUD_TOKEN` (or the AWS
credentials), `MORCA_WORKER_IMAGE` and `MORCA_UI_URL` in its environment;
morca's `docs/morca-toml.md` lists the rest.

## Apps

```toml
[apps.console]
port = 8080
check = "/healthz"              # answers 200 once ready
lake = true                     # gets MORCA_WAREHOUSE and the S3 keys
env = { CONSOLE_SEMANTIC = "1" }
secrets = { POSTHOG_KEY = "orca/posthog-key" }
memory_gb = 1
```

An app gets nothing it doesn't ask for, and never morca's database. The
stack decides which apps it runs (`APPS` in `hetzner.env`).
