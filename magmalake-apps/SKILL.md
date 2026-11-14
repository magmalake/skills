---
name: magmalake-apps
description: Build a data application on magmalake as one Mojo project, with pipelines run by morca, a lake of Iceberg tables in object storage, and a long-running flare web app reading them, then deploy it as a Hetzner stack with morca's deploy tools (up.sh, deploy.sh, promote.sh, HETZNER_ENV). Use when starting a new magmalake project or laying one out (pixi, mojoshelf, `bin/`, `pipelines/`, `src/`, morca.toml, upgrade.toml); when a pipeline step writes Iceberg tables that an app reads; when writing a flare web app, console or dashboard over the lake and declaring it as an `[apps.<name>]`; when running the project locally with Postgres and a local warehouse; when building its image on morca's runtime; or when deploying, promoting from a beta stack, rolling back, writing a release's upgrade steps, or setting up hetzner.env, secrets and overlays. AWS (`morca aws`) is covered briefly. Pipelines in depth are the `morca` skill's.
license: Apache-2.0
metadata:
  author: magmalake
  version: "0.3"
---

<!-- EDITORIAL GUIDELINES FOR THIS SKILL FILE
Every line here is loaded into an agent's context, so stay brief, but write
whole phrases a newcomer can follow: a list of bare nouns is not brief, it is
cryptic. Every command and API shown here is copied from morca (0.30.0) or
flare and was compiled or run; say plainly when something doesn't exist
yet instead of guessing. Blocks fenced as `mojo compile` are built by CI
(`name=` ones are the helpers the others import). Put code and long
stories in references/, and keep this file to what an agent must know
before writing the first file.
-->

A magmalake app is one pixi project holding three kinds of program, all in
Mojo: **pipelines** that morca schedules and that write Iceberg tables, the
project's own **`morca`** (scheduler and UI), and **apps**: long-running
flare servers, such as a console that reads the tables. It ships as one
image built on morca's runtime image. Deployed, it is a **Hetzner stack**:
one ARM server running Postgres, the scheduler, the UI and the apps in
Docker, runs as local processes on it (or on worker pools), the warehouse
in Hetzner Object Storage, and Cloudflare Tunnel in front.

Load the `morca` skill for pipelines in depth (partitions, keyed, sensors,
dista tasks, morca.toml), and write Mojo with Modular's `mojo-syntax`.

## Create the project

```sh
pixi init lake -c https://conda.modular.com/max -c conda-forge \
    -c https://repo.prefix.dev/modular-community
cd lake
# In pixi.toml's [workspace], add:   preview = ["pixi-build"]
pixi add "mojo-compiler==1.2.0"
pixi workspace platform add linux-aarch64 linux-64   # the image's platforms
pixi shelf add morca-mojo
pixi add postgresql                                   # a local Postgres, for development
```

- **`pixi shelf add morca-mojo`** (0.30.0) adds morca and all it needs,
  including `dista-mojo`, `arrow-mlake-mojo`, `flare_mlake` and
  `json_mlake`, so those import at once. It takes many minutes and prints
  every tin's build log; it hasn't hung.
- **Reading a tin's source:** the environment holds compiled `.mojoc`
  packages only, so read the source on GitHub at the `rev` in `pixi.toml`
  (morca and dista are in `morca-dev`, the libraries in `magmalake`) or in
  pixi's git cache: `ls -d ~/Library/Caches/rattler/cache/git-v0/checkouts/*/<rev's first 7>*`
  on macOS, `~/.cache/rattler/cache/git-v0/checkouts/` on Linux.

## Layout

The image builds exactly this layout:

```
pixi.toml, pixi.lock
morca.toml             how each pipeline runs, and the apps (morca skill)
upgrade.toml           optional: steps a release needs around its deploy
bin/scheduler.mojo     the project's morca: the two lines below
bin/<name>.mojo        an app, e.g. bin/console.mojo
pipelines/<name>.mojo  a pipeline, ending in morca_main[...]()
src/                   shared code, on the import path (-I src)
Dockerfile.morca       optional: replaces morca's project.Dockerfile
```

`bin/scheduler.mojo` is exactly this; **never name it `morca.mojo`**, which
would shadow the `morca` package:

```mojo compile
from morca.command import morca_command


def main() raises:
    morca_command()
