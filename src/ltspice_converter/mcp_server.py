"""MCP server for ltspice-converter.

Exposes the four conversion functions plus a lint tool and a stats tool
as MCP tools so AI agents (Claude Code, Cursor, etc.) can author,
convert, and validate LTspice .asc, SPICE .cir, and schemdraw Python
scripts on demand.

Run via the console script ``mcp-ltspice`` (installed by
``pip install ltspice-converter[mcp]``).
"""
from __future__ import annotations

import sys
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from . import conversion
from . import cli as _cli


mcp = FastMCP("mcp-ltspice")


@mcp.tool()
def netlist_to_schemdraw(netlist: str, name: str = "circuit") -> str:
    """Convert a SPICE netlist to a runnable schemdraw Python script.

    Args:
        netlist: SPICE netlist text (with .end). E.g.
            'V1 in 0 AC 1\\nR1 in out 1k\\nC1 out 0 1u\\n.ac dec 20 1 100k\\n.end'
        name: Circuit name for the output file (default 'circuit').

    Returns:
        Runnable Python script that uses schemdraw to draw the circuit.
        Supported elements: R, C, L, V, I, D, BJT (NPN/PNP), MOSFET,
        JFET, opamp.
    """
    return conversion.netlist_to_schemdraw(netlist, name)


@mcp.tool()
def schemdraw_to_netlist(script: str, title: str = "circuit") -> str:
    """Convert a schemdraw Python script to a SPICE netlist.

    Args:
        script: schemdraw Python script text (must create a Drawing).
        title: Title for the netlist (default 'circuit').

    Returns:
        SPICE netlist (.cir) text ready for LTspice simulation.
    """
    return conversion.schemdraw_to_netlist(script, title)


@mcp.tool()
def netlist_to_asc(netlist: str,
                   asy_search_dirs: Optional[List[str]] = None) -> str:
    """Convert a SPICE netlist (.cir) to an LTspice schematic (.asc).

    Args:
        netlist: SPICE netlist text.
        asy_search_dirs: optional list of directory paths to search for
            vendor `.asy` symbol files (e.g. LTspiceControlLibrary,
            LTspicePowerSim). Combined with the ``LTSPICE_ASY_SEARCH_PATH``
            env var.

    Returns:
        LTspice .asc schematic text. Can be saved as a .asc file and
        opened in LTspice.
    """
    return conversion.netlist_to_asc(netlist, asy_search_dirs=asy_search_dirs)


@mcp.tool()
def asc_to_netlist(asc_text: str,
                   use_ltspice: bool = False,
                   asy_search_dirs: Optional[List[str]] = None) -> str:
    """Convert an LTspice schematic (.asc) to a SPICE netlist.

    Args:
        asc_text: LTspice .asc schematic text.
        use_ltspice: If True and LTspice.exe is detected, use LTspice's
            -netlist mode for canonical anonymous-node numbering. Default
            False (pure-Python, no external dependency).
        asy_search_dirs: optional list of vendor `.asy` search dirs.

    Returns:
        SPICE netlist (.cir) text.
    """
    return conversion.asc_to_netlist(
        asc_text, use_ltspice=use_ltspice, asy_search_dirs=asy_search_dirs,
    )


@mcp.tool()
def check_circuit(text: str, fmt: str,
                  asy_search_dirs: Optional[List[str]] = None) -> dict:
    """Lint a circuit: round-trip drift + static netlist checks.

    Same logic as the ``ltspice-convert --check`` CLI command, exposed
    so AI agents can validate their own generated SPICE without
    shelling out.

    Args:
        text: file content (.asc text for ``fmt='asc'``, SPICE netlist
            for ``fmt='cir'``, Python script for ``fmt='py'``).
        fmt: one of ``'asc'``, ``'cir'``, ``'py'``.
        asy_search_dirs: optional list of vendor `.asy` search dirs.

    Returns:
        Dict with keys:

        - ``ok`` (bool): True iff no warnings.
        - ``info`` (list[str]): informational messages
          (round-trip component counts, etc.).
        - ``warnings`` (list[str]): things the user should fix —
          component-count drift, unparsed lines, orphan/undefined
          `.model` references, duplicate instance names, floating
          nodes, undefined ``{PARAM}`` references, etc.

    Example agent workflow: after generating a netlist, call
    ``check_circuit(netlist, 'cir')`` and refuse to ship the netlist
    if ``warnings`` is non-empty.
    """
    info, warn = _cli.check_text(text, fmt, asy_search_dirs)
    return {'ok': not warn, 'info': info, 'warnings': warn}


@mcp.tool()
def info_circuit(text: str, fmt: str,
                 asy_search_dirs: Optional[List[str]] = None) -> dict:
    """Summarise a circuit: component-type counts, symbol kinds,
    `.subckt` block count, `.asy` resolution rate.

    Same logic as ``ltspice-convert --info --json``.

    Args:
        text: file content (.asc, .cir, or .py).
        fmt: one of ``'asc'``, ``'cir'``, ``'py'``.
        asy_search_dirs: optional vendor `.asy` search dirs.

    Returns:
        Dict containing (depending on fmt):

        - ``format``, ``size_bytes``
        - ``component_count``, ``component_types`` (e.g. ``{'R': 4, 'C': 2}``)
        - ``symbol_kinds`` (.asc only)
        - ``symbols_total``, ``symbols_asy_resolved`` (.asc only)
        - ``subckt_blocks``
    """
    return _cli.info_text(text, fmt, asy_search_dirs)


def main() -> int:
    """Entry point for the ``mcp-ltspice`` console script."""
    try:
        mcp.run()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
