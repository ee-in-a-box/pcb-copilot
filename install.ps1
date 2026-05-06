[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$repo       = "ee-in-a-box/pcb-copilot"
$apiUrl     = "https://api.github.com/repos/$repo/releases/latest"
$installDir = "$env:LOCALAPPDATA\pcb-copilot"
$exeDest    = "$installDir\pcb-copilot.exe"
$statePath  = "$env:USERPROFILE\.ee-in-a-box\pcb-copilot-state.json"

function Write-Ok   { param($m) Write-Host "[OK] "    -ForegroundColor Green  -NoNewline; Write-Host $m }
function Write-Warn { param($m) Write-Host "[WARN] "  -ForegroundColor Yellow -NoNewline; Write-Host $m }
function Write-Err  { param($m) Write-Host "[ERROR] " -ForegroundColor Red    -NoNewline; Write-Host $m }

$client = New-Object System.Net.WebClient

# --- Close Claude Desktop ---
$claudeProcs = Get-Process -Name "claude" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "*WindowsApps*Claude*" }
$claudePkg = Get-AppxPackage -Name "Claude" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($claudeProcs) {
    Write-Warn "Claude Desktop will be closed and reopened automatically after install."
    Write-Host "Press Enter to continue, or Ctrl+C to cancel."
    Read-Host | Out-Null
    $claudeProcs | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 1000
}

# --- Kill running pcb-copilot processes ---
Get-Process -Name "pcb-copilot" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

New-Item -ItemType Directory -Force -Path $installDir | Out-Null

# --- Fetch latest release info ---
Write-Ok "Checking latest release..."
try {
    $release = Invoke-RestMethod -Uri $apiUrl -Headers @{ "User-Agent" = "pcb-copilot-installer" }
    $version = $release.tag_name -replace '^v', ''
    $exeUrl  = "https://github.com/$repo/releases/latest/download/pcb-copilot.exe"
} catch {
    Write-Err "Failed to fetch release info: $_"
    exit 1
}

Write-Ok "Latest version: $version"

# --- Download exe ---
$action = if (Test-Path $exeDest) { "Updating" } else { "Downloading" }
Write-Ok "${action} pcb-copilot.exe..."
try {
    $client.DownloadFile($exeUrl, $exeDest)
} catch {
    Write-Err "Download failed: $_"
    exit 1
}
if (!(Test-Path $exeDest)) {
    Write-Host ""
    Write-Warn "WARNING: Download succeeded but pcb-copilot.exe is missing."
    Write-Host "Your antivirus likely quarantined it. To fix:"
    Write-Host "  1. Open Windows Security > Virus & threat protection > Protection history"
    Write-Host "  2. Restore the file, or add $installDir to Defender exclusions."
    Write-Host ""
    exit 1
}

Write-Ok "Installed to: $exeDest"

# --- Add to PATH ---
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notlike "*pcb-copilot*") {
    [Environment]::SetEnvironmentVariable("PATH", "$userPath;$installDir", "User")
    $env:PATH = "$env:PATH;$installDir"
    Write-Ok "Added $installDir to PATH."
}

# --- Register with Claude Desktop ---
$msixPkg = Get-ChildItem "$env:LOCALAPPDATA\Packages\Claude_*" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
$msixConfig    = if ($msixPkg) { "$($msixPkg.FullName)\LocalCache\Roaming\Claude\claude_desktop_config.json" } else { $null }
$roamingConfig = "$env:APPDATA\Claude\claude_desktop_config.json"

$configPath = if ($msixConfig -and (Test-Path $msixConfig)) { $msixConfig }
              elseif (Test-Path $roamingConfig)              { $roamingConfig }
              else                                           { $null }

if ($configPath) {
    $config = if (Test-Path $configPath) {
        Get-Content $configPath -Raw | ConvertFrom-Json
    } else {
        [PSCustomObject]@{ mcpServers = [PSCustomObject]@{} }
    }
    if (-not $config.mcpServers) {
        $config | Add-Member -MemberType NoteProperty -Name mcpServers -Value ([PSCustomObject]@{})
    }
    $entry = [PSCustomObject]@{ command = $exeDest; args = @() }
    $config.mcpServers | Add-Member -MemberType NoteProperty -Name "pcb-copilot" -Value $entry -Force
    New-Item -ItemType Directory -Force -Path (Split-Path $configPath) | Out-Null
    $json = $config | ConvertTo-Json -Depth 10 -Compress
    [System.IO.File]::WriteAllText($configPath, $json, (New-Object System.Text.UTF8Encoding $false))
    Write-Ok "Registered with Claude Desktop. Restart Claude Desktop to apply."
} else {
    Write-Host ""
    Write-Warn "Claude Desktop not found."
    Write-Host "Install it from https://claude.ai/download then re-run this script to register."
    Write-Host ""
}

# --- Register with Claude Code ---
# Resolve claude.exe via PATH first, then fall back to known install locations.
# The official installer (https://claude.ai/install.ps1) puts it under .local\bin,
# which is not on PATH in a fresh elevated PowerShell session.
$claudeExe = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $claudeExe) {
    $candidates = @(
        "$env:USERPROFILE\.local\bin\claude.exe",
        "$env:APPDATA\npm\claude.cmd"
    )
    $claudeExe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($claudeExe) {
    Write-Ok "Registering with Claude Code..."
    # Remove first so re-runs idempotently update the absolute path.
    & $claudeExe mcp remove --scope user pcb-copilot 2>$null
    & $claudeExe mcp add    --scope user pcb-copilot -- $exeDest
    Write-Ok "Done. pcb-copilot is ready in Claude Code."
} else {
    Write-Warn "Claude Code not found — skipping MCP registration."
}

# --- Write state file ---
New-Item -ItemType Directory -Force -Path (Split-Path $statePath) | Out-Null
$state = if (Test-Path $statePath) {
    Get-Content $statePath -Raw | ConvertFrom-Json
} else {
    [PSCustomObject]@{}
}
$state | Add-Member -MemberType NoteProperty -Name installed_version -Value $version -Force
$state | ConvertTo-Json -Depth 5 | Set-Content $statePath -Encoding UTF8

Write-Host ""
Write-Ok "pcb-copilot $version installed successfully."

# --- Reopen Claude Desktop ---
if ($claudeProcs -and $claudePkg) {
    Write-Ok "Reopening Claude Desktop..."
    $manifest = Get-AppxPackageManifest $claudePkg
    $appId = $manifest.Package.Applications.Application.Id
    Start-Process "shell:AppsFolder\$($claudePkg.PackageFamilyName)!$appId"
}
