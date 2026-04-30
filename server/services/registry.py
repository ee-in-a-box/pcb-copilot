# server/services/registry.py
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path.home() / ".ee-in-a-box" / "pcb-copilot-registry.json"


def read_registry() -> dict:
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"projects": []}
    except Exception as e:
        logger.error(f"pcb-copilot: failed to read registry: {e}")
        return {"projects": []}


def write_registry(registry: dict) -> None:
    try:
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY_PATH.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"pcb-copilot: failed to write registry: {e}")


def upsert_registry_entry(path: str) -> None:
    registry = read_registry()
    now = datetime.now(timezone.utc).isoformat()
    projects = registry.get("projects", [])
    for entry in projects:
        if entry["path"] == path:
            entry["last_used"] = now
            break
    else:
        projects.append({"path": path, "last_used": now})
    registry["projects"] = projects
    write_registry(registry)
