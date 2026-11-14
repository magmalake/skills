# Running the project locally

Everything runs on your machine first: the pipelines as plain processes, a
warehouse in a directory, morca's scheduler and UI over a local Postgres, and
the apps on a port of their own. Work inside `pixi shell`, or prefix each
command with `pixi run`.

## Build

Build the way the image does, every program with `-I src`. pixi's task
shell has no `for` (a task with one fails with `Unsupported reserved
word`), so put the loops in `scripts/build.sh`:

```sh
#!/bin/sh
set -e
mkdir -p build
mojo build bin/scheduler.mojo -o build/morca
for p in pipelines/*.mojo; do mojo build -I src "$p" -o "build/$(basename "$p" .mojo)"; done
for a in bin/*.mojo; do n=$(basename "$a" .mojo); [ "$n" = scheduler ] || mojo build -I src "$a" -o "build/$n"; done
```

and run it from a task, `pixi run build`:

```toml
[tasks]
build = "sh scripts/build.sh"
```

Or give each program a task of its own and a `build` task with
`depends-on = [...]` listing them.

## A pipeline on its own

A pipeline binary runs without a database or a scheduler:

```sh
export MORCA_WAREHOUSE=$PWD/build/warehouse
./build/days describe
./build/days run --partitions 2024-01-01..2024-01-03
```

`build/warehouse/demo/days/` then holds the table. Without
`MORCA_DATABASE_URL` the run isn't recorded; with it, `./build/days status`
lists runs and `./build/days resume <run>` finishes one that stopped.

## The scheduler and the UI

They need Postgres. `pixi add postgresql` gives `initdb`, `pg_ctl` and
`createdb` from conda-forge. morca's `scripts/with-postgres.sh` (copy it from
[morca's repository](https://github.com/morca-dev/morca); the tin doesn't
ship it) starts a throwaway server on a
Unix socket, exports `MORCA_DATABASE_URL`, runs a command and removes the
server when it ends:

```sh
scripts/with-postgres.sh sh -c '
  ./build/morca locate build/days --root . &&
  ./build/morca register ./build/days &&
  ./build/morca backfill days 2024-01-04..2024-01-05 &&
  (./build/morca ui & ./build/morca scheduler)'
```

- `morca locate` records where each step's code is, for the UI's links.
- `morca register` takes the binary's path; the pipeline's name comes from
  its `Pipeline["days", ...]`.
- `morca scheduler` fires schedules, backfills and sensors until stopped;
  `morca tick` does one round and returns, which suits scripts.
- `morca ui` serves on `http://127.0.0.1:8270` (`MORCA_UI_ADDR` to change).
  Every page is JSON too, with `Accept: application/json`.
- `./build/morca user add alice` (the password from stdin) makes a login;
  once any user exists, the UI's actions need one.

For a database that survives between sessions, make one with the same
tools the script uses, then export its URL:

```sh
initdb -D build/pg --auth=trust --username=postgres -E UTF8 --locale=C
pg_ctl -D build/pg -l build/pg.log -o "-k $PWD/build -c listen_addresses=" start
createdb -h "$PWD/build" -U postgres morca
export MORCA_DATABASE_URL="postgresql:///morca?host=$PWD/build&user=postgres"
```

macOS limits a socket's path to 104 bytes, so keep the project's path
short or use a short directory for `-k` and `host=`.

## Apps

```sh
PORT=8080 ./build/console      # http://127.0.0.1:8080
```

An app reads the warehouse in `MORCA_WAREHOUSE`, so give it the same one
the local runs wrote: `MORCA_WAREHOUSE=$PWD/build/warehouse`. On a stack,
`lake = true` in its `[apps.<name>]` gives it the stack's.

## S3 locally

morca's `scripts/with-minio.sh` does for MinIO what `with-postgres.sh` does
for Postgres: it starts a throwaway server from conda-forge's
`minio-server`, makes a bucket `morca`, and sets the `AWS_*` environment, so
`MORCA_WAREHOUSE=s3://morca/warehouse` exercises the S3 path, as Hetzner Object Storage is reached, before a
stack does.

## Checking morca.toml

`morca config check` needs no database, so run it in the project's CI and
before a deploy; with a stack's overlay, `morca config check --overlay
<file>`. `morca config show --file --overlay <file>` prints what that
stack would register.
