#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-trip conversion tests: .cir <-> schemdraw <-> .asc

Tests:
1. .cir -> schemdraw script compiles and executes
2. .cir <-> schemdraw round-trip preserves nodes and directives
3. .cir <-> .asc round-trip preserves component count
4. Two routes (direct .asc vs via schemdraw) produce equivalent .asc
"""
from __future__ import annotations

import os
from pathlib import Path
import pytest

from ltspice_converter.parser.cir_to_schemdraw import cir_string_to_schemdraw
from ltspice_converter.parser.schemdraw_to_cir import schemdraw_script_to_cir
from ltspice_converter.parser.netlist_to_asc import NetlistToAsc
from ltspice_converter.parser.asc_parser import AscParser, NetlistExtractor


# =============================================================================
# Author-authored test circuits (minimal, no third-party derivative content)
# =============================================================================
CIRCUITS = {
    "rc_lowpass": """* RC Lowpass Filter
V1 in 0 AC 1
R1 in out 1k
C1 out 0 1u
.ac dec 20 1 100k
.end""",
    "rlc_series": """* RLC Series
V1 in 0 SINE(0 1 1k)
R1 in n1 100
L1 n1 out 10m
C1 out 0 1u
.tran 10m
.end""",
    "voltage_divider": """* Voltage Divider
V1 in 0 5
R1 in out 1k
R2 out 0 1k
.op
.end""",
    "pi_filter": """* Pi Filter
V1 in 0 AC 1
C1 in 0 10u
L1 in out 100m
C2 out 0 10u
R1 out 0 50
.ac dec 20 1 100k
.end""",
    "bandpass": """* RLC Bandpass
V1 in 0 AC 1
R1 in n1 100
L1 n1 out 10m
C1 out 0 100n
R2 out 0 10k
.ac dec 50 100 100k
.end""",
}

DIRECTIVE_CHECKS = {
    "rc_lowpass": ".ac",
    "rlc_series": ".tran",
    "voltage_divider": ".op",
    "pi_filter": ".ac",
    "bandpass": ".ac",
}


# =============================================================================
# Helpers
# =============================================================================
def _count_components(netlist: str) -> int:
    count = 0
    for line in netlist.strip().split("\n"):
        line = line.strip()
        if line and line[0].isalpha() and not line.startswith("*") and not line.startswith("."):
            count += 1
    return count


def _count_signal_nodes(netlist: str) -> int:
    nodes = set()
    for line in netlist.strip().split("\n"):
        parts = line.split()
        if len(parts) >= 3 and parts[0][0].isalpha() and parts[0][0] != "*":
            for p in parts[1:3]:
                if p != "0" and p.lower() != "gnd":
                    nodes.add(p)
    return len(nodes)


def _has_directive(netlist: str, prefix: str) -> bool:
    for line in netlist.strip().split("\n"):
        if line.strip().lower().startswith(prefix.lower()):
            return True
    return False


def _asc_to_netlist_pure_python(asc_text: str) -> str:
    """Pure-Python ASC -> netlist (no LTspice.exe required)."""
    parser = AscParser()
    parser.parse_string(asc_text)
    extractor = NetlistExtractor(parser)
    return extractor.extract()


# =============================================================================
# Tests
# =============================================================================
@pytest.mark.parametrize("name,cir", list(CIRCUITS.items()))
def test_cir_to_schemdraw_compiles(name, cir):
    script = cir_string_to_schemdraw(cir, name)
    compile(script, f"<{name}>", "exec")


@pytest.mark.parametrize("name,cir", list(CIRCUITS.items()))
def test_cir_to_schemdraw_executes(name, cir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = cir_string_to_schemdraw(cir, name)
    exec_globals: dict = {}
    exec(script, exec_globals)


@pytest.mark.parametrize("name,cir", list(CIRCUITS.items()))
def test_schemdraw_roundtrip_nodes(name, cir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = cir_string_to_schemdraw(cir, name)
    recovered = schemdraw_script_to_cir(script, name)
    assert _count_signal_nodes(cir) == _count_signal_nodes(recovered), \
        f"{name}: node count changed: {cir!r} -> {recovered!r}"


@pytest.mark.parametrize("name,cir", list(CIRCUITS.items()))
def test_schemdraw_roundtrip_directives(name, cir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = cir_string_to_schemdraw(cir, name)
    recovered = schemdraw_script_to_cir(script, name)
    expected = DIRECTIVE_CHECKS.get(name)
    if expected:
        assert _has_directive(recovered, expected), \
            f"{name}: directive {expected!r} lost in round-trip"


@pytest.mark.parametrize("name,cir", list(CIRCUITS.items()))
def test_cir_to_asc_roundtrip_component_count(name, cir):
    asc = NetlistToAsc().convert_string(cir)
    recovered = _asc_to_netlist_pure_python(asc)
    assert _count_components(cir) == _count_components(recovered), \
        f"{name}: component count changed via .asc round-trip"


@pytest.mark.parametrize("name,cir", list(CIRCUITS.items()))
def test_route_a_equals_route_b(name, cir, tmp_path, monkeypatch):
    """Direct .cir->.asc and via-schemdraw .cir->schemdraw->.cir->.asc agree."""
    monkeypatch.chdir(tmp_path)
    asc_a = NetlistToAsc().convert_string(cir)
    script = cir_string_to_schemdraw(cir, name)
    cir_b = schemdraw_script_to_cir(script, name)
    asc_b = NetlistToAsc().convert_string(cir_b)

    if asc_a == asc_b:
        return  # identical

    # otherwise require structural equivalence: same WIRE / SYMBOL count
    assert asc_a.count("WIRE") == asc_b.count("WIRE") and \
        asc_a.count("SYMBOL") == asc_b.count("SYMBOL"), \
        f"{name}: route A and route B disagree structurally"


def test_unknown_vendor_symbol_preserved():
    """Regression: vendor-specific SYMBOL (e.g. ISO16750-2) must survive
    a .asc -> netlist -> .asc round-trip. Previously such symbols were
    silently dropped because the extractor produced a 2-token U-statement
    with no model name, which NetlistParser rejected.
    """
    # Hand-crafted minimal vendor-symbol .asc -- same shape as real
    # LTspice vendor templates but no proprietary content.
    asc = """Version 4
