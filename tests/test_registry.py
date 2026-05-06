# tests/test_registry.py
from unittest.mock import patch
from services.registry import read_registry, upsert_registry_entry, register_discovered


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


def test_upsert_stores_last_variant(tmp_path):
    fake_path = tmp_path / ".ee-in-a-box" / "pcb-copilot-registry.json"
    db = str(tmp_path / "Board-pcb-copilot.db")
    (tmp_path / "Board-pcb-copilot.db").touch()
    with patch("services.registry.REGISTRY_PATH", fake_path):
        upsert_registry_entry(db, last_variant="Production")
        registry = read_registry()
    assert registry["projects"][0]["last_variant"] == "Production"


def test_upsert_updates_last_variant(tmp_path):
    fake_path = tmp_path / ".ee-in-a-box" / "pcb-copilot-registry.json"
    db = str(tmp_path / "Board-pcb-copilot.db")
    (tmp_path / "Board-pcb-copilot.db").touch()
    with patch("services.registry.REGISTRY_PATH", fake_path):
        upsert_registry_entry(db, last_variant="Production")
        upsert_registry_entry(db, last_variant="Proto")
        registry = read_registry()
    assert registry["projects"][0]["last_variant"] == "Proto"


def test_get_last_variant_returns_stored(tmp_path):
    from services.registry import get_last_variant
    fake_path = tmp_path / ".ee-in-a-box" / "pcb-copilot-registry.json"
    db = str(tmp_path / "Board-pcb-copilot.db")
    (tmp_path / "Board-pcb-copilot.db").touch()
    with patch("services.registry.REGISTRY_PATH", fake_path):
        upsert_registry_entry(db, last_variant="DVT")
        assert get_last_variant(db) == "DVT"


def test_register_discovered_adds_entry_without_last_used(tmp_path):
    fake_path = tmp_path / ".ee-in-a-box" / "pcb-copilot-registry.json"
    db = str(tmp_path / "Board-pcb-copilot.db")
    (tmp_path / "Board-pcb-copilot.db").touch()
    with patch("services.registry.REGISTRY_PATH", fake_path):
        register_discovered(db)
        registry = read_registry()
    assert len(registry["projects"]) == 1
    assert registry["projects"][0]["path"] == db
    assert "last_used" not in registry["projects"][0]


def test_register_discovered_is_noop_if_already_present(tmp_path):
    fake_path = tmp_path / ".ee-in-a-box" / "pcb-copilot-registry.json"
    db = str(tmp_path / "Board-pcb-copilot.db")
    (tmp_path / "Board-pcb-copilot.db").touch()
    with patch("services.registry.REGISTRY_PATH", fake_path):
        upsert_registry_entry(db)
        register_discovered(db)
        registry = read_registry()
    assert len(registry["projects"]) == 1
    assert "last_used" in registry["projects"][0]


def test_get_last_variant_returns_none_when_missing(tmp_path):
    from services.registry import get_last_variant
    fake_path = tmp_path / ".ee-in-a-box" / "pcb-copilot-registry.json"
    db = str(tmp_path / "Board-pcb-copilot.db")
    (tmp_path / "Board-pcb-copilot.db").touch()
    with patch("services.registry.REGISTRY_PATH", fake_path):
        upsert_registry_entry(db)
        assert get_last_variant(db) is None