```

In the image it becomes `/opt/morca/bin/morca`, other `bin/<name>.mojo`
become `/opt/morca/bin/<name>`, and pipelines `/opt/morca/pipelines/<name>`.

## The lake

morca finds tables through `MORCA_WAREHOUSE`, the root of an Iceberg
filesystem catalog: dataset `demo.days` is the table at
`<warehouse>/demo/days`, a local directory or `s3://bucket/warehouse`. On a
Hetzner stack it is in the stack's Object Storage bucket, reached with
`AWS_ENDPOINT_URL_S3` and the stack's keys. Put shared table definitions in
`src/lake.mojo`, so pipelines and apps agree on names and schemas:

```mojo compile name=src/lake.mojo
"""The lake's tables, shared by the pipelines and the apps."""

from morca.tables import Schema

comptime DAYS = "demo.days"

comptime DAYS_SCHEMA = (
    '{"schema-id":0,"type":"struct","fields":['
    '{"id":1,"name":"day","required":true,"type":"string"},'
    '{"id":2,"name":"events","required":true,"type":"long"}]}'
)


def days_schema() -> Schema:
    return Schema.parse(DAYS_SCHEMA)
```

## Writing tables from a step

A step declares the datasets it writes (`writes=[...]`) and reads
(`reads=[...]`); `morca.tables` refuses any other. `ensure_table` creates
a table on first use, `BatchBuilder` makes the rows, and `append` commits
them so that a retried step finds its earlier commit instead of writing
twice. `pipelines/days.mojo`:

```mojo compile
from std.os import getenv, setenv

from morca import Cron, Ctx, Daily, Day, Mapped, Pipeline, Step, morca_main
from morca.tables import BatchBuilder, append, ensure_table
from parquet import RecordBatch

from lake import DAYS, days_schema


def count(mut ctx: Ctx, d: Day) raises -> Int64:
    var table = ensure_table(ctx, DAYS, days_schema())
    var b = BatchBuilder(days_schema())
    b.add_str(b.col("day"), String(d))
    b.add_int(b.col("events"), Int64(d.day * 10))
    var batches: List[RecordBatch] = [b^.build()]
    var id = append(ctx, table, batches)
    ctx.log(String("appended snapshot ", id))
    return id


comptime Count = Step["count", Day, Int64, count, Mapped, writes=[DAYS]]
comptime Days = Pipeline[
    "days", Count, partitions=Daily["2024-01-01"], schedule=Cron["0 3 * * *"]
]


def main() raises:
    # A deployed stack sets MORCA_WAREHOUSE; locally, default to a directory.
    if not getenv("MORCA_WAREHOUSE"):
        _ = setenv("MORCA_WAREHOUSE", "build/warehouse")
    morca_main[Days]()
```

[references/lake.md](references/lake.md) has partitions, timestamps,
nested columns, filters, gzip input and readers outside the project; the
morca skill's `references/tables.md` has the whole `morca.tables` API.

## A flare app over the lake

An app is a flare `Router` served on `PORT`. It reads tables with
`load_dataset` (no step, so nothing is checked or recorded), which needs
`MORCA_WAREHOUSE`. `bin/console.mojo`:

```mojo compile
from std.os import getenv

from flare.http import HttpServer, Request, Response, Router, ServerConfig
from flare.http import html_escape, internal_error, ok_json
from flare.net import SocketAddr
from morca.tables import ints_of, load_dataset, strings_of

from lake import DAYS


def _html(var body: String) raises -> Response:
    var resp = Response(
        status=200, reason="OK", body=List[UInt8](body.as_bytes())
    )
    resp.headers.set("Content-Type", "text/html; charset=utf-8")
    return resp^


def days(req: Request) raises -> Response:
    try:
        var batches = load_dataset(DAYS).scan().select(["day", "events"]).to_batches()
        var h = String("<!doctype html><title>Days</title><h1>Days</h1><table>")
        for b in batches:
            var day = strings_of(b, "day")
            var events = ints_of(b, "events")
            for r in range(b.num_rows):
                h.write("<tr><td>", html_escape(day[r].value()), "</td><td>")
                h.write(events[r].value(), "</td></tr>")
        h.write("</table>")
        return _html(h^)
    except e:
        return internal_error(String(e))


def days_json(req: Request) raises -> Response:
    var out = String("[")
    for b in load_dataset(DAYS).scan().to_batches():
        var day = strings_of(b, "day")
        var events = ints_of(b, "events")
        for r in range(b.num_rows):
            out.write("," if out.byte_length() > 1 else "")
            out.write('{"day":"', day[r].value(), '","events":', events[r].value(), "}")
    out.write("]")
    return ok_json(out^)


def main() raises:
    var r = Router()
    r.get("/", days)
    r.get("/days.json", days_json)
    var addr = String("0.0.0.0:", getenv("PORT", "8080"))
    var server = HttpServer.bind(SocketAddr.parse(addr), ServerConfig())
    print("console on http://", addr, sep="")
    server.serve(r^)
```