SHEET 1 880 680
WIRE 128 -64 128 -96
WIRE 128 48 128 16
FLAG 128 48 0
SYMBOL ACME_PROPRIETARY_BLOCK 128 -64 R0
SYMATTR InstName U1
SYMBOL ACME_PROPRIETARY_BLOCK 256 -64 R0
SYMATTR InstName U2
TEXT 0 100 Left 2 !.tran 1m
"""
    parser = AscParser()
    parser.parse_string(asc)
    netlist = NetlistExtractor(parser).extract()
    # Both vendor symbols must appear in the netlist with the model name
    assert "ACME_PROPRIETARY_BLOCK" in netlist.lower() or "acme_proprietary_block" in netlist.lower()

    n1 = _count_components(netlist)
    assert n1 == 2, f"expected 2 components, got {n1}: {netlist!r}"

    # Round-trip back to .asc
    asc2 = NetlistToAsc().convert_string(netlist)
    parser2 = AscParser()
    parser2.parse_string(asc2)
    netlist2 = NetlistExtractor(parser2).extract()
    n2 = _count_components(netlist2)
    assert n1 == n2, (
        f"component count drifted in round-trip: {n1} -> {n2}\n"
        f"asc2: {asc2}\nnetlist2: {netlist2}"
    )


def test_modelless_bjt_round_trip():
    """Regression for v0.3.8 D2 fix: BJT symbol without SYMATTR Value must
    survive a .asc -> netlist -> .asc round-trip. Previously the extractor
    emitted ``Q1 N001 N002 0`` (4 tokens, no model -- LTspice allows this
    for npn/pnp via the built-in default model), and NetlistParser silently
    dropped the 4-token form because it required len(parts) >= 5.
    """
    asc = """Version 4
