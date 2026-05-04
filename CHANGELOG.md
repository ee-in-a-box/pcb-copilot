# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

## [0.1.1] - 2026-05-03

### Changed
- `detect_project` and `open_project` consolidated into a single `load_project` tool — no args at session start, auto-loads one remembered project, presents a list when multiple are remembered, or accepts an explicit `db_path` to load or switch projects
- `load_project` now returns a structured `project` dict on success (consistent with the old `detect_project` shape) rather than a summary string
- `install.sh` now uses `~/.local/bin` and checks for `python3` upfront
- Update notice now includes a platform-specific `update_command` field

### Fixed
- Registry test paths updated to use real temp files so dead-path pruning works correctly in tests

## [0.1.0] - 2026-05-03

### Added
- `detect_project` — checks registry for a remembered `.db` file; auto-loads if found
- `open_project` — loads a `.db` snapshot, validates schema version, saves path to registry
- `list_variants` / `set_active_variant` — switch between build variants; DNP components are marked throughout
- `list_sheets` — returns all sheet names in the project
- `get_sheet_context` — returns all components on a sheet with pin-to-net connections and one-hop cross-sheet neighbors
- `get_component` — looks up a component by refdes, MPN, or description keyword; returns pins, nets, and DNP status
- `get_net` — traces a net by name or keyword; high-fanout nets (power/ground rails >25 pins) return a summary instead of flooding context
- Schema version enforcement: raises `SchemaTooNewError` on DBs from a newer altium-copilot, `SchemaTooOldError` on DBs below `MIN_SUPPORTED_SCHEMA_VERSION` — clear error instead of a confusing column crash
- Auto-update: background thread polls GitHub releases every 24 h and surfaces an `update_available` notice when a new version is out
- One-line installer via PowerShell (`irm | iex`) and shell (`curl | bash`) — no Python required
- PyInstaller builds: ships as standalone binaries for Windows, macOS ARM64, and macOS x64 — no Python required on end-user machines
- README demo GIFs: intro walkthrough and usage examples
