#!/usr/bin/env bash
set -euo pipefail

REPO="ee-in-a-box/pcb-copilot"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"
INSTALL_DIR="${HOME}/Library/Application Support/pcb-copilot"
STATE_PATH="${HOME}/.ee-in-a-box/pcb-copilot-state.json"

ok()   { echo "[OK]    $*"; }
warn() { echo "[WARN]  $*"; }
err()  { echo "[ERROR] $*" >&2; exit 1; }

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
fi

# Kill any running pcb-copilot processes
pkill -x "pcb-copilot" 2>/dev/null || true
sleep 0.5

# Fetch latest release info
ok "Checking latest release..."
RELEASE_JSON=$(curl -fsSL -H "User-Agent: pcb-copilot-installer" "${API_URL}")
VERSION=$(echo "${RELEASE_JSON}" | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'].lstrip('v'))")
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
for PROFILE in "${HOME}/.zshrc" "${HOME}/.bashrc"; do
    EXPORT_LINE="export PATH=\"${INSTALL_DIR}:\$PATH\""
    if [ -f "${PROFILE}" ] && grep -q "pcb-copilot" "${PROFILE}"; then
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
    python3 - "${CLAUDE_CONFIG}" "${BINARY_DEST}" <<'EOF'
import sys, json
config_path, binary = sys.argv[1], sys.argv[2]
with open(config_path) as f:
    config = json.load(f)
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
python3 -c "
import json, os
path = '${STATE_PATH}'
state = json.load(open(path)) if os.path.exists(path) else {}
state['installed_version'] = '${VERSION}'
json.dump(state, open(path, 'w'), indent=2)
"

echo ""
ok "pcb-copilot ${VERSION} installed successfully."

# --- Reopen Claude Desktop if it was running ---
if [ "${CLAUDE_WAS_RUNNING}" = true ]; then
    ok "Reopening Claude Desktop..."
    open -a "Claude"
else
    echo "Restart your terminal or run: source ~/.zshrc"
fi
