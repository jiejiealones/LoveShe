param([int]$Port = 8766)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
python -m loveshe.demo --port $Port
