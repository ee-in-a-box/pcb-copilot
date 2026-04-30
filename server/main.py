# server/main.py
import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

try:
    from db import hydrate
    from services.registry import read_registry, upsert_registry_entry
    from services.page_netlist import build_sheet_context, _HIGH_FANOUT_THRESHOLD
except ImportError:
    from server.db import hydrate
    from server.services.registry import read_registry, upsert_registry_entry
    from server.services.page_netlist import build_sheet_context, _HIGH_FANOUT_THRESHOLD

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")


class SchemaTooNewError(ValueError):
    """Raised when the DB schema_version exceeds SUPPORTED_SCHEMA_VERSION."""


class SchemaTooOldError(ValueError):
    """Raised when the DB schema_version is below MIN_SUPPORTED_SCHEMA_VERSION."""


def _manifest_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "manifest.json"
    return Path(__file__).parent.parent / "manifest.json"


def _read_version() -> str:
    try:
        return json.loads(_manifest_path().read_text(encoding="utf-8"))["version"]
    except Exception:
        return "0.0.0"


STATE_PATH = Path.home() / ".ee-in-a-box" / "pcb-copilot-state.json"

_GITHUB_RELEASES_URL = (
    "https://api.github.com/repos/ee-in-a-box/pcb-copilot/releases/latest"
)
_UPDATE_CHECK_INTERVAL_HOURS = 24

# Schema versions supported by this build. Both constants must stay in sync with
# altium-copilot/server/export.py. Bump SUPPORTED_SCHEMA_VERSION (and
# MIN_SUPPORTED_SCHEMA_VERSION if the change is breaking) when the schema changes.
MIN_SUPPORTED_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSION = 1


def _read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _is_newer(latest: str, current: str) -> bool:
    def _t(v: str) -> tuple:
        return tuple(int(x) for x in v.split("."))
    try:
        return _t(latest) > _t(current)
    except ValueError:
        return False


SERVER_INSTRUCTIONS = """\
You are a pcb-copilot — an MCP server that lets cross-functional team members
(firmware, mechanical, test, reliability engineers) query a schematic snapshot
exported from Altium Designer.

## Session Start

Run these steps in order at the start of every session:

1. detect_project — check registry and auto-load if a remembered DB is found.
2. list_variants — always ask the user which variant to work in, then call
   set_active_variant. Never auto-select even if only one variant exists.
3. list_sheets — read all sheet names, then synthesize a summary for the user:
   project name, sheet count, variant count, snapshot age, and your read of
   what the board does based on component descriptions and sheet names.

If detect_project returns loaded=false, ask the user for the .db file path,
then call open_project.

## Switching Projects

Call open_project with the new path, then repeat the session-start steps.

## General Rules

- Never answer from memory about component values, nets, or topology — always
  use the tools.
- Only state conclusions supported by tool results. If uncertain, call more
  tools, search a datasheet, do a websearch or tell the user you are not sure.
- Always work in the context of the active variant. Components with dnp=true
  are not populated. If the user's question implies a different variant, ask
  before switching.
- Nets with 25 or more connections are likely power or ground rails — treat
  them as rails, not signals.

## Answering Questions

- Start with get_sheet_context for any question about a sheet, circuit, or
  signal flow. One call returns all components with pin-to-net data and
  one-hop cross-sheet neighbors — usually sufficient to answer the question.
- For cross-sheet tracing, follow connected_to by calling
  get_sheet_context(sheet_name=...) for the next sheet. Do not call
  get_component one-by-one for cross-sheet components.
- Use get_component for targeted lookups of a specific component by refdes
  or description. Use get_net for targeted lookups of a specific net.
- Do not call get_sheet_context on the same sheet twice in one turn.

## Datasheet Lookups

When the user asks about a specification not stored in the netlist (temperature
rating, voltage rating, tolerance, package type, MTBF, derating curves), the
netlist has the MPN — use it to look up the datasheet and answer from there.
Do not guess or approximate. If the MPN is missing, say so and ask the user
to provide the datasheet or the value directly.

## Behavioral Guidelines

- Think Before Proposing: State your assumptions explicitly. If multiple
  interpretations exist, present them — don't pick silently.
- Simplicity First: Propose the minimum answer. Do not speculate beyond what
  the tool results support.
- Goal-Driven Execution: For multi-step analysis (e.g. "make a table of all
  ICs with their temp rating"), state a brief plan and loop tool calls until
  complete.

## Error Recovery

- Component not found → use get_component with a partial name or description
- Net not found → use get_net with a keyword (e.g. get_net("UART"), get_net("CAN"))
- Sheet not found → call list_sheets and present options to the user
"""

