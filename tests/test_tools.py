# tests/test_tools.py
import json
import sqlite3
from unittest.mock import patch
from conftest import _SCHEMA_DDL


def _make_db(path: str, schema_version: int = 1) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_DDL)
    conn.execute(
        "INSERT INTO project (name, root_dir, exported_at, exported_by,"
        " schema_version, sheet_count, component_count, net_count)"
        " VALUES (?,?,?,?,?,?,?,?)",
        ("TestBoard", "/projects/TestBoard", "2026-04-29T14:32:00Z",
         "altium-copilot v0.1.10", schema_version, 1, 1, 1),
    )
    cur = conn.execute("INSERT INTO sheets (name) VALUES ('MCU')")
    sheet_id = cur.lastrowid
    conn.execute("INSERT INTO variants (name, dnp_refdes) VALUES ('Default', '[]')")
    conn.execute(
        "INSERT INTO nets (name, pin_count) VALUES ('VCC', 1)"
    )
    cur = conn.execute(
        "INSERT INTO components (refdes, mpn, description, value, sheet_id)"
        " VALUES ('U1', 'STM32G474', 'MCU', NULL, ?)", (sheet_id,)
    )
    conn.execute(
        "INSERT INTO pins (component_id, pin_number, pin_name, net_name)"
        " VALUES (?, '1', 'VCC', 'VCC')", (cur.lastrowid,)
    )
    conn.commit()
    conn.close()


# ---- _scan_for_db tests ----

def test_scan_finds_db_in_search_dir(tmp_path):
    import main
    db = tmp_path / "BOARD-pcb-copilot.db"
    db.touch()
    results = main._scan_for_db(search_dirs=[tmp_path])
    assert any(str(db) in path for path, _ in results)


def test_scan_finds_db_nested(tmp_path):
    import main
    nested = tmp_path / "sub1" / "sub2"
    nested.mkdir(parents=True)
    db = nested / "BOARD-pcb-copilot.db"
    db.touch()
    results = main._scan_for_db(search_dirs=[tmp_path])
    assert any(str(db) in path for path, _ in results)


def test_scan_ignores_non_matching_files(tmp_path):
    import main
    (tmp_path / "something.db").touch()
    results = main._scan_for_db(search_dirs=[tmp_path])
    assert results == []


def test_scan_returns_empty_for_missing_dir(tmp_path):
    import main
    missing = tmp_path / "nonexistent"
    results = main._scan_for_db(search_dirs=[missing])
    assert results == []


def test_scan_sorts_by_most_recent(tmp_path):
    import main, time
    db1 = tmp_path / "OLD-pcb-copilot.db"
    db1.touch()
    time.sleep(0.01)
    db2 = tmp_path / "NEW-pcb-copilot.db"
    db2.touch()
    results = main._scan_for_db(search_dirs=[tmp_path])
    assert results[0][0] == str(db2)


def test_format_age_today(tmp_path):
    import main
    p = tmp_path / "f.db"
    p.touch()
    assert main._format_age(p) == "today"


# ---- _sync_registry_with_disk tests ----

def test_sync_registry_with_disk_registers_all_and_returns_ages():
    import main
    db1 = "/tmp/A-pcb-copilot.db"
    db2 = "/tmp/B-pcb-copilot.db"
    with patch("main._scan_for_db", return_value=[(db1, "today"), (db2, "yesterday")]):
        with patch("main.register_discovered") as mock_register:
            result = main._sync_registry_with_disk()
    assert mock_register.call_count == 2
    mock_register.assert_any_call(db1)
    mock_register.assert_any_call(db2)
    assert result == {db1: "today", db2: "yesterday"}


def test_sync_registry_with_disk_empty_scan_returns_empty_dict():
    import main
    with patch("main._scan_for_db", return_value=[]):
        with patch("main.register_discovered") as mock_register:
            result = main._sync_registry_with_disk()
    mock_register.assert_not_called()
    assert result == {}


# ---- load_project tests ----

def test_load_project_no_registry_returns_not_loaded():
    import main
    with patch("main._sync_registry_with_disk", return_value={}):
        with patch("main.read_registry", return_value={"projects": []}):
            result = json.loads(main.load_project())
    assert result["loaded"] is False


