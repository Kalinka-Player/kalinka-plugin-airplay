"""Do not package a cached native receiver that bypasses its configured password."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("patch", [None, b"older patch", "current"])
def test_packaging_requires_current_native_password_fix(tmp_path, patch):
    for directory, binary in (("shairport-sync", "shairport-sync"), ("nqptp", "nqptp")):
        executable = tmp_path / directory / binary
        executable.parent.mkdir()
        executable.write_text("#!/bin/sh\necho 5.5.2-AirPlay2-stdout\n")
        executable.chmod(0o755)
        (tmp_path / f"{directory}-source.tar.gz").touch()
    if patch is not None:
        contents = (
            (ROOT / "patches/shairport-classic-password.patch").read_bytes()
            if patch == "current" else patch
        )
        (tmp_path / "shairport-classic-password.patch").write_bytes(contents)
    result = subprocess.run(
        ["bash", "-c", 'root="$1"; source "$root/scripts/package_common.sh"; use_native_build "$2"',
         "native-check", str(ROOT), str(tmp_path)],
        text=True, capture_output=True,
    )
    if patch == "current":
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "Rebuild native dependencies" in result.stderr
