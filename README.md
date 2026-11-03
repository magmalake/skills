# magmalake skills

Agent skills for building data pipelines and apps in Mojo on
[magmalake](https://magmalake.org): orchestrating them with
[orca](https://github.com/magmalake/orca.mojo), distributing them with
[dista](https://github.com/magmalake/dista.mojo), reading and writing Iceberg
and Parquet tables fast, and deploying a project to AWS.
They follow the [Agent Skills spec](https://agentskills.io/specification), so
any coding agent that reads skills can use them.

They pair with [Modular's skills](https://github.com/modular/skills),
especially `mojo-syntax`, which keeps an agent writing current Mojo. Install
both.

## Install

### Claude Code

```text
/plugin marketplace add magmalake/skills
/plugin install skills@magmalake
```

### Codex, Cursor and other agents

With [Node.js](https://nodejs.org/en/download) installed:

```bash
npx skills add magmalake/skills
```

This puts the skills where your agent looks for them. Update them later with
`npx skills update`, or install one at a time with
`npx skills add magmalake/skills --skill orca`.

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

For finding and adding the libraries these use, install mojoshelf's own
skills too: `npx skills add mojoshelf/mojoshelf --skill mojoshelf-consume`.

## Adding a skill

Each skill is a folder at the top level with a `SKILL.md`, and longer
material in its `references/` folder. Keep `SKILL.md` under about 500 lines:
agents load it whole when the skill applies, and load the references only
when they need them. List the new folder in
`.claude-plugin/marketplace.json`. CI checks both with the spec's validator,
`agentskills validate <folder>` from `pip install skills-ref`.

## License

Apache-2.0, like the rest of magmalake.
