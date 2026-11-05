---
name: magmalake-apps
description: Build a data application on magmalake as one Mojo project, with pipelines run by orca, a lake of Iceberg tables over S3, and a long-running flare web app reading them, then deploy it to AWS with `orca aws`. Use when starting a new magmalake project or laying one out (pixi, mojoshelf, `bin/`, `pipelines/`, `src/`); when a pipeline step writes Iceberg tables that an app reads; when writing a flare web app, console or dashboard over the lake; when running the project locally with Postgres and a local warehouse; or when deploying to AWS (`orca aws up`, `push`, `register`, `app`, `status`, `down`), setting up its hostnames, secrets, public bucket or costs. Pipelines in depth (partitions, keyed, sensors, dista tasks) are the `orca` skill's.
license: Apache-2.0
metadata:
  author: magmalake
  version: "0.2"
---

<!-- EDITORIAL GUIDELINES FOR THIS SKILL FILE
Every line here is loaded into an agent's context, so stay brief, but write
whole phrases a newcomer can follow: a list of bare nouns is not brief, it is
cryptic. Every command and API shown here is copied from orca, iceberg.mojo
or flare and was compiled or run; say plainly when something doesn't exist
yet instead of guessing. Put code and long stories in references/, and keep
this file to what an agent must know before writing the first file.
-->

A magmalake app is one pixi project holding three kinds of program, all in
Mojo: **pipelines** that orca schedules and that write Iceberg tables, the
project's own **`orca`** (scheduler and UI), and **apps**: long-running
flare servers, such as a console that reads the tables. On AWS, one
CloudFormation stack runs orca, Postgres, the apps and Caddy on one control
instance, and each pipeline run on instances of its own.

For pipelines in depth (partitions, keyed, sensors, dista tasks), load the
`orca` skill; write Mojo with Modular's `mojo-syntax` skill. mojoshelf's own:
`npx skills add mojoshelf/mojoshelf --skill mojoshelf-consume --yes`.

## Create the project

```sh
pixi init lake -c https://conda.modular.com/max -c conda-forge
cd lake
# In pixi.toml's [workspace], add:   preview = ["pixi-build"]
pixi add "mojo-compiler==1.2.0"
pixi workspace platform add linux-aarch64   # the AWS image's platform
pixi shelf add orca-mojo
pixi add postgresql                          # a local Postgres, for development
```

- **`pixi shelf add orca-mojo`** (orca 0.3.0) adds orca and all it needs
  to `pixi.toml`, including `dista-mojo`, `iceberg-mojo`, `flare_mlake`,
  `objectstore-mojo` and `json_mlake`, so those import at once. Add other
  tins with `pixi shelf add <tin>`. It takes minutes and prints every
  tin's build log; it hasn't hung.
- **`linux-aarch64` must be in the platforms**, or `orca aws push` refuses
  to build; the image installs with `pixi install --locked`.
- **Reading a tin's source:** the environment holds only compiled
  `.mojoc` packages for most tins (flare and json are the exceptions), so
  read the source on GitHub (`https://github.com/magmalake/<repo>` at the
  `rev` in `pixi.toml`) or in pixi's git cache:
  `ls -d ~/Library/Caches/rattler/cache/git-v0/checkouts/*/<rev's first 7>*`
  on macOS, `~/.cache/rattler/cache/git-v0/checkouts/` on Linux. File paths
  cited in these skills are in those checkouts.

## Layout

`orca aws push` builds exactly this layout:

```
pixi.toml, pixi.lock
bin/scheduler.mojo     the project's orca: the two lines below
bin/<name>.mojo        an app, e.g. bin/console.mojo
pipelines/<name>.mojo  a pipeline, ending in orca_main[...]()
src/                   shared code, on the import path (-I src)
Dockerfile.orca        optional: replaces orca's template Dockerfile
```

`bin/scheduler.mojo` is exactly:

```mojo
from orca.command import orca_command

def main() raises:
    orca_command()
```

**Never name it `orca.mojo`**: it would shadow the `orca` package. In the
image it becomes `/opt/orca/bin/orca`, other `bin/<name>.mojo` become
`/opt/orca/bin/<name>`, and pipelines `/opt/orca/pipelines/<name>`.

