# server/services/registry.py
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path.home() / ".ee-in-a-box" / "pcb-copilot-registry.json"


def read_registry() -> dict:
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"projects": []}
    except Exception as e:
        logger.error(f"pcb-copilot: failed to read registry: {e}")
        return {"projects": []}

    projects = data.get("projects", [])
    live = [p for p in projects if Path(p["path"]).exists()]
    if len(live) != len(projects):
        data["projects"] = live
        write_registry(data)

    return data


def write_registry(registry: dict) -> None:
    try:
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY_PATH.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"pcb-copilot: failed to write registry: {e}")


def upsert_registry_entry(path: str, last_variant: str | None = None) -> None:
    registry = read_registry()
    now = datetime.now(timezone.utc).isoformat()
    projects = registry.get("projects", [])
    for entry in projects:
        if entry["path"] == path:
            entry["last_used"] = now
            if last_variant is not None:
                entry["last_variant"] = last_variant
            break
    else:
        entry = {"path": path, "last_used": now}
        if last_variant is not None:
            entry["last_variant"] = last_variant
        projects.append(entry)
    registry["projects"] = projects
    write_registry(registry)


def register_discovered(path: str) -> None:
    """Add path to registry with no last_used. No-op if already present."""
    registry = read_registry()
    projects = registry.get("projects", [])
    if any(e["path"] == path for e in projects):
        return
    projects.append({"path": path})
    registry["projects"] = projects
    write_registry(registry)


def get_last_variant(path: str) -> str | None:
    registry = read_registry()
    for entry in registry.get("projects", []):
        if entry["path"] == path:
            return entry.get("last_variant")
    return None
