#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install the OpcuaMCP server and OpcuaSimulator as Windows services via NSSM.

.DESCRIPTION
    Installs two services:
      OpcuaMCP        — OPC-UA MCP server     (port 8002)
      OpcuaSimulator  — OPC-UA simulator      (port 4841)

    NSSM must be on PATH (https://nssm.cc/download). Run as Administrator.

.EXAMPLE
    # Edit the Configuration block below, then:
    .\install_services.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Configuration ──────────────────────────────────────────────────────────────

$Root            = $PSScriptRoot
$OpcuaPort       = "4841"
$PublishInterval = "2"

# ── End Configuration ──────────────────────────────────────────────────────────

if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Write-Error "nssm not found on PATH. Download from https://nssm.cc/download and add to PATH."
}

$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$exe = Join-Path $Root ".venv\Scripts\python.exe"

$Services = @(
    @{
        Name   = "OpcuaMCP"
        Args   = "server.py"
        Env    = "FASTMCP_PORT=8002"
        LogOut = Join-Path $LogDir "opcua-mcp-stdout.log"
        LogErr = Join-Path $LogDir "opcua-mcp-stderr.log"
        Desc   = "OPC-UA MCP Server — brownfield OPC-UA data access (port 8002)"
    },
    @{
        Name   = "OpcuaSimulator"
        Args   = "simulator.py"
        Env    = "OPCUA_PORT=$OpcuaPort`nPUBLISH_INTERVAL=$PublishInterval"
        LogOut = Join-Path $LogDir "opcua-simulator-stdout.log"
        LogErr = Join-Path $LogDir "opcua-simulator-stderr.log"
        Desc   = "OPC-UA Simulator — synthetic WTP data via asyncua (port $OpcuaPort)"
    }
)

foreach ($svc in $Services) {
    $name = $svc.Name
    Write-Host "`nInstalling $name ..." -ForegroundColor Cyan

    $existing = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($existing) {
        if ($existing.Status -eq "Running") { nssm stop $name confirm }
        nssm remove $name confirm
    }

    nssm install $name $exe $svc.Args
    nssm set $name AppDirectory $Root
    nssm set $name Description $svc.Desc
    nssm set $name AppEnvironmentExtra $svc.Env
    nssm set $name AppStdout $svc.LogOut
    nssm set $name AppStderr $svc.LogErr
    nssm set $name AppStdoutCreationDisposition 4
    nssm set $name AppStderrCreationDisposition 4
    nssm set $name AppRotateFiles 1
    nssm set $name AppRotateBytes 10485760
    nssm set $name AppExit Default Restart
    nssm set $name AppRestartDelay 60000
    nssm set $name Start SERVICE_AUTO_START

    Write-Host "  $name installed OK" -ForegroundColor Green
}

Write-Host "`nStarting services ..." -ForegroundColor Cyan
# Start simulator first so data is ready when MCP server fields its first query
foreach ($name in @("OpcuaSimulator", "OpcuaMCP")) {
    nssm start $name
    Start-Sleep -Milliseconds 500
    $status = (Get-Service -Name $name).Status
    Write-Host "  $name — $status"
}

Write-Host "`nLogs: $LogDir"