def test_load_project_one_remembered_project_autoloads(tmp_path):
    import main
    db = str(tmp_path / "test.db")
    _make_db(db)
    registry = {"projects": [{"path": db, "last_used": "2026-01-01T00:00:00+00:00"}]}
    with patch("main._sync_registry_with_disk", return_value={db: "today"}):
        with patch("main.read_registry", return_value=registry):
            result = json.loads(main.load_project())
    assert result["loaded"] is True
    assert result["project"]["name"] == "TestBoard"
    assert "server_version" in result


def test_load_project_multiple_remembered_projects_returns_list(tmp_path):
    import main
    db1 = str(tmp_path / "a.db")
    db2 = str(tmp_path / "b.db")
    _make_db(db1)
    _make_db(db2)
    registry = {
        "projects": [
            {"path": db1, "last_used": "2026-01-01T00:00:00+00:00"},
            {"path": db2, "last_used": "2026-01-02T00:00:00+00:00"},
        ]
    }
    with patch("main._sync_registry_with_disk", return_value={db1: "today", db2: "today"}):
        with patch("main.read_registry", return_value=registry):
            result = json.loads(main.load_project())
    assert result["loaded"] is False
    assert "projects" in result
    assert len(result["projects"]) == 2
    # Most recently used should be first
    assert result["projects"][0]["path"] == db2


def test_load_project_file_missing_warns(tmp_path):
    import main
    registry = {"projects": [{"path": str(tmp_path / "missing.db"), "last_used": "2026-01-01T00:00:00+00:00"}]}
    with patch("main._sync_registry_with_disk", return_value={}):
        with patch("main.read_registry", return_value=registry):
            result = json.loads(main.load_project())
    assert result["loaded"] is False


def test_load_project_explicit_path_file_not_found(tmp_path):
    import main
    with patch("main._scan_for_db", return_value=[]):
        result = json.loads(main.load_project(str(tmp_path / "missing.db")))
    assert result["loaded"] is False


def test_load_project_explicit_path_schema_too_new(tmp_path):
    import main
    db = str(tmp_path / "test.db")
    _make_db(db, schema_version=99)
    result = json.loads(main.load_project(db))
    assert result["loaded"] is False
    assert result["error"] == "schema_too_new"
    assert "altium-copilot" in result["message"]


def test_load_project_explicit_path_success(tmp_path):
    import main
    db = str(tmp_path / "test.db")
    _make_db(db)
    with patch("main.upsert_registry_entry"):
        result = json.loads(main.load_project(db))
    assert result["loaded"] is True
    assert result["project"]["name"] == "TestBoard"
    assert "server_version" in result


def test_load_project_registry_pruned_to_one_autoloads(tmp_path):
    """After dead paths are pruned, a single survivor should auto-load."""
    import main
    db = str(tmp_path / "alive.db")
    _make_db(db)
    registry = {"projects": [{"path": db, "last_used": "2026-01-01T00:00:00+00:00"}]}
    with patch("main._sync_registry_with_disk", return_value={db: "today"}):
        with patch("main.read_registry", return_value=registry):
            result = json.loads(main.load_project())
    assert result["loaded"] is True


def test_load_project_scan_finds_one_file(tmp_path):
    import main
    db = str(tmp_path / "BOARD-pcb-copilot.db")
    _make_db(db)
    # Sync discovers the file and adds it to registry; registry then has 1 entry with no last_used
    with patch("main._sync_registry_with_disk", return_value={db: "today"}):
        with patch("main.read_registry", return_value={"projects": [{"path": db}]}):
            with patch("main.upsert_registry_entry"):
                result = json.loads(main.load_project())
    assert result["loaded"] is True
    assert "discovery" in result
    assert result["discovery"]["last_modified"] == "today"
    assert "confirm" in result["discovery"]


