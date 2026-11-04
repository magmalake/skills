# The lake: tables, writes and reads

The tables live in an Iceberg **filesystem catalog**: a warehouse root, and
under it `<namespace>/<table>/metadata` and `.../data`. orca names a table by
its dataset, `namespace.table`. Nothing else (no REST catalog, no Glue) is
involved, so any reader that can list the bucket can open a table.

## src/lake.mojo, the shared definitions

Pipelines and apps both import this, so they agree on names and schemas.
Build every program with `-I src` (the image's build does).

```mojo
"""The lake's tables, shared by pipelines and apps."""

from std.os import getenv

from iceberg.catalog.filesystem import FilesystemCatalog, Table
from iceberg.io import FileIO
from iceberg.schema import Schema

comptime DAYS = "demo.days"

comptime DAYS_SCHEMA = (
    '{"schema-id":0,"type":"struct","fields":['
    '{"id":1,"name":"day","required":true,"type":"string"},'
    '{"id":2,"name":"events","required":true,"type":"long"}]}'
)


def days_schema() raises -> Schema:
    return Schema.parse(DAYS_SCHEMA)


def warehouse() -> String:
    """The lake: ORCA_WAREHOUSE (on AWS, the public bucket), else a directory."""
    return getenv("ORCA_WAREHOUSE", "build/warehouse")


def load(namespace: String, name: String) raises -> Table:
    var root = warehouse()
    if "://" in root and not root.startswith("file://"):
        return FilesystemCatalog(String(root.rstrip("/")), FileIO()).load_table(
            namespace, name
        )
    return FilesystemCatalog.local(root).load_table(namespace, name)
```

`load` does what `orca.tables` does internally: a local directory through
`FilesystemCatalog.local`, an `s3://` root through `FileIO()`, which takes
credentials from the `AWS_*` environment, else the EC2 instance role, else
reads unsigned. Outside a step, `orca.tables.load_dataset("demo.days")`
also loads a dataset from `ORCA_WAREHOUSE`, without checks or records, but
raises when the variable isn't set; `load` falls back to `build/warehouse`.

## Schemas and partitions

A schema is Iceberg's JSON, with a field id for each column. Types used in
magmalake code: `int`, `long`, `double`, `string`, `timestamp`,
`timestamptz`, `date`, `boolean`, and nested `struct`, `list` and `map`.

A partitioned table, from `examples/taxi/tlc.mojo` in
[orca's repository](https://github.com/magmalake/orca.mojo) (month of
field 2):

```mojo
from iceberg.transforms import PartitionField, PartitionSpec, parse_transform

def spec() raises -> PartitionSpec:
    return PartitionSpec(
        0,
        [PartitionField.single(2, 1000, "pickup_month", parse_transform("month"))],
    )
```

Pass it as `ensure_table(ctx, dataset, schema, spec())`. The arguments of
`PartitionField.single` are the source field id, the partition field id
(1000 and up), its name and the transform (`identity`, `hour`, `day`, `month`,
`year`, `bucket[N]`, `truncate[W]`).

## Writing from a step

- **Declare** the dataset in the `Step`: `writes=[DAYS]` to write it,
  `reads=[DAYS]` to read it. `orca.tables` refuses anything else, so the
  scheduler's lineage and sensors stay true.
- **`ensure_table(ctx, dataset, schema, spec)`** returns the table, creating
  it empty the first time; two runs creating it at once is handled.
- **`append(ctx, table, batches)`** writes `List[RecordBatch]` as one
  snapshot and returns its id. A retry of the step finds the snapshot its
  earlier attempt committed and returns it instead of writing again.
- **`commit(ctx, table, operation, files)`** commits data files you wrote
  yourself, such as a compaction's `OP_REPLACE` (see `compact` in orca's
  `examples/taxi/taxi.mojo`). Call `find_commit(ctx, table)`
  first so a retry doesn't write the files again.
- **`scan(ctx, table)`** reads a declared input at a pinned snapshot: a
  retry or a resume sees the same data as the first attempt.
- **`table(ctx, dataset)`** loads a dataset the step declared.

Rows from Mojo values go through `iceberg.batch`:

```mojo
from iceberg.batch import ColumnBuilder, batch_of
from iceberg.values import Datum

    var day = ColumnBuilder.of(schema, 1)     # by field id
    var events = ColumnBuilder.of(schema, 2)
    day.add(Datum.string_("2024-01-01"))
    events.add(Datum.long_(10))
    # events.add_null() for a missing value in an optional column
    var batch = batch_of([day^, events^])
```

