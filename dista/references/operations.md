# Running dista: environment, metrics, Kubernetes, a provider loop

All of this is from dista-mojo 0.5.1's repository
([morca-dev/dista](https://github.com/morca-dev/dista), formerly
magmalake/dista.mojo; the installed tin is compiled only): `src/dista/*.mojo`, `DESIGN.md`, `deploy/`
and `tests/*.sh`.

## Environment variables

| Variable | Default | What it does |
|---|---|---|
| `DISTA_ROLE` | unset | `head` or `worker`; anything else raises |
| `DISTA_HEAD` | unset | the head's `host:port`, for a worker or a driver (and for the `dista` command) |
| `DISTA_PORT` | 6390 | the head's port; 0 lets the OS pick, and the head prints `dista head listening on port N` |
| `DISTA_CONNECT_TIMEOUT` | 60 | seconds workers and drivers keep retrying the head |
| `DISTA_ADVERTISE` | the address it reaches the head from | the address a worker tells others to fetch from; the manifests set the pod IP |
| `DISTA_NODE` | the host name | the node name used for data-aware placement |
| `DISTA_TASK_RETRIES` | 3 | how often a dead worker's task runs again elsewhere |
| `DISTA_CHECKPOINT_URI` | unset | where actor checkpoints go: a directory or `s3://`. Set it on the workers, which write them, and on the head, which restores from them after a restart and reports `checkpoints` in `status --json` |
| `DISTA_DRAIN_SECONDS` | 25 | how long a draining worker may take before it leaves anyway |
| `DISTA_STORE_DIR` | `/dev/shm` on Linux, `$TMPDIR` on macOS | the node-local shared-memory store for large results |
| `DISTA_SHM` | on | `0` turns the store off: large results move over sockets |
| `DISTA_STORE_BYTES` | no limit | store bytes per worker before results spill to disk |
| `DISTA_SPILL_DIR` | `$TMPDIR`, or `/tmp` | where spilled results go |
| `DISTA_MAX_QUEUED` | 100,000 | tasks queued at the head before `remote` blocks; 0 turns it off |
| `DISTA_MAX_QUEUED_BYTES` | 1 GiB | queued argument bytes before `remote` blocks; 0 turns it off |
| `DISTA_MAX_DRIVER_BUFFER` | 256 MiB | result bytes waiting for a driver that isn't reading before its tasks wait |
| `DISTA_METRICS_PORT` | off | serve Prometheus metrics at `/metrics` on this port (0 picks one and prints `dista metrics on port N`) |
| `DISTA_WORKER_SLOTS` | 1 | tasks a worker runs at once on threads of its own; 0 is one per core, `auto` sizes it from the run's grant (below) |
| `DISTA_TASK_MEMORY_MB` | unset | with `auto`, what one task may use; unset, dista learns it from tasks that ran alone |
| `DISTA_TOKEN`, `DISTA_TOKEN_FILE` | unset | a cluster token: the head and workers refuse connections that don't open with it |
| `DISTA_TASK_TIMEOUT` | no limit | seconds after which a running task fails |
| `DISTA_KEEPALIVE_SECONDS` | 10 | TCP keepalive: a silent peer fails after this plus 3 probes (about 25 s); 0 turns it off |
| `DISTA_PEER_CONNECT_TIMEOUT` | 5 | seconds a worker or driver waits to reach a peer it fetches from |
| `DISTA_ORPHAN_SECONDS` | 120 | results no driver comes back for are freed after this |
| `DISTA_WARN_HELD` | 100,000 | the head logs a driver holding this many objects (0: never) |
| `DISTA_DEBUG` | off | `1`: a worker logs each task's inputs as they're assembled |

**`DISTA_WORKER_SLOTS=auto`** (0.5.0 on) reads the grant morca gives a run,
`MORCA_GRANT_CPUS` and `MORCA_GRANT_MEMORY_MB`, shared among a local run's
forked workers (a worker started on its own takes the whole grant). Slots
are the granted CPUs; the memory budget admits as many tasks as fit, each
at `DISTA_TASK_MEMORY_MB` or its measured footprint, and the rest wait at
the head for a worker with room. It starts one task in flight and raises
the count while throughput rises, backing off on memory pressure (PSI),
spills or slower tasks. Without a grant (EC2, Kubernetes) it is a slot per
core and no budget. A number works as before: that many slots, no budget.
With slots above 1, **tasks must be thread-safe**, and N slots hold N
tasks' inputs and results in memory.

On Kubernetes each pod has its own `/dev/shm`, an in-memory volume whose
limit evicts the pod instead of failing a write, so the AWS manifests set
`DISTA_STORE_BYTES` to 1 GiB per worker.

## Metrics

Head: `dista_tasks_submitted_total`, `dista_tasks_finished_total{outcome=...}`,
`dista_tasks_retried_total`, `dista_task_duration_seconds` (buckets 1 ms to
60 s), `dista_queue_depth`, `dista_tasks_ready`, `dista_tasks_waiting`,
`dista_tasks_running`, `dista_queued_bytes`, `dista_admission_paused`,
`dista_admission_pauses_total`, `dista_drivers`, `dista_drivers_behind`,
`dista_workers`, `dista_workers_idle`, `dista_workers_draining`,
`dista_objects`, `dista_stored_results`, `dista_head_result_bytes`,
`dista_results_moved_total`, `dista_actors`, `dista_actors_waiting`,
`dista_actor_restarts_total`, `dista_actors_moved_total`,
`dista_checkpoints_total`.

Worker: `dista_worker_tasks_finished_total`,
`dista_worker_task_duration_seconds`, `dista_worker_queue_depth`,
`dista_worker_results`, `dista_worker_result_bytes`,
`dista_worker_store_bytes`, `dista_worker_results_spilled_total`,
`dista_worker_results_adopted_total`, `dista_worker_draining`,
`dista_worker_hosts_actor`.

## Kubernetes

`deploy/Dockerfile` builds one image for every role (about 140 MB): the
program at `/opt/dista/bin/app` and the `dista` command at
`/opt/dista/bin/dista`. `deploy/k8s/cluster.yaml` runs the head as a
StatefulSet running `dista head --port 6390` behind a headless Service
`dista-head`, and the workers as a Deployment with `DISTA_ROLE=worker`,
`DISTA_HEAD=dista-head:6390`, `DISTA_ADVERTISE` from the pod IP and
`DISTA_METRICS_PORT=9090`. A driver is a Job with
`DISTA_HEAD=dista-head:6390` (`deploy/k8s/driver-job.yaml`). The head exits
at SIGTERM; workers drain. A long-lived cluster without morca can be scaled
by a HorizontalPodAutoscaler on `dista_queue_depth`.

## A provider loop around `dista scale --once`

`dista scale --once` prints one line each: `want N`, `start N`, one
`drain ADDRESS` per worker to drain, and `short_since T` to pass back.
Flags: `--min`, `--max`, `--per`, `--up-after`, `--idle`, `--step`, and the
loop's own `--pending` and `--short-since`. The loop in
`tests/autoscale.sh`, trimmed:

```sh
since=0
while true; do
  alive=...                                    # worker processes still running
  n=$(./build/dista status --json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["workers"]))')
  pending=$((alive > n ? alive - n : 0))       # started, not yet joined
  out=$(./build/dista scale --once --min 0 --max 6 --step 4 --up-after 1 --idle 2 \
    --pending "$pending" --short-since "$since")
  since=$(awk '$1 == "short_since" {print $2}' <<< "$out")
  start=$(awk '$1 == "start" {print $2}' <<< "$out")
  for ((k = 0; k < start; k++)); do DISTA_ROLE=worker ./app & done
  for a in $(awk '$1 == "drain" {print $2}' <<< "$out"); do ./build/dista drain "$a"; done
  sleep 0.5
done
```

A drained worker exits 0 by itself; a provider that can't signal the
worker's process (an EC2 instance seen from another machine) drains it by
address and lets the instance's own script terminate it when the worker
exits. A Spot interruption is the same drain: send the worker SIGTERM.
