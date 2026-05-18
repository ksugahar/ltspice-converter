"""Command-line interface for ltspice-converter.

Three modes -- convert, check, info -- wrapped around the same Python API
the MCP server uses. Wired up as the console script ``ltspice-convert``
in ``pyproject.toml``.

Examples
--------
Convert a single file (auto target by extension):

    ltspice-convert input.asc -o output.cir
    ltspice-convert input.cir -o output.py

Convert multiple files (output is a directory, target chosen by --to):

    ltspice-convert *.asc -o build/ --to cir

Lint a circuit (round-trip check + .asy availability):

    ltspice-convert --check dimmer.asc
    ltspice-convert --check --strict *.asc      # exit 1 on any warning

Show a summary of a circuit:

    ltspice-convert --info input.asc
    ltspice-convert --info --json input.asc     # machine-readable

Add a third-party symbol library to the .asy search path:

    ltspice-convert --asy-dir /path/to/MyLib/sym input.asc -o output.cir

The ``--asy-dir`` flag and the ``LTSPICE_ASY_SEARCH_PATH`` env var are
equivalent; CLI flags take precedence.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections import Counter
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__, conversion
from .parser.asc_parser import (
    AscParser, NetlistExtractor, AsyParser, SYMBOL_TO_SPICE,
)


# =============================================================================
# Format detection
# =============================================================================

_EXT_TO_FMT = {
    '.asc': 'asc',
    '.cir': 'cir',
    '.net': 'cir',
    '.sp':  'cir',
    '.spice': 'cir',
    '.py':  'py',
}


def detect_format(path: Path) -> Optional[str]:
    return _EXT_TO_FMT.get(path.suffix.lower())


def read_text(path: Path) -> str:
    """Read .asc / .cir / .py text. Handles LTspice 17+ UTF-16 LE .asc files."""
    raw = path.read_bytes()
    if raw[:2] == b'\xff\xfe':
        return raw.decode('utf-16-le')
    # asc / cir / py are typically latin-1 or utf-8
    for enc in ('utf-8', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def write_text(path: Path, content: str, fmt: str) -> None:
    """Write output. ASC uses latin-1 (LTspice convention), others UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == 'asc':
        path.write_text(content, encoding='latin-1', errors='replace')
    else:
        path.write_text(content, encoding='utf-8')


# =============================================================================
# Conversion routing
# =============================================================================

def convert(src_text: str, src_fmt: str, dst_fmt: str,
            name: str = 'circuit',
            asy_search_dirs: Optional[List[str]] = None) -> str:
    """Dispatch any-to-any conversion.

    src_fmt / dst_fmt ∈ {asc, cir, py}.
    """
    if src_fmt == dst_fmt:
        return src_text

    # ASC -> *
    if src_fmt == 'asc':
        ap = AscParser(asy_search_dirs=[Path(d) for d in (asy_search_dirs or [])] or None)
        ap.parse_string(src_text)
        netlist = NetlistExtractor(ap).extract()
        if dst_fmt == 'cir':
            return netlist
        if dst_fmt == 'py':
            return conversion.netlist_to_schemdraw(netlist, name)
        raise ValueError(f'unsupported target format: {dst_fmt}')

    # CIR -> *
    if src_fmt == 'cir':
        if dst_fmt == 'asc':
            # NetlistToAsc reads asy_search_dirs at construction time
            from .parser.netlist_to_asc import NetlistToAsc
            converter = NetlistToAsc(asy_search_dirs=asy_search_dirs)
            return converter.convert_string(src_text)
        if dst_fmt == 'py':
            return conversion.netlist_to_schemdraw(src_text, name)
        raise ValueError(f'unsupported target format: {dst_fmt}')

    # PY -> *
    if src_fmt == 'py':
        netlist = conversion.schemdraw_to_netlist(src_text, name)
        if dst_fmt == 'cir':
            return netlist
        if dst_fmt == 'asc':
            from .parser.netlist_to_asc import NetlistToAsc
            converter = NetlistToAsc(asy_search_dirs=asy_search_dirs)
            return converter.convert_string(netlist)
        raise ValueError(f'unsupported target format: {dst_fmt}')

    raise ValueError(f'unsupported source format: {src_fmt}')


# =============================================================================
# Mode: convert
# =============================================================================

def _default_target(src_fmt: str) -> str:
    """Default conversion direction when --to / -o are not given."""
    if src_fmt == 'asc':
        return 'cir'
    if src_fmt == 'cir':
        return 'asc'
    if src_fmt == 'py':
        return 'cir'
    raise ValueError(f'no default target for source format: {src_fmt}')


