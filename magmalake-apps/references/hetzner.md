# A project on one Hetzner server

The short form of morca's `docs/hetzner.md` (morca 0.30.0). The scripts
come from the release asset `morca-hetzner-<version>.tar.gz` (or
`deploy/hetzner` of a morca checkout) and act on the stack `HETZNER_ENV`
names; each says which stack it acts on before it does anything.

```
laptop: hcloud + ssh + up.sh / deploy.sh / promote.sh
   |
firewall: ssh from ADMIN_IP only       Hetzner Cloud server (CAX21, Ubuntu)
                                       docker network "morca":
Cloudflare --- tunnel ---------------> cloudflared -> ui:8270, console:8080
                                       postgres (data on a Volume), scheduler,
                                       ui, sampler, the apps
                                          |  S3 API
                                       Object Storage: warehouse/, logs/, backups/pg/
```

Runs are local processes on the server (`MORCA_RUNNER=local`), each in a
cgroup sized to its grant, or on worker pools the scheduler grows. The
price is fixed by the month; nothing is launched on demand.

## Before the first `up.sh`, once

1. **Hetzner Cloud:** a project and an API token (read and write), then
   `hcloud context create <name>` on the laptop.
2. **Object Storage:** a bucket in the server's `LOCATION`, and its S3
   keys.
3. **Cloudflare Zero Trust:** a tunnel, its token, and a public hostname
   per service (`orca.example.com` to `http://ui:8270`, `example.com` to
   `http://console:8080`), **Proxied**. Delete an existing A record for a
   name first; the tunnel runs over HTTP/2, since QUIC timed out on Hetzner.
4. **The laptop:** `hcloud`, `ssh`, `jq` and a POSIX `sh`; and your
   address for the firewall (`curl -4 ifconfig.me`, plus `/32`).
5. **The image:** the project's, on GHCR, built by its CI (the SKILL's
   "The image"). A private package needs `GHCR_USER` and `GHCR_TOKEN_FILE`.

## hetzner.env

Copy `hetzner.env.example`, which explains each line, to
`~/.config/morca-hetzner/<stack>.env`, outside any repository, and
`export HETZNER_ENV=` it. Relative file names in it are read from its
folder, and a laptop's own `ADMIN_IP` and ssh key can go in
`~/.config/morca-hetzner/<NAME>.local.env`.

| Key | What it is |
|---|---|
| `NAME` | prefixes every Hetzner resource; `orca` for the first stack, kept from before the rename |
| `HCLOUD_CONTEXT` | the hcloud context, for a stack in a project of its own |
| `LOCATION`, `SERVER_TYPE_SMALL`, `SERVER_TYPE_BIG`, `VOLUME_SIZE_GB` | where and how big |
| `ADMIN_IP`, `SSH_PUBLIC_KEY_FILE` | who may ssh in |
| `BUCKET`, `S3_ENDPOINT`, `S3_REGION`, `S3_CREDENTIALS_FILE` | the warehouse's bucket |
| `MORCA_IMAGE`, `MORCA_TAG` | the project's image; the tag only the first `up.sh` starts |
| `APPS` | the apps it runs, from `morca.toml`'s `[apps.*]` |
| `SECRETS_FILE` | one SOPS file holding every secret, decrypted once per deploy (Touch ID once with age-plugin-se) |
| `CONFIG_OVERLAY_FILE` | this stack's overlay on `morca.toml` |
| `DRAIN_SECONDS` | required: how long an old scheduler may drain its runs after a deploy |
| `UI_URL`, `TUNNEL_TOKEN_FILE`, `CLOUDFLARED_IMAGE` | the tunnel |
| `NTFY_TOPIC_FILE`, `NTFY_URL` | alerts to a phone through ntfy |
| `EXTRA_ENV_FILE` | more lines for the server's `settings.env` (a hand edit there is undone) |
| `CANARY`, `CANARY_TIMEOUT` | runs the check after each deploy starts, for a beta stack |
| `BACKUP_KEEP`, `FIXED_COST`, `POSTHOG_KEY` | backups kept, the cost tile's text, analytics |

