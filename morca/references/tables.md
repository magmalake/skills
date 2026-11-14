# `morca.tables` in full

Iceberg from steps, as `src/morca/tables.mojo` has it in morca-mojo 0.30.0.
Since 0.29.0 the work runs in **liblake**, a shared library the morca
package ships in `lib/` (`/opt/morca/lib` in an image), loaded on first use
(`LAKE_LIB` names another). A pipeline's build compiles neither iceberg nor
parquet. Batches are still `parquet.RecordBatch` (arrow-mlake's), and the
types below mirror iceberg's under iceberg's names, so code reads the same.
`-D MORCA_LAKE_SOURCE=1` builds the old way, with iceberg compiled in.

## Finding tables

| Function | What it gives |
|---|---|
| `table(ctx, "ns.t")` | a dataset the step declared in `reads=`, `writes=` or `replaces=` |
| `ensure_table(ctx, "ns.t", schema, spec, properties)` | a dataset it writes, created if missing; columns the schema has and the table lacks are added |
| `load_dataset("ns.t")` | a dataset with no `Ctx` (in a dista task or an app): nothing checked or recorded |
| `table_exists("ns.t")` | whether the warehouse has it |
| `Table.load(location)` | a table by location, outside the warehouse |

`t.metadata` is a copy of the table's metadata taken when it loaded,
refreshed or committed: `location`, `current_snapshot_id`, `snapshots`
(each with its `summary`), `properties`, `schema()` and `spec()`.
`t.refresh()` takes the latest. Snapshot summaries carry morca's keys under
the old name, `orca.pipeline`, `orca.run`, `orca.step`, `orca.partition`
and so on, which is how a retry recognises its commits.

`ensure_table` refuses what it can't do in place: a column whose type
changed, a new required column, or a field id that isn't the one the table
gave that column. Give a new column the next id up.

## Schemas, specs and batches

`Schema` and `PartitionSpec` are Iceberg JSON (`Schema.parse(text)`). The
library reads a schema's fields for you: `schema.fields()` (every field,
nested ones after their parent, dotted names such as `tags.element`),
`columns()` (top level only), `find_by_name(name)`, `find_field(id)`,
`has_field(id)` and `has_field_named(name)`, each a `SchemaField` with
`id`, `name`, `type` (as JSON), `required` and `parent_id`.
`PartitionSpec(spec_id, [PartitionField.single(source_id, field_id, name,
parse_transform("day"))])` builds a spec; field ids start at 1000.