def _resolve_output_path(input_path: Path, output: Optional[str],
                         to_fmt: Optional[str], multi: bool) -> Tuple[Path, str]:
    """Decide output path and format.

    Rules:
      - --to <fmt> chooses the target format. -o <file> can override.
      - If -o is a directory (or multi-input), write alongside in that dir.
      - If no -o, write alongside the input file (same dir).
    """
    src_fmt = detect_format(input_path) or 'asc'

    if output:
        out_path = Path(output)
        if multi or out_path.exists() and out_path.is_dir() or output.endswith(('/', '\\')):
            out_path.mkdir(parents=True, exist_ok=True)
            tgt_fmt = to_fmt or _default_target(src_fmt)
            return out_path / (input_path.stem + '.' + tgt_fmt), tgt_fmt
        # Treat as explicit file path
        tgt_fmt = to_fmt or detect_format(out_path) or _default_target(src_fmt)
        return out_path, tgt_fmt

    # No -o: write alongside input
    tgt_fmt = to_fmt or _default_target(src_fmt)
    return input_path.with_suffix('.' + tgt_fmt), tgt_fmt


def cmd_convert(args) -> int:
    inputs = [Path(p) for p in args.inputs]
    multi = len(inputs) > 1
    errors = 0
    for inp in inputs:
        try:
            if not inp.is_file():
                print(f'error: {inp}: not a file', file=sys.stderr)
                errors += 1
                continue
            src_fmt = detect_format(inp)
            if src_fmt is None:
                print(f'error: {inp}: unknown source extension {inp.suffix!r}',
                      file=sys.stderr)
                errors += 1
                continue
            out_path, tgt_fmt = _resolve_output_path(inp, args.output, args.to, multi)
            src_text = read_text(inp)
            result = convert(src_text, src_fmt, tgt_fmt,
                             name=inp.stem,
                             asy_search_dirs=args.asy_dir)
            write_text(out_path, result, tgt_fmt)
            print(f'{inp} -> {out_path}  ({len(result)} bytes)')
        except Exception as e:
            print(f'error: {inp}: {type(e).__name__}: {e}', file=sys.stderr)
            if args.traceback:
                traceback.print_exc()
            errors += 1
    return 1 if errors else 0


# =============================================================================
# Mode: check (lint)
# =============================================================================

def _count_components(netlist: str) -> int:
    n = 0
    for line in netlist.split('\n'):
        s = line.strip()
        if s and s[0].isalpha() and not s.startswith('*') and not s.startswith('.'):
            n += 1
    return n


def _gnd_pin_positions(netlist: str) -> dict:
    out: dict = {}
    for line in netlist.split('\n'):
        s = line.strip()
        if not s or s.startswith('*') or s.startswith('.'):
            continue
        parts = s.split()
        if len(parts) >= 3 and parts[0][0].isalpha():
            out[parts[0]] = frozenset(
                i for i, n in enumerate(parts[1:], 1)
                if n == '0' or n.lower() == 'gnd'
            )
    return out


