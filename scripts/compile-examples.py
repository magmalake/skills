#!/usr/bin/env python3
"""Compiles the skills' Mojo examples that are marked as compilable.

A fenced block whose info string is `mojo compile` is a whole program: it is
written to a file of its own and built with `mojo build`. One without a
`def main` gets an empty one, so a module of declarations builds too.

    ```mojo compile
    from morca import Ctx, Month, Mapped, Step
    ...
    ```

`mojo compile name=src/lake.mojo` writes the block to that path instead and
doesn't build it alone: it's a helper module that the skill's other blocks
import. Every block of a skill is built with `-I` its `src/` and its root, so
`from lake import ...` finds it.

Plain `mojo` blocks are excerpts and are not built. Run inside the pixi
environment of ci/pixi.toml, which pins the morca and dista the skills
describe:

    pixi run --manifest-path ci/pixi.toml python3 -I scripts/compile-examples.py

Options: `--list` prints the blocks without building, `--jobs N` builds N at
once (default 2), and paths limit it to those Markdown files.
"""

import argparse
import concurrent.futures
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "examples"
FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*(.*)$")


def blocks(md: pathlib.Path):
    """Yields (line, info, code) for each fenced block of `md`."""
    lines = md.read_text().splitlines()
    i = 0
    while i < len(lines):
        m = FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        indent, fence, info = m.group(1), m.group(2), m.group(3).strip()
        start = i + 1
        body = []
        i += 1
        while i < len(lines):
            close = lines[i].strip()
            if close.startswith(fence[0] * len(fence)) and close.strip(fence[0]) == "":
                break
            line = lines[i]
            body.append(line[len(indent):] if line.startswith(indent) else line.lstrip())
            i += 1
        i += 1
        yield start, info, "\n".join(body) + "\n"


def compilable(info: str):
    """The block's `name=` (or "" for a program), or None if not marked."""
    words = info.split()
    if len(words) < 2 or words[0] != "mojo" or words[1] != "compile":
        return None
    for w in words[2:]:
        if w.startswith("name="):
            return w[len("name="):]
    return ""


def markdown_files(args):
    if args:
        return [pathlib.Path(a).resolve() for a in args]
    found = []
    for md in sorted(ROOT.glob("*/**/*.md")):
        rel = md.relative_to(ROOT)
        if rel.parts[0] in ("vendor", "build", ".pixi", "ci") or rel.parts[0].startswith("."):
            continue
        found.append(md)
    return found


def build(job):
    src, includes, label = job
    exe = src.with_suffix("")
    cmd = ["mojo", "build"]
    for inc in includes:
        cmd += ["-I", str(inc)]
    cmd += [str(src), "-o", str(exe)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if exe.exists():
        exe.unlink()
    return label, p.returncode, (p.stdout + p.stderr).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("files", nargs="*")
    args = ap.parse_args()

    if OUT.exists():
        shutil.rmtree(OUT)
    jobs = []
    for md in markdown_files(args.files):
        rel = md.relative_to(ROOT)
        skill = OUT / rel.parts[0]
        for line, info, code in blocks(md):
            name = compilable(info)
            if name is None:
                continue
            label = f"{rel}:{line}"
            if name:
                path = skill / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(code)
                if args.list:
                    print(f"{label}  (helper {name})")
                continue
            if not re.search(r"^def main\(", code, re.M):
                code += "\n\ndef main():\n    pass\n"
            stem = re.sub(r"[^A-Za-z0-9]+", "_", str(rel.with_suffix("")))
            path = skill / "examples" / f"ex_{stem}_{line}.mojo"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code)
            if args.list:
                print(label)
            jobs.append((path, [skill / "src", skill], label))
    if args.list:
        return 0
    if not jobs:
        print("no `mojo compile` blocks found")
        return 1

    failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for label, rc, out in ex.map(build, jobs):
            if rc == 0:
                print(f"ok    {label}")
            else:
                failed += 1
                print(f"FAIL  {label}\n{out}\n")
    print(f"{len(jobs) - failed} of {len(jobs)} examples compiled")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
