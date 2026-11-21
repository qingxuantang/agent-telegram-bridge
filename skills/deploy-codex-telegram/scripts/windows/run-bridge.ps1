# SPDX-License-Identifier: MIT
# run-bridge.ps1 — launch the Codex Telegram bridge on Windows.
# NOTE: this invokes tg-codex-bridge.py — on Windows, use the Windows-patched
# copy (tg-codex-bridge-winpc.py): copy it over tg-codex-bridge.py in the
# scripts dir, or adjust the path below. TODO: verify on your host.
# Adjust the Python path below to your install if it differs.
# Sets CODEX_API_KEY from codex.env (0600, ACL-locked) and extends PATH with
# the npm global bin dir, then runs Python 3.13 + the bridge.
# Pass-through args (e.g. --dry-run) are forwarded to the bridge.
$codexEnv = "C:\Users\<user>\.codex\tg\codex.env"
$apiKey = $null
foreach ($line in Get-Content $codexEnv) {
    if ($line -match '^CODEX_API_KEY=(.*)$') { $apiKey = $Matches[1].Trim() }
}
if (-not $apiKey) { Write-Error "CODEX_API_KEY not found in $codexEnv"; exit 1 }
$env:CODEX_API_KEY = $apiKey
$env:PATH = "C:\Users\<user>\AppData\Roaming\npm;$env:PATH"
& C:\Python313\python.exe C:\Users\<user>\codex-tg\scripts\tg-codex-bridge.py --config C:\Users\<user>\.codex\tg\config.json @args