## The lake: tables in a warehouse

orca finds tables through `ORCA_WAREHOUSE`, the root of an Iceberg
filesystem catalog: dataset `demo.days` is the table at
`<warehouse>/demo/days`. The warehouse is a local directory or an S3
location (`s3://bucket/warehouse`). On AWS the stack puts it in the
lake's bucket, `mlake-public-<account>`:

| Variable | Value on AWS | Who reads it |
|---|---|---|
| `ORCA_WAREHOUSE` | `s3://mlake-public-<account>/warehouse` | pipelines (through `orca.tables`), apps; anyone unsigned only with `--public-data true` |
| `ORCA_PUBLIC_WAREHOUSE` | the same location | code that wants to say "the public one" |

orca's own bucket holds only logs and run state. The lake's bucket is
private by default (orca 0.3.6+): the instances read and write it with their
roles. `orca aws up --public-data true` lets anyone read it unsigned (`aws s3
ls --no-sign-request s3://mlake-public-<account>/warehouse/`, or DuckDB and
pyiceberg in their anonymous modes), so every table a pipeline writes
becomes public, and downloads are billed to the project.

Put shared table definitions in `src/lake.mojo`, so pipelines and apps
agree on names and schemas. The samples below import three things from it,
copied in full in [references/lake.md](references/lake.md):

- `DAYS = "demo.days"`, the dataset's name;
- `days_schema()`, its Iceberg schema (`day` string, field 1; `events`
  long, field 2), from `Schema.parse` of Iceberg's JSON;
- `load(namespace, name)`, a table from `ORCA_WAREHOUSE` (default
  `build/warehouse`), through `FilesystemCatalog.local(root)` or, for
  `s3://`, `FilesystemCatalog(root, FileIO())`.

## Writing tables from a step

A step declares the datasets it writes (`writes=[...]`) and reads
(`reads=[...]`); `orca.tables` refuses any other. Use `ensure_table` to
create a table on first use and `append` to commit, so a retried step finds
its earlier commit instead of writing twice. `pipelines/days.mojo`:

```mojo
from std.os import getenv, setenv

from iceberg.batch import ColumnBuilder, batch_of
from iceberg.values import Datum
from orca import Cron, Ctx, Daily, Day, Mapped, Pipeline, Step, orca_main
from orca.tables import append, ensure_table

from lake import DAYS, days_schema

def count(mut ctx: Ctx, d: Day) raises -> Int64:
    var schema = days_schema()
    var table = ensure_table(ctx, DAYS, schema)
    var day = ColumnBuilder.of(schema, 1)       # by field id
    var events = ColumnBuilder.of(schema, 2)
    day.add(Datum.string_(String(d)))
    events.add(Datum.long_(Int64(d.day * 10)))
    var id = append(ctx, table, [batch_of([day^, events^])])
    ctx.log(String("appended snapshot ", id))
    return id

comptime Count = Step["count", Day, Int64, count, Mapped, writes=[DAYS]]
comptime Days = Pipeline[
    "days", Count, partitions=Daily["2024-01-01"], schedule=Cron["0 3 * * *"]
]

def main() raises:
    # On AWS the stack sets ORCA_WAREHOUSE; locally, default to a directory.
    if not getenv("ORCA_WAREHOUSE"):
        _ = setenv("ORCA_WAREHOUSE", "build/warehouse")
    orca_main[Days]()
```

- **Defaulting `ORCA_WAREHOUSE` in `main`** is what orca's taxi example
  does; never override a value that is already set.
- **Parquet input** already comes as `RecordBatch`es; pass them to
  `append`. `scan(ctx, table)` reads a declared input at a pinned snapshot.
  [references/lake.md](references/lake.md) has partitions, scans, filters
  and nested columns.

## A flare app over the lake

An app is a flare `Router` served on `PORT`. `bin/console.mojo`:

```mojo
from std.os import getenv

from flare.http import HttpServer, Request, Response, Router, ServerConfig
from flare.http import html_escape, internal_error, ok_json
from flare.net import SocketAddr

from lake import load

def _html(var body: String) raises -> Response:
    var resp = Response(status=200, reason="OK", body=List[UInt8](body.as_bytes()))
    resp.headers.set("Content-Type", "text/html; charset=utf-8")
    return resp^

def days(req: Request) raises -> Response:
    try:
        var rows = load("demo", "days").scan().select(["day", "events"]).to_table()
        var h = String("<!doctype html><title>Days</title><h1>Days</h1><table>")
        for r in range(rows.num_rows()):
            h.write("<tr><td>", html_escape(rows.value(r, 0).repr_()), "</td><td>")
            h.write(html_escape(rows.value(r, 1).repr_()), "</td></tr>")
        h.write("</table>")
        return _html(h^)
    except e:
        return internal_error(String(e))

def days_json(req: Request) raises -> Response:
    return ok_json(load("demo", "days").scan().to_table().to_json())

def main() raises:
    var r = Router()
    r.get("/", days)
    r.get("/days.json", days_json)
    var addr = String("0.0.0.0:", getenv("PORT", "8080"))
    var server = HttpServer.bind(SocketAddr.parse(addr), ServerConfig())
    print("console on http://", addr, sep="")
    server.serve(r^)
```

- **Listen on `0.0.0.0:$PORT`.** On AWS, Caddy reaches the app by its
  container name on that port; `127.0.0.1` is unreachable from it.