def test_load_project_scan_finds_multiple_files(tmp_path):
    import main
    db1 = str(tmp_path / "A-pcb-copilot.db")
    db2 = str(tmp_path / "B-pcb-copilot.db")
    projects = [{"path": db1}, {"path": db2}]
    # Sync discovers both; registry has both with no last_used; picker shown
    with patch("main._sync_registry_with_disk", return_value={db1: "today", db2: "3 days ago"}):
        with patch("main.read_registry", return_value={"projects": projects}):
            result = json.loads(main.load_project())
    assert result["loaded"] is False
    assert "projects" in result
    assert len(result["projects"]) == 2


def test_load_project_scan_finds_nothing_returns_copy_as_path_message(tmp_path):
    import main
    with patch("main._sync_registry_with_disk", return_value={}):
        with patch("main.read_registry", return_value={"projects": []}):
            result = json.loads(main.load_project())
    assert result["loaded"] is False
    assert "Copy as path" in result["message"] or "copy" in result["message"].lower()


def test_load_project_partial_path_triggers_filename_scan(tmp_path):
    import main
    db = str(tmp_path / "BOARD-pcb-copilot.db")
    _make_db(db)
    partial = str(tmp_path / "subdir" / "BOARD-pcb-copilot.db")
    with patch("main._scan_for_db", return_value=[(db, "today")]) as mock_scan:
        with patch("main.upsert_registry_entry"):
            result = json.loads(main.load_project(partial))
    mock_scan.assert_called_once_with(filename_filter="BOARD-pcb-copilot.db")
    assert result["loaded"] is True


def test_load_project_partial_path_scan_finds_nothing(tmp_path):
    import main
    partial = str(tmp_path / "subdir" / "BOARD-pcb-copilot.db")
    with patch("main._scan_for_db", return_value=[]):
        result = json.loads(main.load_project(partial))
    assert result["loaded"] is False
    assert "Copy as path" in result["message"] or "copy" in result["message"].lower()


# ---- list_variants / set_active_variant tests ----

def _load_test_db(tmp_path):
    """Load a test DB into main module state."""
    import main
    db = str(tmp_path / "test.db")
    _make_db(db)
    main._load(db)
    return db


def test_list_variants_no_project():
    import main
    main._project = None
    with patch("main._sync_registry_with_disk", return_value={}):
        with patch("main.read_registry", return_value={"projects": []}):
            result = json.loads(main.list_variants())
    assert result["loaded"] is False


def test_list_variants_returns_all(tmp_path):
    import main
    db = str(tmp_path / "test.db")
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA_DDL)
    conn.execute(
        "INSERT INTO project (name, root_dir, exported_at, exported_by,"
        " schema_version, sheet_count, component_count, net_count)"
        " VALUES ('B','/',  '2026-04-29T00:00:00Z', 'altium-copilot v0.1', 1, 1, 0, 0)"
    )
    conn.execute("INSERT INTO variants (name, dnp_refdes) VALUES ('Default', '[]')")
    conn.execute("INSERT INTO variants (name, dnp_refdes) VALUES ('Lite', '[\"R1\"]')")
    conn.commit()
    conn.close()
    main._load(db)
    result = json.loads(main.list_variants())
    names = [v["name"] for v in result["variants"]]
    assert "Default" in names
    assert "Lite" in names


def test_set_active_variant_unknown(tmp_path):
    import main
    _load_test_db(tmp_path)
    result = json.loads(main.set_active_variant("NonExistent"))
    assert result["error"] == "variant_not_found"


def test_set_active_variant_success(tmp_path):
    import main
    _load_test_db(tmp_path)
    result = json.loads(main.set_active_variant("Default"))
    assert result["active"] == "Default"
    assert main._active_variant is not None
    assert main._active_variant["name"] == "Default"
    # Case-insensitive matching
    result2 = json.loads(main.set_active_variant("default"))
    assert result2["active"] == "Default"


