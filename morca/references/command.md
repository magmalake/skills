# The `morca` command

The project's own binary, built from `bin/scheduler.mojo` (two lines that
call `morca_command()`). The commands that read or change registrations
and runs need `MORCA_DATABASE_URL`, a Postgres URL; `config check`,
`config show --file` and `config apps` need none. With `--remote <url>` (or `MORCA_REMOTE`) the commands that
change runs go to a deployed `morca ui` instead, as the user `morca login`
logged in. Exit codes: 0, 2 for a usage error, 3 for any other error. This
is morca-mojo 0.30.0's `morca` with no arguments, grouped.

On a Hetzner stack, run it inside the scheduler's container:
`ssh root@<ip> docker exec scheduler morca status`.

## Registering

```text
morca register [<pipeline binary> ...]
               [--config <morca.toml>|--no-config] [--overlay <file>]
               [--placement control|ec2|worker:<pool>|default]
               [--max-runs N|auto] [--cpus N|MIN..MAX] [--memory-gb N|MIN..MAX]
               [--workers N|MIN..MAX] [--worker-procs N|auto]
               [--image I] [--head-type T] [--worker-type T] [--spot]
               [--secret NAME=orca/ID ...]
morca retire <pipeline>
morca locate <pipeline binary> ... [--root <dir>]
```

- With no binaries, `register` registers every `[pipelines.<name>]` of the
  file that isn't `enabled = false`, each as `<dir>/<name>`, where `<dir>`
  is `MORCA_PIPELINES_DIR` or `pipelines/` next to the file. A project
  image sets `MORCA_CONFIG` and `MORCA_PIPELINES_DIR`, so a bare `morca
  register` registers the whole file.
- The file is `--config`, else `MORCA_CONFIG`, else `./morca.toml`. A flag
  overrides the file for that registration only.
- Registering new code moves queued runs to it; running ones finish on the
  old code, or end early if they check `outdated()`.
- **`retire`** takes a pipeline out of what the scheduler runs and what
  `status` lists; its runs and tables stay. `register` brings it back. Use
  it for a pipeline the image no longer has (the deploy check says so).
- `locate` writes `<binary>.source.json`, step to source line, for the UI's
  code links.

## Queueing and changing runs

```text
morca backfill [--rerun [--allow-append]] [--preview|--as-fill] <pipeline> KEYS
morca backfill --cancel <pipeline>
morca start <pipeline> KEYS                  one run over them all, trigger `cli`
morca cancel <pipeline> KEYS|@all            its queued, deferred and running runs, and failed ones due a retry
morca cancel <run>
morca resume [--allow-new-code] <run>
morca rerun [--allow-append] <run> <step>    a new run, from that step on
morca pause|unpause <pipeline>
morca rebuild <dataset> KEYS                 everything downstream of it, in order
```

`KEYS` is `KEY[..KEY][,...]` (`2024-01..2024-06`, `2024-01-01`,
`2026-11-11T05`), or for a keyed pipeline `NAME[,...]`, `@all` or
`@file:<path>`.

- **`--rerun`** queues keys that already succeeded too. It's refused for a
  step that appends, since its rows would be there twice, unless
  `--allow-append`; a step whose dataset is in `replaces=` is fine.
- **`--preview`** prints what it would queue and queues nothing.
- **`--as-fill`** records a *fill request* instead of a run per key: the
  scheduler runs its keys as fill runs, `keys_per_run` at a time, within
  `fill_max_runs`, `fill_window` and `fill_budget_per_day`, after the
  pipeline's other runs and after higher priorities, and goes on across
  restarts and deploys. A pipeline has one open request; another adds keys
  to it. `morca status <pipeline>` shows its progress (`fill request 4
  (2023-01-01..2026-11-13, rerun): 62 of 1048 done, ...`).
  `--as-fill` and `--cancel` need the database, not `--remote`.
- **`--cancel`** ends the pipeline's fill request and cancels its runs.

## Looking

```text
morca status [<pipeline>] [--json]     state, latest runs, why runs wait, fill progress
morca config show [<pipeline>] [--json]          each setting and where it came from
morca config show --file [<morca.toml>] [--overlay F]   the file as register reads it
morca config check [<morca.toml>] [--overlay F] [--secrets]   no database, for CI
morca config export [--out <path>|-] [--force]   a morca.toml from what's registered
morca config apps [<morca.toml>] [--overlay F]   the [apps.*] tables, a line per fact
morca lineage <dataset> [<snapshot>]   what made a snapshot, and what it made
morca alerts [--kind K] [--open|--resolved] [--json]
morca watchdog status | snooze <rule> <duration> | ack [--all|<trip id>]
```

`status` says why a queued run waits, such as `waiting: needs 2 GB, 1.3 GB
free of grants`, or `more than the 6.5 GB this host grants at most`; a run
that can never start trips the watchdog's `unmet` rule, which pauses the
pipeline. `config check` refuses what `register` would, with
`file:line: [table] key: why`, and exits 1.

## Running the stack

```text
morca scheduler                 schedules, sensors, fills, retries, the watchdog
morca scheduler status          its instances: role, runs, last seen
morca ui                        http://127.0.0.1:8270 (MORCA_UI_ADDR)
morca worker --pool <pool> --remote <url> [--max-runs N] [--advertise <addr>]
morca worker-token create --pool <pool> [--label L] | list | revoke <id>
morca user add|remove <name>    add reads the password from stdin
morca login [<url>] / morca logout
morca expire <dataset>|--all [--older-than 30d] [--keep N] [--dry-run]
morca notify test               a message to MORCA_NOTIFY_URL (ntfy)
morca upgrade --from V --to V   a release's upgrade steps, for deploy.sh
```

The UI's actions are form `POST`s refused unless their `Origin` is the
UI's own, and once a user exists they need the session cookie from `POST
/login`; `morca --remote` is the scripted way in. Its JSON is under
`/api/...`, such as `/api/pipelines/<name>`.
