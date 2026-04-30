import json
import sqlite3


def hydrate(db_path: str) -> tuple[dict, list[dict], list[dict], dict]:
    """Read a pcb-copilot .db file and return (project_meta, sheets, variants, netlist).

    netlist mirrors altium-copilot's structure:
      {
        "nets": {net_name: [(refdes, pin_number), ...]},
        "pin_to_net": {refdes: {pin_number: net_name}},
        "components": {refdes: {mpn, description, value, sheet, pins: {pin_num: {name, net}}}},
      }

    Raises ValueError if row counts don't match project metadata (corrupt DB).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM project LIMIT 1").fetchone()
        if not row:
            raise ValueError("DB missing project row — file may be corrupt.")
        project_meta = dict(row)

        comp_count = conn.execute("SELECT COUNT(*) FROM components").fetchone()[0]
        net_count = conn.execute("SELECT COUNT(*) FROM nets").fetchone()[0]
        if comp_count != project_meta["component_count"]:
            raise ValueError(
                f"DB corrupt: project metadata claims {project_meta['component_count']}"
                f" components but found {comp_count}."
            )
        if net_count != project_meta["net_count"]:
            raise ValueError(
                f"DB corrupt: project metadata claims {project_meta['net_count']}"
                f" nets but found {net_count}."
            )

        sheets = [
            {"id": r["id"], "name": r["name"]}
            for r in conn.execute("SELECT id, name FROM sheets ORDER BY id").fetchall()
        ]
        sheet_id_to_name = {s["id"]: s["name"] for s in sheets}

        variants = []
        for r in conn.execute(
            "SELECT id, name, dnp_refdes FROM variants ORDER BY id"
        ).fetchall():
            variants.append(
                {"id": r["id"], "name": r["name"], "dnp_refdes": json.loads(r["dnp_refdes"])}
            )

        components: dict = {}
        comp_id_to_refdes: dict[int, str] = {}
        for r in conn.execute(
            "SELECT id, refdes, mpn, description, value, sheet_id FROM components"
        ).fetchall():
            sheet_name = sheet_id_to_name.get(r["sheet_id"], "") if r["sheet_id"] else ""
            components[r["refdes"]] = {
                "mpn": r["mpn"],
                "description": r["description"],
                "value": r["value"],
                "sheet": sheet_name,
                "pins": {},
            }
            comp_id_to_refdes[r["id"]] = r["refdes"]

        nets: dict[str, list[tuple[str, str]]] = {
            r["name"]: []
            for r in conn.execute("SELECT name FROM nets ORDER BY name").fetchall()
        }
        pin_to_net: dict[str, dict] = {refdes: {} for refdes in components}

        for r in conn.execute(
            "SELECT component_id, pin_number, pin_name, net_name FROM pins"
        ).fetchall():
            refdes = comp_id_to_refdes.get(r["component_id"])
            if refdes is None:
                continue
            pin_num = r["pin_number"]
            net_name = r["net_name"]
            components[refdes]["pins"][pin_num] = {
                "name": r["pin_name"],
                "net": net_name,
            }
            if net_name:
                if net_name not in nets:
                    nets[net_name] = []
                nets[net_name].append((refdes, pin_num))
                pin_to_net[refdes][pin_num] = net_name

        netlist = {"nets": nets, "pin_to_net": pin_to_net, "components": components}
        return project_meta, sheets, variants, netlist
    finally:
        conn.close()