def _make_db_multi_variant(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_DDL)
    conn.execute(
        "INSERT INTO project (name, root_dir, exported_at, exported_by,"
        " schema_version, sheet_count, component_count, net_count)"
        " VALUES (?,?,?,?,?,?,?,?)",
        ("TestBoard", "/projects/TestBoard", "2026-04-29T14:32:00Z",
         "altium-copilot v0.1.10", 1, 1, 0, 1),
    )
    conn.execute("INSERT INTO sheets (name) VALUES ('MCU')")
    conn.execute("INSERT INTO variants (name, dnp_refdes) VALUES ('Production', '[]')")
    conn.execute("INSERT INTO variants (name, dnp_refdes) VALUES ('Proto', '[\"R1\"]')")
    conn.execute("INSERT INTO nets (name, pin_count) VALUES ('VCC', 1)")
    conn.commit()
    conn.close()


def test_list_variants_single_auto_selects(tmp_path):
    import main
    _load_test_db(tmp_path)
    result = json.loads(main.list_variants())
    active = [v for v in result["variants"] if v["active"]]
    assert len(active) == 1
    assert active[0]["name"] == "Default"
    assert result["auto_selected"] == "Default"
    assert "announce" in result


def test_list_variants_multi_no_last_variant_returns_none_active(tmp_path):
    import main
    db = str(tmp_path / "test.db")
    _make_db_multi_variant(db)
    main._load(db)
    with patch("main.get_last_variant", return_value=None):
        result = json.loads(main.list_variants())
    assert not any(v["active"] for v in result["variants"])
    assert "auto_selected" not in result


def test_list_variants_multi_uses_last_variant(tmp_path):
    import main
    db = str(tmp_path / "test.db")
    _make_db_multi_variant(db)
    main._load(db)
    with patch("main.get_last_variant", return_value="Proto"):
        result = json.loads(main.list_variants())
    active = [v for v in result["variants"] if v["active"]]
    assert len(active) == 1
    assert active[0]["name"] == "Proto"
    assert result["auto_selected"] == "Proto"


def test_set_active_variant_persists_last_variant(tmp_path):
    import main
    db = str(tmp_path / "BOARD-pcb-copilot.db")
    _make_db(db)
    main._load(db)
    with patch("main.upsert_registry_entry") as mock_upsert:
        main.set_active_variant("Default")
    mock_upsert.assert_called_once_with(db, last_variant="Default")


# ---- list_sheets / get_sheet_context tests ----

def test_list_sheets_no_project():
    import main
    main._project = None
    with patch("main._sync_registry_with_disk", return_value={}):
        with patch("main.read_registry", return_value={"projects": []}):
            result = json.loads(main.list_sheets())
    assert result["loaded"] is False


def test_list_sheets_returns_names(tmp_path):
    import main
    _make_db(str(tmp_path / "test.db"))
    main._load(str(tmp_path / "test.db"))
    result = json.loads(main.list_sheets())
    assert "MCU" in result["sheets"]


def test_get_sheet_context_unknown_sheet(tmp_path):
    import main
    _make_db(str(tmp_path / "test.db"))
    main._load(str(tmp_path / "test.db"))
    main.set_active_variant("Default")
    result = json.loads(main.get_sheet_context("NoSuchSheet"))
    assert result["error"] == "sheet_not_found"
    assert "MCU" in result["available_sheets"]


def test_get_sheet_context_no_project():
    import main
    main._project = None
    with patch("main._sync_registry_with_disk", return_value={}):
        with patch("main.read_registry", return_value={"projects": []}):
            result = json.loads(main.get_sheet_context("MCU"))
    assert result["loaded"] is False


def test_get_sheet_context_returns_components(tmp_path):
    import main
    _make_db(str(tmp_path / "test.db"))
    main._load(str(tmp_path / "test.db"))
    main.set_active_variant("Default")
    result = json.loads(main.get_sheet_context("MCU"))
    assert result["sheet"] == "MCU"
    refdes_list = [c["refdes"] for c in result["components"]]
    assert "U1" in refdes_list


