# tests/corpus — frozen regression fixtures

The STEP files here are owned by the test suite and never change after being
committed. Tests hardcode paths into this folder, so any byte change here
could invalidate their expectations (edge ids, entity counts, ...).

Rules:

- New tests that need a specific model copy it here and hardcode the path.
- Never edit files here; add a *new* file instead.
- The slow corpus sweeps discover files under the repository `corpus/` and
  `STEPSCOPE_CORPUS` only — this folder is deliberately not swept.
