# 4. Reduce snapshot copying and dependency bookkeeping

## Evidence

`GeometryBuilder._advance_prefix()` is already incremental: it traverses incoming references and recomputes affected geometry. However, for geometry changes it copies point and vertex dictionaries and geometry lists, builds ID dictionaries from those lists, then sorts IDs and materializes lists again. That work grows with the accumulated geometry, even when only one point changes.

In the sampled 40-step window around order 22,000, the builder used only about 7 ms total under cProfile. Dictionary copying accounted for about 3 ms. This is measurable overhead, but nowhere near enough to explain five-second steps there.

Face cache lookup also runs dependency completeness checks and computes a recursive visible-dependency signature first. Those paths may matter later when many faces become available. Failed tessellations are not cached; repeated attempts for an unchanged dependency state are another possible later cost. Neither is established as the current bottleneck.

## Proposed changes, in stages

1. Keep internal geometry indexed by entity ID, with stable ordering maintained separately. Materialize ordered snapshot lists only when presenting/exporting rather than on every record during a Turbo batch.
2. Define snapshot ownership explicitly before reusing mutable collections. Reports, analysis, and the viewport retain snapshots, so replacing copies with shared mutable dictionaries without a contract would introduce stale-state bugs.
3. Use precomputed `document.outgoing` references in dependency traversals where semantics match. Cache dependency closures or completeness state only after profiling identifies repeated traversal as significant.
4. Consider caching failed meshes by the full dependency state and healing option. Invalidate when relevant dependencies change; never cache an incomplete prefix as a permanent failure.

Preserve ordering and forward-reference handling. AL39's file order and entity ID order are not always identical.

## Validation and priority

First profile later windows to decide which stage is justified. Compare incremental snapshots with fresh full-prefix builds at representative orders and after backward jumps. Check point coordinates, vertex references, edge samples, face triangles, control-net status, healing toggles, and final reports. Measure allocations and memory as well as time: dependency caches can trade CPU savings for excessive retained memory.

This is a later optimization, behind renderer work for the observed window.
