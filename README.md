# ltspice-converter

Convert between three circuit representations:

```
   LTspice .asc  <---->  SPICE .cir  <---->  schemdraw Python script
```

Pure-Python — LTspice.exe is optional (used only for canonical anonymous-node
numbering when available). Built for AI agents to round-trip circuits between
schematic and netlist forms without launching LTspice.

## Install

```bash
pip install ltspice-converter
```

For MCP server support (Claude Code, Cursor, etc.):

```bash
pip install ltspice-converter[mcp]
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

Exposes four tools: `netlist_to_schemdraw`, `schemdraw_to_netlist`,
`netlist_to_asc`, `asc_to_netlist`.

## Supported elements

| SPICE | schemdraw | LTspice symbol |
|-------|-----------|----------------|
| R     | Resistor  | res, res2      |
| C     | Capacitor | cap, polcap    |
| L     | Inductor  | ind, ind2      |
| V     | SourceV   | voltage        |
| I     | SourceI   | current        |
| D     | Diode     | diode, zener   |
| Q (NPN/PNP) | BjtNpn / BjtPnp | npn, pnp |
| M (NMOS/PMOS) | NFet / PFet | nmos, pmos |
| J (NJF/PJF) | JFetN / JFetP | njf, pjf |
| X (opamp) | Opamp | opamp, opamp2 |

`.ac`, `.tran`, `.op`, `.dc` directives are preserved through the round-trip.

## Performance

Current `.asc → netlist → .asc` round-trip pass rate on real-world
corpora (component count preserved):

| Source | Pass rate (100 samples) |
|---|---|
| LTspice "Applications" examples | 100% |
| LTspice "Examples" examples     | 99%  |
| Textbook circuits (training-adjacent) | 100% |
| GitHub repos (unseen)            | 98%  |

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