- **Listen on `0.0.0.0:$PORT`.** The tunnel reaches the app by its
  container name on the stack's Docker network; `127.0.0.1` is unreachable
  from it.
- **Every request scans the table again**, from object storage, on one
  event loop: fine for a small console. For state, write a struct
  conforming to flare's `Handler`.
- **Path parameters:** `r.get("/repos/:id", repo)`, then `req.param("id")`.
- **A `Response` can't give up its body** (`return resp.body^` fails with
  "destroyed out of the middle of a value"). Swap it out:
  `var body = List[UInt8]()`, then `swap(body, resp.body)`.

Declare the app in `morca.toml`; the stack gives it exactly that and never
morca's database:

```toml
[apps.console]
port = 8080
check = "/"               # a path that answers 200 once it's ready
lake = true               # MORCA_WAREHOUSE, the endpoint and the S3 keys
```

## Run it locally

```sh
mkdir -p build
mojo build bin/scheduler.mojo -o build/morca
mojo build -I src pipelines/days.mojo -o build/days
mojo build -I src bin/console.mojo -o build/console
./build/days run --partitions 2024-01-01..2024-01-03     # no database needed
MORCA_WAREHOUSE=build/warehouse PORT=8080 ./build/console   # http://127.0.0.1:8080
```

Run them in `pixi shell` or with `pixi run`. The scheduler and UI need
Postgres: copy morca's `scripts/with-postgres.sh` (the tin doesn't ship
it), which runs a command beside a throwaway server with
`MORCA_DATABASE_URL` set:

```sh
export MORCA_WAREHOUSE=$PWD/build/warehouse
scripts/with-postgres.sh sh -c '
  ./build/morca register ./build/days &&
  ./build/morca backfill days 2024-01-04..2024-01-05 &&
  (./build/morca ui & ./build/morca scheduler)'   # UI at http://127.0.0.1:8270
```

[references/local.md](references/local.md): a lasting database, logins,
MinIO, and checking morca.toml in CI.

## The image

A project's image starts `FROM ghcr.io/morca-dev/morca-runtime:<version>`,
the morca-mojo version `pixi.lock` pins, and adds the project's programs
and `morca.toml` (as `MORCA_CONFIG`). morca's template Dockerfile doesn't
copy `upgrade.toml`: a project with upgrade steps needs a `Dockerfile.morca`
of its own that puts it at `/opt/morca/upgrade.toml`, as the census's
does. Build it in the
project's CI with morca's reusable workflow, pinned to the same morca tag:

```yaml
jobs:
  image:
    uses: morca-dev/morca/.github/workflows/project-image.yml@v0.30.0
    permissions: { contents: read, packages: write }
    with:
      image: ghcr.io/<owner>/<project>
      push: ${{ github.event_name == 'push' }}
```

