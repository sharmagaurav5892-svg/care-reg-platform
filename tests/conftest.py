import pytest

from careplatform import config


@pytest.fixture
def lakehouse_tmp(tmp_path, monkeypatch):
    """Point landing and lakehouse at a temp folder so tests never touch real data."""
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    return tmp_path