SHEET 1 880 680
WIRE 64 -16 64 -48
WIRE 64 96 64 64
FLAG 64 96 0
FLAG 64 -48 VCC
SYMBOL npn 32 0 R0
SYMATTR InstName Q1
TEXT 0 200 Left 2 !.op
"""
    parser = AscParser()
    parser.parse_string(asc)
    netlist = NetlistExtractor(parser).extract()
    # Confirm the trigger condition: BJT line has no model token (4 tokens).
    q_lines = [l for l in netlist.split("\n") if l.strip().upper().startswith("Q")]
    assert q_lines, "expected Q line in extracted netlist"
    assert len(q_lines[0].split()) == 4, (
        f"expected modelless 4-token Q line, got: {q_lines[0]!r}"
    )

    asc2 = NetlistToAsc().convert_string(netlist)
    parser2 = AscParser()
    parser2.parse_string(asc2)
    netlist2 = NetlistExtractor(parser2).extract()
    q_lines2 = [l for l in netlist2.split("\n") if l.strip().upper().startswith("Q")]
    assert q_lines2, (
        f"BJT dropped on round-trip; netlist2 was:\n{netlist2}"
    )


def test_instname_prefix_fix_for_non_spice_letter():
    """Regression for v0.3.8 D2 fix: a user-given InstName starting with a
    letter that is not a SPICE prefix (e.g. ``NTC`` on a ``res`` symbol)
    must be prefix-fixed to ``R§NTC`` so the netlist line begins with R
    and round-trips. Previously the gate ``name[0] in _SPICE_PREFIXES``
    excluded ``N`` (not a SPICE prefix for res) and the component was
    emitted as ``NTC ...`` -- which NetlistParser rejected (N is not a
    known device prefix).
    """
    asc = """Version 4
SHEET 1 880 680
WIRE 64 16 64 -16
WIRE 64 128 64 96
FLAG 64 128 0
FLAG 64 -16 IN
SYMBOL res 48 16 R0
SYMATTR InstName NTC
SYMATTR Value R={ if(time<1m, 5, 0.5) }
TEXT 0 200 Left 2 !.tran 2m
"""
    parser = AscParser()
    parser.parse_string(asc)
    netlist = NetlistExtractor(parser).extract()
    # The prefix-fix must rename NTC -> R§NTC so the netlist line starts
    # with R (a valid SPICE resistor prefix).
    nl_lower = netlist.lower()
    assert "r§ntc" in nl_lower or "rntc" in nl_lower, (
        f"InstName NTC not prefix-fixed to R-prefix form; netlist:\n{netlist}"
    )

    n_components_src = sum(
        1 for line in netlist.split("\n")
        if line.strip() and line.strip()[0].isalpha() and line.strip()[0] not in ".*"
    )

    asc2 = NetlistToAsc().convert_string(netlist)
    parser2 = AscParser()
    parser2.parse_string(asc2)
    netlist2 = NetlistExtractor(parser2).extract()
    n_components_rt = sum(
        1 for line in netlist2.split("\n")
        if line.strip() and line.strip()[0].isalpha() and line.strip()[0] not in ".*"
    )
    assert n_components_src == n_components_rt, (
        f"component count drifted: {n_components_src} -> {n_components_rt}\n"
        f"netlist:  {netlist}\nnetlist2: {netlist2}"
    )


def test_sym_hint_ordering_does_not_misclassify_next_component():
    """Regression for v0.3.10 D3-1 fix: the ``* @sym=<kind>`` comment must
    precede the component line it describes -- before this fix the
    NetlistExtractor appended the hint AFTER the component, so the parser
    (which associates hints with the NEXT component) tied every hint to
    the wrong component, and the trailing hint orphaned onto an unrelated
    next-class component (e.g. an ``Rload`` resistor receiving a
    ``polcap`` hint, getting re-emitted as a SYMBOL polcap, and then
    being dropped on re-extraction because polcap → C-prefix vs name R).
    """
    asc = """Version 4
