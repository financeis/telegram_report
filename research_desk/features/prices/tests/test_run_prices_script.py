"""scripts/run-prices.ps1: the daily schedule's entry for ``prices update``.

Windows PowerShell 5.1, saved as UTF-8 with a BOM (5.1 reads a BOM-less script in the system
code page and breaks the Korean text). It moves to the repository folder, runs
``<python> -u -m research_desk prices update`` and gives back that exit code; without a python it
runs nothing and ends with 4.

The python it runs here is a stand-in batch file that records its arguments and folder and exits
with the code the test asks for: the real command would read the real .env and reach the real DB
and KIS.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import research_desk

REPOSITORY = Path(research_desk.__file__).resolve().parent.parent
SCRIPT = REPOSITORY / "scripts" / "run-prices.ps1"
POWERSHELL = shutil.which("powershell")
needs_powershell = pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell is not on this PC")

FAKE_PYTHON = ('@echo off\r\n'
               'echo %*> "%~dp0args.txt"\r\n'
               'cd > "%~dp0cwd.txt"\r\n'
               'exit /b %FAKE_EXIT%\r\n')


def run_script(tmp_path: Path, python: Path, exit_code: int = 0) -> subprocess.CompletedProcess:
    environment = dict(os.environ, FAKE_EXIT=str(exit_code))
    environment.pop("RESEARCH_DESK_PY", None)
    return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
                           "-Python", str(python)],
                          cwd=tmp_path, env=environment, capture_output=True, timeout=120)


def same_folder(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def test_the_script_is_utf8_with_a_bom_and_runs_prices_update():
    data = SCRIPT.read_bytes()
    assert data[:3] == b"\xef\xbb\xbf"
    text = data[3:].decode("utf-8")
    assert "-m research_desk prices update" in text
    assert "주가" in text   # Korean guidance


@needs_powershell
@pytest.mark.parametrize("exit_code", [0, 1, 4])
def test_the_script_runs_the_command_in_the_repository_and_gives_back_its_exit_code(tmp_path, exit_code):
    fake = tmp_path / "python.cmd"
    fake.write_text(FAKE_PYTHON, encoding="ascii")
    result = run_script(tmp_path, fake, exit_code)
    assert result.returncode == exit_code, result.stderr
    assert (tmp_path / "args.txt").read_text(encoding="ascii").strip() == "-u -m research_desk prices update"
    assert same_folder(Path((tmp_path / "cwd.txt").read_text(encoding="mbcs").strip()), REPOSITORY)


@needs_powershell
def test_without_a_python_the_script_runs_nothing_and_ends_with_4(tmp_path):
    result = run_script(tmp_path, tmp_path / "missing" / "python.exe")
    assert result.returncode == 4
    assert not (tmp_path / "args.txt").exists()
