# server/main.py
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from db import hydrate
    from services.registry import read_registry, upsert_registry_entry, get_last_variant, register_discovered
    from services.page_netlist import build_sheet_context, MAX_RESULT_SIZE_CHARS, _HIGH_FANOUT_THRESHOLD
except ImportError:
    from server.db import hydrate
    from server.services.registry import read_registry, upsert_registry_entry, get_last_variant, register_discovered
    from server.services.page_netlist import build_sheet_context, MAX_RESULT_SIZE_CHARS, _HIGH_FANOUT_THRESHOLD

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")


class SchemaTooNewError(ValueError):
    """Raised when the DB schema_version exceeds SUPPORTED_SCHEMA_VERSION."""


class SchemaTooOldError(ValueError):
    """Raised when the DB schema_version is below MIN_SUPPORTED_SCHEMA_VERSION."""


def _format_age(path: Path) -> str:
    days = (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)).days
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _format_age_safe(path_str: str) -> str:
    try:
        return _format_age(Path(path_str))
    except Exception:
        return "unknown"


def _sync_registry_with_disk(search_dirs: list[Path] | None = None) -> dict[str, str]:
    """Scan disk, register all found *-pcb-copilot.db files, return {path: last_modified}."""
    found = _scan_for_db(search_dirs=search_dirs)
    for path_str, _ in found:
        register_discovered(path_str)
    return {path_str: age for path_str, age in found}


def _scan_for_db(
    filename_filter: str = "*-pcb-copilot.db",
    timeout: float = 5.0,
    search_dirs: list[Path] | None = None,
) -> list[tuple[str, str]]:
    """Scan standard dirs for .db files matching filename_filter.

    Returns [(resolved_path_str, last_modified_str)] sorted newest-first.
    search_dirs is injectable for testing; defaults to Downloads/Desktop/Documents.
    Uses os.walk with depth cap of 6 and a wall-clock timeout.
    """
    if search_dirs is None:
        search_dirs = [
            Path.home() / "Downloads",
            Path.home() / "Desktop",
            Path.home() / "Documents",
        ]
    found: list[Path] = []
    deadline = time.monotonic() + timeout
    for base in search_dirs:
        if not base.exists():
            continue
        try:
            for root, dirs, files in os.walk(str(base)):
                if time.monotonic() > deadline:
                    dirs.clear()
                    break
                depth = len(Path(root).relative_to(base).parts)
                if depth >= 6:
                    dirs.clear()
                for f in files:
                    if Path(f).match(filename_filter):
                        found.append(Path(root) / f)
        except PermissionError:
            continue
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [(str(p), _format_age(p)) for p in found]


def _manifest_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "manifest.json"
    return Path(__file__).parent.parent / "manifest.json"


def _read_version() -> str:
    try:
        return json.loads(_manifest_path().read_text(encoding="utf-8"))["version"]
    except Exception:
        return "0.0.0"


STATE_PATH = Path(
    os.environ.get("USERPROFILE") or str(Path.home())
) / ".ee-in-a-box" / "pcb-copilot-state.json"

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

If you haven't called load_project() yet this session and the user asks anything
schematic-related (GPIO, net, component, connector, board, schematic, pin, refdes,
signal, power rail, DNP, variant), call load_project() first — it will auto-discover
the file.

If the user pastes a path ending in .db, immediately call load_project(db_path=<that path>).

After load_project succeeds, call list_variants(). If list_variants returns no variant
marked active, ask the user which variant to work in before proceeding.

If load_project returns a `projects` list instead of loading, ask the user which
project they want ("I found X and Y — which one?"), then call
load_project(db_path=<chosen path>).

## Switching Projects

Call load_project(db_path=<new path>), then call list_variants().

## General Rules

- Never answer from memory about component values, nets, or topology — always
  use the tools.
- Only state conclusions supported by tool results. If uncertain, call more
  tools, search a datasheet, do a websearch or tell the user you are not sure.
- Always work in the context of the active variant. Components with dnp=true are not
  populated. If the user's question implies a different variant, ask before switching.
- Nets with 25 or more connections are likely power or ground rails — treat
  them as rails, not signals.

## Answering Questions

- Start with get_sheet_context for any question about a sheet, circuit, or
  signal flow. One call returns all components with pin-to-net data and
  one-hop cross-sheet neighbors — usually sufficient to answer the question.
