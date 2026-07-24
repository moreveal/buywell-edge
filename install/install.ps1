param(
  [string]$PairCode = "",
  [string]$Version = "latest",
  [string]$Repository = "moreveal/buywell-edge"
)
$ErrorActionPreference = "Stop"
$installRoot = Join-Path $env:ProgramFiles "Buywell Edge"
$stateRoot = Join-Path $env:ProgramData "Buywell\Edge"
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("buywell-edge-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $temporary, $installRoot, $stateRoot | Out-Null
try {
  $archive = Join-Path $temporary "edge.zip"
  $checksum = Join-Path $temporary "edge.zip.sha256"
  $url = "https://github.com/$Repository/releases/$Version/download/buywell-edge-windows-x86_64.zip"
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
  Invoke-WebRequest -UseBasicParsing -Uri "$url.sha256" -OutFile $checksum
  $expected = (Get-Content -Raw $checksum).Split(" ")[0].Trim().ToLowerInvariant()
  $actual = (Get-FileHash -Algorithm SHA256 $archive).Hash.ToLowerInvariant()
  if ($expected -ne $actual) { throw "Buywell Edge checksum verification failed" }
  $release = $actual.Substring(0, 16)
  $target = Join-Path $installRoot "releases\$release"
  Expand-Archive -LiteralPath $archive -DestinationPath $target
  $current = Join-Path $installRoot "current"
  if (Test-Path -LiteralPath $current) { Remove-Item -Force -LiteralPath $current }
  New-Item -ItemType Junction -Path $current -Target $target | Out-Null
  $executable = Join-Path $current "buywell-edge.exe"
  $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
  if (($machinePath -split ";") -notcontains $current) {
    [Environment]::SetEnvironmentVariable("Path", ($machinePath.TrimEnd(";") + ";" + $current), "Machine")
  }
  $existing = Get-Service -Name BuywellEdge -ErrorAction SilentlyContinue
  if ($existing) { & sc.exe stop BuywellEdge | Out-Null; & sc.exe delete BuywellEdge | Out-Null }
  & sc.exe create BuywellEdge binPath= "`"$executable`" run" start= auto | Out-Null
  & sc.exe failure BuywellEdge reset= 86400 actions= restart/5000/restart/30000 | Out-Null
  & sc.exe start BuywellEdge | Out-Null
  if ($PairCode) { & $executable connect $PairCode }
  Write-Host "Buywell Edge is installed. Run: `"$executable`" status"
}
finally {
  if (Test-Path -LiteralPath $temporary) { Remove-Item -Recurse -Force -LiteralPath $temporary }
}