A `timestamptz` value is microseconds since the epoch, as a `Datum` of
that kind (`P_TIMESTAMP` for a `timestamp` without zone):

```mojo
from iceberg.types import P_TIMESTAMPTZ

    at.add(Datum.integral(P_TIMESTAMPTZ, micros))   # micros: Int64
```

Nested columns take one JSON value per row, from
[iceberg.mojo's README](https://github.com/magmalake/iceberg.mojo):

```mojo
from iceberg.batch import ColumnBuilder, NestedBuilder, batch_of_columns

    var ids = ColumnBuilder.of(schema, 1)
    var addr = NestedBuilder.of(schema, 2)      # struct<city, zip>
    ids.add(Datum.long_(1))
    addr.add('{"city":"eu","zip":10}')
    var batch = batch_of_columns([ids.build_tree(), addr.build()])
```

Rows already in Parquet come as batches from
`ParquetReader[AllCodecs].open(path)` (`has_next()`, `read_batch()`), as in
orca's `examples/taxi/tlc.mojo`; append them as they are. Iceberg's writer
casts each column to the table's type. A `RecordBatch`'s row count is the
field `b.num_rows`.

## Gzip input

`deflate-mojo` is raw DEFLATE (RFC 1951) only, and no tin reads gzip's
framing. Read the RFC 1952 header yourself, inflate the body with
`inflate_at`, which reports where the stream ended, and skip the 8-byte
trailer; repeat for concatenated members (GH Archive's files have them).
It decompressed 690 MB of JSON in 0.8 s on an M-series Mac:

```mojo
from deflate import inflate_at

def gunzip(data: Span[UInt8, _]) raises -> List[UInt8]:
    var out = List[UInt8]()
    var pos = 0
    var n = len(data)
    while pos + 18 <= n:
        if data[pos] != 0x1F or data[pos + 1] != 0x8B:
            if len(out) > 0:
                break  # trailing garbage after a complete member
            raise Error("gunzip: not a gzip stream")
        if data[pos + 2] != 8:
            raise Error("gunzip: unknown compression method")
        var flg = Int(data[pos + 3])
        var i = pos + 10
        if flg & 4:  # FEXTRA
            var xlen = Int(data[i]) | (Int(data[i + 1]) << 8)
            i += 2 + xlen
        if flg & 8:  # FNAME
            while i < n and data[i] != 0:
                i += 1
            i += 1
        if flg & 16:  # FCOMMENT
            while i < n and data[i] != 0:
                i += 1
            i += 1
        if flg & 2:  # FHCRC
            i += 2
        var used = 0
        var part = inflate_at(data[i:], used)
        if len(out) == 0:
            out = part^
        else:
            out.extend(Span(part))
        pos = i + used + 8  # CRC32 and ISIZE
    return out^
```

It doesn't check the CRC32.

## Reading in an app

```mojo
var t = load("demo", "days")
var rows = (
    t.scan()
    .filter('[">=","day","2024-01-02"]')
    .select(["day", "events"])
    .to_table()
)
```

The result (`ScanResult`) has `num_rows()` (a method, unlike
`RecordBatch.num_rows`), `num_columns()`, `name(i)`,
`value(row, col)` (a `Datum`; `.repr_()` prints it, `.valid` is false for
null), `cell(row, col)` (one value as JSON), `to_json()` and `to_csv()`.
`to_batches()` returns `List[RecordBatch]` instead, for large tables.
`t.scan().use_snapshot(id)` reads an older snapshot, and `t.refresh()`
reloads the table's current metadata.

Filters are JSON S-expressions: `["=", "col", v]`, `["<", ...]`,
`["in", "col", [...]]`, `["is-null", "col"]`, `["starts-with", "col", "p"]`,
`["and", f, g]`, `["or", f, g]`, `["not", f]`. Literals are typed by the
column. [iceberg.mojo's README](https://github.com/magmalake/iceberg.mojo)
has the whole grammar.

## Readers outside the project

The public bucket allows `s3:GetObject` and `s3:ListBucket` to anyone, and
the project's resource control policy refuses other accounts' signed
requests, so outside readers read **unsigned**:

```sh
aws s3 ls --no-sign-request s3://mlake-public-<account>/warehouse/demo/days/metadata/
```

DuckDB and pyiceberg read it in their anonymous modes, from the table's
newest `metadata/*.metadata.json`. The bucket allows CORS `GET` from any
origin, so a browser page (DuckDB-Wasm) can read it too. Downloads are
billed to the project.
