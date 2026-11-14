# Deploying to AWS with `morca aws`

The older path, still in morca 0.30.0; magmalake's own stacks now run on
Hetzner ([hetzner.md](hetzner.md)). `morca aws` drives the `aws` and
`docker` commands you already have, logged in with `aws login` or
`AWS_PROFILE`:

```
morca aws up --email ADDR [--hostname H] [--console-hostname H] [--budget N]
            [--zone AZ] [--public-data true|false] [--env KEY=VALUE ...]
morca aws push [--tag T]
morca aws register [--tag T] [--config F|--no-config] [register flags] <pipeline> [...]
morca aws app <name> [--host H] [--port 8080] [--tag T]
morca aws status
morca aws replace-control [--plan] [--wait] ... | --resume | --rollback
morca aws down --yes
(each takes --stack NAME, default orca, and --region R)
```

## What the stack is

One CloudFormation stack: a VPC with one public subnet (Availability Zone
`us-east-2a` unless `--zone` names one of the project's Region), a
**control instance** (`t4g.large`, an Elastic IP) running Postgres, the
scheduler, the UI, Caddy and the apps, morca's private bucket for logs and
run state, the lake's bucket `mlake-public-<account>` (private unless
`--public-data true`), an ECR repository, instance roles, and a Budgets
alert to `--email` (`--budget`, 100 dollars a month). Each pipeline run
gets on-demand instances of its own (`MORCA_RUNNER=ec2`), terminated when
it ends, unless morca.toml places it `control`.

Confirm the project's Region first, in AWS Settings > View all projects >
Overview > Additional Info > Region, and create everything there.

## The steps

1. `./build/morca aws up --email you@example.com --hostname orca.example.com
   --console-hostname app.example.com`
2. `./build/morca aws status` prints `PublicAddress`: an A record for each
   hostname to it, **DNS only** on Cloudflare so Caddy can get
   certificates. `SslipUrl` works at once.
3. Commit, then `./build/morca aws push`: builds `bin/` and `pipelines/`
   for linux-aarch64 on morca's runtime image and pushes it, tagged with the
   commit.
4. `./build/morca aws register days`: moves the scheduler and UI to the
   new image and registers each pipeline with the project's morca.toml.
5. `./build/morca aws app console`: serves `bin/console` at the console
   hostname.

Secrets go in Secrets Manager under `orca/` (`aws secretsmanager
create-secret --name orca/github-token ...`) and in morca.toml's
`secrets`. Commands the stack has no `aws` subcommand for run on the
control instance through Session Manager: `sudo docker exec scheduler
morca user add alice`. Tear down with `morca aws down --yes`, after
emptying the buckets.

## Cost and limits

- The control instance, its volume and its address cost money all month.
- Runs use on-demand instances; some plans deny Spot. `workers =
  "0..N"` keeps an idle run at no workers.
- Fills need `fill_budget_per_day`: runs cost money here, so morca won't
  fill without a budget, and the watchdog's cost rule pauses everything
  past `MORCA_WATCHDOG_COST_DAY`.
- If things that worked start failing with Access Denied, ask whether a
  spend limit paused the project (AWS Settings > Billing).
