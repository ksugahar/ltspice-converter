# Benchmarks

This document reports the converter's performance on a corpus of
real-world LTspice circuits.  All numbers come from LAB-local
benchmarks; the corpus itself is not redistributed (see [README §
Project history & scope](../README.md)).

## Methodology

For each `.asc` in the sample:

1. **`asc_to_netlist`**: parse the .asc and emit a SPICE netlist
   (pure-Python path, no LTspice.exe).
2. **`netlist_to_asc`**: round-trip the netlist back through
   `NetlistToAsc().convert_string()`.
3. **Round-trip count match**: re-parse the round-tripped .asc and
   verify the **component count** is preserved.

We also measure two stricter checks:

- **Symbol-kind drift**: count how many original SYMBOL kinds (e.g.
  `ind2`, `schottky`, `pnp`, `polcap`) disappear from the regenerated
  .asc.  This captures whether the schematic *looks* the same when
  re-opened in LTspice.
- **GND-connectivity preservation**: rebuild the bipartite
  component-to-node graph and check whether each component's set of
  ground-touching pin positions matches between original and
  round-tripped netlists.  This is a node-rename-invariant proxy for
  topology preservation.

Per-source sample size is 100 (count) / 80 (kind + GND) circuits, drawn
deterministically (`random.seed(0)`).  `github_repos` is the most
diverse and the closest proxy for unseen real-world input;
`textbook` was iterated against during development and is a noisy
upper bound.

The script is `bench/baseline.py` (gitignored; depends on a
LAB-private corpus path).

## Headline results — v0.2.1 (after Phase A: A1-A4)

### schemdraw script execution (new check)

| Source | Failures (v0.2.0) | Failures (v0.2.1) |
|---|---|---|
| 150 samples (50 each of LTspice examples / applications / github_repos) | **7 / 150** | **0 / 150** |

Three classes of bug fixed in Phase A:
- `AttributeError: 'X.end' not defined` when an Opamp/multi-pin element
  is the last in a parallel group (now falls back to `d.move()`).
- `ValueError: Axis limits cannot be NaN or Inf` on empty drawings (now
  emits an invisible guard line).
- 4-pin BJT substrate variants (`npn3`, `pnp4`) were re-emitted as
  `SYMBOL <Q-model-name>` instead of `SYMBOL npn3` (model name was
  being confused with symbol kind). Fixed by extending `SYMBOL_TO_SPICE`
  to recognise substrate variants.

## Headline results — v0.2.0 (after P1 + P4)

### `.asc → netlist → .asc` round-trip (component count)

| Source | v0.1.0 | v0.2.0 | Δ |
|---|---|---|---|
| textbook (training-adjacent)    | 100%   | 100%   | 0   |
| LTspice Applications            |  98%   | **100%** | +2  |
| LTspice Examples                |  94%   |  **99%** | +5  |
| GitHub repos (unseen)           |  79%   |  **98%** | +19 |
| **Mean (400 samples)**          | **92.75%** | **99.25%** | **+6.5** |

### `netlist → schemdraw script → netlist` round-trip

| Source | v0.1.0 | v0.2.0 | Δ |
|---|---|---|---|
| textbook                        | 100%   | 100%   | 0   |
| LTspice Applications            |  96%   |  **98%** | +2  |
| LTspice Examples                |  74%   |  **80%** | +6  |
| GitHub repos                    |  60%   |  **82%** | +22 |
| **Mean (200 samples)**          | **82.5%** | **90.0%** | **+7.5** |

The schemdraw arm shares the `asc_to_netlist` stage with the
.asc-round-trip arm, so P1 vendor-symbol handling improved both.

### Symbol-kind preservation (240 samples, after P4)

Drift events (smaller is better):

| Symbol kind | v0.1.0 | v0.2.0 | Δ |
|---|---:|---:|---:|
| `ind2`                          | 91 |   0 | -91 |
| `schottky`                      | 56 |   0 | -56 |
| `pnp`                           | 47 |   4 | -43 |
| `cap` (variant losses)          | 23 |  34 |   ? |
| `polcap`                        | 11 |   0 | -11 |
| `npn3`                          | 13 |  13 |   0 |
| `LTspiceControlLibrary\*` (15 kinds) |  ~90 | ~85 |  -5 |
| **Files with any drift**        | 195 | 145 | -50 |

The `cap`/`res`/`voltage` rows are mostly count side-effects, not
actual variant losses — investigated separately.

### GND-connectivity preservation (new metric in v0.2.0)

This is the strictest check we run, and it shows the **biggest
remaining weakness** — topology drift in regenerated schematics:

| Source | GND-connectivity preserved |
|---|---|
| LTspice Examples                |  43.8% |
| LTspice Applications            |  13.8% |
| GitHub repos                    |  30.0% |

Even when the component count is exactly right, the regenerated .asc
often has different pin connections to ground than the original.  The
biggest contributor is **multi-pin vendor symbols** (LTC*/ADP*/ISO*
ICs with 5–20 pins): the converter currently preserves the
**count** of such symbols but reduces them to 2-pin generic blocks
because it does not have access to the symbol's `.asy` definition.

If your use case requires faithful topology preservation for vendor
ICs, the workaround today is:

- Keep the original `.asc` as the source of truth and use the converter
  for **netlist generation only** (asc → netlist, which is 100% across
  all sources).
- For the reverse direction (netlist → asc), use the converter only on
  circuits built from standard LTspice library symbols (R/C/L/V/I/D/Q/M/J).

## Known failure modes (after v0.2.0)

Even at 98–100% count-preservation pass rate, some classes of circuit
are weak:

- **Multi-pin vendor IC topology** — counts are preserved but pin
  layout collapses to 2 pins.  See [GND-connectivity preservation]
  (#gnd-connectivity-preservation-new-metric-in-v020) above.
- **`.subckt` body inclusion** — when a circuit embeds a `.subckt`
  block in the same .asc, only the invocation is preserved on
  round-trip; the body may be lost.
- **`LTspiceControlLibrary\\*`** math/logic blocks — currently treated
  as 5-pin opamps but the math operation identity (`and`, `mul`, etc.)
  is not fully recovered.
- **`npn3` (BJT with substrate symbol)** — recovered as `npn` (3-pin
  view), substrate connection lost.
- **schemdraw script execution errors on some inputs** — observed in
  ~6% of github_repos samples (XML parse errors, axis-limit NaN,
  unknown element types).  These produce no output but raise a
  Python exception, so they are at least visible to the caller.

These will be addressed in subsequent releases.  Run
`python bench/baseline.py` against your own corpus and file an
[issue](https://github.com/ksugahar/ltspice-converter/issues) with the
failing `.asc` if you hit one of these or a new class.

## Reproducibility

```bash
pip install ltspice-converter[test]
git clone https://github.com/ksugahar/ltspice-converter
cd ltspice-converter
# Edit bench/baseline.py:CORPUS_ROOTS to point at your own .asc corpus
python bench/baseline.py --per-source 100 --out bench/my_baseline.json
```
