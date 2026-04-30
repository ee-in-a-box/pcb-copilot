# Contributing to pcb-copilot

## How contributions work

`main` is protected — no one can push to it directly, including maintainers.
All changes go through a pull request and require:
- Passing CI (lint + tests)
- An approving review from a maintainer

**To contribute:**
1. Fork the repo
2. Create a branch (`git checkout -b my-feature`)
3. Make your changes and ensure `pytest` passes locally
4. Open a PR against `main`

Only maintainers can tag a release (`git tag v*`), which triggers the build and publish workflow. Contributors cannot ship a release.

---

## DB Schema Changes

`server/main.py` defines two constants that must stay in sync with `SCHEMA_VERSION` in `altium-copilot/server/export.py`:

```python
MIN_SUPPORTED_SCHEMA_VERSION = 1  # oldest DB this build can open
SUPPORTED_SCHEMA_VERSION = 1      # newest DB this build supports
```

Any PR that requires a DB schema change **must** also:

1. Bump `SUPPORTED_SCHEMA_VERSION` in `server/main.py`
2. Bump `SCHEMA_VERSION` in `altium-copilot/server/export.py` (separate repo)
3. Bump `MIN_SUPPORTED_SCHEMA_VERSION` only if the change is breaking (old DBs can no longer be read)

Failing to bump causes pcb-copilot to silently open a DB with an incompatible schema, or crash on a missing column instead of giving a clear error.

---

## Prerequisites

- Python 3.13+

```bash
# Mac/Linux
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# Windows
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## Running locally

```bash
# Mac/Linux
.venv/bin/python server/main.py

# Windows
.venv\Scripts\python server/main.py
```

Or register with Claude Code directly:

```json
{
  "mcpServers": {
    "pcb-copilot": {
      "command": "/path/to/pcb-copilot/.venv/bin/python",
      "args": ["/path/to/pcb-copilot/server/main.py"]
    }
  }
}
```

## Running tests

```bash
# Mac/Linux
.venv/bin/pytest

# Windows
.venv\Scripts\pytest
```

---

## Releasing a new version

Releases are fully automated via GitHub Actions (`.github/workflows/release.yml`).
Push a version tag and the workflow builds all three binaries and publishes a GitHub Release — no manual build steps needed.

```bash
git tag v0.2.0
git push origin v0.2.0
```

The workflow:
1. Runs lint and tests — a failure blocks the release
2. Reads the version from the tag (`v0.2.0` → `0.2.0`)
3. Patches `manifest.json` with that version
4. Builds `pcb-copilot.exe` on Windows, `pcb-copilot-darwin-arm64` on macOS ARM64, and `pcb-copilot-darwin-x64` on macOS x64
5. Creates a GitHub Release with all three binaries as downloadable assets

### Building locally (for testing before a release)

**Windows:**
```powershell
.venv\Scripts\pip install pyinstaller

.venv\Scripts\pyinstaller `
    --onefile `
    --name pcb-copilot `
    --distpath dist `
    --workpath build `
    --add-data "manifest.json;." `
    --paths server `
    server/main.py
```

**Mac:**
```bash
.venv/bin/pip install pyinstaller

.venv/bin/pyinstaller \
    --onefile \
    --name pcb-copilot-darwin-arm64 \
    --distpath dist \
    --workpath build \
    --add-data "manifest.json:." \
    --paths server \
    server/main.py
```

Test the resulting binary on a machine **without Python installed** before tagging.

---

## Project structure

```
server/
  main.py              — MCP tool definitions (FastMCP) and module state
  db.py                — SQLite hydration: reads .db into in-memory netlist
  services/
    page_netlist.py    — sheet context builder and high-fanout net logic
    registry.py        — remembers which .db file was last opened

tests/                 — pytest suite
```

## What gets shipped

The GitHub Release contains only the compiled binaries (`pcb-copilot.exe`, `pcb-copilot-darwin-arm64`, `pcb-copilot-darwin-x64`).
Python source, tests, docs, and `.venv` are never shipped to end users.