It builds arm64 for `neoverse-n1` (Hetzner's CAX CPU; a binary built for
GitHub's own arm64 runners dies there with SIGILL) and amd64 for
`x86-64-v3`, runs `morca config check`, checks every pipeline answers
`describe`, and tags the image with the commit's short sha. The runtime is
distroless: a `RUN` in a stage `FROM` it has busybox `sh` and no `apt-get`,
so install extras in a build stage and copy them in.

## Deploy to Hetzner

morca's release assets carry the deploy scripts, so no checkout of morca is
needed:

```sh
curl -fsSL https://github.com/morca-dev/morca/releases/latest/download/morca-hetzner.tar.gz | tar -xz
cd morca-hetzner-<version>
mkdir -p ~/.config/morca-hetzner && cp hetzner.env.example ~/.config/morca-hetzner/hetzner.env
export HETZNER_ENV=~/.config/morca-hetzner/hetzner.env   # every script reads it
./up.sh --dry-run                     # prints every hcloud and ssh command, runs none
./up.sh                               # server, firewall, Volume, settings, containers
ssh root@<ip> docker exec -it scheduler morca user add <you>
ssh root@<ip> morca-stack register    # the image's morca.toml, with the stack's overlay
./deploy.sh <short sha>               # every later deploy
```

- **`hetzner.env`** is the stack: `NAME` (it prefixes every Hetzner
  resource), `LOCATION`, `SERVER_TYPE_SMALL`, `BUCKET`, `ADMIN_IP`,
  `MORCA_IMAGE` (the project's image), `APPS=console`, `DRAIN_SECONDS`
  (required), `SECRETS_FILE` (one SOPS file for every secret) and
  `CONFIG_OVERLAY_FILE` (the stack's overlay on morca.toml). Keep it
  outside the repository. Every `deploy.sh` brings the server's settings in
  line with it first.
- **`deploy.sh <tag>`** backs Postgres up, replaces only the containers
  whose image or settings changed, overlaps the old scheduler with the new
  one so running runs finish, registers the pipelines, runs the release's
  upgrade steps, and ends with `morca-stack check`. `--rollback` goes back
  to the tags deployed before; `--all` replaces every container.
- **A beta stack** is a second `hetzner.env` (`NAME=orca-dev`, its own
  bucket, tunnel and overlay, `CANARY="pipeline=key ..."` runs).
  **`promote.sh --from <beta settings>`**, run with production's
  `HETZNER_ENV`, deploys to production exactly the tags beta's last check
  passed on.
- **Upgrade steps:** a release that needs something done around its deploy
  lists it in the image's `upgrade.toml`, and `deploy.sh` asks for or runs
  each step once:

  ```toml
  [[version]]
  version = "1.3.0"

  [[version.step]]
  id = "1.3.0-repos"            # unique and stable
  when = "after"                # before | after (the default)
  run = "morca backfill --rerun --as-fill mojo_repos 2023-01-01..2026-11-13"

  [[version.step]]
  id = "1.3.0-egress"
  when = "before"
  manual = "The console now calls huggingface.co: allow outbound HTTPS."
  ```

[references/hetzner.md](references/hetzner.md) has the first setup
(Cloudflare tunnel, Object Storage, SOPS), what a deploy does step by step,
the check, rollbacks, the beta stack and costs.
[references/aws.md](references/aws.md) is the older AWS path, `morca aws`.

## Common mistakes

| Mistake | What happens | Fix |
|---|---|---|
| The scheduler file is `bin/morca.mojo` | it shadows the `morca` package and nothing builds | name it `bin/scheduler.mojo` |
| Rows built with `iceberg.batch` | it no longer comes with morca (0.29.0 on) | `morca.tables.BatchBuilder` |
| `ORCA_*` names in `hetzner.env` or a project | ignored or refused since morca 0.25.0 | `MORCA_*`; stored names (`orca/` secret IDs, `/etc/orca`) stay |
| Building without `-I src` | `from lake import ...` isn't found | pass `-I src`, as the image's build does |
| A step writes a dataset not in `writes=` | `morca.tables` refuses it | declare it in the `Step` |
| A step writes with iceberg directly | a retry commits the data twice | use `morca.tables.append` or `commit` |
| The app listens on `127.0.0.1` | the tunnel gets no answer | `0.0.0.0:$PORT` |
| An app without `lake = true` reads tables | `load_dataset` fails: no `MORCA_WAREHOUSE` | set `lake = true` in its `[apps.<name>]` |
| An image built on a GitHub arm64 runner without a CPU target | SIGILL (exit 132) on the CAX server | morca's `project-image.yml`, or `--target-cpu neoverse-n1` |
| `RUN apt-get` in a stage `FROM` the runtime | no apt-get or bash there | install in a build stage, then copy |
| `splitlines()` on tab-separated text | Mojo 1.2.0 also splits on `\t` | `split("\n")` |
| `b.num_rows()` on a `RecordBatch` | `'Int' does not implement the '__call__' method` | `num_rows` is a field |
| A `for` loop in a pixi task | `Unsupported reserved word` | put the loop in a script ([references/local.md](references/local.md#build)) |