def test_get_sheet_context_all_dnp_warns(tmp_path):
    import main
    db = str(tmp_path / "dnp.db")
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA_DDL)
    conn.execute(
        "INSERT INTO project (name, root_dir, exported_at, exported_by,"
        " schema_version, sheet_count, component_count, net_count)"
        " VALUES ('B', '/', '2026-04-29T00:00:00Z', 'v', 1, 1, 1, 0)"
    )
    cur = conn.execute("INSERT INTO sheets (name) VALUES ('MCU')")
    sheet_id = cur.lastrowid
    conn.execute("INSERT INTO variants (name, dnp_refdes) VALUES ('Lite', '[\"U1\"]')")
    conn.execute(
        "INSERT INTO components (refdes, mpn, description, value, sheet_id)"
        " VALUES ('U1', NULL, NULL, NULL, ?)", (sheet_id,)
    )
    conn.commit()
    conn.close()
    main._load(db)
    main.set_active_variant("Lite")
    result = json.loads(main.get_sheet_context("MCU"))
    assert "warning" in result
    assert "DNP" in result["warning"]


# ---- get_component tests ----

def _load_richer_db(tmp_path) -> str:
    """Create a DB with U1 on MCU sheet and R1 on Comms sheet, two nets."""
    db = str(tmp_path / "rich.db")
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA_DDL)
    conn.execute(
        "INSERT INTO project (name, root_dir, exported_at, exported_by,"
        " schema_version, sheet_count, component_count, net_count)"
        " VALUES ('TestBoard', '/', '2026-04-29T00:00:00Z', 'altium-copilot v0.1', 1, 2, 2, 2)"
    )
    cur = conn.execute("INSERT INTO sheets (name) VALUES ('MCU')")
    mcu_id = cur.lastrowid
    cur = conn.execute("INSERT INTO sheets (name) VALUES ('Comms')")
    comms_id = cur.lastrowid
    conn.execute("INSERT INTO variants (name, dnp_refdes) VALUES ('Default', '[]')")
    conn.execute("INSERT INTO nets (name, pin_count) VALUES ('MCU_TX', 2)")
    conn.execute("INSERT INTO nets (name, pin_count) VALUES ('GND', 2)")
    cur = conn.execute(
        "INSERT INTO components (refdes, mpn, description, value, sheet_id)"
        " VALUES ('U1', 'STM32G474', 'ARM Cortex-M4 MCU', NULL, ?)", (mcu_id,)
    )
    u1_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO components (refdes, mpn, description, value, sheet_id)"
        " VALUES ('R1', 'RC0402', 'RES 10K OHM 0402', '10K', ?)", (comms_id,)
    )
    r1_id = cur.lastrowid
    conn.execute(
        "INSERT INTO pins (component_id, pin_number, pin_name, net_name)"
        " VALUES (?, 'PA9', 'PA9', 'MCU_TX')", (u1_id,)
    )
    conn.execute(
        "INSERT INTO pins (component_id, pin_number, pin_name, net_name)"
        " VALUES (?, 'GND', 'GND', 'GND')", (u1_id,)
    )
    conn.execute(
        "INSERT INTO pins (component_id, pin_number, pin_name, net_name)"
        " VALUES (?, '1', '~', 'MCU_TX')", (r1_id,)
    )
    conn.execute(
        "INSERT INTO pins (component_id, pin_number, pin_name, net_name)"
        " VALUES (?, '2', '~', 'GND')", (r1_id,)
    )
    conn.commit()
    conn.close()
    return db


def test_get_component_exact_match(tmp_path):
    import main
    main._load(_load_richer_db(tmp_path))
    main.set_active_variant("Default")
    result = json.loads(main.get_component("U1"))
    assert result["refdes"] == "U1"
    assert result["mpn"] == "STM32G474"
    assert result["dnp"] is False
    assert "PA9" in result["pins"]


def test_get_component_case_insensitive_exact(tmp_path):
    import main
    main._load(_load_richer_db(tmp_path))
    main.set_active_variant("Default")
    result = json.loads(main.get_component("u1"))
    assert result["refdes"] == "U1"


def test_get_component_fuzzy_by_description(tmp_path):
    import main
    main._load(_load_richer_db(tmp_path))
    main.set_active_variant("Default")
    result = json.loads(main.get_component("cortex"))
    assert "fuzzy_matches" in result
    assert any("U1" in m["refdes"] for m in result["fuzzy_matches"])


def test_get_component_no_match(tmp_path):
    import main
    main._load(_load_richer_db(tmp_path))
    main.set_active_variant("Default")
    result = json.loads(main.get_component("ZZZNOMATCH"))
    assert result["error"] == "not_found"


