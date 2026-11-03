# Running the project locally

Everything runs on your machine first: the pipelines as plain processes, a
warehouse in a directory, orca's scheduler and UI over a local Postgres, and
the apps on a port of their own. Work inside `pixi shell`, or prefix each
command with `pixi run`.

## Build

Build the way the image does, every program with `-I src`:

```sh
mkdir -p build
mojo build bin/scheduler.mojo -o build/orca
for p in pipelines/*.mojo; do mojo build -I src "$p" -o "build/$(basename "$p" .mojo)"; done
for a in bin/*.mojo; do n=$(basename "$a" .mojo); [ "$n" = scheduler ] || mojo build -I src "$a" -o "build/$n"; done
```

A pixi task per program (`[tasks]` in `pixi.toml`) saves typing, as orca's
own `pixi.toml` does.

## A pipeline on its own

A pipeline binary runs without a database or a scheduler:

```sh
export ORCA_WAREHOUSE=$PWD/build/warehouse
./build/days describe
./build/days run --partitions 2024-01-01..2024-01-03
```

`build/warehouse/demo/days/` then holds the table. Without
`ORCA_DATABASE_URL` the run isn't recorded; with it, `./build/days status`
lists runs and `./build/days resume <run>` finishes one that stopped.

## The scheduler and the UI

They need Postgres. `pixi add postgresql` gives `initdb`, `pg_ctl` and
`createdb` from conda-forge. orca's `scripts/with-postgres.sh` (copy it from
orca's repository; the tin doesn't ship it) starts a throwaway server on a
Unix socket, exports `ORCA_DATABASE_URL`, runs a command and removes the
server when it ends:

```sh
scripts/with-postgres.sh sh -c '
  ./build/orca locate build/days --root . &&
  ./build/orca register ./build/days &&
  ./build/orca backfill days 2024-01-04..2024-01-05 &&
  (./build/orca ui & ./build/orca scheduler)'
```

- `orca locate` records where each step's code is, for the UI's links.
- `orca register` takes the binary's path; the pipeline's name comes from
  its `Pipeline["days", ...]`.
- `orca scheduler` fires schedules, backfills and sensors until stopped;
  `orca tick` does one round and returns, which suits scripts.
- `orca ui` serves on `http://127.0.0.1:8270` (`ORCA_UI_ADDR` to change).
  Every page is JSON too, with `Accept: application/json`.
- `./build/orca user add alice` (the password from stdin) makes a login;
  once any user exists, the UI's actions need one.

For a database that survives between sessions, make one with the same
tools the script uses, then export its URL:

```sh
initdb -D build/pg --auth=trust --username=postgres -E UTF8 --locale=C
pg_ctl -D build/pg -l build/pg.log -o "-k $PWD/build -c listen_addresses=" start
createdb -h "$PWD/build" -U postgres orca
export ORCA_DATABASE_URL="postgresql:///orca?host=$PWD/build&user=postgres"
```

macOS limits a socket's path to 104 bytes, so keep the project's path
short or use a short directory for `-k` and `host=`.

## Apps

```sh
PORT=8080 ./build/console      # http://127.0.0.1:8080
```

`src/lake.mojo`'s `warehouse()` reads `ORCA_WAREHOUSE` (default
`build/warehouse`), the same one the local runs wrote.

## S3 locally

orca's `scripts/with-minio.sh` does for MinIO what `with-postgres.sh` does
for Postgres: it starts a throwaway server from conda-forge's
`minio-server`, makes a bucket `orca`, and sets the `AWS_*` environment, so
`ORCA_WAREHOUSE=s3://orca/warehouse` exercises the S3 path before AWS.
