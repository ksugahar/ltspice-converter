# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.4] — 2026-05-19

### Added

- **`ltspice-convert` CLI** (new console script). End users can now
  drive every conversion from the shell without writing Python.

  - `ltspice-convert input.asc -o output.cir` -- single-file convert,
    with target format inferred from `-o` or from the input
    extension.
  - `ltspice-convert *.asc -o build/ --to cir` -- batch into a
    directory.
  - `ltspice-convert --check input.asc` -- round-trip lint mode that
    reports component-count drift, GND-pin position drift, and
    SYMBOLs whose `.asy` cannot be located. `--strict` promotes any
    warning to exit code 1.
  - `ltspice-convert --info input.asc [--json]` -- summary of a
    schematic / netlist (component count by type, symbol kinds,
    `.asy` resolution rate, `.subckt` blocks).
  - `--asy-dir DIR` (repeatable) and `LTSPICE_ASY_SEARCH_PATH` env
    var both feed the third-party symbol search path; CLI flag wins.

- **GitHub Actions reusable workflow**:
  [`docs/example-workflows/asc-check.yml`](docs/example-workflows/asc-check.yml)
  can be copied into any repository that stores `.asc` files to lint
  them on every PR with `ltspice-convert --check --strict`.

- 15 new pytest tests covering convert / check / info / batch /
  error paths.

## [0.3.3] — 2026-05-19

### Added

- **C3**: `.subckt ... .ends` bodies survive `.asc → netlist → .asc`
  intact. NetlistParser now accumulates every line between `.subckt`
  and `.ends` into a single multi-line directive instead of leaking
  the internal components into the top-level component list, which
  previously caused the body to vanish during regeneration.
  Verified byte-equal on `dimmer.asc` (DIAC: 15 lines, TRIAC: 11
  lines).
- `_apply_name_remap` uses negative lookbehind `(?<!§)` so a token
  already prefixed (e.g. `X§Q1` inside a subckt body) does not get
  re-prefixed into `X§X§Q1` on a second pass.

## [0.3.2] — 2026-05-19

### Added

- **C4**: `LTSPICE_ASY_SEARCH_PATH` environment variable lets users
  point the converter at third-party LTspice symbol libraries
  (`LTspiceControlLibrary`, `LTspicePowerSim`, custom `MyLib`, …).
  Multiple paths are separated by the OS path separator (`;` on
  Windows, `:` on Linux). The env var is consumed by both the asc
  parser (extraction side) and the asc generator (emission side), so
  the round-trip is self-consistent.
  `NetlistToAsc(asy_search_dirs=[...])` accepts the same list
  programmatically.

### Notes

The C4 env var does not, on its own, shift the public benchmark
because the benchmark measured against a baseline that already had
both directions silently dropping the same vendor symbols (matching
"both wrong" cases). With the env var set, the round-trip is
genuinely correct for those symbols but the benchmark surfaces
previously-masked failures elsewhere. For users with third-party
libraries, the user-visible behaviour is strictly better.

## [0.3.1] — 2026-05-18

### Added

- **C2**: multi-pin SUBCIRCUITs now round-trip with correct topology
  when LTspice's `lib.zip` (or any `.asy` search path) is available.
  AscGenerator places each SUBCIRCUIT in an isolated coordinate band
  (x ≥ 4096) and emits one FLAG per pin at the canonical pin offset
  reported by `AsyParser.get_terminal_offsets`. asc_parser uses the
  same lookup on re-extraction, so pins land on identical coordinates
  and the node names match exactly.

### Performance (GND-connectivity preservation, 240 samples)

| Source | v0.3.0 | v0.3.1 |
|---|---|---|
| LTspice Examples     | 43.8 % | **48.8 %**  |
| LTspice Applications | 13.8 % | **76.2 %**  |
| GitHub repos         | 30.0 % | **46.2 %**  |
| **Mean**             | **29.2 %** | **57.1 %** (+27.9 pt) |

Count preservation unchanged at 99.25 %. All 32 tests pass.

## [0.3.0] — 2026-05-18

### Added

- `Component.extra_nodes: List[str]` captures the full pin list of
  multi-pin SUBCIRCUIT invocations (e.g. 20-pin vendor ICs such as
  LTC2945) through NetlistParser → AscGenerator.
- AscGenerator emits a 4×5 grid of `FLAG + WIRE` lines around each
  SUBCIRCUIT so the regenerated `.asc` retains every pin label, not
  just the first two.
- 4-pin BJT / MOSFET substrate variants (`npn3`, `pnp4`, `nmos4`,
  `pmos4`, `njf4`, `pjf4`) are recognised by `SYMBOL_TO_SPICE` and
  preserved end-to-end. Previously the model name (`SB`, `NP`, …) was
  re-emitted as the SYMBOL kind.
- `* @sym=<kind>` netlist comment preserves LTspice SYMBOL variants
  (`ind2`, `schottky`, `pnp`, `polcap`, …) through `.asc → netlist →
  .asc`. Drift on a 240-file corpus dropped from 195 to ≈ 145 files.
- GitHub Actions CI: `pytest` matrix on Linux + Windows × Python
  3.10 / 3.11 / 3.12, plus an MCP import smoke test.
- GitHub Actions release workflow: builds + publishes to PyPI via
  OIDC trusted publishing on `v*` tag push.
- Issue templates (`bug_report.yml`, `feature_request.yml`) and
  `CONTRIBUTING.md`.
- `docs/BENCHMARKS.md` documents `.asc ↔ netlist`, schemdraw arm,
  symbol-kind drift, and GND-connectivity metrics with v0.1.0 →
  v0.3.0 comparison.

### Fixed

- schemdraw script execution: 7 out of 150 sampled GitHub-repo
  circuits failed `exec()`; all now run cleanly.
  - `AttributeError: 'X.end' not defined` on multi-pin elements at
    the end of a parallel group → runtime `getattr` guard with
    `d.move()` fallback.
  - `ValueError: Axis limits cannot be NaN or Inf` on empty drawings
    (source `.asc` had only directives, no SYMBOLs) → invisible
    guard line.
  - `ValueError: SVG backend only supports saving SVG format figures`
    on headless Linux → `.pdf` → `.svg` fallback in generated script.

### Performance (400-sample real-world corpus)

| Metric | v0.1.0 | v0.3.0 |
|---|---|---|
| `.asc → netlist → .asc` count match (mean) | 92.75 % | **99.25 %** |
| `netlist → schemdraw → netlist` count match (mean) | 82.5 % | **≥ 90 %** |
| schemdraw script exec failures | 7 / 150 | **0 / 150** |
| Symbol-kind drift events | 195 files | **≈ 145 files** |

### Known limitations (target Phase C+ work)

- GND-connectivity preservation: 14–44 % on multi-pin vendor IC
  topology. Geometric layout collisions defeat the multi-pin FLAG
  scheme; needs either layouter isolation zones or `.asy` file
  lookup.
- `LTspiceControlLibrary\*` math/logic blocks: requires `.asy`
  lookup to recover pin definitions.
- `.subckt` body inclusion: invocation preserved, body may be lost.

## [0.1.0] — 2026-05-18

Initial public release. Successor to the (now-private)
`ksugahar/circuit-converter`. Conversion-only scope: `.asc` ↔ `.cir`
↔ schemdraw Python scripts. Bundles an MCP server (`mcp-ltspice`).