def test_get_component_unconnected_flag(tmp_path):
    """A component with no net connections must be marked unconnected=true."""
    import main
    db = str(tmp_path / "unc.db")
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA_DDL)
    conn.execute(
        "INSERT INTO project (name, root_dir, exported_at, exported_by,"
        " schema_version, sheet_count, component_count, net_count)"
        " VALUES ('B', '/', '2026-04-29T00:00:00Z', 'v', 1, 1, 1, 0)"
    )
    cur = conn.execute("INSERT INTO sheets (name) VALUES ('MCU')")
    sheet_id = cur.lastrowid
    conn.execute("INSERT INTO variants (name, dnp_refdes) VALUES ('Default', '[]')")
    cur = conn.execute(
        "INSERT INTO components (refdes, mpn, description, value, sheet_id)"
        " VALUES ('TP1', NULL, 'Test Point', NULL, ?)", (sheet_id,)
    )
    conn.execute(
        "INSERT INTO pins (component_id, pin_number, pin_name, net_name)"
        " VALUES (?, '1', '1', NULL)", (cur.lastrowid,)
    )
    conn.commit()
    conn.close()
    main._load(db)
    main.set_active_variant("Default")
    result = json.loads(main.get_component("TP1"))
    assert result["refdes"] == "TP1"
    assert result["unconnected"] is True


def test_get_component_dnp_true(tmp_path):
    """get_component must return dnp=True when the active variant marks that refdes DNP."""
    import main
    db = str(tmp_path / "dnp.db")
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA_DDL)
    conn.execute(
        "INSERT INTO project (name, root_dir, exported_at, exported_by,"
        " schema_version, sheet_count, component_count, net_count)"
        " VALUES ('B', '/', '2026-04-29T00:00:00Z', 'v', 1, 1, 1, 1)"
    )
    cur = conn.execute("INSERT INTO sheets (name) VALUES ('MCU')")
    sheet_id = cur.lastrowid
    conn.execute("INSERT INTO variants (name, dnp_refdes) VALUES ('Default', '[]')")
    conn.execute("INSERT INTO variants (name, dnp_refdes) VALUES ('Lite', '[\"U1\"]')")
    conn.execute("INSERT INTO nets (name, pin_count) VALUES ('VCC', 1)")
    cur = conn.execute(
        "INSERT INTO components (refdes, mpn, description, value, sheet_id)"
        " VALUES ('U1', 'STM32G474', 'MCU', NULL, ?)", (sheet_id,)
    )
    conn.execute(
        "INSERT INTO pins (component_id, pin_number, pin_name, net_name)"
        " VALUES (?, '1', 'VCC', 'VCC')", (cur.lastrowid,)
    )
    conn.commit()
    conn.close()
    main._load(db)
    main.set_active_variant("Lite")
    result = json.loads(main.get_component("U1"))
    assert result["dnp"] is True


# ---- get_net tests ----

def test_get_net_exact_match(tmp_path):
    import main
    main._load(_load_richer_db(tmp_path))
    result = json.loads(main.get_net("MCU_TX"))
    assert result["net"] == "MCU_TX"
    refdes_list = [p["refdes"] for p in result["pins"]]
    assert "U1" in refdes_list
    assert "R1" in refdes_list


def test_get_net_case_insensitive_exact(tmp_path):
    import main
    main._load(_load_richer_db(tmp_path))
    result = json.loads(main.get_net("mcu_tx"))
    assert result["net"] == "MCU_TX"


def test_get_net_fuzzy_returns_matches(tmp_path):
    import main
    main._load(_load_richer_db(tmp_path))
    result = json.loads(main.get_net("mcu"))
    assert "fuzzy_matches" in result
    assert "MCU_TX" in result["fuzzy_matches"]


def test_get_net_no_match(tmp_path):
    import main
    main._load(_load_richer_db(tmp_path))
    result = json.loads(main.get_net("ZZZNOMATCH"))
    assert result["error"] == "not_found"


