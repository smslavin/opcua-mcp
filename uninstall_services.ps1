#Requires -RunAsAdministrator
foreach ($name in @("AVEVA Demo OpcuaMCP", "AVEVA Demo OpcuaSimulator")) {
    $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
    if (-not $svc) { Write-Host "$name not installed, skipping"; continue }
    if ($svc.Status -eq "Running") { nssm stop $name confirm }
    nssm remove $name confirm
    Write-Host "$name removed."
}
