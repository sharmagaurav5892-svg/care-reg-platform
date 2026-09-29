"""The app installs requirements.txt, so it must hold only what the code needs to run (ADR-013).
Test and build tools belong in requirements-dev.txt."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV_ONLY = {"pytest", "deltalake", "duckdb", "pypdf", "beautifulsoup4", "tiktoken", "openai", "neo4j"}


def names(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("-r"):
            out.add(re.split(r"[<>=!~\[ ]", line)[0].lower())
    return out


def test_app_requirements_stay_lean():
    assert not names(ROOT / "requirements.txt") & DEV_ONLY


def test_dev_requirements_include_the_runtime_ones():
    assert "-r requirements.txt" in (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")


def test_ci_installs_the_dev_requirements():
    for wf in ("ci.yml", "deploy.yml"):
        assert "pip install -r requirements-dev.txt" in (ROOT / ".github" / "workflows" / wf).read_text()