def _check_one(path: Path, asy_search_dirs: List[str]) -> Tuple[List[str], List[str]]:
    """Return (info_msgs, warning_msgs) for path."""
    info: List[str] = []
    warn: List[str] = []
    src_fmt = detect_format(path)
    if src_fmt is None:
        warn.append(f'unknown source extension {path.suffix!r}')
        return info, warn

    src_text = read_text(path)

    # Initial extraction
    if src_fmt == 'asc':
        ap = AscParser(asy_search_dirs=[Path(d) for d in asy_search_dirs] or None)
        ap.parse_string(src_text)
        nl1 = NetlistExtractor(ap).extract()
        n1 = _count_components(nl1)
        info.append(f'asc -> netlist: {n1} components extracted')
        # Probe each SYMBOL for .asy availability — but only flag those
        # that would actually need it. Standard symbols (res/cap/ind/
        # voltage/diode/Q/M/J/...) have hardcoded pin offsets in
        # TERMINAL_OFFSETS_* tables, so they round-trip correctly even
        # when no .asy file is available (e.g. on Linux CI without
        # LTspice installed). Only multi-pin vendor symbols whose SPICE
        # prefix would fall back to "X" need .asy for topology.
        unresolved = []
        for sym in ap.symbols:
            kind = sym.symbol_type
            if not kind:
                continue
            # Standard LTspice symbol → skip the warning
            if SYMBOL_TO_SPICE.get(kind.lower()) is not None:
                continue
            offs = AsyParser.get_terminal_offsets(
                kind, sym.rotation or 'R0',
                search_dirs=[Path(d) for d in asy_search_dirs] or None,
            )
            if not offs:
                unresolved.append(f'{sym.inst_name or "?"} ({kind})')
        if unresolved:
            warn.append(
                f'{len(unresolved)} vendor symbol(s) with no resolvable .asy '
                f'(topology may drift on round-trip): '
                + ', '.join(unresolved[:5])
                + (' ...' if len(unresolved) > 5 else '')
            )

        # Round-trip
        from .parser.netlist_to_asc import NetlistToAsc
        asc2 = NetlistToAsc(asy_search_dirs=asy_search_dirs).convert_string(nl1)
        ap2 = AscParser(asy_search_dirs=[Path(d) for d in asy_search_dirs] or None)
        ap2.parse_string(asc2)
        nl2 = NetlistExtractor(ap2).extract()
        n2 = _count_components(nl2)
        info.append(f'asc -> netlist -> asc -> netlist: {n2} components')
        if n1 != n2:
            warn.append(f'component count drift: {n1} -> {n2}')
        g1 = _gnd_pin_positions(nl1)
        g2 = _gnd_pin_positions(nl2)
        common = set(g1) & set(g2)
        gnd_drift = sum(1 for k in common if g1[k] != g2[k])
        if gnd_drift:
            warn.append(f'GND-pin position drift on {gnd_drift} component(s)')
        else:
            info.append('GND-pin positions preserved on common components')

    elif src_fmt == 'cir':
        n1 = _count_components(src_text)
        info.append(f'netlist: {n1} components')
        from .parser.netlist_to_asc import NetlistToAsc
        asc = NetlistToAsc(asy_search_dirs=asy_search_dirs).convert_string(src_text)
        ap = AscParser(asy_search_dirs=[Path(d) for d in asy_search_dirs] or None)
        ap.parse_string(asc)
        nl2 = NetlistExtractor(ap).extract()
        n2 = _count_components(nl2)
        info.append(f'netlist -> asc -> netlist: {n2} components')
        if n1 != n2:
            warn.append(f'component count drift: {n1} -> {n2}')

    else:  # py
        # Just compile-check the script
        try:
            compile(src_text, str(path), 'exec')
            info.append('schemdraw script compiles OK')
        except SyntaxError as e:
            warn.append(f'script does not compile: {e}')

    return info, warn


def cmd_check(args) -> int:
    inputs = [Path(p) for p in args.inputs]
    any_warn = False
    any_err = False
    for inp in inputs:
        if not inp.is_file():
            print(f'{inp}: error: not a file', file=sys.stderr)
            any_err = True
            continue
        try:
            info, warn = _check_one(inp, args.asy_dir)
            print(f'== {inp} ==')
            for m in info:
                print(f'  [ok]   {m}')
            for w in warn:
                print(f'  [warn] {w}')
            verdict = 'PASS' if not warn else (
                'FAIL' if args.strict else 'PASS (with warnings)'
            )
            print(f'  -> {verdict}')
            if warn:
                any_warn = True
        except Exception as e:
            print(f'{inp}: error: {type(e).__name__}: {e}', file=sys.stderr)
            if args.traceback:
                traceback.print_exc()
            any_err = True
    if any_err:
        return 2
    if any_warn and args.strict:
        return 1
    return 0


# =============================================================================
# Mode: info
# =============================================================================

