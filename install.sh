#!/usr/bin/env bash
set -euo pipefail

REPO="ee-in-a-box/pcb-copilot"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"
INSTALL_DIR="${HOME}/.local/bin"
STATE_PATH="${HOME}/.ee-in-a-box/pcb-copilot-state.json"

ok()   { echo "[OK]    $*"; }
warn() { echo "[WARN]  $*"; }
err()  { echo "[ERROR] $*" >&2; exit 1; }

if ! command -v python3 &>/dev/null; then
    warn "python3 not found. Opening installer — click Install, then re-run this script."
    xcode-select --install 2>/dev/null || true
    exit 1
fi

# Detect architecture
ARCH=$(uname -m)
case "${ARCH}" in
  arm64)  BINARY_SUFFIX="darwin-arm64" ;;
  x86_64) BINARY_SUFFIX="darwin-x64"  ;;
  *)      err "Unsupported architecture: ${ARCH}" ;;
esac

BINARY_NAME="pcb-copilot-${BINARY_SUFFIX}"
BINARY_DEST="${INSTALL_DIR}/pcb-copilot"

# --- Close Claude Desktop if running ---
CLAUDE_WAS_RUNNING=false
if pgrep -x "Claude" &>/dev/null; then
    CLAUDE_WAS_RUNNING=true
    warn "Claude Desktop will be closed and reopened automatically after install."
    echo "Press Enter to continue, or Ctrl+C to cancel."
    read -r
    osascript -e 'quit app "Claude"' 2>/dev/null || pkill -x "Claude" || true
    sleep 1
    if pgrep -x "Claude" &>/dev/null; then
        warn "Claude Desktop is still running — you may need to close it manually before the new version takes effect."
    fi
fi

# Kill any running pcb-copilot processes
pkill -x "pcb-copilot" 2>/dev/null || true
sleep 0.5

# Fetch latest release info
ok "Checking latest release..."
RELEASE_JSON=$(curl -fsSL -H "User-Agent: pcb-copilot-installer" "${API_URL}")
VERSION=$(echo "${RELEASE_JSON}" | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin)['tag_name'].lstrip('v'))
except Exception as e:
    print(f'Failed to parse release info: {e}', file=sys.stderr)
    sys.exit(1)
") || err "Failed to parse release info from GitHub API"
DOWNLOAD_URL="https://github.com/${REPO}/releases/latest/download/${BINARY_NAME}"

ok "Latest version: ${VERSION}"

# Install directory
mkdir -p "${INSTALL_DIR}"

# Download binary
ACTION="Downloading"
[ -f "${BINARY_DEST}" ] && ACTION="Updating"
ok "${ACTION} ${BINARY_NAME}..."
curl -fsSL -o "${BINARY_DEST}" "${DOWNLOAD_URL}"
chmod +x "${BINARY_DEST}"
ok "Installed to: ${BINARY_DEST}"

# Add to PATH in shell profiles
for PROFILE in "${HOME}/.zshrc" "${HOME}/.bash_profile" "${HOME}/.bashrc"; do
    EXPORT_LINE="export PATH=\"${INSTALL_DIR}:\$PATH\""
    if [ -f "${PROFILE}" ] && grep -q "${INSTALL_DIR}" "${PROFILE}"; then
        true  # already present
    else
        echo "" >> "${PROFILE}"
        echo "# pcb-copilot" >> "${PROFILE}"
        echo "${EXPORT_LINE}" >> "${PROFILE}"
        ok "Added pcb-copilot to PATH in ${PROFILE}."
    fi
done
export PATH="${INSTALL_DIR}:${PATH}"

# Register with Claude Desktop
CLAUDE_CONFIG="${HOME}/Library/Application Support/Claude/claude_desktop_config.json"
if [ -f "${CLAUDE_CONFIG}" ]; then
    python3 - <<EOF
import sys, json, shutil
config_path = """${CLAUDE_CONFIG}"""
binary = """${BINARY_DEST}"""
with open(config_path) as f:
    config = json.load(f)
shutil.copy2(config_path, config_path + ".bak")
config.setdefault("mcpServers", {})["pcb-copilot"] = {"command": binary, "args": []}
with open(config_path, "w") as f:
    json.dump(config, f, indent=2)
EOF
    ok "Registered with Claude Desktop."
else
    warn "Claude Desktop config not found — skipping Claude Desktop registration."
    echo "Install Claude Desktop from https://claude.ai/download then re-run."
fi

# Register with Claude Code
if command -v claude &>/dev/null; then
    ok "Registering with Claude Code..."
    claude mcp add --scope user pcb-copilot -- pcb-copilot
    ok "Done. pcb-copilot is ready in Claude Code."
else
    warn "Claude Code not found — skipping MCP registration."
fi

# Write state file
mkdir -p "$(dirname "${STATE_PATH}")"
STATE_PATH="${STATE_PATH}" VERSION="${VERSION}" python3 - <<'EOF'
import json, os
path = os.environ['STATE_PATH']
state = json.load(open(path)) if os.path.exists(path) else {}
state['installed_version'] = os.environ['VERSION']
json.dump(state, open(path, 'w'), indent=2)
EOF

echo ""
ok "pcb-copilot ${VERSION} installed successfully."

# --- Reopen Claude Desktop if it was running ---
if [ "${CLAUDE_WAS_RUNNING}" = true ]; then
    ok "Reopening Claude Desktop..."
    open -a "Claude"
else
    echo "Restart your terminal or run: source ~/.zshrc"
fi
