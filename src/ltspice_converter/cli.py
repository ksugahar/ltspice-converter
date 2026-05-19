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
import re
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

            # B5: surface silent drops to stderr. The NetlistParser
            # would otherwise quietly discard malformed lines.
            _warn_unparsed_lines(inp, src_text, src_fmt, args)
        except Exception as e:
            print(f'error: {inp}: {type(e).__name__}: {e}', file=sys.stderr)
            if args.traceback:
                traceback.print_exc()
            errors += 1
    return 1 if errors else 0


def _warn_unparsed_lines(inp: Path, src_text: str, src_fmt: str, args) -> None:
    """B5: emit stderr warnings for lines the parser silently dropped.

    For .cir input, run NetlistParser and check `unparsed_lines`.
    For .asc input, extract the netlist first then check the same.
    For .py input, skip — schemdraw script semantics are too loose.
    """
    if src_fmt == 'py':
        return
    try:
        from .parser.netlist_to_asc import NetlistParser
        if src_fmt == 'asc':
            ap = AscParser(
                asy_search_dirs=[Path(d) for d in args.asy_dir] or None
            )
            ap.parse_string(src_text)
            netlist_text = NetlistExtractor(ap).extract()
        else:  # cir
            netlist_text = src_text
        np_parser = NetlistParser()
        np_parser.parse_string(netlist_text)
        for lno, line in np_parser.unparsed_lines:
            msg = _format_unparsed_line(lno, line)
            print(f'warning: {inp.name}: {msg}', file=sys.stderr)
    except Exception:
        # B5 is best-effort; do not let a parse failure here mask
        # the successful primary conversion.
        if args.traceback:
            traceback.print_exc()


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


# =============================================================================
# Static checks (C5)
# =============================================================================

_SUBCKT_RE = re.compile(r'^\s*\.subckt\s+', re.IGNORECASE)
_ENDS_RE   = re.compile(r'^\s*\.ends\b', re.IGNORECASE)
_MODEL_RE  = re.compile(r'^\s*\.model\s+(\S+)\s+(\S+)', re.IGNORECASE)
_SUBCKT_NAME_RE = re.compile(r'^\s*\.subckt\s+(\S+)', re.IGNORECASE)
_PARAM_RE  = re.compile(r'^\s*\.param\b(.*)$', re.IGNORECASE)
_REF_RE    = re.compile(r'\{([^{}]+)\}')  # `{R1+10k}` -> 'R1+10k'


def _split_top_level_lines(netlist: str) -> List[str]:
    """Return only TOP-LEVEL netlist lines, dropping `.subckt ... .ends`
    bodies and comments. Used by static checks so symbols inside a
    subckt do not pollute top-level duplicate / floating analyses.
    """
    out = []
    in_subckt = 0
    for line in netlist.split('\n'):
        if _SUBCKT_RE.match(line):
            in_subckt += 1
            continue
        if _ENDS_RE.match(line):
            in_subckt = max(0, in_subckt - 1)
            continue
        if in_subckt:
            continue
        s = line.strip()
        if not s or s.startswith('*'):
            continue
        out.append(s)
    return out


def _component_lines(lines: List[str]) -> List[Tuple[str, List[str]]]:
    """Return [(name, tokens-after-name)] for component lines (R/C/L/V/I/...).
    Excludes directives and the title.
    """
    out = []
    for line in lines:
        if line.startswith('.'):
            continue
        parts = line.split()
        if parts and parts[0][0].isalpha() and parts[0][0].upper() in 'RCLVIDQMJXEFGHBSTUKA':
            out.append((parts[0], parts[1:]))
    return out


