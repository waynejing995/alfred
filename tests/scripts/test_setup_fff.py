import importlib.util
import stat
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "setup_fff",
    Path(__file__).resolve().parents[2] / "scripts" / "setup_fff.py",
)
setup_fff = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(setup_fff)


def test_make_executable_sets_exec_bits(tmp_path):
    target = tmp_path / "fff"
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)

    setup_fff.make_executable(target)

    assert target.stat().st_mode & stat.S_IXUSR
