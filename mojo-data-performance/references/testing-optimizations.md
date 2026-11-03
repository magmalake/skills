# Testing an optimization, not the happy path

An optimization that silently never runs passes every test. These are the
tests that catch it.

**Run negative controls: break your own optimisation deliberately and prove a
test fails.** Twice in one session a test passed for the wrong reason. If
breaking the code does not break the test, you do not have a test.

**Fold structure, not only values.** A fingerprint that walks a tree from its
roots folds a *consistently permuted* arena to an identical value: every value
correct, the layout wrong, every test green. Zero tests would have caught it.
Include positions, offsets, and traversal order in the fingerprint, not just
the payload.

**Mutation-test the tests.** Introduce five deliberate breakages and check
which the suite catches. In one round, the new test caught all five and the
existing suite caught only some — which is the only evidence that the new test
was worth adding.

**Assert the compiler's silence as well as its errors.** A file that must
compile without warning and then print the wrong answer is a regression test
against the *toolchain*: it tells you when the compiler gets good enough to
catch what it currently accepts.