mcp = FastMCP("pcb-copilot", instructions=SERVER_INSTRUCTIONS)

# ---------- module-level state ----------
_project: dict | None = None
_sheets: list[dict] = []
_variants: list[dict] = []
_active_variant: dict | None = None
_netlist: dict = {}


def _load(db_path: str) -> None:
    global _project, _sheets, _variants, _active_variant, _netlist
    # Hydrate into locals first — only commit to globals after all validation
    # passes, so a failed load never leaves the previous project partially replaced.
    project_meta, sheets, variants, netlist = hydrate(db_path)
    schema_version = project_meta["schema_version"]
    if schema_version > SUPPORTED_SCHEMA_VERSION:
        raise SchemaTooNewError(
            f"This DB was exported with a newer altium-copilot. Update pcb-copilot "
            f"to open it. (DB schema_version={schema_version}, "
            f"supported={SUPPORTED_SCHEMA_VERSION}, "
            f"exported_by={project_meta['exported_by']})"
        )
    if schema_version < MIN_SUPPORTED_SCHEMA_VERSION:
        raise SchemaTooOldError(
            f"This DB was exported with an older altium-copilot and is no longer "
            f"supported. Re-export the project to open it. "
            f"(DB schema_version={schema_version}, "
            f"min_supported={MIN_SUPPORTED_SCHEMA_VERSION}, "
            f"exported_by={project_meta['exported_by']})"
        )
    _project = project_meta
    _sheets = sheets
    _variants = variants
    _active_variant = None
    _netlist = netlist


# ---------- detect_project ----------

@mcp.tool(title="Detect Project", annotations=ToolAnnotations(readOnlyHint=True))
def detect_project() -> str:
    """Check registry for a remembered .db file. If found and file exists, auto-loads
    the project — no second call needed. Returns {loaded: true, project: {...}} on
    success or {loaded: false} when the user needs to provide a path."""
    registry = read_registry()
    projects = registry.get("projects", [])
    if not projects:
        return json.dumps({"loaded": False, "server_version": _read_version()})

    latest = max(projects, key=lambda p: p.get("last_used", ""))
    db_path = latest["path"]

    if not Path(db_path).exists():
        return json.dumps({
            "loaded": False,
            "warning": (
                f"Previously used DB not found: {db_path}. "
                "Ask the user to provide the .db file path."
            ),
            "server_version": _read_version(),
        })

    try:
        _load(db_path)
    except Exception as e:
        return json.dumps({"loaded": False, "error": str(e),
                           "server_version": _read_version()})

    state = _read_state()
    current = _read_version()
    result: dict = {"loaded": True, "project": _project, "server_version": current}
    update_available = state.get("update_available")
    if update_available and _is_newer(update_available, current):
        result["update_available"] = update_available
    return json.dumps(result, indent=2)


# ---------- open_project ----------

