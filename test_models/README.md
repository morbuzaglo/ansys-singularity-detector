# Benchmark geometry fixtures

Per spec §41: a repeated manual model-building workflow is **not** acceptable; a
one-time static geometry fixture (version-controlled) **is**. The convergence
study and the Mechanical analysis are then built around these automatically.

## Needed for Milestone 0 / 1

- `bar.stp` — a plain rectangular bar (e.g. 100 × 10 × 10 mm). Fixed on the
  min-X face, axial force on the max-X face. Used by
  `extension/mechanical_scripts/milestone0_hello.py` (set `SD_GEOMETRY` or pass
  `--geometry`). Until it exists the Milestone 0 run reports `NO_GEOMETRY` and
  only proves the launch→script→JSON→exit plumbing.

## Validation suite (spec §40) — to add

| File | Model | Expected classification |
|---|---|---|
| `plate_with_hole.stp` | plate, central circular hole | non-singular (converges) |
| `reentrant_corner.stp` | L-bracket / 90° re-entrant corner | singular (diverges) |
| `cantilever_point_load.stp` | bar + vertex load | local singularity at load |
| `cantilever_distributed.stp` | same, pressure load | regular |
| `sharp_notch.stp` | V-notch, zero root radius | singular |
| `fillet_radius.stp` | same notch, finite fillet | non-singular |
| `fixed_face_edge.stp` | block, fixed face | restraint-edge singularity |
| `contact_sharp_edge.stp` | two blocks, contact ends at an edge | contact singularity |
| `poor_smooth_mesh.stp` | regular part, deliberately bad initial mesh | high error → converges (NOT singular) |
| `crack_tip.stp` | edge crack | singular; recommend fracture mechanics |

## How fixtures get generated

Preferred: `devtools/make_test_geometry.py` (to be written) driving SpaceClaim
scripting in batch (`SpaceClaim.exe /Headless=True /RunScript=... /ExitAfterScript=True`)
— SpaceClaim scripting can create primitives, notches, fillets, holes. Output
`.stp` (portable across releases) committed here with `git add -f`.

Fallback: build once by hand in SpaceClaim/DesignModeler, export `.stp`, commit.
Document provenance (dimensions, units) in a sidecar `*.stp.md`.

Do **not** commit `.scdocx` / `.agdb` (see `.gitignore`) — keep fixtures as `.stp`.
