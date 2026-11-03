# Deploying with `orca aws`

`orca aws` (orca's `src/orca/aws.mojo`) drives the `aws` and `docker`
commands you already have, logged in with `aws login` or `AWS_PROFILE`. It is
in orca-mojo 0.3.0 and later. Its usage, as it prints it:

```
orca aws up --email ADDR [--hostname H] [--console-hostname H] [--budget N]
              [--zone AZ]
orca aws push [--tag T]
orca aws register [--tag T] [register flags] <pipeline> [...]
orca aws app <name> [--host H] [--port 8080] [--tag T]
orca aws status
orca aws down --yes
(each takes --stack NAME, default orca, and --region R)
```

`--region` defaults to `AWS_REGION`, else the `aws` command's own. Files it
writes go to `build/orca-aws/`.

## What the stack holds

One CloudFormation stack (template `deploy/aws/orca.yaml`, carried inside
orca), so it is reviewed as one file and deleted as one stack:

- a VPC with one public subnet, in Availability Zone `us-east-2a` unless
  `up --zone` names another, and a gateway endpoint for S3;
- **the control instance** (`t4g.large`, arm64 Amazon Linux 2023, 40 GB
  gp3, an Elastic IP), running Postgres, the scheduler, `orca ui`, Caddy and
  your apps as containers that restart with the instance;
- **orca's bucket** (private): run state, and logs under `logs/`, kept
  30 days;
- **the public bucket** `mlake-public-<account>`, the lake, readable by
  anyone, with CORS `GET` from any origin: both `ORCA_WAREHOUSE` and
  `ORCA_PUBLIC_WAREHOUSE` are `s3://<it>/warehouse`;
- the ECR repository `orca`, which keeps the last 5 images;
- a role for run instances (both buckets, the image, secrets under `orca/`)
  and one for the control instance (both buckets, the image, starting and
  ending tagged run instances of the allowed types);
- a Budgets alert to `--email`, at 80% of `--budget` (default 100 dollars a
  month) spent and at 100% forecast.

Only ports 80 and 443 accept connections from outside. Postgres accepts
the run instances only. The UI is public for reading; its actions need a
login.

`orca aws status` prints the outputs: `SslipUrl` (the UI, at once),
`PublicAddress`, `Repository`, `Bucket`, `PublicBucket`,
`ControlInstance` and `ConsoleHostname`.

## The steps, with what each does

1. **`up`** writes the template and runs `aws cloudformation deploy`. A new
   stack needs `--email`, and gets a generated database password. Running
   `up` again updates the stack and keeps values you don't pass.
   `--hostname` names the UI and `--console-hostname` the console; without
   them the template's defaults are magmalake's own `orca-demo.mlake.dev`
   and `libs.mlake.dev`. The console's hostname can't be set to empty
   through `up`. `--zone AZ` picks the Availability Zone (default
   `us-east-2a`); outside us-east-2, pass a zone of the project's Region.
2. **DNS.** Make an A record for each hostname to `PublicAddress`. With
   Cloudflare, "DNS only" (grey cloud): Caddy gets Let's Encrypt
   certificates itself and can't through Cloudflare's proxy.
3. **`push`** needs `pixi.toml`, `pixi.lock` and `bin/scheduler.mojo` in the
   current directory, and `linux-aarch64` in the platforms. It copies
   `pixi.toml`, `pixi.lock`, `bin/`, `pipelines/` and `src/` to
   `build/orca-aws/context`, builds with your `Dockerfile.orca` or else
   orca's template (`docker build --platform linux/arm64`), and pushes
   `<repository>:<tag>` and `:latest`. The tag is `--tag`, else the
   commit's first 12 characters, else `latest`. The image carries every
   tin's shared library from the environment (`lib*mojo*.so`, the codecs
   included), libpq and flare's. The build records the git
   remote and commit, which each run links in the UI. On the control
   instance, the stack waits for the first image before starting orca.
4. **`register`** runs, on the control instance through SSM,
   `orca-refresh <tag>` (pull the image, restart the scheduler and UI from
   it) and then `orca register --image <repository>:<tag> [flags]
   /opt/orca/pipelines/<name>` for each pipeline. The flags are
   `orca register`'s: `--head-type T`, `--worker-type T`,
   `--workers N|MIN..MAX`, `--spot` and `--secret NAME=ID`. It refuses
   `--image`: the image is always the project's.
