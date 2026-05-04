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
    db = str(tmp_path / "Board-pcb-copilot.db")
    (tmp_path / "Board-pcb-copilot.db").touch()
    with patch("services.registry.REGISTRY_PATH", fake_path):
        upsert_registry_entry(db)
        registry = read_registry()
    assert len(registry["projects"]) == 1
    assert registry["projects"][0]["path"] == db
    assert "last_used" in registry["projects"][0]


def test_upsert_existing_entry_updates_time(tmp_path):
    fake_path = tmp_path / ".ee-in-a-box" / "pcb-copilot-registry.json"
    db = str(tmp_path / "A.db")
    (tmp_path / "A.db").touch()
    with patch("services.registry.REGISTRY_PATH", fake_path):
        upsert_registry_entry(db)
        upsert_registry_entry(db)
        registry = read_registry()
    assert len(registry["projects"]) == 1


def test_upsert_multiple_different_paths(tmp_path):
    fake_path = tmp_path / ".ee-in-a-box" / "pcb-copilot-registry.json"
    db_a = str(tmp_path / "A.db")
    db_b = str(tmp_path / "B.db")
    (tmp_path / "A.db").touch()
    (tmp_path / "B.db").touch()
    with patch("services.registry.REGISTRY_PATH", fake_path):
        upsert_registry_entry(db_a)
        upsert_registry_entry(db_b)
        registry = read_registry()
    assert len(registry["projects"]) == 2