**`BatchBuilder(schema)`** builds a batch a value at a time: find a column
once with `b.col("name")` (dotted for nested fields), then per row
`add_int` (also dates in days and timestamps in the column's unit),
`add_float`, `add_bool`, `add_str`, `add_bytes` or `add_null`; after a
list's elements `end_list(col)`, after a struct's fields `end_struct(col)`.
`b^.build()` returns the batch and checks every column has the same rows;
`b.batch()` copies it so far. Reading a column back: `strings_of(batch,
"name")` and `ints_of(batch, "name")` (each a list of `Optional`), or
`batch.column_i64(i)` for the raw values. `concat_batches(list)` joins
batches into one.

## Reading

`scan(ctx, t)` is a `TableScan` pinned at the snapshot the step's first
attempt read, so a retry or resume sees the same data. Each method returns
a new scan: `select(["a", "b"])`, `filter(dsl)` (iceberg's JSON
expression, such as `'[">=","created_at",1704067200000000]'`),
`use_snapshot(id)`. Then:

- `to_batches()`: every row, as a list.
- `to_batch_reader()`: a `BatchReader` whose `next_batch()` returns an
  `Optional[RecordBatch]`, a file's batches at a time, for folding a big
  table without holding it.
- `plan_files()`: the `FileScanTask`s, each with its `data_file`
  (`file_path`, `record_count`, `file_size_in_bytes`, and `partition`, one
  `PartitionValue` per partition field: `.i` for integers, dates and a
  `day`/`month`/`hour`/`bucket` transform's number, `.s` for strings,
  `.valid` False for a null). `to_batches_for_tasks(tasks)` reads some of
  them.
- `count()`: the rows, without reading them.

A table that had no snapshot when the step first read it stays empty for
that step, even if another writer commits meanwhile.

## Writing

- **`append(ctx, t, batches)`** commits a list of batches as one snapshot
  and returns its id. A retry finds the snapshot its attempt already made
  and returns it instead of committing again.
- **Several commits in one step** (an hour at a time) are numbered, and a
  retry recognises its earlier attempt's in the same order.
  `find_commit(ctx, t)` is the snapshot of the step's *next* commit if an
  earlier attempt made it, else -1: check it before doing expensive work
  for that commit, and step past a done one with an empty `append` (or
  `append_files(ctx, t, DataFiles())`), which returns the old snapshot.
  A run with no database never finds earlier commits.
- **Files written elsewhere:** in a dista task (no `Ctx`),
  `write_files(load_dataset(ds), batches)` writes uncommitted data files
  and returns `DataFiles`, a dista `Codec`. Or use a `FileWriter(table)`:
  `write(batches)` writes a file per partition per call, and
  `write_row_group(batch^)` adds rows to each partition's open file, so a
  big file is written a slice at a time; `w^.finish()` returns the
  entries. `w.options` (`codec`, `row_group_rows`, `compression_level`,
  `num_workers`, ...) can change before writing. The step then commits with
  `append_files(ctx, t, files)`, under the same `writes=` check and retry
  rule.
- **Replacing:** to a dataset in `replaces=`, `append` and `append_files`
  make the run's first commit to each partition an `overwrite` that
  removes what earlier runs left there. `replace_partitions` and
  `replace_partition_files` do the same for one call, to any table. Two
  runs replacing one partition at once: one fails, committing nothing.
- **`commit(ctx, t, "replace", changes)`** commits a `FileChanges`
  (`added_data`, `added_deletes`, `removed` paths), as a compaction does
  (`examples/taxi/taxi.mojo`'s `compact`). It refuses to remove a file
  that isn't in the current snapshot. Check `find_commit` first so a retry
  doesn't write the replacement files again.

## A worked example

Each day's step sends four dista tasks that write a partition's files with
a `FileWriter`, a row group at a time, and commits them in one snapshot;
the gather step reads the partition values back. It builds and runs as
is (`./build/x run --partitions 2024-01-01..2024-01-02` with
`MORCA_WAREHOUSE` set).

```mojo compile
from dista import ObjectRef, Seq, Task2
from parquet import RecordBatch

from morca import Ctx, Daily, Day, Gather, Mapped, Pipeline, Step, Tasks, morca_main
from morca.tables import (
    BatchBuilder,
    DataFiles,
    FileWriter,
    PartitionField,
    PartitionSpec,
    Schema,
    append_files,
    ensure_table,
    find_commit,
    load_dataset,
    parse_transform,
    scan,
    table,
)

comptime EVENTS = "demo.tagged"
comptime SCHEMA = (
    '{"type":"struct","schema-id":0,"fields":['
    '{"id":1,"name":"day","required":true,"type":"string"},'
    '{"id":2,"name":"id","required":true,"type":"long"},'
    '{"id":3,"name":"tags","required":false,"type":{"type":"list",'
    '"element-id":4,"element":"string","element-required":true}}]}'
)


def by_day() -> PartitionSpec:
    return PartitionSpec(
        0, [PartitionField.single(1, 1000, "day", parse_transform("identity"))]
    )


def write_part(day: String, part: Int) raises -> DataFiles:
    """On a worker: no Ctx, so the table comes from load_dataset."""
    var t = load_dataset(EVENTS)
    var w = FileWriter(t)
    for group in range(2):
        var b = BatchBuilder(t.metadata.schema())
        var c_day = b.col("day")
        var c_id = b.col("id")
        var c_tags = b.col("tags")
        var c_tag = b.col("tags.element")
        for i in range(100):
            b.add_str(c_day, day)
            b.add_int(c_id, Int64(part * 1000 + group * 100 + i))
            if i % 10 == 0:
                b.add_null(c_tags)
            else:
                b.add_str(c_tag, String("part-", part))
                b.end_list(c_tags)
        w.write_row_group(b^.build())   # one file per partition, two row groups
    return w^.finish()


comptime WritePart = Task2[String, Int, DataFiles, write_part]
comptime WriteTasks = Tasks[WritePart]


def load(mut ctx: Ctx, d: Day) raises -> Int64:
    var t = ensure_table(ctx, EVENTS, Schema.parse(SCHEMA), by_day())
    if find_commit(ctx, t) >= 0:        # a retry: the files are committed
        return append_files(ctx, t, DataFiles())
    ref rt = ctx.runtime[WriteTasks]()
    var refs = List[ObjectRef[DataFiles]]()
    for part in range(4):
        refs.append(rt.remote[WritePart](String(d), part))
    var files = DataFiles()
    for r in refs:
        files.add(rt.take(r))
    ctx.log(String(len(files), " files"))
    return append_files(ctx, t, files)


def report(mut ctx: Ctx, snapshots: Seq[Int64]) raises -> Int:
    var t = table(ctx, EVENTS)
    var schema = t.metadata.schema()
    ctx.log(String("tags is field ", schema.find_by_name("tags").id))
    var rows = 0
    for task in scan(ctx, t).plan_files():
        ref f = task.data_file
        ctx.log(String(f.partition[0].s, ": ", f.record_count, " rows"))
        rows += Int(f.record_count)
    return rows


comptime Load = Step["load", Day, Int64, load, Mapped, replaces=[EVENTS]]
comptime Report = Step[
    "report", Seq[Int64], Int, report, Gather, after=Load, reads=[EVENTS]
]
comptime Tagged = Pipeline[
    "tagged",
    Load,
    Report,
    tasks=WriteTasks,
    partitions=Daily["2024-01-01"],
]


def main() raises:
    morca_main[Tagged]()
```

## Table history

The scheduler expires old snapshots and metadata by default
(`history.expire.max-snapshot-age-ms`, settable through `ensure_table`'s
`properties`). `morca expire <dataset>|--all [--older-than 30d] [--keep N]
[--dry-run]` does it by hand, through the `morca_maintain` binary the
runtime image ships.