Secrets reach runs through `MORCA_SECRET_FETCH`, which reads
`/etc/orca/secrets/<id>` on the server: a pipeline's `secrets = {
GITHUB_TOKEN = "orca/github-token" }` gets the SOPS file's
`secrets/orca/github-token` key. A `*_FILE` named `*.sops` is decrypted on
its way to the server, never to a file on the laptop.

## The first setup

```sh
./up.sh --dry-run          # every hcloud and ssh command, none run
./up.sh                    # key, firewall, Volume (delete-protected), server, settings, containers
ssh root@<ip> docker exec -it scheduler morca user add <you>   # reads a password
ssh root@<ip> morca-stack register
```

`up.sh` is safe to run again: it fixes drift and starts again only the
containers whose settings changed, and won't restart a scheduler with runs
going unless given `--force`.

## A deploy, step by step

`./deploy.sh <tag> [app=<tag> ...] [--all] [--yes] [--confirmed id,...]`:

1. Checks `DRAIN_SECONDS`, then syncs the server's settings and secrets
   from `hetzner.env` (printing the names that changed, never values).
2. Reads the new image's `before` upgrade steps and asks for each, or runs
   them with `--yes`; an unconfirmed manual step stops the deploy with
   nothing changed. The new image's morca checks `morca.toml` with the
   overlay before anything is paused or replaced.
3. Backs Postgres up to the bucket (`backups/pg/`).
4. Plans and applies: only containers whose image, env file or flags
   changed are replaced; the UI and apps side by side, so they don't go
   down. A new scheduler **overlaps** the old one: the old one keeps
   draining its runs (for at most `DRAIN_SECONDS`, and runs that check
   `outdated()` end early) while the new one leads.
5. Registers the pipelines from the new image, runs the `after` upgrade
   steps, and unpauses what it paused.
6. Ends with `morca-stack check`: Postgres, every container on the right
   image, exactly one leading scheduler, every registered pipeline's binary
   in the image (`morca retire` one the image dropped), and the `CANARY`
   runs. A failed check fails the deploy and undoes nothing.

`./deploy.sh --rollback` goes back to the tags deployed before and
registers again; run it twice to go forward. The rollback puts back tags,
not settings: to undo a setting, change it back in `hetzner.env` and
deploy. Settings and secrets change only through `hetzner.env`.

## Upgrade steps

A release that needs something around its deploy says so in the image's
`/opt/morca/upgrade.toml` (`MORCA_UPGRADE`): `[[version]]` tables, each with
`[[version.step]]`s of an `id`, `when` (`before` or `after`, the default),
and either `run`, one `morca` command, or `manual`, text for a person.
`morca upgrade --from <deployed> --to <new>` prints the plan. A deploy
from 1.1.0 to 1.3.0 takes 1.2.0's and 1.3.0's steps; one command several
versions ask for runs once. Nothing is recorded, so deploying the same tag
again runs none. A big re-run belongs in a step as
`morca backfill --rerun --as-fill <pipeline> KEYS`, which the scheduler
works through under the pipeline's fill limits.

## A beta stack and promotion

A second `hetzner.env` makes a second stack: `NAME=orca-dev`, a small
server (`cax11`), its own bucket, tunnel, overlay (no fills) and `CANARY`
runs, and no alerts or analytics. Release check on it: `./deploy.sh
<tag>` ends `check: all ok`, then a manual run per pipeline, a deploy with
a run in flight, and a rollback and forward again. Then, with
production's settings:

```sh
export HETZNER_ENV=~/.config/morca-hetzner/hetzner.env
./promote.sh --from ~/.config/morca-hetzner/dev.env --dry-run
./promote.sh --from ~/.config/morca-hetzner/dev.env
```

`promote.sh` reads beta's last check (`morca-stack checked`), refuses
unless it passed on the tags beta runs now, and deploys exactly those, so
what ships is what was tested. `./down.sh` stops a stack's server between
releases and keeps its Volume.

## On the server

```sh
ssh root@<ip> morca-stack status          # containers, deployed and previous tags
ssh root@<ip> morca-stack check           # the post-deploy check, by hand
ssh root@<ip> docker exec scheduler morca status
ssh root@<ip> docker logs --tail 100 scheduler
```

`rescale.sh up|down` moves the server between `SERVER_TYPE_SMALL` and
`SERVER_TYPE_BIG`, for an import; pipelines with `cpus` and `memory_gb`
requests and `"auto"` values use the bigger grant with no change.