def static_checks(netlist: str) -> List[str]:
    """Return a list of warning strings from static netlist analysis.

    Checks:
      1. Duplicate instance names (R1 appearing twice at top level).
      2. Floating nodes (a node that only one component touches).
      3. Orphan .model directives (no component references the model).
      4. Undefined model references (component lists model that no
         .model defines -- often inside .lib/.include, so just a warning).
      5. Undefined .param references (`{NAME}` token without a .param NAME=).
    """
    warnings: List[str] = []
    top = _split_top_level_lines(netlist)
    comps = _component_lines(top)

    # --- 1. Duplicate instance names ---
    seen: Counter = Counter(c[0] for c in comps)
    dups = [n for n, k in seen.items() if k > 1]
    if dups:
        warnings.append(
            f'duplicate instance name(s): {", ".join(sorted(dups)[:5])}'
            + (' ...' if len(dups) > 5 else '')
        )

    # --- 2. Floating nodes (only 1 component touching a non-GND node) ---
    # Heuristic: collect tokens that look like node names from positions
    # 1..K of each component line. Identifying which tokens are nodes vs
    # model names is fragile across SPICE dialects, so use a conservative
    # rule: any token that another component also names is a node (we
    # are not trying to be a full parser here).
    node_touches: Counter = Counter()
    for name, rest in comps:
        # Number of "node positions" is element-class-dependent:
        # R/C/L/V/I/D: 2, BJT/MOSFET/JFET: 3 (substrate ignored), X: variable.
        cls = name[0].upper()
        if cls in 'RCLVID':
            ks = rest[:2]
        elif cls in 'QM':
            ks = rest[:3] if len(rest) >= 3 else rest
        elif cls == 'J':
            ks = rest[:3] if len(rest) >= 3 else rest
        elif cls == 'X':
            # subckt: last token is model name, rest are nodes
            ks = rest[:-1] if len(rest) >= 2 else rest
        elif cls == 'U':
            # opamp: 3-pin (no model) or 5-pin (last token is model)
            if len(rest) == 3:
                ks = rest
            elif len(rest) >= 5:
                ks = rest[:-1]
            else:
                ks = rest
        else:
            ks = rest[:2]  # conservative
        for tok in ks:
            node_touches[tok] += 1
    floating = [
        n for n, k in node_touches.items()
        if k == 1 and n != '0' and n.lower() != 'gnd'
        # Reject obvious non-nodes (model names, values with units)
        and not n[0].isdigit()
        and '{' not in n
    ]
    if floating:
        warnings.append(
            f'floating node(s) (only one connection): {", ".join(sorted(floating)[:5])}'
            + (' ...' if len(floating) > 5 else '')
        )

    # --- 3 & 4. Model / subcircuit declared-vs-used cross-reference ---
    #
    # Two separate namespaces. SPICE rules:
    #   - D / Q / M / J components reference a `.model <name> <type>`
    #     definition (built-in device-level model).
    #   - X components are SUBCKT INVOCATIONS: their last token names
    #     a `.subckt <name> ...` block, NOT a .model.
    #
    # `.model` and `.subckt` MUST be checked separately or X-class
    # invocations get falsely flagged as "undefined .model" (the
    # historical C5-fp-1) and subckt-internal .model declarations get
    # falsely flagged as "never used" (C5-fp-2).
    #
    # Scope: only TOP-LEVEL .model declarations count for the orphan
    # check. .model lines inside a `.subckt ... .ends` body have local
    # scope -- they are referenced by components defined inside that
    # same body, which `comps` (top-level only) deliberately does not
    # see. Counting them would always produce false orphans.
    defined_models = set()
    for line in top:
        m = _MODEL_RE.match(line)
        if m:
            defined_models.add(m.group(1))
    defined_subckts = set()
    for line in netlist.split('\n'):  # .subckt headers are findable anywhere
        m = _SUBCKT_NAME_RE.match(line)
        if m:
            defined_subckts.add(m.group(1))

    referenced_models = set()
    referenced_subckts = set()
    for name, rest in comps:
        cls = name[0].upper()
        if not rest:
            continue
        if cls == 'X':
            referenced_subckts.add(rest[-1])
        elif cls in 'DQMJ':
            referenced_models.add(rest[-1])

    orphan_models = defined_models - referenced_models
    orphan_subckts = defined_subckts - referenced_subckts
    undefined_models = referenced_models - defined_models
    undefined_subckts = referenced_subckts - defined_subckts

    if orphan_models:
        warnings.append(
            f'.model declared but never used: {", ".join(sorted(orphan_models)[:5])}'
            + (' ...' if len(orphan_models) > 5 else '')
        )
    if orphan_subckts:
        warnings.append(
            f'.subckt declared but never used: {", ".join(sorted(orphan_subckts)[:5])}'
            + (' ...' if len(orphan_subckts) > 5 else '')
        )
    # Tone down undefined-model warning: many circuits rely on
    # .lib/.include which the parser does not chase. Only flag if the
    # name does NOT look like a known LTspice standard model.
    suspicious_undef = [
        m for m in undefined_models
        if not _looks_like_standard_model(m)
    ]
    if suspicious_undef:
        warnings.append(
            f'model(s) referenced but not defined inline '
            f'(.lib/.include not chased): {", ".join(sorted(suspicious_undef)[:5])}'
            + (' ...' if len(suspicious_undef) > 5 else '')
        )
    if undefined_subckts:
        warnings.append(
            f'subckt(s) referenced but not defined inline '
            f'(.lib/.include not chased): {", ".join(sorted(undefined_subckts)[:5])}'
            + (' ...' if len(undefined_subckts) > 5 else '')
        )

    # --- 5. {PARAM} references without .param ---
    defined_params = set()
    for line in netlist.split('\n'):
        m = _PARAM_RE.match(line)
        if m:
            # ".param A=1 B=2" -> A, B
            for assign in m.group(1).split():
                if '=' in assign:
                    defined_params.add(assign.split('=', 1)[0].strip())
    referenced_params = set()
    for line in top:
        for ref in _REF_RE.findall(line):
            # Pull out identifiers from `{A+B*2-C}`
            for tok in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', ref):
                # Filter out function names + numeric units
                if tok.lower() in ('exp', 'log', 'log10', 'sin', 'cos', 'tan',
                                    'abs', 'sqrt', 'pwr', 'min', 'max',
                                    'pi', 'e', 'time', 'temp', 'tnom',
                                    'k', 'm', 'meg', 'g', 'u', 'n', 'p', 'f'):
                    continue
                referenced_params.add(tok)
    undefined_params = referenced_params - defined_params
    if undefined_params:
        warnings.append(
            f'parameter(s) referenced without .param definition: '
            f'{", ".join(sorted(undefined_params)[:5])}'
            + (' ...' if len(undefined_params) > 5 else '')
        )

    return warnings


