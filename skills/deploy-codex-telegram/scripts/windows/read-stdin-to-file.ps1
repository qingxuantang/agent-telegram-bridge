# SPDX-License-Identifier: MIT
# read-stdin-to-file.ps1
# Reads all of stdin and writes it (trimmed + trailing newline, UTF-8 no BOM)
# to the given -Path. Used to install secrets without them appearing in a
# command line or a stored file. Usage:
#   <secret-line> | powershell -File read-stdin-to-file.ps1 -Path 'C:\path\file'
param([string]$Path)
$content = [Console]::In.ReadToEnd()
$dir = Split-Path $Path -Parent
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
[System.IO.File]::WriteAllText($Path, $content.Trim() + "`n", [System.Text.UTF8Encoding]::new($false))
Write-Output "wrote $Path"
