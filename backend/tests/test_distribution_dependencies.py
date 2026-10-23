"""Source-upgrade dependency setup on Windows non-ASCII checkout paths."""
import importlib.util
import sys
from pathlib import Path


def test_runtime_editable_path_is_safe_for_windows_legacy_codepages(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "packaging/distribution/install_dependencies.py"
    spec = importlib.util.spec_from_file_location("distribution_dependencies", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pth = tmp_path / "runtime.pth"
    source = str(tmp_path / "开源项目" / "sdk-runtime" / "src")
    pth.write_text(source + "\n", encoding="utf-8")
    module.normalize_runtime_editable_path(pth)
    code = pth.read_text(encoding="ascii").strip()
    monkeypatch.setattr(sys, "path", list(sys.path))
    exec(compile(code, str(pth), "exec"))  # noqa: S102 - execute the generated local .pth fixture
    assert source in sys.path
    previous = pth.read_bytes()
    module.normalize_runtime_editable_path(pth)
    assert pth.read_bytes() == previous
