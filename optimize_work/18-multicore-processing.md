# Multicore processing: useful, but presentation is still the limiting factor

The application currently runs parsing, prefix geometry advancement, Qt panel work, and viewport presentation synchronously from the UI. Separating geometry from presentation is a worthwhile architecture change. It is not equivalent to distributing every source record to different cores.

## What can move

- Parsing and geometry construction can run in a worker process. Give the worker sole ownership of its GeometryBuilder, caches, and prefix state.
- Independent, dependency-complete faces can eventually be tessellated by a process pool using immutable inputs and plain mesh results. Face readiness and result application must still respect playback visibility.
- Qt widgets and QPixmap operations stay on the GUI thread. Keep the current embedded VTK renderer, actors, and OpenGL context there as well. Non-rendering VTK filters used by tessellation can be constructed privately inside worker processes; no rendering objects should cross that boundary.

Standard CPython threads do not provide parallel execution of the builder's Python loops because of the GIL. Native operations can release it, but that does not make the whole builder parallel. Processes are the conservative first choice rather than relying on a new Python runtime or VTK/Qt extension compatibility with free-threaded builds.

Sources: [Qt threads and object ownership](https://doc.qt.io/qtforpython-6.10/overviews/qtdoc-threads-qobject.html), [Python ProcessPoolExecutor](https://docs.python.org/3/library/concurrent.futures.html#processpoolexecutor).

## Why more cores alone will not solve the recorded long run

In the late range of the 77-minute session, rendering accounted for roughly 50%, clipping 16%, and surface-control UI rebuilding 17% of measured exclusive time. Geometry building was roughly 6% excluding separately timed geometry children. No faces had been built at the stopping point, so a face-tessellation pool would not have addressed most of that measured interval.

Accelerating only geometry cannot eliminate the dominant presentation work. A worker that emits every intermediate snapshot at full speed would simply build an enormous UI backlog. The main architectural win is allowing geometry to advance independently while the UI consumes coalesced results less frequently.

## Recommended staged design

1. First make a builder batch return plain geometry deltas and an ending source order, without touching Qt controls. Preserve the existing synchronous path for comparison.
2. Run that ordered batch engine in one background process. This permits construction to overlap rendering on another core without breaking prefix dependencies. Send the document once, or load it once per process; do not serialize the complete document or growing snapshot for every record.
3. Use bounded transport/backpressure and merge all intervening deltas before presentation. Dropping an older snapshot is safe only if its delta has been incorporated or the newer snapshot is complete. Record processed and presented orders separately.
4. Pause stops further batches and flushes the acknowledged processed state before inspection. Seeks, file changes, healing changes, and worker restarts require generation IDs and an explicit reset protocol so stale results cannot change the current scene. Close cancels/joins workers without blocking indefinitely.
5. Measure IPC/serialization time, queue depth, memory, main-thread presentation time, and worker CPU time. The current profiler is UI-thread-oriented and must not be shared as mutable state across threads/processes. Aggregate separately identified worker timings.
6. Once measurements show costly independent ready faces, try a small process pool. Chunk work to amortize scheduling, bound outstanding tasks, and avoid oversubscribing native VTK threads. Never share one mutable GeometryBuilder across workers.

The first process overlaps one sequential builder with the UI; it does not make prefix construction use all CPU cores. The later face pool can use more cores when enough independent heavy faces are ready. Preserve small-model behavior, forward references, pause/resume, backward seeking, final geometry/report correctness, and responsiveness before comparing throughput.

No multicore implementation has been made yet. Presentation coalescing and changed-only surface controls remain necessary even with workers.