# Conservative list of LTspice-bundled model names that commonly appear
# without an inline .model directive (they live in standard.dio/bjt/
# etc.). Used to suppress false-positive "undefined model" warnings.
_KNOWN_STANDARD_MODELS = {
    'd', 'd1n4148', 'd1n4001', 'd1n4007', 'd1n5400', 'd1n5817',
    '1n4148', '1n4001', '1n4007', '1n5400', '1n5817',
    'npn', 'pnp', 'nmos', 'pmos', 'njf', 'pjf', 'nigbt',
    'q2n2222', 'q2n3904', 'q2n3906', '2n2222', '2n3904', '2n3906',
    'mn', 'mp', 'irf540', 'irfp250', 'irf9540',
    'sw', 'ld1117', 'lm317',
}


def _format_unparsed_line(lno: int, line: str) -> str:
    """B4: format an unparsed-line warning with a 'did you mean?' hint
    when the user's first token resembles a known SPICE prefix.
    """
    import difflib
    parts = line.strip().split()
    if not parts:
        return f'line {lno}: empty (unexpected)'
    first = parts[0]
    head = first[0].upper() if first else ''
    # Known SPICE prefixes
    known_prefixes = list('RCLVIDQMJXEFGHBSTUKA')
    suggestions = difflib.get_close_matches(head, known_prefixes, n=1, cutoff=0.0)
    # Common typo dictionary (whole-word, case-insensitive)
    typo_map = {
        'resistor': 'res (R<name> n1 n2 value)',
        'capacitor': 'cap (C<name> n1 n2 value)',
        'inductor': 'ind (L<name> n1 n2 value)',
        'voltage': 'voltage source (V<name> n+ n- value)',
        'current': 'current source (I<name> n+ n- value)',
        'diode': 'diode (D<name> anode cathode model)',
        'transistor': 'BJT (Q<name> C B E model) / MOSFET (M<name> D G S B model)',
        'subckt': 'X<name> n1 n2 ... subckt_name (note: subckt invocations start with X)',
    }
    hint = typo_map.get(first.lower())
    if hint:
        return (f'line {lno}: unrecognised element {first!r} '
                f'(did you mean: {hint}?)')
    if suggestions and suggestions[0] != head:
        return (f'line {lno}: unrecognised element {first!r} '
                f'(SPICE elements start with one of '
                f'R/C/L/V/I/D/Q/M/J/X/...)')
    return f'line {lno}: unrecognised element {first!r}'