def test_get_net_high_fanout_returns_summary_only(tmp_path):
    """High-fanout nets must return a summary message, not a list of pins."""
    db = str(tmp_path / "hf.db")
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA_DDL)
    conn.execute(
        "INSERT INTO project (name, root_dir, exported_at, exported_by,"
        " schema_version, sheet_count, component_count, net_count)"
        " VALUES ('B', '/', '2026-04-29T00:00:00Z', 'v', 1, 1, 26, 1)"
    )
    conn.execute("INSERT INTO sheets (name) VALUES ('P')")
    conn.execute("INSERT INTO nets (name, pin_count) VALUES ('GND', 26)")
    # 26 components each with one GND pin
    for i in range(26):
        cur = conn.execute(
            "INSERT INTO components (refdes, mpn, description, value, sheet_id)"
            " VALUES (?, NULL, NULL, NULL, 1)", (f"R{i}",)
        )
        conn.execute(
            "INSERT INTO pins (component_id, pin_number, pin_name, net_name)"
            " VALUES (?, '1', '1', 'GND')", (cur.lastrowid,)
        )
    conn.execute("INSERT INTO variants (name, dnp_refdes) VALUES ('Default', '[]')")
    conn.commit()
    conn.close()
    import main
    main._load(db)
    result = json.loads(main.get_net("GND"))
    assert result["net"] == "GND"
    assert "high_fanout" in result
    assert "pins" not in result
    assert "message" in result


def test_get_component_no_project():
    import main
    main._project = None
    with patch("main._sync_registry_with_disk", return_value={}):
        with patch("main.read_registry", return_value={"projects": []}):
            result = json.loads(main.get_component("U1"))
    assert result["loaded"] is False


def test_get_net_no_project():
    import main
    main._project = None
    with patch("main._sync_registry_with_disk", return_value={}):
        with patch("main.read_registry", return_value={"projects": []}):
            result = json.loads(main.get_net("VCC"))
    assert result["loaded"] is False


# ---- _ensure_project_loaded tests ----

def test_ensure_returns_none_when_project_loaded(tmp_path):
    import main
    db = str(tmp_path / "test.db")
    _make_db(db)
    main._load(db)
    assert main._ensure_project_loaded() is None


def test_ensure_loads_single_registry_match(tmp_path):
    import main
    db = str(tmp_path / "BOARD-pcb-copilot.db")
    _make_db(db)
    with patch("main._sync_registry_with_disk", return_value={db: "today"}):
        with patch("main.read_registry", return_value={"projects": [{"path": db}]}):
            result = main._ensure_project_loaded()
    assert result is None
    assert main._project is not None


def test_ensure_returns_picker_for_multiple_registry_entries(tmp_path):
    import main
    db1 = str(tmp_path / "A-pcb-copilot.db")
    db2 = str(tmp_path / "B-pcb-copilot.db")
    projects = [{"path": db1}, {"path": db2}]
    with patch("main._sync_registry_with_disk", return_value={db1: "today", db2: "3 days ago"}):
        with patch("main.read_registry", return_value={"projects": projects}):
            result = json.loads(main._ensure_project_loaded())
    assert result["error"] == "project_required"
    assert len(result["projects"]) == 2


def test_ensure_returns_copy_as_path_when_nothing_found():
    import main
    with patch("main._sync_registry_with_disk", return_value={}):
        with patch("main.read_registry", return_value={"projects": []}):
            result = json.loads(main._ensure_project_loaded())
    assert result["loaded"] is False
    assert "Copy as path" in result["message"] or "copy" in result["message"].lower()


def test_get_net_self_heals_when_no_project(tmp_path):
    import main
    db = str(tmp_path / "BOARD-pcb-copilot.db")
    _make_db(db)
    with patch("main._sync_registry_with_disk", return_value={db: "today"}):
        with patch("main.read_registry", return_value={"projects": [{"path": db}]}):
            result = json.loads(main.get_net("VCC"))
    assert result.get("error") != "no_project"
    assert main._project is not None


