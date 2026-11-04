# magmalake skills

Agent skills for building data pipelines and apps in Mojo on
[magmalake](https://magmalake.org): orchestrating them with
[orca](https://github.com/magmalake/orca.mojo), distributing them with
[dista](https://github.com/magmalake/dista.mojo), reading and writing Iceberg
and Parquet tables fast, and deploying a project to AWS.
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
`npx skills add magmalake/skills --skill orca`.

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

- [`orca`](orca/SKILL.md): pipelines in Mojo with orca: steps, partitions
  by month, day or name (keyed), schedules and sensors, steps that call
  dista, Iceberg tables with pinned reads and commits a retry recognises,
  and the `orca` command: registering, backfilling, the scheduler, the UI,
  the runners, autoscaling and secrets.
- [`dista`](dista/SKILL.md): distributed Mojo with dista: tasks and the
  refs they return, actors and checkpoints, local and cluster mode, the
  `dista` command, draining and autoscaling.
- [`magmalake-apps`](magmalake-apps/SKILL.md): a whole project on
  magmalake: its layout, a lake on Iceberg over S3, a flare app reading it,
  developing locally, and deploying to AWS with `orca aws`.

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

## License

Apache-2.0, like the rest of magmalake. The copies in `vendor/` keep their
own licenses, in each folder's `LICENSE`.