def _info_one(path: Path, asy_search_dirs: List[str]) -> dict:
    src_fmt = detect_format(path)
    if src_fmt is None:
        return {'path': str(path), 'error': f'unknown extension {path.suffix!r}'}

    src_text = read_text(path)
    out: dict = {
        'path': str(path),
        'format': src_fmt,
        'size_bytes': len(src_text),
    }

    if src_fmt == 'asc':
        ap = AscParser(asy_search_dirs=[Path(d) for d in asy_search_dirs] or None)
        ap.parse_string(src_text)
        netlist = NetlistExtractor(ap).extract()
        comp_types: Counter = Counter()
        sym_kinds: Counter = Counter()
        for line in netlist.split('\n'):
            s = line.strip()
            if s and s[0].isalpha() and not s.startswith('*') and not s.startswith('.'):
                comp_types[s[0].upper()] += 1
        for sym in ap.symbols:
            sym_kinds[sym.symbol_type] += 1
        out['component_count'] = sum(comp_types.values())
        out['component_types'] = dict(comp_types)
        out['symbol_kinds'] = dict(sym_kinds.most_common())
        # Symbol resolution rate.
        # Count standard symbols (hardcoded pin tables) AND .asy-resolved
        # symbols as "resolved" — both round-trip correctly.
        resolved = 0
        for sym in ap.symbols:
            if SYMBOL_TO_SPICE.get((sym.symbol_type or '').lower()) is not None:
                resolved += 1
                continue
            offs = AsyParser.get_terminal_offsets(
                sym.symbol_type, sym.rotation or 'R0',
                search_dirs=[Path(d) for d in asy_search_dirs] or None,
            )
            if offs:
                resolved += 1
        out['symbols_total'] = len(ap.symbols)
        out['symbols_asy_resolved'] = resolved
        out['subckt_blocks'] = sum(
            1 for t in ap.texts
            if t.is_directive and '.subckt' in t.text.lower()
        )
    elif src_fmt == 'cir':
        comp_types: Counter = Counter()
        for line in src_text.split('\n'):
            s = line.strip()
            if s and s[0].isalpha() and not s.startswith('*') and not s.startswith('.'):
                comp_types[s[0].upper()] += 1
        out['component_count'] = sum(comp_types.values())
        out['component_types'] = dict(comp_types)
        out['subckt_blocks'] = src_text.lower().count('.subckt')

    return out


def cmd_info(args) -> int:
    inputs = [Path(p) for p in args.inputs]
    results = []
    errors = 0
    for inp in inputs:
        if not inp.is_file():
            print(f'error: {inp}: not a file', file=sys.stderr)
            errors += 1
            continue
        try:
            info = _info_one(inp, args.asy_dir)
            results.append(info)
        except Exception as e:
            print(f'error: {inp}: {type(e).__name__}: {e}', file=sys.stderr)
            if args.traceback:
                traceback.print_exc()
            errors += 1

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for r in results:
            print(f'== {r["path"]} ==')
            for k, v in r.items():
                if k == 'path':
                    continue
                print(f'  {k}: {v}')
    return 1 if errors else 0


# =============================================================================
# main()
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='ltspice-convert',
        description='Convert between LTspice .asc, SPICE .cir, '
                    'and schemdraw Python scripts.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  ltspice-convert input.asc -o output.cir
  ltspice-convert *.asc -o build/ --to cir
  ltspice-convert --check input.asc
  ltspice-convert --info input.asc --json
""",
    )
    p.add_argument(
        'inputs', nargs='+', metavar='INPUT',
        help='input file(s): .asc, .cir, .net, .sp, .py',
    )
    p.add_argument(
        '-o', '--output', metavar='PATH',
        help='output path (file when 1 input; directory when many)',
    )
    p.add_argument(
        '--to', choices=['asc', 'cir', 'py'], metavar='FMT',
        help='target format (asc/cir/py); inferred from -o extension or '
             'defaults to the "opposite" of input',
    )
    p.add_argument(
        '--check', action='store_true',
        help='lint mode: run round-trip and report drift / .asy gaps',
    )
    p.add_argument(
        '--strict', action='store_true',
        help='with --check, exit 1 on any warning',
    )
    p.add_argument(
        '--info', action='store_true',
        help='print a summary of the input file(s) and exit',
    )
    p.add_argument(
        '--json', action='store_true',
        help='with --info, emit machine-readable JSON',
    )
    p.add_argument(
        '--asy-dir', action='append', default=[], metavar='DIR',
        help='additional .asy search directory (repeatable); merged with '
             'the LTSPICE_ASY_SEARCH_PATH env var, CLI flags take priority',
    )
    p.add_argument(
        '--traceback', action='store_true',
        help='print full Python traceback on errors',
    )
    p.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # Merge CLI --asy-dir with env var (CLI takes priority).
    env_dirs = []
    env_val = os.environ.get('LTSPICE_ASY_SEARCH_PATH', '')
    if env_val:
        env_dirs = [d for d in env_val.split(os.pathsep) if d.strip()]
    args.asy_dir = list(args.asy_dir) + env_dirs

    if args.info:
        return cmd_info(args)
    if args.check:
        return cmd_check(args)
    return cmd_convert(args)


if __name__ == '__main__':
    sys.exit(main())