def test_get_component_returns_picker_when_multiple_projects(tmp_path):
    import main
    db1 = str(tmp_path / "A-pcb-copilot.db")
    db2 = str(tmp_path / "B-pcb-copilot.db")
    projects = [{"path": db1}, {"path": db2}]
    with patch("main._sync_registry_with_disk", return_value={db1: "today", db2: "3 days ago"}):
        with patch("main.read_registry", return_value={"projects": projects}):
            result = json.loads(main.get_component("U1"))
    assert result["error"] == "project_required"


# ---- _check_for_update tests ----

def test_check_for_update_skips_if_recent(monkeypatch):
    import main
    from unittest.mock import MagicMock
    from datetime import datetime, timezone, timedelta

    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    monkeypatch.setattr("main._read_state", lambda: {"last_update_check": recent})

    mock_get = MagicMock()
    monkeypatch.setattr("main.httpx.get", mock_get)

    main._check_for_update()

    mock_get.assert_not_called()


def test_check_for_update_writes_new_version(monkeypatch):
    import main
    from unittest.mock import MagicMock

    written = {}

    def fake_write_state(state):
        written.update(state)

    mock_response = MagicMock()
    mock_response.json.return_value = {"tag_name": "v2.0.0"}
    mock_response.raise_for_status.return_value = None

    monkeypatch.setattr("main._read_state", lambda: {})
    monkeypatch.setattr("main._write_state", fake_write_state)
    monkeypatch.setattr("main._read_version", lambda: "1.0.0")
    monkeypatch.setattr("main.httpx.get", lambda *a, **kw: mock_response)

    main._check_for_update()

    assert written.get("update_available") == "2.0.0"
    assert "last_update_check" in written


def test_check_for_update_clears_stale_update(monkeypatch):
    import main
    from unittest.mock import MagicMock

    written = {}

    def fake_write_state(state):
        written.update(state)

    mock_response = MagicMock()
    mock_response.json.return_value = {"tag_name": "v1.0.0"}
    mock_response.raise_for_status.return_value = None

    monkeypatch.setattr("main._read_state", lambda: {"update_available": "1.0.0"})
    monkeypatch.setattr("main._write_state", fake_write_state)
    monkeypatch.setattr("main._read_version", lambda: "1.0.0")
    monkeypatch.setattr("main.httpx.get", lambda *a, **kw: mock_response)

    main._check_for_update()

    assert "update_available" not in written
    assert "last_update_check" in written


def test_check_for_update_silent_on_network_error(monkeypatch):
    import main
    import httpx as _httpx

    def _raise_connect_error(*a, **kw):
        raise _httpx.ConnectError("timeout")

    monkeypatch.setattr("main._read_state", lambda: {})
    monkeypatch.setattr("main.httpx.get", _raise_connect_error)

    # Must not raise
    main._check_for_update()


def test_get_net_exactly_25_pins_is_high_fanout(tmp_path):
    """A net with exactly 25 pins must be treated as high-fanout (>= threshold)."""
    db = str(tmp_path / "hf25.db")
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA_DDL)
    conn.execute(
        "INSERT INTO project (name, root_dir, exported_at, exported_by,"
        " schema_version, sheet_count, component_count, net_count)"
        " VALUES ('B', '/', '2026-04-29T00:00:00Z', 'v', 1, 1, 25, 1)"
    )
    conn.execute("INSERT INTO sheets (name) VALUES ('P')")
    conn.execute("INSERT INTO nets (name, pin_count) VALUES ('VCC', 25)")
    for i in range(25):
        cur = conn.execute(
            "INSERT INTO components (refdes, mpn, description, value, sheet_id)"
            " VALUES (?, NULL, NULL, NULL, 1)", (f"R{i}",)
        )
        conn.execute(
            "INSERT INTO pins (component_id, pin_number, pin_name, net_name)"
            " VALUES (?, '1', '1', 'VCC')", (cur.lastrowid,)
        )
    conn.execute("INSERT INTO variants (name, dnp_refdes) VALUES ('Default', '[]')")
    conn.commit()
    conn.close()
    import main
    main._load(db)
    result = json.loads(main.get_net("VCC"))
    assert result.get("high_fanout") is True, "25-pin net must be high-fanout"
    assert "pins" not in result
