# Mojo hazards in fast code

Ownership, threading and packaging traps that produced wrong answers or
wrong numbers in magmalake. Written against Mojo 1.0.

**Values die at their last textual use, not at the end of scope.** A benchmark
that reads a buffer through a span must keep the owner alive — `keep(data)`
after the timed region — or it times a use-after-free that returns
plausible-looking garbage rather than crashing.

**`MutUntrackedOrigin` is a contract, not a bug.** Every wrong answer worth
chasing in this codebase followed a line that told the compiler to stop
tracking an origin. When something is inexplicably wrong, grep for the
untracked conversions first.

**A guard holding the last share of shared state can free that state inside
its own critical section.** A pool lease took a mutex inline; the owning
`ArcPointer` reached its last use mid-section, freeing the `pthread_mutex_t`
before `unlock()` wrote into freed heap. Fix: every critical section takes the
state as a **borrowed argument**, which cannot be destroyed during the call.
macOS was green throughout; only Linux crashed — so a green macOS CI leg is
not evidence here.

**ThreadSanitizer reports on any allocating threaded Mojo program.** The 1.0.0
runtime allocator is invisible to TSan's intercepts. Before believing *or*
dismissing a report, build a control: the same harness with task bodies that
only read, only write, and only allocate. Compare against that baseline.

**`.mojoc` packages are stamped with the compiler that built them** and are
refused by any other, so a package built with stable cannot be loaded by
nightly. Build the package with the toolchain that will consume it.

**`-I <path>` does not override a package already on the environment's import
path.** Testing a modified library against a consumer requires shadowing the
packaged copy; otherwise you silently test the old one and conclude your
change did nothing.