- Results are paginated. If the response contains has_more:true, call
  get_sheet_context again with the offset from the next: line and the same
  sheet_name, and repeat until has_more:false. Accumulate all pages before
  answering the user.
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

## Updates

If load_project returns update_available, tell the user immediately:
"A new version of pcb-copilot is available (vX.X.X). Run this to update: <update_command>"
Do this before continuing with any other response.

## Error Recovery

- Component not found → use get_component with a partial name or description
- Net not found → use get_net with a keyword or regex (e.g. get_net("UART"), get_net("UART.*"), get_net("3V3|5V0"))
- Sheet not found → call list_sheets and present options to the user
"""

mcp = FastMCP("pcb-copilot", instructions=SERVER_INSTRUCTIONS)

# ---------- module-level state ----------
_project: dict | None = None
_sheets: list[dict] = []
_variants: list[dict] = []
_active_variant: dict | None = None
_netlist: dict = {}
_db_path: str | None = None


def _load(db_path: str) -> None:
    global _project, _sheets, _variants, _active_variant, _netlist, _db_path
    # Hydrate into locals first — only commit to globals after all validation
    # passes, so a failed load never leaves the previous project partially replaced.
    project_meta, sheets, variants, netlist = hydrate(db_path)
    schema_version = project_meta["schema_version"]
    if schema_version > SUPPORTED_SCHEMA_VERSION:
        if sys.platform == "win32":
            update_cmd = "irm https://raw.githubusercontent.com/ee-in-a-box/pcb-copilot/main/install.ps1 | iex"
        else:
            update_cmd = "curl -fsSL https://raw.githubusercontent.com/ee-in-a-box/pcb-copilot/main/install.sh | bash"
        raise SchemaTooNewError(
            f"This DB was exported with a newer altium-copilot. Update pcb-copilot "
            f"to open it: {update_cmd} "
            f"(DB schema_version={schema_version}, "
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
    _db_path = db_path


# ---------- load_project ----------

_COPY_AS_PATH_MESSAGE = (
    "No .db file found automatically. To load one:\n"
    "Windows: right-click the file in Explorer → 'Copy as path' → paste it here.\n"
    "Mac: right-click in Finder → hold Option → 'Copy as Pathname' → paste it here."
)


@mcp.tool(title="Load Project", annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def load_project(db_path: str | None = None) -> str:
    """Call this at the start of any session, or whenever the user asks about a PCB,
    schematic, net, GPIO, component, or connector. Auto-discovers the .db file in
    standard locations — no path needed in most cases. Pass db_path only if
    auto-discovery fails or to switch projects."""

    def _update_notice(result: dict) -> dict:
        state = _read_state()
        current = _read_version()
        update_available = state.get("update_available")
        if update_available and _is_newer(update_available, current):
            result["update_available"] = update_available
            if sys.platform == "win32":
                result["update_command"] = (
                    "irm https://raw.githubusercontent.com/ee-in-a-box/pcb-copilot"
                    "/main/install.ps1 | iex"
                )
            else:
                result["update_command"] = (
                    "curl -fsSL https://raw.githubusercontent.com/ee-in-a-box/pcb-copilot"
                    "/main/install.sh | bash"
                )
        return result

    def _load_and_respond(path: str, discovered: bool = False, age: str | None = None) -> str:
        try:
            _load(path)
        except SchemaTooNewError as e:
            return json.dumps({"loaded": False, "error": "schema_too_new", "message": str(e)})
        except SchemaTooOldError as e:
            return json.dumps({"loaded": False, "error": "schema_too_old", "message": str(e)})
        except Exception as e:
            return json.dumps({"loaded": False, "error": "load_failed", "message": str(e)})
        if path.endswith("-pcb-copilot.db"):
            upsert_registry_entry(path)
        result: dict = {"loaded": True, "project": _project, "server_version": _read_version()}
        if discovered:
            result["discovery"] = {
                "path": path,
                "last_modified": age or "unknown",
                "confirm": "Tell the user what file was found and its age, and ask if this is the right file.",
            }
        return json.dumps(_update_notice(result), indent=2)

    # --- Explicit path provided ---
    if db_path is not None:
        if Path(db_path).exists():
            return _load_and_respond(db_path)
        # Path doesn't exist — try scanning for the filename
        filename = Path(db_path).name
        found = _scan_for_db(filename_filter=filename)
        if len(found) == 1:
            return _load_and_respond(found[0][0], discovered=True, age=found[0][1])
        if len(found) > 1:
            return json.dumps({
                "loaded": False,
                "files": [{"path": p, "last_modified": age} for p, age in found],
                "hint": "Multiple matches found. Call load_project(db_path=<path>) with the correct one.",
            }, indent=2)
        return json.dumps({"loaded": False, "message": _COPY_AS_PATH_MESSAGE})

    # --- No path: sync disk → registry is source of truth ---
    current = _read_version()
    scan_ages = _sync_registry_with_disk()
    registry = read_registry()
    projects = registry.get("projects", [])

    for entry in projects:
        entry["last_modified"] = scan_ages.get(entry["path"]) or _format_age_safe(entry["path"])
        entry.setdefault("last_used", None)

    if len(projects) == 0:
        return json.dumps({"loaded": False, "server_version": current, "message": _COPY_AS_PATH_MESSAGE})

    if len(projects) == 1:
        entry = projects[0]
        discovered = entry["last_used"] is None
        return _load_and_respond(entry["path"], discovered=discovered, age=entry["last_modified"])

    sorted_projects = sorted(projects, key=lambda p: p.get("last_used") or "", reverse=True)
    return json.dumps({
        "loaded": False,
        "server_version": current,
        "projects": sorted_projects,
    }, indent=2)


# ---------- list_variants ----------

@mcp.tool(title="List Variants", annotations=ToolAnnotations(readOnlyHint=True))
def list_variants() -> str:
    """List all build variants and auto-select the active one. Auto-selects the
    last-used variant from the registry, or the only variant when there is one.
    When multiple variants exist with no prior selection, returns the list with no
    active variant — Claude must ask the user to choose."""
    if early_exit := _ensure_project_loaded():
        return early_exit

    global _active_variant

    # Attempt server-side auto-selection
    if _active_variant is None:
        candidate = None
        if _db_path:
            last = get_last_variant(_db_path)
            if last:
                candidate = next((v for v in _variants if v["name"] == last), None)
        if candidate is None and len(_variants) == 1:
            candidate = _variants[0]
        if candidate is not None:
            _active_variant = candidate

    active_name = _active_variant["name"] if _active_variant else None

    variants_out = [
        {
            "name": v["name"],
            "dnp_count": len(v["dnp_refdes"]),
            "dnp_refdes": v["dnp_refdes"],
            "active": v["name"] == active_name,
        }
        for v in _variants
    ]

    result: dict = {"variants": variants_out}
    if active_name:
        result["auto_selected"] = active_name
        result["announce"] = "Tell the user which variant is active and list the others. Do not ask for confirmation."

    return json.dumps(result, indent=2)


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
    if _db_path and _db_path.endswith("-pcb-copilot.db"):
        upsert_registry_entry(_db_path, last_variant=match["name"])
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


def _ensure_project_loaded() -> str | None:
    """Returns None if a project is ready. Returns a JSON error string if blocked.

    Callers use: if early_exit := _ensure_project_loaded(): return early_exit
    """
    if _project is not None:
        return None
    scan_ages = _sync_registry_with_disk()
    registry = read_registry()
    projects = registry.get("projects", [])
    for entry in projects:
        entry["last_modified"] = scan_ages.get(entry["path"]) or _format_age_safe(entry["path"])
        entry.setdefault("last_used", None)
    if len(projects) == 1:
        try:
            _load(projects[0]["path"])
            return None
        except Exception:
            pass
    if len(projects) > 1:
        sorted_projects = sorted(projects, key=lambda p: p.get("last_used") or "", reverse=True)
        return json.dumps({
            "error": "project_required",
            "projects": sorted_projects,
            "hint": "Ask the user which project they want, then call load_project(db_path=<path>).",
        })
    return json.dumps({"loaded": False, "message": _COPY_AS_PATH_MESSAGE})


# ---------- list_sheets ----------

@mcp.tool(title="List Sheets", annotations=ToolAnnotations(readOnlyHint=True))
def list_sheets() -> str:
    """Return all sheet names in the project. Call this when the user asks about a sheet
    by name you don't recognize, or to know what sheets exist before calling
    get_sheet_context."""
    if early_exit := _ensure_project_loaded():
        return early_exit
    return json.dumps({"sheets": [s["name"] for s in _sheets]})


# ---------- get_sheet_context ----------

@mcp.tool(title="Get Sheet Context", annotations=ToolAnnotations(readOnlyHint=True),
          meta={"anthropic/maxResultSizeChars": MAX_RESULT_SIZE_CHARS})
def get_sheet_context(sheet_name: str, offset: int = 0) -> str:
    """Get all components on a schematic sheet with their pin-to-net connections and
    cross-sheet neighbors. The primary tool for any question about what is on a sheet,
    how a circuit works, or how signals flow. Use for questions about signal flow,
    circuit function, or what's connected on a specific sheet — covers GPIO mappings,
    bus topology, power distribution, and cross-sheet tracing. Call this first for
    most questions.

    Results are paginated by character budget. If the response contains has_more:true,
    call this tool again with the offset from the next: line and the same sheet_name,
    and repeat until has_more:false. Accumulate all pages before answering the user."""
    if early_exit := _ensure_project_loaded():
        return early_exit
    sheet_names = [s["name"] for s in _sheets]
    canonical = next((s for s in sheet_names if s.lower() == sheet_name.lower()), None)
    if canonical is None:
        return json.dumps({
            "error": "sheet_not_found",
            "message": f"Sheet '{sheet_name}' not found.",
            "available_sheets": sheet_names,
        })
    adapter = _VariantAdapter(_active_variant)
    return build_sheet_context(_netlist, canonical, adapter, offset)


# ---------- get_component ----------

@mcp.tool(title="Get Component", annotations=ToolAnnotations(readOnlyHint=True))
def get_component(query: str) -> str:
    """Get full detail for a component: MPN, value, description, sheet, all pins with
    names and nets, and DNP status for the active variant. Use for targeted lookups of
    a specific IC, resistor, connector, or any component by refdes (U1, R4, J3), MPN,
    or description keyword. Tries exact refdes match first, then case-insensitive search
    across refdes, MPN, and description."""
    if early_exit := _ensure_project_loaded():
        return early_exit
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
    """Look up a net by name or pattern. Use when asked about a specific signal, bus, or
    rail — e.g. UART_TX, SPI_CS, VDD, GND. Supports regex (e.g. UART.*, .*_TX, 3V3|5V0).
    Tries exact match first, then regex, then substring fuzzy. For normal nets: returns
    all pins with refdes, pin number, pin name, and sheet. For high-fanout nets (power/
    ground rails with >=25 pins): returns a summary with a pins_sample (up to 10 pins)
    and a directive to filter by component or sheet. For multiple matches: returns
    fuzzy_matches list."""
    if early_exit := _ensure_project_loaded():
        return early_exit
    nets = _netlist.get("nets", {})

    # Exact match (case-insensitive)
    net_key = next((k for k in nets if k.lower() == query.lower()), None)

    # Regex match (if no exact match)
    if net_key is None:
        try:
            pattern = re.compile(query, re.IGNORECASE)
            regex_matches = [name for name in nets if pattern.search(name)]
            if len(regex_matches) == 1:
                net_key = regex_matches[0]
            elif len(regex_matches) > 1:
                return json.dumps({"fuzzy_matches": sorted(regex_matches)}, indent=2)
            # 0 matches → fall through to fuzzy
        except re.error:
            pass  # invalid regex (e.g. +5V) → fall through to fuzzy

    if net_key:
        connections = nets[net_key]
        components = _netlist.get("components", {})

        if len(connections) >= _HIGH_FANOUT_THRESHOLD:
            pins_sample = [
                {
                    "refdes": refdes,
                    "pin": pin,
                    "sheet": components.get(refdes, {}).get("sheet"),
                }
                for refdes, pin in connections[:10]
            ]
            return json.dumps({
                "net": net_key,
                "pin_count": len(connections),
                "high_fanout": True,
                "pins_sample": pins_sample,
                "message": (
                    f"{net_key} has {len(connections)} connections — "
                    "this is likely a power or ground plane.\n"
                    "Ask me about a specific component "
                    f'(e.g. "what {net_key} pins does U5 have?") '
                    "or a specific sheet "
                    f'(e.g. "what\'s connected to {net_key} on the Power sheet?").'
                ),
            }, indent=2)

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
    try:
        mcp.run()
    finally:
        # When the MCP stdio transport closes, Python's exit sequence tries to flush
        # sys.stdout which is now a closed pipe → ValueError. Redirect to devnull so
        # the process exits cleanly and Claude Desktop doesn't see a crashed server.
        try:
            sys.stdout = open(os.devnull, "w")  # noqa: WPS515
            sys.stderr = open(os.devnull, "w")  # noqa: WPS515
        except Exception:
            pass
