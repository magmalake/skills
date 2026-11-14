# magmalake skills

Agent skills for building data pipelines and apps in Mojo on
[magmalake](https://magmalake.org): orchestrating them with
[morca](https://github.com/morca-dev/morca), distributing them with
[dista](https://github.com/morca-dev/dista), reading and writing Iceberg
and Parquet tables fast, and deploying a project to a Hetzner server.
They follow the [Agent Skills spec](https://agentskills.io/specification), so
any coding agent that reads skills can use them.

One install brings them all, together with the two skills from elsewhere
that they lean on: Modular's [`mojo-syntax`](https://github.com/modular/skills/tree/main/mojo-syntax),
which keeps an agent writing current Mojo, and mojoshelf's
[`mojoshelf-consume`](https://github.com/mojoshelf/mojoshelf/tree/main/skills/mojoshelf-consume),
which finds the libraries (tins) these skills use and adds them to a
project.

## Install

### Claude Code

```text
/plugin install magmalake --marketplace magmalake/skills
```

That needs Claude Code 2.1.275 or later. On an older one, add the
marketplace first:

```text
/plugin marketplace add magmalake/skills
/plugin install magmalake@magmalake
```

Either way, the `magmalake` plugin installs `mojo-syntax` and
`mojoshelf-consume` with it, as dependencies. Claude Code fetches those two
from their own repositories, so they stay current with Modular and
mojoshelf. If you had installed `skills@magmalake`, the plugin's earlier
name, Claude Code renames it to `magmalake@magmalake`; run the install line
once to fetch the new one.

### Codex, Cursor and other agents

With [Node.js](https://nodejs.org/en/download) installed:

```bash
npx skills add magmalake/skills
```

This puts all six skills where your agent looks for them. Update them later
with `npx skills update`, or install one at a time with
`npx skills add magmalake/skills --skill morca`.

`npx skills` installs only the skills inside one repository, so this one
keeps unchanged copies of `mojo-syntax` and `mojoshelf-consume` in
[`vendor/`](vendor), each with its upstream license. CI checks every week
and on every change that they still match upstream;
`scripts/sync-vendored.sh` updates them.

### By hand

Clone this repository and copy or symlink a skill's folder into your
agent's skills folder; for Claude Code that is `~/.claude/skills/`.

## Skills

- [`mojo-data-performance`](mojo-data-performance/SKILL.md): how to make
  Mojo data code fast, from a pipeline's per-row loops over record batches
  down to the readers, writers and codecs under them; how to find where the
  time goes without a second implementation to compare against; and how to
  take benchmark numbers that survive being checked. Every rule carries the
  measurement behind it, from magmalake's own benchmarks. It replaces
  `writing-performant-data-code`, which is now part of it.

- [`morca`](morca/SKILL.md) (morca-mojo 0.30.0): pipelines in Mojo with
  morca, which was called orca until 0.25.0: steps, partitions by month,
  day, hour or name (keyed), schedules and sensors, steps that call dista
  or a Rust worker, Iceberg tables through `morca.tables` with pinned reads
  and commits a retry recognises, the `morca` command (registering,
  backfilling and fill requests, retiring, status and config), morca.toml
  (requests and grants, fills, priority, overlays) and worker pools. It
  replaces the `orca` skill.
- [`dista`](dista/SKILL.md) (dista-mojo 0.5.1): distributed Mojo with
  dista: tasks and the refs they return, actors and checkpoints, local and
  cluster mode and its fork rules, several tasks per worker sized from a
  morca grant, the `dista` command, draining and autoscaling.
- [`magmalake-apps`](magmalake-apps/SKILL.md): a whole project on
  magmalake: its layout, a lake of Iceberg tables in object storage, a
  flare app reading it, developing locally, its image on morca's runtime,
  and deploying it as a Hetzner stack with `deploy.sh` and `promote.sh`
  (AWS's `morca aws` briefly).

From elsewhere, installed with them:

- [`mojo-syntax`](https://github.com/modular/skills/tree/main/mojo-syntax),
  from [Modular's skills](https://github.com/modular/skills) (Apache-2.0
  with LLVM exceptions): current Mojo syntax and conventions. Modular's
  other Mojo skills, such as GPU programming and Python interop, are in
  their own marketplace: `/plugin install mojo-skills --marketplace modular/skills`.
- [`mojoshelf-consume`](https://github.com/mojoshelf/mojoshelf/tree/main/skills/mojoshelf-consume),
  from [mojoshelf](https://github.com/mojoshelf/mojoshelf) (MIT): finding
  tins on mojoshelf and adding them to a project.

## Adding a skill

Each skill is a folder at the top level with a `SKILL.md`, and longer
material in its `references/` folder. Keep `SKILL.md` under about 500 lines:
agents load it whole when the skill applies, and load the references only
when they need them. List the new folder in the `magmalake` plugin's
`skills` in `.claude-plugin/marketplace.json`, and bump the marketplace's
`metadata.version`. CI checks that the folder is listed, and checks it with
the spec's validator, `agentskills validate <folder>` from
`pip install skills-ref`.

A skill from another repository goes in as a plugin entry of its own in
`.claude-plugin/marketplace.json`, fetched from that repository, and in the
`magmalake` plugin's `dependencies`; for `npx skills`, add it to
`scripts/sync-vendored.sh` and run the script.

## Examples that CI compiles

A fenced block marked `mojo compile` instead of `mojo` is a whole program,
and CI builds it against the morca and dista versions the skills describe,
pinned in [`ci/pixi.toml`](ci/pixi.toml):

````markdown
```mojo compile
from morca import Ctx, Month, Mapped, Step
...
```
````

A block without `def main` gets an empty one. `mojo compile
name=src/lake.mojo` writes the block to that path instead of building it:
a helper module the skill's other blocks import (`-I src` and the skill's
folder are on the path). Plain `mojo` blocks are excerpts and aren't
built. Locally:

```sh
pixi install --manifest-path ci/pixi.toml      # builds morca and dista: many minutes
pixi run --manifest-path ci/pixi.toml python3 -I scripts/compile-examples.py
pixi run --manifest-path ci/pixi.toml python3 -I scripts/compile-examples.py morca/SKILL.md
```

## When morca or dista releases

The skills follow a release of morca-mojo or dista-mojo that changes its
public API (morca's `src/morca/__init__.mojo` and the modules it lists;
anything with a minor-version note in their changelogs):

1. Re-pin `ci/pixi.toml` to the new versions: in `ci/`, `pixi shelf add
   morca-mojo@<version> dista-mojo@<version>`, then commit `pixi.toml` and
   `pixi.lock`.
2. Run the compile check above, and fix the examples it fails on.
3. Read the release's changelog for what the skills describe in prose
   (commands, morca.toml keys, deploy steps), update the version each
   SKILL.md names, and bump `metadata.version` in
   `.claude-plugin/marketplace.json`.

## License

Apache-2.0, like the rest of magmalake. The copies in `vendor/` keep their
own licenses, in each folder's `LICENSE`.
