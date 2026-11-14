# The lake: tables, writes and reads

The tables live in an Iceberg **filesystem catalog**: a warehouse root
(`MORCA_WAREHOUSE`), and under it `<namespace>/<table>/metadata` and
`.../data`. morca names a table by its dataset, `namespace.table`. There is
no REST catalog or Glue, so any reader that can list the bucket can open a
table. Everything here goes through `morca.tables` (morca 0.30.0), whose
work runs in the liblake library the morca package ships; the morca skill's
`references/tables.md` has its whole API.

## Schemas and partitions

A schema is Iceberg's JSON, with a field id for each column. Types used in
magmalake code: `int`, `long`, `double`, `string`, `boolean`, `date`,
`timestamp`, `timestamptz`, and nested `struct`, `list` and `map`. A
partition spec names a source field id, a partition field id (1000 and
up), a name and a transform (`identity`, `hour`, `day`, `month`, `year`,
`bucket[N]`, `truncate[W]`):

```mojo compile
from morca.tables import BatchBuilder, PartitionField, PartitionSpec, Schema, parse_transform
from parquet import RecordBatch

comptime VISITS_SCHEMA = (
    '{"schema-id":0,"type":"struct","fields":['
    '{"id":1,"name":"id","required":true,"type":"long"},'
    '{"id":2,"name":"at","required":true,"type":"timestamptz"},'
    '{"id":3,"name":"addr","required":false,"type":{"type":"struct","fields":['
    '{"id":4,"name":"city","required":true,"type":"string"},'
    '{"id":5,"name":"zip","required":false,"type":"int"}]}}]}'
)


def visits_spec() -> PartitionSpec:
    """Partitioned by the day of `at` (field 2)."""
    return PartitionSpec(
        0, [PartitionField.single(2, 1000, "at_day", parse_transform("day"))]
    )


def visits(micros: Int64) raises -> RecordBatch:
    var b = BatchBuilder(Schema.parse(VISITS_SCHEMA))
    var id = b.col("id")
    var at = b.col("at")
    var addr = b.col("addr")
    var city = b.col("addr.city")
    var zip = b.col("addr.zip")
    for i in range(3):
        b.add_int(id, Int64(i))
        b.add_int(at, micros + Int64(i) * 3_600_000_000)   # microseconds
        if i == 2:
            b.add_null(addr)            # a null struct: its fields get one too
        else:
            b.add_str(city, "Berlin")
            b.add_null(zip)
            b.end_struct(addr)
    return b^.build()


def main() raises:
    var batch = visits(1_704_067_200_000_000)   # 2024-01-01T00:00Z
    print(batch.num_rows, "rows")               # num_rows is a field
```

Pass the spec as `ensure_table(ctx, dataset, schema, spec)`. A
`timestamptz` or `timestamp` value is microseconds since the epoch, a
`date` days since it; all go in with `add_int`. A list column takes its
elements on `<col>.element` and then `end_list(col)`; a map's are
`<col>.key` and `<col>.value`.

## Writing from a step

- **Declare** the dataset in the `Step`: `writes=[DAYS]` to write it,
  `reads=[DAYS]` to read it, `replaces=[DAYS]` when a rerun should replace
  a partition's rows. `morca.tables` refuses anything else, so the
  scheduler's lineage and sensors stay true.
- **`ensure_table(ctx, dataset, schema, spec)`** returns the table,
  creating it the first time, and adds new columns the schema has.
- **`append(ctx, table, batches)`** commits `List[RecordBatch]` as one
  snapshot and returns its id; a retry returns the snapshot its earlier
  attempt committed instead of writing again.
- **Rows already in Parquet** come as batches from a reader; append them as
  they are, and the writer casts each column to the table's type.
  `RecordBatch.num_rows` is a field, not a method.

## Reading in an app

An app has no `Ctx`, so it loads a dataset with `load_dataset("demo.days")`
from `MORCA_WAREHOUSE` (which raises when the variable isn't set) and scans
it at the current snapshot:

```mojo
var batches = (
    load_dataset(DAYS)
    .scan()
    .filter('[">=","day","2024-01-02"]')
    .select(["day", "events"])
    .to_batches()
)
for b in batches:
    var day = strings_of(b, "day")      # List[Optional[String]]
    var events = ints_of(b, "events")   # List[Optional[Int64]]
```

`to_batch_reader()` reads a batch at a time instead, `count()` counts the
rows, and `use_snapshot(id)` reads an older snapshot. Filters are JSON
S-expressions: `["=", "col", v]`, `["<", ...]`, `["in", "col", [...]]`,
`["is-null", "col"]`, `["starts-with", "col", "p"]`, `["and", f, g]`,
`["or", f, g]`, `["not", f]`; literals take the column's type.
[iceberg.mojo's README](https://github.com/magmalake/iceberg.mojo) has the
whole grammar.

## Gzip input

`deflate-mojo` is raw DEFLATE (RFC 1951) only, and no tin reads gzip's
framing. Read the RFC 1952 header yourself, inflate the body with
`inflate_at`, which reports where the stream ended, and skip the 8-byte
trailer; repeat for concatenated members (GH Archive's files have them).
It decompressed 690 MB of JSON in 0.8 s on an M-series Mac. `pixi shelf
add deflate-mojo` if the project doesn't have it yet:

```mojo compile
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

## Readers outside the project

A table is plain files in the bucket, so DuckDB, pyiceberg or Spark read
it from its newest `metadata/*.metadata.json` with the bucket's S3
credentials and endpoint (Hetzner Object Storage is
`https://<location>.your-objectstorage.com`). A bucket made public for
reading lets them read unsigned, and downloads are then billed to the
project; to share a lake that's read a lot, mirror it to a store with free
egress that speaks the S3 API (such as Cloudflare R2). A dataset's Data
tab in morca's UI is public unless `[datasets."ns.t"] public = false`.