- **Every request scans the table again** (from S3, on one event loop):
  fine for a small console. For state, write a struct conforming to
  `Handler`, as orca's UI does (`struct Ui` in `src/orca/ui.mojo`, in
  [orca's repository](https://github.com/magmalake/orca.mojo)).
- **Path parameters:** `r.get("/repos/:id", repo)`, then `req.param("id")`.
- **Downloading with flare's `HttpClient`:** a `Response` can't give up
  its body (`return resp.body^` fails with "destroyed out of the middle of
  a value"). Swap it out; `swap` is a builtin, no import:

  ```mojo
  from flare.http import HttpClient, Request

  var c = HttpClient(timeout_ms=120_000)
  var resp = c.send(Request(method="GET", url=url))
  var body = List[UInt8]()
  swap(body, resp.body)
  return body^
  ```
- **On AWS an app gets only** `AWS_REGION`, `ORCA_WAREHOUSE`,
  `ORCA_PUBLIC_WAREHOUSE` and `PORT`, and reads S3 with the control
  instance's role. It reads the lake, never orca's database.

## Run it locally

```sh
mkdir -p build
mojo build bin/scheduler.mojo -o build/orca
mojo build -I src pipelines/days.mojo -o build/days
mojo build -I src bin/console.mojo -o build/console
./build/days run --partitions 2024-01-01..2024-01-03   # no database needed
PORT=8080 ./build/console                              # http://127.0.0.1:8080
```

Run them in `pixi shell` or with `pixi run`. The scheduler and UI need
Postgres: orca's [`scripts/with-postgres.sh`](https://github.com/magmalake/orca.mojo/blob/main/scripts/with-postgres.sh)
(copy it; the tin doesn't ship it) runs a command with a throwaway server
and `ORCA_DATABASE_URL` set:

```sh
export ORCA_WAREHOUSE=$PWD/build/warehouse
scripts/with-postgres.sh sh -c '
  ./build/orca locate build/days --root . &&
  ./build/orca register ./build/days &&
  ./build/orca backfill days 2024-01-04..2024-01-05 &&
  (./build/orca ui & ./build/orca scheduler)'   # UI at http://127.0.0.1:8270
```

[references/local.md](references/local.md): a lasting database, logins, MinIO.

## Deploy to AWS

`orca aws` is in orca-mojo 0.3.0 and later; `./build/orca aws` prints its
usage.


1. **Log in** with `aws login` (or set `AWS_PROFILE`); `docker` must run.
   Confirm the project's Region in AWS Settings > View all projects >
   Overview > Additional Info > Region. The stack's Availability Zone
   defaults to `us-east-2a`; in another Region pass one of its zones to
   `up` with `--zone`, such as `--zone eu-north-1a`.
2. **Create the stack:** `./build/orca aws up --email you@example.com
   --hostname orca.example.com --console-hostname app.example.com`. The
   email gets the Budgets alerts (`--budget N`, default 100 dollars a
   month). Without the hostname options the stack uses magmalake's own
   (`orca-demo.mlake.dev`, `libs.mlake.dev`).
3. **Point DNS at it:** `./build/orca aws status` prints `PublicAddress`;
   make an A record for each hostname to it. On Cloudflare, set the records
   to "DNS only", or Caddy can't get certificates. The UI answers at
   `SslipUrl` at once.
4. **Commit, then push the image:** `./build/orca aws push` builds `bin/`
   and `pipelines/` for linux-aarch64 and pushes it to the stack's ECR
   repository, tagged with the commit's first 12 characters and `latest`.
5. **Register pipelines:** `./build/orca aws register --workers 0..4 days`
   moves the scheduler and UI to the new image and registers each named
   `pipelines/<name>`, with `orca register`'s flags (`--head-type`,
   `--worker-type`, `--workers`, `--spot`, `--secret`).
6. **Start the app:** `./build/orca aws app console` serves `bin/console`
   at `https://<console hostname>`. Other apps need `--host` and a DNS
   record of their own.

After a code change: commit, `push`, `register` again (which moves the
instance to the new tag), and `app console` again. **Secrets** go in
Secrets Manager under `orca/`; runs get them as variables, masked in every
log orca writes:

```sh
aws secretsmanager create-secret --name orca/github-token --secret-string "$TOKEN"
./build/orca aws register --secret GITHUB_TOKEN=orca/github-token days
```

**Tear down** with `./build/orca aws down --yes`, after emptying the buckets.
[references/aws.md](references/aws.md) has the stack's contents, logins for
the UI, backfills on AWS and troubleshooting.

## Cost

- **The control instance runs all month** (`t4g.large` on demand, 40 GB,
  an Elastic IP), whether or not anything runs.
- **Runs start on-demand instances of their own.** Some AWS plans deny
  Spot, so `--spot` works only where allowed. `--workers 0..N` keeps an
  idle run at no workers; measure one partition before a large backfill.
- **Public downloads are billed to the project.** If things that worked
  start failing with Access Denied, ask whether a spend limit paused the
  project (AWS Settings > Billing).

## Common mistakes

| Mistake | What happens | Fix |
|---|---|---|
| The scheduler file is `bin/orca.mojo` | it shadows the `orca` package and nothing builds | name it `bin/scheduler.mojo` |
| No `linux-aarch64` platform | `orca aws push` refuses | `pixi workspace platform add linux-aarch64` |
| Building without `-I src` | `from lake import ...` isn't found | pass `-I src`, as the image's build does |
| A step writes a dataset not in `writes=` | `orca.tables` refuses it | declare it in the `Step` |
| A step writes with iceberg directly | a retry commits the data twice | use `orca.tables.append` or `commit` |
| The app listens on `127.0.0.1` | Caddy gets no answer on AWS | `0.0.0.0:$PORT` |
| Pushing uncommitted changes | the image holds them but is tagged and linked as the last commit | commit first |
| `splitlines()` on tab-separated text, such as `git log --numstat` | Mojo 1.2.0 also splits on `\t`: `"1\t2\tf\n3\t4\tb".splitlines()` has 6 parts, so rows silently don't match | `split("\n")` |
| `b.num_rows()` on a `RecordBatch` | `'Int' does not implement the '__call__' method` | `RecordBatch.num_rows` is a field; `ScanResult.num_rows()` (from `to_table()`) is a method |
| Reading `.gz` with `deflate-mojo` | it's raw DEFLATE only; no tin reads gzip framing | parse the gzip header yourself and call `inflate_at`: [references/lake.md](references/lake.md#gzip-input) |
| A `for` loop in a pixi task | `Unsupported reserved word`: pixi's task shell has no loops | put the loop in a script and run that from the task ([references/local.md](references/local.md#build)) |
