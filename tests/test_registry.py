# tests/test_registry.py
from unittest.mock import patch
from services.registry import read_registry, upsert_registry_entry


def test_read_registry_missing_file(tmp_path):
    fake_path = tmp_path / "nonexistent.json"
    with patch("services.registry.REGISTRY_PATH", fake_path):
        result = read_registry()
    assert result == {"projects": []}


def test_upsert_new_entry(tmp_path):
    fake_path = tmp_path / ".ee-in-a-box" / "pcb-copilot-registry.json"
    with patch("services.registry.REGISTRY_PATH", fake_path):
        upsert_registry_entry("/projects/Board/Board-pcb-copilot.db")
        registry = read_registry()
    assert len(registry["projects"]) == 1
    assert registry["projects"][0]["path"] == "/projects/Board/Board-pcb-copilot.db"
    assert "last_used" in registry["projects"][0]


def test_upsert_existing_entry_updates_time(tmp_path):
    fake_path = tmp_path / ".ee-in-a-box" / "pcb-copilot-registry.json"
    with patch("services.registry.REGISTRY_PATH", fake_path):
        upsert_registry_entry("/projects/A.db")
        upsert_registry_entry("/projects/A.db")
        registry = read_registry()
    assert len(registry["projects"]) == 1


def test_upsert_multiple_different_paths(tmp_path):
    fake_path = tmp_path / ".ee-in-a-box" / "pcb-copilot-registry.json"
    with patch("services.registry.REGISTRY_PATH", fake_path):
        upsert_registry_entry("/projects/A.db")
        upsert_registry_entry("/projects/B.db")
        registry = read_registry()
    assert len(registry["projects"]) == 2
