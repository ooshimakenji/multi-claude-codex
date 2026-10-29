param(
  [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '..\mod\codex-delegar' | Resolve-Path
$destination = 'C:\Users\vinicius.oshima\.claude\dev-mods\81657d10-4a53-47ac-954b-d80e0b70d066\codex-delegar'

if (-not (Test-Path -LiteralPath $destination)) {
  throw "Destino não existe: $destination"
}

$files = Get-ChildItem -LiteralPath $source -Recurse -File |
  Where-Object { $_.FullName -notmatch '\\node_modules\\' }
$changed = 0

foreach ($file in $files) {
  $relative = $file.FullName.Substring($source.Path.Length + 1)
  $target = Join-Path $destination $relative
  $same = $false
  if (Test-Path -LiteralPath $target) {
    $same = (Get-FileHash -LiteralPath $file.FullName).Hash -eq
      (Get-FileHash -LiteralPath $target).Hash
  }
  if ($same) { continue }

  $changed++
  if ($WhatIf) {
    Write-Output "WHATIF copiaria: $relative"
    continue
  }

  $parent = Split-Path -Parent $target
  if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -LiteralPath $parent -Force | Out-Null
  }
  Copy-Item -LiteralPath $file.FullName -Destination $target -Force
  Write-Output "copiado: $relative"
}

if ($changed -eq 0) {
  Write-Output 'nenhuma mudança'
} else {
  Write-Output ("total de arquivos alterados: {0}" -f $changed)
}
