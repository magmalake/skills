# The per-row loop, before and after

This is the group-by from dista's shuffle benchmark (`deploy/shuffle.mojo` in
magmalake/dista.mojo, PR #58): trips are grouped by pickup zone, dropoff
zone and hour of day, and each group adds up a count and three sums. It ran
over 77M rows of NYC taxi data on one thread. Both versions give the same
answer, bit for bit.

| Version | Time in the loop | Whole run, 1 thread | Whole run, 8 threads |
|---|---:|---:|---:|
| Before | 2,091 ms | 4.6 s | 2.6 s |
| Two passes, one slot per key | 1,405 ms | 3.6 s | 1.9 s |
| ...and raw column pointers | 777 ms | 3.1 s | 1.25 s |

## Before: one branchy loop, four arrays

```mojo
struct Dense(Movable):
    var n: List[Int32]          # one array per number a group adds up
    var speed: List[Float64]
    var tip: List[Float64]
    var fare: List[Float64]

    def add(mut self, b: RecordBatch) raises:
        var pu = b.column_i64(_column(b, "PULocationID"))  # (values, valid)
        var dist = b.column_f64(_column(b, "trip_distance"))
        # ... five more columns ...
        for i in range(b.num_rows):
            if not (pu[1][i] and dist[1][i] and ...):      # nulls
                continue
            var p = Int(pu[0][i])                          # Tuple, then List
            if p < 1 or p > 265 or ...:                    # range checks
                continue
            var key = (p * ZONES + d) * HOURS + hour
            self.n[key] += 1                               # four arrays: up to
            self.speed[key] += miles / hours               # four cache misses
            self.tip[key] += tip[0][i] / dollars
            self.fare[key] += dollars
```

Three things make this slow. Each value is read through a `Tuple` and then a
`List`, which the compiler doesn't turn into a plain load. The branches
depend on the data, so the CPU mispredicts them. And each row updates four
arrays at the same random key, so it can miss the cache four times.

## After: two passes, one slot per key

```mojo
comptime SLOT = 4  # n, speed, tip, fare, side by side

struct Dense(Movable):
    var slots: List[Float64]    # KEYS * SLOT; counts as floats are exact
    var keys: List[Int]         # scratch, reused for every batch
    var weights: List[Float64]  # scratch, three per kept row

    def add(mut self, b: RecordBatch) raises:
        var pu = b.column_i64(_column(b, "PULocationID"))
        # ... the other six columns ...
        var rows = b.num_rows
        self.keys.resize(rows, 0)
        self.weights.resize(rows * 3, 0.0)
        var keys = self.keys.unsafe_ptr()
        var weights = self.weights.unsafe_ptr()
        # One pointer per column, taken once per batch. Take each on its own
        # line: two `unsafe_ptr()` calls on one tuple in one expression are an
        # aliasing error.
        var pu_v = pu[0].unsafe_ptr()
        var pu_ok = pu[1].unsafe_ptr()
        # ... the same for the other columns ...

        # Pass 1: every row, no branches. A row is always written at `kept`,
        # and `kept` only moves past the rows that count, so the others are
        # overwritten by the next row.
        var kept = 0
        for i in range(rows):
            var p = pu_v[unsafe_offset=i]
            # ... read the other values the same way ...
            var keep = (
                pu_ok[unsafe_offset=i] & dist_ok[unsafe_offset=i] & ...
                & (p >= 1) & (p <= 265) & (miles > 0.0) & ...
            )
            keys[unsafe_offset=kept] = (Int(p) * ZONES + Int(d)) * HOURS + hour
            weights[unsafe_offset=kept * 3] = miles / hours
            weights[unsafe_offset=kept * 3 + 1] = tip / dollars
            weights[unsafe_offset=kept * 3 + 2] = dollars
            kept += Int(keep)

        # Pass 2: only the kept rows, into one cache line each.
        var slots = self.slots.unsafe_ptr()
        for j in range(kept):
            var s = slots.unsafe_offset(keys[unsafe_offset=j] * SLOT)
            s[unsafe_offset=0] += 1.0
            s[unsafe_offset=1] += weights[unsafe_offset=j * 3]
            s[unsafe_offset=2] += weights[unsafe_offset=j * 3 + 1]
            s[unsafe_offset=3] += weights[unsafe_offset=j * 3 + 2]
```

Use `&`, not `and`, to combine the conditions: `and` short-circuits, which
brings the branches back. Dividing by a zero fare in pass 1 is harmless:
floats give `inf` instead of trapping, and the row isn't kept.

Splitting the time showed what mattered. In the two-pass version with `List`
indexing, pass 1 took 897 ms and pass 2 only 188 ms. So the scatter into
memory was never the problem; reading values through `Tuple` and `List` was.
Switching pass 1 to raw pointers took the whole loop from 1,405 to 777 ms.

## What this pattern is called elsewhere

Pass 2 is NumPy's `np.bincount(keys, weights=w, minlength=K)`: an array of
`K` zeros, then `out[keys[i]] += w[i]` for every `i`. NumPy does one pass per
array. The Mojo version adds all four numbers in the same pass.

## Still to do here

- **The column copies.** `column_i64` and `column_f64` copy each column into
  new `List`s, and that was 325 ms of the 777 ms. Reading the batch's Arrow
  buffers directly would avoid it, but that hasn't been tried in magmalake
  yet; check the column's type width and its null count before doing it.
- **One thread does the work.** With 8 decoding threads, the loop still runs
  on the thread that reads the batches. One `Dense` per worker, merged at the
  end, would fix it, at about 54 MB per worker for this key space.