5. **`app <name>`** runs `orca-app` on the control instance: the image's
   `/opt/orca/bin/<name>` as a container named `<name>`, with `AWS_REGION`,
   `ORCA_WAREHOUSE`, `ORCA_PUBLIC_WAREHOUSE` and `PORT` (default 8080), and
   a Caddy site for the host.
   `console` takes the stack's `ConsoleHostname`; any other app needs
   `--host`. With `--tag` it first moves the instance to that image.
   The console's Caddy site is fixed at port 8080, so leave its `--port`
   alone.
6. **`down --yes`** deletes the stack and waits. A bucket or repository that
   still holds anything stops the delete; empty it
   (`aws s3 rm --recursive s3://<bucket>`) and run `down --yes` again.

## The environment runs and apps get

The scheduler and UI get the control instance's `/etc/orca.env`:
`ORCA_DATABASE_URL`, `AWS_REGION`, `ORCA_WAREHOUSE`,
`ORCA_PUBLIC_WAREHOUSE`, `ORCA_LOGS`, `ORCA_RUNNER=ec2` and the `ORCA_EC2_*`
and `ORCA_UI_*` settings. **Apps get only `AWS_REGION`, `ORCA_WAREHOUSE`,
`ORCA_PUBLIC_WAREHOUSE` and `PORT`**: an app reads the lake, not orca's
database. Run instances get `ORCA_WAREHOUSE`, `ORCA_PUBLIC_WAREHOUSE`
(through `ORCA_RUN_ENV`), orca's own variables and any registered secrets.
S3 access in all of them comes from the instance role.

## Secrets

```sh
aws secretsmanager create-secret --name orca/github-token --secret-string "$TOKEN"
./build/orca aws register --secret GITHUB_TOKEN=orca/github-token days
```

Only IDs under `orca/` work: run instances may read nothing else. The run's
environment gets `GITHUB_TOKEN`, every log orca writes masks its value, and
Postgres keeps only the ID. The UI shows logs to anyone, so never print a
secret any other way.

## Logins for the UI's actions

On AWS the UI requires a login for its actions (start, rerun, backfill,
cancel). There's no `orca aws` command for users yet. Add one on the
control instance, for example through Session Manager
(`aws ssm start-session --target <ControlInstance>`, which needs the
Session Manager plugin):

```sh
sudo docker exec -i scheduler orca user add alice    # the password from stdin
```

Backfills can then be started from the UI. There is also no
`orca aws backfill`; `sudo docker exec scheduler orca backfill days
2024-01-01..2024-01-31` does it from the same shell.

## Instances and cost

- Runs use on-demand instances. Some AWS plans deny Spot, so `--spot` fails
  there; it stays for accounts that allow it.
- The stack lets the scheduler start only `c7g.large`, `c7g.xlarge`,
  `c7g.2xlarge`, `c7g.4xlarge`, `t4g.medium` and `t4g.large`. Without
  `--head-type` and `--worker-type`, runs use `c7g.large` heads and
  `c7g.xlarge` workers.
- `--workers 0..N` lets a run start with no workers, grow to N while tasks
  wait, and drain idle ones; that is what keeps on-demand runs affordable.
- The control instance, its volume and its Elastic IP cost money all month,
  whether or not anything runs. Check the current prices for your Region.
- Downloads from the public bucket are billed to the project.

## When something fails

| Symptom | Cause |
|---|---|
| `up` fails at once in a Region other than us-east-2 | the default zone is `us-east-2a`; run `up --zone <a zone of your Region>` |
| The UI's hostname has no certificate | the A record is missing or proxied; the `SslipUrl` works meanwhile |
| `push`: "no bin/scheduler.mojo calling orca.command" | the file is missing or named otherwise |
| `push`: a `mojo build` error | the image builds every `bin/*.mojo` and `pipelines/*.mojo` with `-I src`; build them all locally first |
| `register`: the command on the control instance failed | its output is printed; often the image tag wasn't pushed |
| A run can't read or write a table | it isn't under the stack's buckets, or the step didn't declare it |
| Things that worked return Access Denied | ask whether a spend limit paused the project (AWS Settings > Billing) |
