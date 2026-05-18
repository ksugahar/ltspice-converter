"""ltspice-converter — convert LTspice .asc and SPICE .cir files
to/from schemdraw Python scripts.

Public API
----------
- netlist_to_schemdraw(netlist, name) -> str
- schemdraw_to_netlist(script, title) -> str
- netlist_to_asc(netlist) -> str
- asc_to_netlist(asc_text, use_ltspice=False) -> str

CLI / MCP server
----------------
- mcp-ltspice                (FastMCP stdio server)
"""
from __future__ import annotations

from .conversion import (
    netlist_to_schemdraw,
    schemdraw_to_netlist,
    netlist_to_asc,
    asc_to_netlist,
)

__all__ = [
    "netlist_to_schemdraw",
    "schemdraw_to_netlist",
    "netlist_to_asc",
    "asc_to_netlist",
]

__version__ = "0.3.8"
