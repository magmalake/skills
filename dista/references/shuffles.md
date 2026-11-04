# Shuffles: `TaskMany` and `TaskWith`

A shuffle's map task makes one partition per reducer, and each reducer takes
its partition number beside every map task's part. dista has one task shape
for each (from `src/dista/task.mojo` in
[dista.mojo's repository](https://github.com/magmalake/dista.mojo)):

```
TaskMany[A_, X, f: def(A_) thin raises -> Seq[X]]
TaskWith[A_, X, R_, f: def(A_, Seq[X]) thin raises -> R_]
```

This program was built and run against dista 0.2.0:

```mojo
from dista import Runtime, Seq, TaskMany, TaskWith

def split3(x: Int) -> Seq[Int]:
    return Seq[Int]([x, x + 1, x + 2])

def scaled_sum(k: Int, xs: Seq[Int]) -> Int:
    var t = 0
    for i in range(len(xs)):
        t += xs[i]
    return k * t

comptime Split3 = TaskMany[Int, Int, split3]
comptime ScaledSum = TaskWith[Int, Int, Int, scaled_sum]

def main() raises:
    var rt = Runtime[Split3, ScaledSum](workers=2)
    var parts = rt.remote_many[Split3](10, 3)        # List[ObjectRef[Int]]
    print(rt.get(parts[1]))                          # 11
    print(rt.get(rt.remote[ScaledSum](2, parts)))    # 66
```

- **`rt.remote_many[T](arg, n)`** returns `n` refs. `f` must return a `Seq`
  of exactly `n` items, or every part fails with `returned 1 results, not
  2`. Each part is stored, fetched and freed on its own.
- **`remote_many` can wait on other results:** `rt.remote_many[T](ref, n)`
  on one result, or `rt.remote_many[T](refs, n)` when `T` takes a `Seq`, so
  a later stage partitions again what earlier tasks made without the driver
  fetching it.
- **`rt.remote[T](value, refs)`** submits a `TaskWith`: the refs resolve,
  in order, into the `Seq` it receives.
- **A `TaskMany` whose worker dies after answering some parts fails**
  instead of running again (DESIGN.md, "Failure handling").
- **Locality helps little here.** A task prefers the node holding more than
  half of its inputs' bytes; an all-to-all reducer's inputs are spread
  evenly, so it is placed as before.

dista.mojo's `tests/test_shuffle.mojo` runs a whole group-by this way: 8 map tasks
partition 20,000 rows each by key for 4 reducers, and the sums are checked
against one process. `deploy/shuffle.mojo` is the benchmark version.
