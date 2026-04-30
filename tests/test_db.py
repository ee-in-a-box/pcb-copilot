import sqlite3
import pytest
from db import hydrate
from conftest import _SCHEMA_DDL


def _make_test_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_DDL)

    conn.execute(
        "INSERT INTO project (name, root_dir, exported_at, exported_by, schema_version,"
        " sheet_count, component_count, net_count) VALUES (?,?,?,?,?,?,?,?)",
        ("TestBoard", "/projects/TestBoard", "2026-04-29T14:32:00Z",
         "altium-copilot v0.1.10", 1, 2, 2, 2),
    )
    cur = conn.execute("INSERT INTO sheets (name) VALUES ('MCU')")
    mcu_id = cur.lastrowid
    cur = conn.execute("INSERT INTO sheets (name) VALUES ('Comms')")
    comms_id = cur.lastrowid

    conn.execute(
        "INSERT INTO variants (name, dnp_refdes) VALUES ('Default', '[]')"
    )
    conn.execute(
        "INSERT INTO variants (name, dnp_refdes) VALUES ('Lite', '[\"R1\"]')"
    )

    conn.execute("INSERT INTO nets (name, pin_count) VALUES ('MCU_TX', 2)")
    conn.execute("INSERT INTO nets (name, pin_count) VALUES ('GND', 2)")

    cur = conn.execute(
        "INSERT INTO components (refdes, mpn, description, value, sheet_id)"
        " VALUES ('U1', 'STM32G474', 'MCU', NULL, ?)", (mcu_id,)
    )
    u1_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO components (refdes, mpn, description, value, sheet_id)"
        " VALUES ('R1', 'RC0402', 'RES 10K', '10K', ?)", (comms_id,)
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


def test_hydrate_project_metadata(tmp_path):
    db = str(tmp_path / "test.db")
    _make_test_db(db)
    project, sheets, variants, netlist = hydrate(db)
    assert project["name"] == "TestBoard"
    assert project["schema_version"] == 1
    assert project["exported_by"] == "altium-copilot v0.1.10"


def test_hydrate_sheets(tmp_path):
    db = str(tmp_path / "test.db")
    _make_test_db(db)
    _, sheets, _, _ = hydrate(db)
    names = [s["name"] for s in sheets]
    assert names == ["MCU", "Comms"]


def test_hydrate_variants(tmp_path):
    db = str(tmp_path / "test.db")
    _make_test_db(db)
    _, _, variants, _ = hydrate(db)
    assert variants[0]["name"] == "Default"
    assert variants[0]["dnp_refdes"] == []
    assert variants[1]["name"] == "Lite"
    assert variants[1]["dnp_refdes"] == ["R1"]


def test_hydrate_netlist_components(tmp_path):
    db = str(tmp_path / "test.db")
    _make_test_db(db)
    _, _, _, netlist = hydrate(db)
    comps = netlist["components"]
    assert "U1" in comps
    assert comps["U1"]["mpn"] == "STM32G474"
    assert comps["U1"]["sheet"] == "MCU"
    assert comps["U1"]["pins"]["PA9"]["name"] == "PA9"
    assert comps["U1"]["pins"]["PA9"]["net"] == "MCU_TX"
    assert "R1" in comps
    assert comps["R1"]["value"] == "10K"
    assert comps["R1"]["sheet"] == "Comms"


def test_hydrate_netlist_nets(tmp_path):
    db = str(tmp_path / "test.db")
    _make_test_db(db)
    _, _, _, netlist = hydrate(db)
    nets = netlist["nets"]
    assert set(nets["MCU_TX"]) == {("U1", "PA9"), ("R1", "1")}
    assert set(nets["GND"]) == {("U1", "GND"), ("R1", "2")}
    # Verify elements are tuples (not lists)
    assert all(isinstance(entry, tuple) for entry in nets["MCU_TX"])


def test_hydrate_netlist_pin_to_net(tmp_path):
    db = str(tmp_path / "test.db")
    _make_test_db(db)
    _, _, _, netlist = hydrate(db)
    p2n = netlist["pin_to_net"]
    assert p2n["U1"]["PA9"] == "MCU_TX"
    assert p2n["R1"]["2"] == "GND"


def test_hydrate_corrupt_component_count_raises(tmp_path):
    db = str(tmp_path / "test.db")
    _make_test_db(db)
    # Tamper: add a component without updating project metadata
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO components (refdes, mpn, description, value, sheet_id)"
        " VALUES ('C99', NULL, NULL, NULL, NULL)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="component"):
        hydrate(db)


def test_hydrate_corrupt_net_count_raises(tmp_path):
    db = str(tmp_path / "test.db")
    _make_test_db(db)
    # Tamper: add a net without updating project metadata
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO nets (name, pin_count) VALUES ('EXTRA_NET', 0)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="net"):
        hydrate(db)
