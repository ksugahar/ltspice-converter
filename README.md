# ltspice-converter

[![CI](https://github.com/ksugahar/ltspice-converter/actions/workflows/test.yml/badge.svg)](https://github.com/ksugahar/ltspice-converter/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://github.com/ksugahar/ltspice-converter)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

What's new in [v0.3.9](CHANGELOG.md): MCP server gains `check_circuit`
and `info_circuit` tools so AI agents can lint their own generated
SPICE without shelling out.  v0.3.8 fixed two round-trip-drop bugs
(GitHub-corpus pass rate 96.7 % → **99.6 %**).

Convert between three circuit representations:

```
   LTspice .asc  <---->  SPICE .cir  <---->  schemdraw Python script
```

Pure-Python — LTspice.exe is optional (used only for canonical anonymous-node
numbering when available). Built for AI agents to round-trip circuits between
schematic and netlist forms without launching LTspice.

## Install

The package is distributed from GitHub (no PyPI release). Install
directly with pip:

```bash
pip install git+https://github.com/ksugahar/ltspice-converter
```

For MCP server support (Claude Code, Cursor, etc.):

```bash
pip install "ltspice-converter[mcp] @ git+https://github.com/ksugahar/ltspice-converter"
```

Pinning a specific version:

```bash
pip install git+https://github.com/ksugahar/ltspice-converter@v0.3.9
```

For development:

```bash
git clone https://github.com/ksugahar/ltspice-converter
cd ltspice-converter
pip install -e .[test,mcp]
```

## Python API

```python
import ltspice_converter as lc

netlist = """* RC Lowpass Filter
V1 in 0 AC 1
R1 in in1 1k
C1 in1 0 1u
.ac dec 20 1 100k
.end"""

# SPICE netlist -> runnable schemdraw script
script = lc.netlist_to_schemdraw(netlist, name="rc")

# schemdraw script -> SPICE netlist
recovered = lc.schemdraw_to_netlist(script, title="rc")

# SPICE netlist -> LTspice .asc text
asc_text = lc.netlist_to_asc(netlist)

# LTspice .asc text -> SPICE netlist (pure-Python; pass use_ltspice=True
# to use LTspice.exe for canonical anonymous-node numbering)
recovered_netlist = lc.asc_to_netlist(asc_text)
```

## Command-line tool

Installing the package wires up the `ltspice-convert` console script.
No Python knowledge needed.

### Conversion

```bash
# Single file (target inferred from -o or the "opposite" of input)
ltspice-convert input.asc -o output.cir
ltspice-convert input.cir -o output.asc
ltspice-convert input.cir -o output.py        # netlist -> schemdraw script

# Auto output path (same dir, sensible default extension)
ltspice-convert input.asc                     # writes input.cir alongside
ltspice-convert input.asc --to py             # writes input.py alongside

# Batch (output is a directory; --to picks target format)
ltspice-convert *.asc -o build/ --to cir
```

### Round-trip check (lint mode)

`--check` reads a file, runs it through the full round-trip, and
reports drift / `.asy` resolution gaps. Use `--strict` to make any
warning exit non-zero -- handy in CI.

```bash
ltspice-convert --check input.asc
# -> PASS / PASS (with warnings) / FAIL  on stdout, with [ok]/[warn] details

ltspice-convert --check --strict *.asc        # exit 1 if any warning
```

Static netlist checks `--check` runs (in addition to round-trip):

- Duplicate instance names (`R1` appearing twice at top level)
- Floating nodes (only one component touches it -- usually a wire
  the user forgot to finish)
- Orphan `.model` declarations (model defined but never referenced)
- Undefined model references (device names a model that is not
  defined inline; standard library names like `1N4148`, `2N3904`,
  `LT1001`, ... are exempt)
- `{PARAM}` references without a matching `.param NAME=...`
- Lines the parser could not classify (with a "did you mean ...?"
  hint for common typos like `Resistor` / `Capacitor`)

### Info / stats

```bash
ltspice-convert --info input.asc              # human-readable
ltspice-convert --info --json input.asc       # machine-readable JSON
```

### Third-party `.asy` libraries

`--asy-dir` (repeatable) is equivalent to setting the
`LTSPICE_ASY_SEARCH_PATH` env var:

```bash
ltspice-convert --asy-dir /path/to/MyLib/sym input.asc -o out.cir
ltspice-convert --asy-dir A --asy-dir B input.asc -o out.cir
```

CLI flags take priority over the env var; both can be combined.

### CI / GitHub Actions

Run `--check --strict` on every PR that touches `.asc` files. See
[`docs/example-workflows/asc-check.yml`](docs/example-workflows/asc-check.yml)
for a reusable workflow template you can copy into a circuit-design
repository.

## MCP server

For AI agents (Claude Code, Cursor) that author or refactor SPICE:
the MCP server lets the agent convert between formats AND lint its
own generated netlists in the same conversation, without shelling out
to a CLI.

Install with the `[mcp]` extra and add to your MCP client config:

```json
{
  "mcpServers": {
    "ltspice": {
      "command": "mcp-ltspice"
    }
  }
}
```

Exposes six tools:

| Tool | Purpose |
|---|---|
| `netlist_to_schemdraw(netlist, name)` | SPICE → schemdraw Python script |
| `schemdraw_to_netlist(script, title)` | schemdraw script → SPICE |
| `netlist_to_asc(netlist, asy_search_dirs?)` | SPICE → LTspice `.asc` |
| `asc_to_netlist(asc_text, use_ltspice?, asy_search_dirs?)` | LTspice `.asc` → SPICE |
| `check_circuit(text, fmt, asy_search_dirs?)` | Lint: round-trip drift + static netlist checks. Returns `{ok, info, warnings}`. |
| `info_circuit(text, fmt, asy_search_dirs?)` | Summary: component counts, symbol kinds, `.subckt` blocks. |

Typical agent loop: generate netlist → `check_circuit(..., 'cir')` →
if `warnings` non-empty, fix and re-check → only ship when clean.

## Supported elements

| SPICE | schemdraw | LTspice symbol |
|-------|-----------|----------------|
| R     | Resistor  | res, res2      |
| C     | Capacitor | cap, polcap    |
| L     | Inductor  | ind, ind2      |
| V     | SourceV   | voltage        |
| I     | SourceI   | current        |
| D     | Diode     | diode, zener, schottky, varactor, tvs |
| Q (NPN/PNP, 3- or 4-pin substrate) | BjtNpn / BjtPnp | npn, pnp, npn3, pnp3, npn4, pnp4 |
| M (NMOS/PMOS, 3- or 4-pin substrate) | NFet / PFet | nmos, pmos, nmos4, pmos4 |
| J (NJF/PJF, 3- or 4-pin substrate) | JFetN / JFetP | njf, pjf, njf4, pjf4 |
| B (behavioral source) | — | bv, bi, bi2 |
| E, G (VCVS, VCCS) | — | e, e2, g, g2 |
| F, H (CCCS, CCVS) | — | f, h |
| S (voltage-controlled switch) | — | sw |
| T (transmission line) | — | tline |
| K (mutual inductance, directive) | — | — |
| X (subcircuit / opamp / IC) | Opamp | opamp, opamp2, lt1018, ... and arbitrary multi-pin vendor symbols |
| U (digital flop / gate) | — | Digital\\\\srflop, Comparators\\\\..., ... |

`.ac`, `.tran`, `.op`, `.dc`, `.param`, `.model`, `.subckt`/`.ends`
directives are preserved through the round-trip.

## `.subckt` round-trip

The converter preserves an entire `.subckt` block --- header, body
components, models, `.param`s, comments, and `.ends` line --- byte
for byte. This means a netlist file like:

```spice
* myckt with a diac model
V1 in 0 SINE(0 230 50)
X1 in 0 mydiac
.tran 20m

.subckt mydiac T1 T2
* simplified DIAC: two opposing zeners
.model BD D Bv=30
D1 T1 T2 BD
D2 T2 T1 BD
.ends mydiac
.end
```

round-trips through `ltspice-convert` cleanly:

```bash
ltspice-convert myckt.cir -o myckt.asc      # write LTspice schematic
ltspice-convert myckt.asc -o back.cir       # extract back
diff myckt.cir back.cir                     # node names may rename;
                                            # the .subckt block is byte-equal
```

The same holds for `ltspice-convert --check myckt.cir`: any drift
inside the subckt body would show up as `component count drift` or a
parser warning.

## Performance

Current `.asc → netlist → .asc` round-trip pass rate on real-world
corpora (component count preserved):

| Source | Pass rate |
|---|---|
| LTspice "Applications" examples (4099 files) | 100%  |
| LTspice "Examples" examples (100 samples)    |  99%  |
| Textbook circuits (training-adjacent, 28 files) | 100%  |
| GitHub repos (unseen, 720 files)              | 99.6% |

The schemdraw round-trip `netlist → schemdraw script → netlist` runs
at 80–100% on the same corpus (lower because schemdraw's element
library is smaller than LTspice's symbol library).

### Third-party symbol libraries

For round-tripping schematics that use third-party LTspice libraries
(not bundled in `lib.zip`), point the
`LTSPICE_ASY_SEARCH_PATH` environment variable at the library's
`sym/` root directory. Multiple paths are separated by the OS path
separator (`;` on Windows, `:` on Linux/macOS):

```bash
# Linux / macOS
export LTSPICE_ASY_SEARCH_PATH="/path/to/LTspiceControlLibrary/lib/sym:/path/to/MyLib/sym"

# Windows (cmd.exe)
set LTSPICE_ASY_SEARCH_PATH=C:\Libs\LTspiceControlLibrary\lib\sym;C:\Libs\MyLib\sym
```

When the env var is set, both `asc → netlist` and `netlist → asc`
use the same `.asy` files, so node-to-pin topology survives the
round-trip for those vendor symbols.

See [docs/BENCHMARKS.md](docs/BENCHMARKS.md) for methodology, the
schemdraw arm, and known failure modes.  Pass rate is not perfect —
file a [GitHub issue](https://github.com/ksugahar/ltspice-converter/issues)
with a failing `.asc` and we'll fix it.

## Project history & scope

This package is the successor to the (now-private)
`ksugahar/circuit-converter` repository.  The previous repo also contained
training/research material — textbook circuits, LTspice's own bundled
examples (Educational/, Applications/), and aggregated third-party GitHub
content — none of which can be redistributed.

This re-release is intentionally **converter-only**: just the trained
converter, a small set of author-authored test circuits, and API
documentation.  Higher-level circuit analysis features that lived in
the old repo are not in scope here.

### Distribution policy: trained converter, private corpus

Development uses **private training corpora** (textbook circuits,
LTspice bundled examples, third-party GitHub aggregates) to measure
round-trip fidelity and to surface converter bugs.  **Only the
converter is published** — every corpus stays on the author's
LAB-private storage.  Concretely:

- Public: source code, CLI, MCP server, API docs, the LAB-authored
  test fixtures under `tests/fixtures/` (~58 minimal RC/RLC/filter
  circuits the author wrote from scratch).
- LAB-private: textbook DB JSONs, the ~5,000-file `bench/` corpus,
  the harvest pipeline scripts, the textbook PDFs themselves.

The benchmark numbers in [docs/BENCHMARKS.md](docs/BENCHMARKS.md)
report measurements made on LAB-private corpora; you cannot reproduce
them bit-for-bit without your own corpus, but the converter behaviour
they describe is exactly what you get from `pip install
git+https://github.com/ksugahar/ltspice-converter` here.

## Test fixtures

Everything under `tests/fixtures/` is author-authored: small RC / RLC /
filter circuits in either of three forms (`.asc`, `.cir`, `.gen.py`)
used to exercise the round-trip.  No textbook content, no LTspice
bundled examples, no third-party repo dumps.

To test against larger sets, run the converter on **your own local copy**
of LTspice's `Educational/` and `Applications/` directories — do not
redistribute the results.

## Copyright notice

| Asset | Owner | Status here |
|-------|-------|-------------|
| Converter source code | © 2026 Mitsutoshi Sugahara | **MIT** |
| Test fixtures in `tests/fixtures/` | © 2026 Mitsutoshi Sugahara | **MIT** |
| API reference in `docs/pyltspice_api.md` | reformat of public PyLTSpice/spicelib API | fair-use technical citation |
| LTspice itself | © Analog Devices, Inc. | not redistributed (install separately) |
| LTspice bundled example circuits | © Analog Devices, Inc. | **excluded** |
| Textbook circuits / problem sets used during development | © respective publishers | **excluded** |
| Third-party GitHub circuits used during development | © respective authors | **excluded** |

## Author

[Mitsutoshi Sugahara (菅原光俊)](https://github.com/ksugahar) —
Department of Electric and Electronic Engineering, Kindai University

## License

MIT — see [LICENSE](LICENSE).