@mcp.tool(title="Open Project", annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def open_project(db_path: str) -> str:
    """Open a pcb-copilot .db snapshot file. Validates the schema version, loads all
    project data into memory, and saves the path to the registry. Call at session start
    when detect_project returns loaded=false, or to switch to a different board."""
    if not Path(db_path).exists():
        return json.dumps({
            "error": "file_not_found",
            "message": f"File not found: {db_path}. Check the path and try again.",
        })

    try:
        _load(db_path)
    except SchemaTooNewError as e:
        return json.dumps({"error": "schema_too_new", "message": str(e)})
    except SchemaTooOldError as e:
        return json.dumps({"error": "schema_too_old", "message": str(e)})
    except ValueError as e:
        return json.dumps({"error": "open_failed", "message": str(e)})

    upsert_registry_entry(db_path)

    p = _project
    exported_at = p["exported_at"][:16].replace("T", " ")
    return (
        f"Snapshot from {exported_at} UTC (exported by {p['exported_by']}).\n"
        f"Project: {p['name']} — {p['sheet_count']} sheets, "
        f"{p['component_count']} components, {len(_variants)} variants."
    )


# ---------- list_variants ----------

@mcp.tool(title="List Variants", annotations=ToolAnnotations(readOnlyHint=True))
def list_variants() -> str:
    """List all variants in the project with their DNP component lists. Shows which variant
    is currently active. Always call this after loading a project so the user can choose."""
    if _project is None:
        return json.dumps({"error": "no_project",
                           "message": "No project loaded. Provide a .db file path."})
    active_name = _active_variant["name"] if _active_variant else None
    return json.dumps({
        "variants": [
            {
                "name": v["name"],
                "dnp_count": len(v["dnp_refdes"]),
                "dnp_refdes": v["dnp_refdes"],
                "active": v["name"] == active_name,
            }
            for v in _variants
        ]
    }, indent=2)


# ---------- set_active_variant ----------

@mcp.tool(title="Set Active Variant", annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def set_active_variant(name: str) -> str:
    """Set the active build variant by name. All subsequent tool calls filter components
    by this variant's DNP list. Always call after the user selects a variant."""
    global _active_variant
    if _project is None:
        return json.dumps({"error": "no_project",
                           "message": "No project loaded. Provide a .db file path."})
    match = next((v for v in _variants if v["name"].lower() == name.lower()), None)
    if match is None:
        available = [v["name"] for v in _variants]
        return json.dumps({
            "error": "variant_not_found",
            "message": f"Variant '{name}' not found.",
            "available": available,
        })
    _active_variant = match
    return json.dumps({
        "active": match["name"],
        "dnp_count": len(match["dnp_refdes"]),
    })


# ---------- _VariantAdapter ----------

class _VariantAdapter:
    """Wraps _active_variant dict to satisfy build_sheet_context's is_dnp() interface."""
    def __init__(self, active_variant: dict | None):
        self._dnp = set(active_variant["dnp_refdes"]) if active_variant else set()

    def is_dnp(self, refdes: str) -> bool:
        return refdes in self._dnp


# ---------- list_sheets ----------

@mcp.tool(title="List Sheets", annotations=ToolAnnotations(readOnlyHint=True))
def list_sheets() -> str:
    """Return all sheet names in the project. Call this when the user asks about a sheet
    by name you don't recognize, or to know what sheets exist before calling
    get_sheet_context."""
    if _project is None:
        return json.dumps({"error": "no_project",
                           "message": "No project loaded. Provide a .db file path."})
    return json.dumps({"sheets": [s["name"] for s in _sheets]})


# ---------- get_sheet_context ----------

@mcp.tool(title="Get Sheet Context", annotations=ToolAnnotations(readOnlyHint=True))
def get_sheet_context(sheet_name: str) -> str:
    """Get all components on a schematic sheet with their pin-to-net connections and
    cross-sheet neighbors. The primary tool for any question about what is on a sheet,
    how a circuit works, or how signals flow. Call this first for most questions."""
    if _project is None:
        return json.dumps({"error": "no_project",
                           "message": "No project loaded. Provide a .db file path."})
    sheet_names = [s["name"] for s in _sheets]
    canonical = next((s for s in sheet_names if s.lower() == sheet_name.lower()), None)
    if canonical is None:
        return json.dumps({
            "error": "sheet_not_found",
            "message": f"Sheet '{sheet_name}' not found.",
            "available_sheets": sheet_names,
        })
    adapter = _VariantAdapter(_active_variant)
    result = json.loads(build_sheet_context(_netlist, canonical, adapter))
    comps = result.get("components", [])
    if comps and all(c["dnp"] for c in comps):
        result["warning"] = (
            f"All {len(comps)} components on sheet '{canonical}' are DNP "
            f"in the '{_active_variant['name']}' variant. "
            "Switch variants with set_active_variant to see populated components."
        )
    return json.dumps(result, indent=2)


# ---------- get_component ----------

@mcp.tool(title="Get Component", annotations=ToolAnnotations(readOnlyHint=True))
def get_component(query: str) -> str:
    """Get full detail for a component: MPN, value, description, sheet, all pins with
    names and nets, and DNP status for the active variant. Tries exact refdes match
    first, then case-insensitive search across refdes, MPN, and description."""
    if _project is None:
        return json.dumps({"error": "no_project",
                           "message": "No project loaded. Provide a .db file path."})
    components = _netlist.get("components", {})

    # Exact refdes match (case-insensitive)
    matched = next((k for k in components if k.lower() == query.lower()), None)
    if matched:
        comp = components[matched]
        dnp = (
            matched in _active_variant["dnp_refdes"]
            if _active_variant
            else False
        )
        pins = comp.get("pins", {})
        has_net = any(
            (p.get("net") if isinstance(p, dict) else p)
            for p in pins.values()
        )
        return json.dumps({
            "refdes": matched,
            "mpn": comp.get("mpn"),
            "description": comp.get("description"),
            "value": comp.get("value"),
            "sheet": comp.get("sheet"),
            "dnp": dnp,
            "unconnected": not has_net,
            "pins": pins,
        }, indent=2)

    # Fuzzy: case-insensitive search across refdes, MPN, description
    q = query.lower()
    groups: dict[str, list] = {}
    for refdes, comp in components.items():
        if (
            q in refdes.lower()
            or q in (comp.get("mpn") or "").lower()
            or q in (comp.get("description") or "").lower()
        ):
            key = comp.get("mpn") or f"__no_mpn_{refdes}__"
            groups.setdefault(key, []).append({
                "refdes": refdes,
                "mpn": comp.get("mpn"),
                "description": comp.get("description"),
                "value": comp.get("value"),
                "sheet": comp.get("sheet"),
            })

    if not groups:
        return json.dumps({
            "error": "not_found",
            "message": (
                f"No component matching '{query}'. Try a partial refdes, MPN, "
                "or description keyword."
            ),
        })

    return json.dumps({
        "fuzzy_matches": [
            {
                "mpn": items[0]["mpn"],
                "description": items[0]["description"],
                "count": len(items),
                "refdes": [h["refdes"] for h in items],
            }
            for items in groups.values()
        ]
    }, indent=2)


# ---------- get_net ----------

@mcp.tool(title="Get Net", annotations=ToolAnnotations(readOnlyHint=True))
def get_net(query: str) -> str:
    """Look up a net by name. Tries exact match first, then case-insensitive search.
    For normal nets: returns all pins with component context. For high-fanout nets
    (power/ground rails): returns a summary with directive to filter by component or
    sheet. For fuzzy matches: returns list of matching net names to narrow down."""
    if _project is None:
        return json.dumps({"error": "no_project",
                           "message": "No project loaded. Provide a .db file path."})
    nets = _netlist.get("nets", {})

    # Exact match (case-insensitive)
    net_key = next((k for k in nets if k.lower() == query.lower()), None)

    if net_key:
        connections = nets[net_key]
        if len(connections) >= _HIGH_FANOUT_THRESHOLD:
            return json.dumps({
                "net": net_key,
                "pin_count": len(connections),
                "high_fanout": True,
                "message": (
                    f"{net_key} has {len(connections)} connections — "
                    "this is likely a power or ground plane.\n"
                    "Ask me about a specific component "
                    f'(e.g. "what {net_key} pins does U5 have?") '
                    "or a specific sheet "
                    f'(e.g. "what\'s connected to {net_key} on the Power sheet?").'
                ),
            }, indent=2)

        components = _netlist.get("components", {})
        return json.dumps({
            "net": net_key,
            "pin_count": len(connections),
            "pins": [
                {
                    "refdes": refdes,
                    "pin": pin,
                    "pin_name": (
                        components.get(refdes, {})
                        .get("pins", {})
                        .get(pin, {})
                        .get("name", pin)
                        if isinstance(
                            components.get(refdes, {}).get("pins", {}).get(pin), dict
                        )
                        else pin
                    ),
                    "sheet": components.get(refdes, {}).get("sheet"),
                }
                for refdes, pin in connections
            ],
        }, indent=2)

    # Fuzzy: case-insensitive substring search
    q = query.lower()
    fuzzy = [name for name in nets if q in name.lower()]
    if not fuzzy:
        return json.dumps({
            "error": "not_found",
            "message": (
                f"No net matching '{query}'. Try a keyword like 'UART', 'CAN', or 'VDD'."
            ),
        })

    return json.dumps({"fuzzy_matches": sorted(fuzzy)}, indent=2)


# ---------- update-check daemon ----------

def _check_for_update() -> None:
    """Background thread: poll GitHub releases once per 24 h, write result to state.json."""
    try:
        state = _read_state()
        last = state.get("last_update_check", "")
        if last:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if elapsed < _UPDATE_CHECK_INTERVAL_HOURS * 3600:
                return
        resp = httpx.get(_GITHUB_RELEASES_URL, timeout=10,
                         headers={"Accept": "application/vnd.github+json",
                                  "User-Agent": "pcb-copilot"})
        resp.raise_for_status()
        latest = resp.json().get("tag_name", "").lstrip("v").split("-")[0]
        state["last_update_check"] = datetime.now(timezone.utc).isoformat()
        if latest and _is_newer(latest, _read_version()):
            state["update_available"] = latest
        else:
            state.pop("update_available", None)
        _write_state(state)
    except Exception:
        pass  # network errors are silent — never crash the server


_update_thread = threading.Thread(target=_check_for_update, daemon=True)
_update_thread.start()


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(f"pcb-copilot v{_read_version()}")  # noqa: T201
        sys.exit(0)
    mcp.run()