def _looks_like_standard_model(name: str) -> bool:
    n = name.lower().strip('"\'')
    if n in _KNOWN_STANDARD_MODELS:
        return True
    # Most LTspice/SPICE bundled models follow vendor prefixes
    for pref in ('lt', 'lm', 'ad', 'op', 'tl', 'ne', 'ma', 'irf', 'irfp',
                 '1n', '2n', 'bav', 'bat', 'bzx', 'fet', 'd1n', 'q2n'):
        if n.startswith(pref):
            return True
    return False


def check_text(src_text: str, src_fmt: str,
                asy_search_dirs: Optional[List[str]] = None
                ) -> Tuple[List[str], List[str]]:
    """Run --check logic on in-memory text (no path / no file I/O).

    Returns (info_msgs, warning_msgs). Used by both the CLI ``--check``
    command and the MCP ``check_netlist`` / ``check_asc`` tools so AI
    agents can lint generated SPICE without touching the filesystem.

    Args:
        src_text: file content as text
        src_fmt: ``'asc'``, ``'cir'``, or ``'py'`` (caller's responsibility
            to detect; CLI uses :func:`detect_format`)
        asy_search_dirs: optional list of directory paths for vendor `.asy`
            symbol resolution (combined with ``LTSPICE_ASY_SEARCH_PATH``
            env var by the AscParser/NetlistToAsc layer).
    """
    info: List[str] = []
    warn: List[str] = []
    if asy_search_dirs is None:
        asy_search_dirs = []
    if src_fmt not in ('asc', 'cir', 'py'):
        warn.append(f'unknown source format {src_fmt!r}')
        return info, warn

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

        # C5: static netlist checks on the extracted netlist
        for w in static_checks(nl1):
            warn.append(w)

    elif src_fmt == 'cir':
        n1 = _count_components(src_text)
        info.append(f'netlist: {n1} components')
        from .parser.netlist_to_asc import NetlistToAsc, NetlistParser
        # B4: surface unparsed lines from NetlistParser (silent drops
        # are the #1 cause of bug reports that we cannot reproduce).
        np_parser = NetlistParser()
        np_parser.parse_string(src_text)
        for lno, line in np_parser.unparsed_lines:
            warn.append(_format_unparsed_line(lno, line))

        asc = NetlistToAsc(asy_search_dirs=asy_search_dirs).convert_string(src_text)
        ap = AscParser(asy_search_dirs=[Path(d) for d in asy_search_dirs] or None)
        ap.parse_string(asc)
        nl2 = NetlistExtractor(ap).extract()
        n2 = _count_components(nl2)
        info.append(f'netlist -> asc -> netlist: {n2} components')
        if n1 != n2:
            warn.append(f'component count drift: {n1} -> {n2}')
        # C5: static checks on the source netlist
        for w in static_checks(src_text):
            warn.append(w)

    else:  # py
        # Just compile-check the script
        try:
            compile(src_text, '<schemdraw>', 'exec')
            info.append('schemdraw script compiles OK')
        except SyntaxError as e:
            warn.append(f'script does not compile: {e}')

    return info, warn


def _check_one(path: Path, asy_search_dirs: List[str]) -> Tuple[List[str], List[str]]:
    """File-based wrapper around :func:`check_text` (used by the CLI)."""
    src_fmt = detect_format(path)
    if src_fmt is None:
        return [], [f'unknown source extension {path.suffix!r}']
    return check_text(read_text(path), src_fmt, asy_search_dirs)


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

def info_text(src_text: str, src_fmt: str,
              asy_search_dirs: Optional[List[str]] = None) -> dict:
    """Run --info logic on in-memory text. Used by both the CLI and MCP.

    Args:
        src_text: file content as text
        src_fmt: ``'asc'``, ``'cir'``, or ``'py'``
        asy_search_dirs: optional list of vendor `.asy` search dirs
    """
    if asy_search_dirs is None:
        asy_search_dirs = []
    if src_fmt not in ('asc', 'cir', 'py'):
        return {'error': f'unknown source format {src_fmt!r}'}

    out: dict = {
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


def _info_one(path: Path, asy_search_dirs: List[str]) -> dict:
    """File-based wrapper around :func:`info_text` (used by the CLI)."""
    src_fmt = detect_format(path)
    if src_fmt is None:
        return {'path': str(path), 'error': f'unknown extension {path.suffix!r}'}
    out = info_text(read_text(path), src_fmt, asy_search_dirs)
    out['path'] = str(path)
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
