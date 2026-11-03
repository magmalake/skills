# magmalake skills

Agent skills for building data pipelines in Mojo on
[magmalake](https://magmalake.org): reading Iceberg and Parquet tables, and
filtering, grouping and aggregating their rows, with
[dista](https://github.com/magmalake/dista.mojo) and the magmalake libraries.
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
`npx skills add magmalake/skills --skill mojo-data-performance`.

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

## Adding a skill

Each skill is a folder at the top level with a `SKILL.md`, and longer
material in its `references/` folder. Keep `SKILL.md` under about 500 lines:
agents load it whole when the skill applies, and load the references only
when they need them. List the new folder in
`.claude-plugin/marketplace.json`. CI checks both with the spec's validator,
`agentskills validate <folder>` from `pip install skills-ref`.

## License

Apache-2.0, like the rest of magmalake.
