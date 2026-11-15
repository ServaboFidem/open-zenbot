# Install host binaries that mcp.json servers declare under `requires`.
# Flags: --server NAME (one server only), --dry-run (show commands, run nothing).
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
}
& uv run python -m src.harness.host_deps install @args
exit $LASTEXITCODE
