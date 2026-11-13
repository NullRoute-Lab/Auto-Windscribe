"""Offline archive audit; runs after uv build in CI and before publication."""

import tarfile
import zipfile
from pathlib import Path

for archive in Path("dist").glob("auto_windscribe-0.3.0*"):
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as wheel:
            names = wheel.namelist()
            assert "auto_windscribe/preflight.py" in names
            entry = next(n for n in names if n.endswith("entry_points.txt"))
            assert b"auto-windscribe = auto_windscribe.cli:main" in wheel.read(entry)
    else:
        with tarfile.open(archive) as source:
            names = source.getnames()
            assert any(n.endswith("benchmarks/preflight.py") for n in names)
            assert any(n.endswith("docs/TEST_AUTHORIZATION.md") for n in names)
    for name in names:
        assert not any(
            part in name
            for part in (
                ".sqlite",
                ".venv",
                ".uv-cache",
                "AutoScribe-main",
                "Windscribe-Analyzer-main",
            )
        )
        assert not name.endswith((".key", ".pem", ".ovpn", ".env", ".log"))
    print(f"Audited {archive.name}: {len(names)} files")
assert list(Path("dist").glob("auto_windscribe-0.3.0*.whl"))
assert list(Path("dist").glob("auto_windscribe-0.3.0*.tar.gz"))
