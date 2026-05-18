"""CLI smoke tests via subprocess.

These intentionally invoke the installed ``ltspice-convert`` console
script (not the function directly) so we exercise the same code path
end-users will hit, including exit-code propagation through the
setuptools-generated entry-point wrapper.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "bidirectional"
RC_ASC = FIXTURES / "00_converter_test_rc_lowpass.asc"
RC_CIR = FIXTURES / "00_converter_test_rc_lowpass.cir"


def run_cli(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Invoke the CLI as `python -m ltspice_converter.cli ARGS`.

    Using `python -m` avoids depending on whether the console script
    is on PATH in the CI environment.
    """
    return subprocess.run(
        [sys.executable, "-m", "ltspice_converter.cli", *args],
        capture_output=True,
        text=True,
        check=check,
    )


# =============================================================================
# Basics
# =============================================================================

def test_version():
    r = run_cli("--version")
    assert r.returncode == 0
    assert r.stdout.strip().startswith("ltspice-convert ")


def test_help():
    r = run_cli("--help")
    assert r.returncode == 0
    assert "input file" in r.stdout.lower() or "input" in r.stdout.lower()


def test_error_on_unknown_extension(tmp_path):
    bad = tmp_path / "x.txt"
    bad.write_text("garbage")
    r = run_cli(str(bad))
    assert r.returncode != 0
    assert "unknown" in r.stderr.lower() or "extension" in r.stderr.lower()


def test_error_on_missing_file(tmp_path):
    r = run_cli(str(tmp_path / "nope.asc"))
    assert r.returncode != 0


# =============================================================================
# Convert mode
# =============================================================================

def test_convert_asc_to_cir(tmp_path):
    out = tmp_path / "rc.cir"
    r = run_cli(str(RC_ASC), "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "R1" in text
    assert "C1" in text


def test_convert_cir_to_asc(tmp_path):
    out = tmp_path / "rc.asc"
    r = run_cli(str(RC_CIR), "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert out.is_file()
    text = out.read_text(encoding="latin-1")
    assert "Version" in text or "SYMBOL" in text


def test_convert_auto_output_path(tmp_path):
    src = tmp_path / "rc.asc"
    src.write_text(RC_ASC.read_text(encoding="utf-8"))
    r = run_cli(str(src))   # no -o, expect rc.cir alongside
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "rc.cir").is_file()


def test_convert_with_to_flag(tmp_path):
    src = tmp_path / "rc.asc"
    src.write_text(RC_ASC.read_text(encoding="utf-8"))
    r = run_cli(str(src), "--to", "py")
    assert r.returncode == 0, r.stderr
    py_out = tmp_path / "rc.py"
    assert py_out.is_file()
    assert "schemdraw" in py_out.read_text(encoding="utf-8")


def test_convert_batch_to_dir(tmp_path):
    out_dir = tmp_path / "batch"
    inputs = list(FIXTURES.glob("00_converter_test_*.asc"))
    assert len(inputs) >= 5
    r = run_cli(*[str(p) for p in inputs], "-o", str(out_dir), "--to", "cir")
    assert r.returncode == 0, r.stderr
    cir_outputs = list(out_dir.glob("*.cir"))
    assert len(cir_outputs) == len(inputs)


# =============================================================================
# Check (lint) mode
# =============================================================================

def test_check_clean_file_pass():
    r = run_cli("--check", str(RC_ASC))
    assert r.returncode == 0, r.stderr
    assert "PASS" in r.stdout
    assert "[warn]" not in r.stdout


def test_check_strict_clean_still_pass():
    r = run_cli("--check", "--strict", str(RC_ASC))
    assert r.returncode == 0, r.stderr


def test_check_multiple_files():
    inputs = list(FIXTURES.glob("00_converter_test_*.asc"))[:3]
    r = run_cli("--check", *[str(p) for p in inputs])
    assert r.returncode == 0, r.stderr
    # Each file should produce its own section
    assert r.stdout.count("==") >= len(inputs) * 2  # header has == on both sides


# =============================================================================
# Info mode
# =============================================================================

def test_info_text():
    r = run_cli("--info", str(RC_ASC))
    assert r.returncode == 0, r.stderr
    assert "component_count" in r.stdout
    assert "component_types" in r.stdout


def test_info_json():
    r = run_cli("--info", "--json", str(RC_ASC))
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert isinstance(data, list)
    assert len(data) == 1
    item = data[0]
    assert item["format"] == "asc"
    assert item["component_count"] == 3
    assert item["component_types"] == {"I": 1, "R": 1, "C": 1}


# =============================================================================
# .asy search dir
# =============================================================================

def test_asy_dir_flag_accepted(tmp_path):
    # Flag should be parsed even if dir does not exist (just no-op there)
    r = run_cli("--asy-dir", str(tmp_path),
                "--info", str(RC_ASC))
    assert r.returncode == 0, r.stderr