SHEET 1 880 680
WIRE 0 -32 0 -64
WIRE 0 80 0 48
WIRE 256 -32 256 -64
WIRE 256 80 256 48
FLAG 0 80 0
FLAG 0 -64 NA
FLAG 256 80 0
FLAG 256 -64 NB
SYMBOL polcap 0 -32 R0
SYMATTR InstName C1
SYMATTR Value 100u
SYMBOL res 240 -48 R0
SYMATTR InstName Rload
SYMATTR Value 100
TEXT 0 200 Left 2 !.tran 1m
"""
    parser = AscParser()
    parser.parse_string(asc)
    netlist = NetlistExtractor(parser).extract()
    n1 = sum(
        1 for line in netlist.split("\n")
        if line.strip() and line.strip()[0].isalpha() and line.strip()[0] not in ".*"
    )
    assert n1 == 2, f"expected 2 components extracted, got {n1}: {netlist!r}"

    asc2 = NetlistToAsc().convert_string(netlist)
    parser2 = AscParser()
    parser2.parse_string(asc2)
    netlist2 = NetlistExtractor(parser2).extract()
    n2 = sum(
        1 for line in netlist2.split("\n")
        if line.strip() and line.strip()[0].isalpha() and line.strip()[0] not in ".*"
    )
    assert n1 == n2, (
        f"component count drifted: {n1} -> {n2}; the polcap hint may have "
        f"leaked onto Rload\nnetlist1:  {netlist}\nnetlist2:  {netlist2}"
    )
    # Specifically: Rload must remain an R-prefix line (not C§Rload).
    rlines = [l for l in netlist2.split("\n") if "load" in l.lower()]
    assert rlines, f"Rload missing from regenerated netlist:\n{netlist2}"
    assert rlines[0].strip()[0].upper() == "R", (
        f"Rload reclassified by orphan polcap hint: {rlines[0]!r}"
    )


def test_one_pin_x_subcircuit_round_trip():
    """Regression for v0.3.10 D3-2 fix: a 1-pin X-prefix vendor symbol
    (e.g. PowerSim CONST: 1 output pin, constant value) must round-trip.
    Before this fix the extractor produced a 2-token line ``X10 N001``
    (no subckt name), which NetlistParser dropped as too-short.
    """
    asc = """Version 4
SHEET 1 880 680
WIRE 0 0 0 -32
FLAG 0 0 OUT
SYMBOL CONST 0 -32 R0
SYMATTR InstName X10
SYMATTR SpiceLine K=42
TEXT 0 200 Left 2 !.tran 1m
"""
    parser = AscParser()
    parser.parse_string(asc)
    netlist = NetlistExtractor(parser).extract()
    x_lines = [l for l in netlist.split("\n") if l.strip().startswith("X10")]
    assert x_lines, f"X10 missing from extracted netlist:\n{netlist}"
    # Must include a subckt name token, not just ``X10 OUT``.
    assert len(x_lines[0].split()) >= 3, (
        f"1-pin X emitted with no subckt name: {x_lines[0]!r}; "
        f"NetlistParser would drop this as too-short."
    )

    asc2 = NetlistToAsc().convert_string(netlist)
    parser2 = AscParser()
    parser2.parse_string(asc2)
    netlist2 = NetlistExtractor(parser2).extract()
    x_lines2 = [l for l in netlist2.split("\n") if l.strip().startswith("X10")]
    assert x_lines2, (
        f"1-pin X dropped on round-trip; netlist2:\n{netlist2}"
    )


def test_subckt_body_round_trip():
    """.subckt body must survive .cir -> .asc -> .cir byte-equal.

    Regression for the C3 fix: previously a `.subckt` block's body
    components leaked into the top-level component list and were
    re-emitted as ordinary SYMBOLs outside the block, so the
    regenerated .asc had an empty `.subckt ... .ends` shell.
    """
    src = (
        Path(__file__).parent / "fixtures" / "bidirectional" /
        "00_converter_test_subckt_diac.cir"
    ).read_text(encoding="utf-8")
    import ltspice_converter as lc
    asc = lc.netlist_to_asc(src)
    recovered = lc.asc_to_netlist(asc)

    def subckt_body(text):
        out = []
        capturing = False
        for line in text.split("\n"):
            ll = line.strip().lower()
            if ll.startswith(".subckt mydiac"):
                capturing = True
            if capturing:
                out.append(line.strip())
                if ll.startswith(".ends"):
                    break
        return out

    src_body = subckt_body(src)
    recovered_body = subckt_body(recovered)
    assert src_body == recovered_body, (
        f"subckt body drifted:\n  src:       {src_body}\n"
        f"  recovered: {recovered_body}"
    )


def test_top_level_api_smoke():
    """Public API smoke test."""
    import ltspice_converter as lc
    netlist = CIRCUITS["rc_lowpass"]
    script = lc.netlist_to_schemdraw(netlist, "rc")
    assert "schemdraw" in script
    recovered = lc.schemdraw_to_netlist(script, "rc")
    assert _count_signal_nodes(netlist) == _count_signal_nodes(recovered)
