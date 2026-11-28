"""Compila y ejecuta las pruebas del núcleo de efectos del firmware ESP32 (si hay g++)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "esp32"


@pytest.mark.skipif(shutil.which("g++") is None, reason="sin compilador de C++")
def test_esp32_effects_core(tmp_path):
    exe = tmp_path / "test_core"
    build = subprocess.run(
        ["g++", "-std=gnu++11", "-O2", "-Wall", "-Wextra", "-Werror",
         "-I", str(ROOT / "rkbx_lights" / "include"),
         str(ROOT / "rkbx_lights" / "src" / "lights_core.cpp"),
         str(ROOT / "test_host" / "test_core.cpp"), "-o", str(exe)],
        capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
