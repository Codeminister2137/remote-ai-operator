<# Remote AI Operator - Mode B self-host installer (Windows, no admin needed
   except for the optional private-network step).

   What it does:
     1. Checks Python (>= 3.12).
     2. Offers to install uv and ffmpeg when missing, then creates or
        reuses .venv and installs the operator (prefers uv, falls back
        to plain Python).
     3. Creates .env with a random owner password (OPERATOR_ACCESS_TOKEN)
        when missing. An existing password is kept and NEVER printed.
     4. Asks which code folder the agent may develop (project tasks).
        Enter keeps the sample task only; re-run any time to change it.
     5. Offers to install Tailscale (private-network app for phone access).
        You can say no; the PC program still works on this PC.
     6. Registers the serve to start automatically at sign-in (a per-user
        Scheduled Task - no admin needed), starts it now and checks that
        it answers. The summary always shows how to start it by hand.
     7. Prints the phone web address and what is left to do by hand.

   Run from the project folder in PowerShell:
     powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1

   Useful switches:
     -NoTailscale     Skip the private-network step silently this run.
     -NonInteractive  No questions; safe defaults (skips install/sign-in offers).
     -Uninstall       Remove the automatic start entry again (keeps your files).
     -Port 8765       Loopback port the helper program listens on.
     -WorkRoot a,b    Code folder(s) the agent may develop (project tasks).
                      Without it the installer asks once; Enter keeps sample-only.

   Re-running is safe: every step checks the current state first.
#>
[CmdletBinding()]
param(
  [int]$Port = 8765,
  [string]$TaskName = 'RemoteAIOperatorServe',
  [string]$RepoRoot = '',
  [string[]]$WorkRoot = @(),
  [switch]$NonInteractive,
  [switch]$NoTailscale,
  [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- helpers ---

function Write-Step($Text) {
  Write-Host ''
  Write-Host "==> $Text"
}

function Read-Choice($Prompt, [bool]$DefaultYes) {
  if ($NonInteractive) { return $DefaultYes }
  $hint = 'Y/n'
  if (-not $DefaultYes) { $hint = 'y/N' }
  $answer = (Read-Host "$Prompt [$hint]").Trim().ToLowerInvariant()
  if ($answer -eq '') { return $DefaultYes }
  return ($answer -eq 'y' -or $answer -eq 'yes')
}

function Get-RepoRoot {
  if ($RepoRoot -ne '') { return (Resolve-Path $RepoRoot).Path }
  return (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}

function Confirm-Checkout($Root) {
  $seed = Join-Path $Root 'sample\issue_import.py'
  $checks = Join-Path $Root 'tests\acceptance\run_checks.py'
  if (-not (Test-Path $seed) -or -not (Test-Path $checks)) {
    throw "This does not look like the remote-ai-operator project folder: $Root"
  }
}

# --------------------------------------------- python / install (.venv) ---

function Find-Python {
  foreach ($name in @('python', 'python3', 'py')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { continue }
    try {
      $out = (& $cmd.Source --version 2>&1 | Out-String).Trim()
    } catch { continue }
    if ($out -match 'Python\s+3\.(\d+)') {
      $minor = [int]$Matches[1]
      if ($minor -ge 12) { return $cmd.Source }
    }
  }
  return $null
}

function Test-VenvImport($VenvPython) {
  if (-not (Test-Path $VenvPython)) { return $false }
  try {
    & $VenvPython -c 'import remote_operator.cli' 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch { return $false }
}

function Ensure-Install($Root, $PythonExe) {
  $venvPython = Join-Path $Root '.venv\Scripts\python.exe'
  $uv = Get-Command uv -ErrorAction SilentlyContinue
  if ($null -ne $uv) {
    Write-Host "Installing with uv (uses the project lockfile)..."
    Push-Location $Root
    try {
      & $uv.Source sync --locked
      if ($LASTEXITCODE -ne 0) { throw 'uv sync failed.' }
    } finally { Pop-Location }
  } elseif (Test-VenvImport $venvPython) {
    Write-Host 'Program files are already installed; skipping reinstall.'
    return $venvPython
  } else {
    Write-Host 'uv not found; using plain Python (.venv + pip)...'
    if (-not (Test-Path $venvPython)) {
      & $PythonExe -m venv (Join-Path $Root '.venv')
      if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv.' }
    }
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
    & $venvPython -m pip install -e $Root
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }
  }
  if (-not (Test-VenvImport $venvPython)) {
    throw 'Install finished but the operator module still cannot be loaded.'
  }
  return $venvPython
}

# ------------------------------------------------------------- .env file ---

function Read-DotEnv($Path) {
  $values = @{}
  if (-not (Test-Path $Path)) { return $values }
  $lines = [System.IO.File]::ReadAllLines($Path)
  foreach ($raw in $lines) {
    $line = $raw.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { continue }
    $eq = $line.IndexOf('=')
    if ($eq -le 0) { continue }
    $key = $line.Substring(0, $eq).Trim()
    $val = $line.Substring($eq + 1).Trim().TrimEnd("`r", "`n")
    if ($val.Length -ge 2 -and (($val.StartsWith('"') -and $val.EndsWith('"')) -or
        ($val.StartsWith("'") -and $val.EndsWith("'")))) {
      $val = $val.Substring(1, $val.Length - 2)
    }
    if ($key -match '^[A-Za-z_][A-Za-z0-9_]*$') { $values[$key] = $val }
  }
  return $values
}

function Set-DotEnvValue($Path, $Key, $Value) {
  $lines = @()
  if (Test-Path $Path) { $lines = [System.IO.File]::ReadAllLines($Path) }
  # Strip a possible byte-order mark carried by older editors.
  if ($lines.Count -gt 0 -and $lines[0].Length -gt 0 -and $lines[0][0] -eq [char]0xFEFF) {
    $lines[0] = $lines[0].Substring(1)
  }
  $done = $false
  for ($i = 0; $i -lt $lines.Count; $i++) {
    $trimmed = $lines[$i].TrimStart()
    if (-not $done -and ($trimmed -eq "$Key=" -or $trimmed.StartsWith("$Key=") -or
        $trimmed.StartsWith("$Key ="))) {
      $lines[$i] = "$Key=$Value"
      $done = $true
    }
  }
  if (-not $done) { $lines += "$Key=$Value" }
  $text = ($lines -join "`r`n") + "`r`n"
  [System.IO.File]::WriteAllText($Path, $text, [System.Text.UTF8Encoding]::new($false))
}

function New-AccessToken {
  $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'.ToCharArray()
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $chars = foreach ($b in $bytes) { $alphabet[$b % $alphabet.Length] }
  return (-join $chars)
}

# Returns a hash with IsNew. Token carries the password ONLY when just created.
function Ensure-AccessToken($EnvPath) {
  $values = Read-DotEnv $EnvPath
  if (-not $values.ContainsKey('OPERATOR_MODE')) {
    Set-DotEnvValue $EnvPath 'OPERATOR_MODE' 'scripted'
  }
  $current = ''
  if ($values.ContainsKey('OPERATOR_ACCESS_TOKEN')) { $current = $values['OPERATOR_ACCESS_TOKEN'] }
  if ($current.Length -ge 32) {
    return @{ IsNew = $false; Token = $null }
  }
  $fresh = New-AccessToken
  Set-DotEnvValue $EnvPath 'OPERATOR_ACCESS_TOKEN' $fresh
  return @{ IsNew = $true; Token = $fresh }
}

# --------------------------------- start wrapper + automatic start entry ---

function Write-StartWrapper($Root, $Port, $VenvPython) {
  $runtime = Join-Path $Root '.runtime'
  if (-not (Test-Path $runtime)) { [void](New-Item -ItemType Directory $runtime) }
  $wrapper = Join-Path $runtime 'start-serve.ps1'
  $template = @'
# Generated by scripts/install.ps1 - do not edit by hand, re-run the installer.
# Loads the project .env file (passwords stay in memory only) and starts the
# operator helper program. Nothing secret is ever printed here.
$ErrorActionPreference = 'Stop'
$RepoRoot = '__REPO__'
$Port = __PORT__
$EnvPath = Join-Path $RepoRoot '.env'
$LogFile = Join-Path $RepoRoot '.runtime\serve.log'
$VenvPython = '__VENVPYTHON__'

function Import-DotEnv($Path) {
  $lines = [System.IO.File]::ReadAllLines($Path)
  foreach ($raw in $lines) {
    $line = $raw.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { continue }
    $eq = $line.IndexOf('=')
    if ($eq -le 0) { continue }
    $key = $line.Substring(0, $eq).Trim()
    $val = $line.Substring($eq + 1).Trim()
    if ($val.Length -ge 2 -and (($val.StartsWith('"') -and $val.EndsWith('"')) -or
        ($val.StartsWith("'") -and $val.EndsWith("'")))) {
      $val = $val.Substring(1, $val.Length - 2)
    }
    if ($key -match '^[A-Za-z_][A-Za-z0-9_]*$') {
      [System.Environment]::SetEnvironmentVariable($key, $val, 'Process')
    }
  }
}

if (-not (Test-Path $EnvPath)) {
  throw "Missing .env file. Re-run scripts/install.ps1 to create it."
}
Import-DotEnv $EnvPath
$token = [System.Environment]::GetEnvironmentVariable('OPERATOR_ACCESS_TOKEN', 'Process')
if ([string]::IsNullOrEmpty($token) -or $token.Length -lt 32) {
  throw 'OPERATOR_ACCESS_TOKEN is missing or too short. Re-run scripts/install.ps1.'
}
$origin = [System.Environment]::GetEnvironmentVariable('OPERATOR_PUBLIC_ORIGIN', 'Process')
$serveArgs = @(
  '-m', 'remote_operator.cli',
  '--project-root', $RepoRoot,
  '--runtime', (Join-Path $RepoRoot '.runtime')
)
$workRoots = [System.Environment]::GetEnvironmentVariable('OPERATOR_WORK_ROOTS', 'Process')
if (-not [string]::IsNullOrWhiteSpace($workRoots)) {
  foreach ($dir in $workRoots.Split(';')) {
    $trimmed = $dir.Trim()
    if ($trimmed -ne '') { $serveArgs += @('--work-root', $trimmed) }
  }
}
$serveArgs += @('serve', '--port', "$Port")
if (-not [string]::IsNullOrWhiteSpace($origin)) { $serveArgs += @('--public-origin', $origin) }

'' + "`n=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') starting serve ===`n" | Out-File -Append -Encoding utf8 $LogFile
& $VenvPython @serveArgs *>> $LogFile
'@
  $content = $template.Replace('__REPO__', $Root).Replace('__PORT__', "$Port").Replace(
    '__VENVPYTHON__', $VenvPython)
  $old = ''
  if (Test-Path $wrapper) { $old = [System.IO.File]::ReadAllText($wrapper) }
  [System.IO.File]::WriteAllText($wrapper, $content, [System.Text.UTF8Encoding]::new($false))
  return (@{ Path = $wrapper; Changed = ($old -ne $content) })
}

function Register-ServeTask($TaskName, $Root, $WrapperPath) {
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$WrapperPath`"" `
    -WorkingDirectory $Root
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Start the Remote AI Operator helper at sign-in.' -Force | Out-Null
}

function Stop-OwnServe($Root) {
  # Best effort: stop only helper processes started for THIS project folder.
  try {
    $mine = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop |
      Where-Object { $null -ne $_.CommandLine -and
        $_.CommandLine.Contains('remote_operator.cli') -and $_.CommandLine.Contains($Root) }
    foreach ($p in $mine) {
      try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { }
    }
  } catch { }
}

function Test-ServePort($Port) {
  $client = New-Object Net.Sockets.TcpClient
  try {
    $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
    return $iar.AsyncWaitHandle.WaitOne(1000)
  } catch { return $false } finally { $client.Close() }
}

function Restart-ServeTask($TaskName, $Root, [bool]$ConfigChanged) {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($null -eq $task) { return 'missing' }
  if ($task.State -eq 'Running' -and $ConfigChanged) {
    Write-Host 'Restarting the helper so the new settings take effect...'
    try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop } catch { }
    Start-Sleep -Seconds 2
    Stop-OwnServe $Root
    Start-Sleep -Seconds 1
  }
  if ((Get-ScheduledTask -TaskName $TaskName).State -ne 'Running') {
    try {
      Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
      return 'started'
    } catch {
      return 'start-failed'
    }
  }
  return 'already-running'
}

# ------------------------------------------------------------- tailscale ---

function Find-Tailscale {
  $cmd = Get-Command tailscale.exe, tailscale -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($null -ne $cmd) { return $cmd.Source }
  $probe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
  if (Test-Path $probe) { return $probe }
  return $null
}

function Get-TailscaleSelf($TailscaleExe) {
  try {
    $json = (& $TailscaleExe status --json 2>$null | Out-String)
    $data = $json | ConvertFrom-Json -ErrorAction Stop
    if ($null -ne $data.Self -and $null -ne $data.Self.DNSName -and $data.Self.DNSName -ne '') {
      return ($data.Self.DNSName.TrimEnd('.'))
    }
  } catch { }
  return $null
}

function Test-TailscaleServing($TailscaleExe, $Port) {
  try {
    $status = (& $TailscaleExe serve status 2>$null | Out-String)
    return ($status.Contains("127.0.0.1:$Port") -or $status.Contains("localhost:$Port"))
  } catch { return $false }
}

function Find-UvCommand {
  $cmd = Get-Command uv -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -ne $cmd) { return $cmd.Source }
  # A fresh winget install may not be on this shell's PATH yet; refresh it
  # from the registry once before giving up.
  $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') +
    ';' + [System.Environment]::GetEnvironmentVariable('Path', 'User')
  $cmd = Get-Command uv -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -ne $cmd) { return $cmd.Source }
  return $null
}

function Install-Uv {
  Write-Host 'Installing uv (fast Python installer)...'
  $winget = Get-Command winget.exe, winget -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($null -eq $winget) { throw 'winget is not available.' }
  & $winget.Source install -e --id astral-sh.uv `
    --accept-source-agreements --accept-package-agreements
  if ($LASTEXITCODE -ne 0) { throw 'winget could not install uv.' }
  if ($null -eq (Find-UvCommand)) { throw 'uv installed but is not on PATH yet.' }
}

function Install-Ffmpeg {
  Write-Host 'Installing ffmpeg (audio converter for voice dictation)...'
  $winget = Get-Command winget.exe, winget -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($null -eq $winget) { throw 'winget is not available.' }
  & $winget.Source install -e --id Gyan.FFmpeg `
    --accept-source-agreements --accept-package-agreements
  if ($LASTEXITCODE -ne 0) { throw 'winget could not install ffmpeg.' }
  $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') +
    ';' + [System.Environment]::GetEnvironmentVariable('Path', 'User')
  if ($null -eq (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw 'ffmpeg installed but is not on PATH yet (sign out and back in).'
  }
}

function Install-Tailscale {
  Write-Host 'Installing Tailscale (Windows may show an administrator approval dialog)...'
  $winget = Get-Command winget.exe, winget -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($null -eq $winget) { throw 'winget is not available.' }
  & $winget.Source install -e --id Tailscale.Tailscale `
    --accept-source-agreements --accept-package-agreements
  if ($LASTEXITCODE -ne 0) { throw 'winget could not install Tailscale.' }
}

function Write-TailscaleSkipped($GuideRef) {
  Write-Host ''
  Write-Host 'Skipping the private-network step. The helper program will still'
  Write-Host 'work on THIS PC, but your phone cannot reach it yet.'
  Write-Host ''
  Write-Host 'What the service needs instead: a stable, private web address that'
  Write-Host 'starts with https://, that your phone can open, and that forwards'
  Write-Host 'to the helper program on this PC.'
  Write-Host 'Examples people use instead: ZeroTier, Cloudflare Tunnel,'
  Write-Host 'or a home VPN / router port-forwarding rule.'
  Write-Host "See $GuideRef, section 'If you skipped the private-network step'."
  Write-Host 'Finding and setting up an alternative is your responsibility;'
  Write-Host 'the installer cannot do it for you.'
}

# ------------------------------------------------------------------ main ---

function Invoke-Uninstall($Root, $TaskName) {
  Write-Step "Removing the automatic start entry '$TaskName'"
  try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
  Start-Sleep -Seconds 1
  Stop-OwnServe $Root
  try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host 'Removed the automatic start entry.'
  } catch {
    Write-Host 'No automatic start entry was found (nothing to remove).'
  }
  $wrapper = Join-Path $Root '.runtime\start-serve.ps1'
  if (Test-Path $wrapper) { Remove-Item $wrapper -Force }
  Write-Host ''
  Write-Host 'Kept on purpose: .venv (program files), .env (your password),'
  Write-Host '.runtime (your tasks and logs). Delete them by hand if you want'
  Write-Host 'a full removal - and keep a copy of your password first.'
  Write-Host 'If Tailscale serves only this helper, you can also run:'
  Write-Host '  tailscale serve reset'
}

function Invoke-Installer {
  $root = Get-RepoRoot
  Confirm-Checkout $root
  if ($Port -lt 1 -or $Port -gt 65535) { throw "Port $Port is out of range (1-65535)." }
  $guideRef = 'docs/self-host-access-guide.md'
  $manualSteps = @()

  if ($Uninstall) {
    Invoke-Uninstall $root $TaskName
    return
  }

  Write-Step "Remote AI Operator setup ($root)"

  # 1. Python ----------------------------------------------------------
  $pythonExe = Find-Python
  if ($null -eq $pythonExe) {
    throw ('Python 3.12 or newer was not found. Install it from ' +
      'https://www.python.org/downloads/ (tick "Add python.exe to PATH"), ' +
      'then re-run this installer.')
  }
  Write-Host "Using Python: $pythonExe"

  # uv: faster installs using the project lockfile; offer to install it.
  if ($null -eq (Find-UvCommand)) {
    $wantsUv = $false
    if (-not $NonInteractive) {
      Write-Host 'uv (fast Python installer) was not found.'
      $wantsUv = Read-Choice 'Install uv now (winget)?' $true
    }
    if ($wantsUv) {
      try {
        Install-Uv
        Write-Host 'uv installed.'
      } catch {
        Write-Host "Could not install uv automatically: $($_.Exception.Message)"
        Write-Host 'Continuing with plain Python instead.'
      }
    } else {
      Write-Host 'Skipping uv; plain Python will be used instead.'
    }
  }

  # ffmpeg: the server needs it to convert browser audio for Meta transcription.
  if ($null -eq (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    $wantsFfmpeg = $false
    if (-not $NonInteractive) {
      Write-Host 'ffmpeg (audio converter for voice dictation) was not found.'
      $wantsFfmpeg = Read-Choice 'Install ffmpeg now (winget)?' $true
    }
    if ($wantsFfmpeg) {
      try {
        Install-Ffmpeg
        Write-Host 'ffmpeg installed.'
      } catch {
        Write-Host "Could not install ffmpeg automatically: $($_.Exception.Message)"
        $manualSteps += 'Install ffmpeg (winget install -e --id Gyan.FFmpeg) or voice dictation falls back to browser speech.'
      }
    } else {
      Write-Host 'Skipping ffmpeg; voice dictation will use browser speech instead of Meta.'
      $manualSteps += 'Install ffmpeg (winget install -e --id Gyan.FFmpeg) to enable Meta voice dictation.'
    }
  }

  # 2. Program files ---------------------------------------------------
  Write-Step 'Installing the program files'
  $venvPython = Ensure-Install $root $pythonExe
  Write-Host 'Program files are ready.'

  # Docker: task checking needs Docker Desktop; offer to install it.
  # The helper revives Docker Desktop on its own when it is installed.
  if ($null -eq (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'Docker was not found. Task checking needs Docker Desktop.'
    $wantsDocker = $false
    if (-not $NonInteractive) {
      Write-Host 'Docker Desktop needs the WSL2 backend and usually a reboot'
      Write-Host 'before it works the first time.'
      $wantsDocker = Read-Choice 'Install Docker Desktop now (winget)?' $true
    }
    if ($wantsDocker) {
      try {
        $winget = Get-Command winget.exe, winget -ErrorAction SilentlyContinue |
          Select-Object -First 1
        if ($null -eq $winget) { throw 'winget is not available.' }
        Write-Host 'Installing Docker Desktop (may take several minutes)...'
        & $winget.Source install -e --id Docker.DockerDesktop --silent `
          --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw 'winget could not install Docker Desktop.' }
        Write-Host 'Docker Desktop installed. If task checks report Docker'
        Write-Host 'unreachable, reboot once and start Docker Desktop, then continue.'
      } catch {
        Write-Host "Could not install Docker automatically: $($_.Exception.Message)"
        $manualSteps += ('Install Docker Desktop from ' +
          'https://www.docker.com/products/docker-desktop/ (needs WSL2 + a reboot).')
      }
    } else {
      $manualSteps += ('Docker was not found. Task checking needs Docker Desktop; ' +
        "install it from https://www.docker.com/products/docker-desktop/ if the " +
        "helper later reports Docker unreachable (see $guideRef).")
    }
  }

  # 3. Password file ---------------------------------------------------
  Write-Step 'Checking the password file (.env)'
  $envPath = Join-Path $root '.env'
  $tokenResult = Ensure-AccessToken $envPath
  if ($tokenResult.IsNew) {
    Write-Host ''
    Write-Host '>>> Your new helper password (save it now, it is shown once):'
    Write-Host ">>> $($tokenResult.Token)"
    Write-Host '>>> You will type this password on your phone.'
    Write-Host ''
    if (-not $NonInteractive) {
      [void](Read-Host 'Press Enter to confirm you saved the password somewhere safe')
    }
  } else {
    # The existing password is never printed.
    Write-Host 'Kept your existing password (not shown).'
  }

  # 3b. Agent code folder(s) -------------------------------------------
  Write-Step 'Choosing the code folder for project tasks'
  $envValues = Read-DotEnv $envPath
  $existingRoots = @()
  if ($envValues.ContainsKey('OPERATOR_WORK_ROOTS')) {
    $existingRoots = @($envValues['OPERATOR_WORK_ROOTS'].Split(';') |
      ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
  }
  if ($WorkRoot.Count -gt 0) {
    $chosenRoots = @($WorkRoot)
  } elseif ($NonInteractive) {
    $chosenRoots = $existingRoots
  } else {
    if ($existingRoots.Count -gt 0) {
      Write-Host "Currently: $($existingRoots -join '; ')"
    } else {
      Write-Host 'Currently: sample task only (safe practice file).'
    }
    Write-Host 'The agent can read files and propose changes in this folder.'
    Write-Host 'Nothing is written until you approve the diff in the browser.'
    Write-Host 'A short folder name works too (like demo-repo); the full path is saved.'
    $answer = (Read-Host 'Code folder for the agent? [Enter keeps current, "none" clears]').Trim()
    if ($answer -eq '') { $chosenRoots = $existingRoots }
    elseif ($answer.ToLowerInvariant() -eq 'none') { $chosenRoots = @() }
    else { $chosenRoots = @($answer) }
  }
  $validRoots = @()
  foreach ($dir in $chosenRoots) {
    $resolved = $dir.Trim()
    if ($resolved -eq '') { continue }
    if (-not (Test-Path $resolved -PathType Container)) {
      Write-Host "Skipped (not a folder): $resolved"
      continue
    }
    $validRoots += (Resolve-Path $resolved).Path
  }
  Set-DotEnvValue $envPath 'OPERATOR_WORK_ROOTS' ($validRoots -join ';')
  if ($validRoots.Count -gt 0) {
    Write-Host "Project tasks enabled for: $($validRoots -join '; ')"
  } else {
    Write-Host 'Project tasks off: only the sample task is available.'
    Write-Host 'Re-run the installer any time to add a code folder.'
  }

  # 4. Private-network step (Tailscale) --------------------------------
  Write-Step 'Phone access (private-network step)'
  $declinedMarker = Join-Path $root '.runtime\.tailscale-declined'
  $tailscale = Find-Tailscale
  $serveUrl = $null
  $originChanged = $false

  if (($null -eq $tailscale) -and (-not $NoTailscale)) {
    $declinedBefore = Test-Path $declinedMarker
    $wantsInstall = $false
    if ($NonInteractive -or $declinedBefore) {
      if ($declinedBefore) { Write-Host 'Private-network install was declined before; skipping.' }
    } else {
      Write-Host 'Phones cannot reach this PC directly. Tailscale is a'
      Write-Host 'private-network app that gives this PC a private web address'
      Write-Host 'only your own devices can open. This is the only step that'
      Write-Host 'may ask for administrator approval.'
      $wantsInstall = Read-Choice 'Install Tailscale now?' $true
    }
    if ($wantsInstall) {
      try {
        Install-Tailscale
        $tailscale = Find-Tailscale
        if (Test-Path $declinedMarker) { Remove-Item $declinedMarker -Force }
      } catch {
        Write-Host "Could not install Tailscale automatically: $($_.Exception.Message)"
        Write-Host 'You can install it by hand from https://tailscale.com/download'
        Write-Host "and re-run this installer. Details: $guideRef."
        $manualSteps += 'Install Tailscale by hand and re-run the installer.'
      }
    } elseif (-not $NonInteractive -and -not $declinedBefore) {
      if (-not (Test-Path (Join-Path $root '.runtime'))) {
        [void](New-Item -ItemType Directory (Join-Path $root '.runtime'))
      }
      [System.IO.File]::WriteAllText($declinedMarker, "declined`r`n",
        [System.Text.UTF8Encoding]::new($false))
      Write-TailscaleSkipped $guideRef
      $manualSteps += 'Phone access is not set up (private-network step skipped).'
    }
  }

  if ($null -ne $tailscale) {
    $selfName = Get-TailscaleSelf $tailscale
    if ($null -eq $selfName) {
      Write-Host 'Tailscale is installed but not signed in.'
      $signIn = $false
      if (-not $NonInteractive) {
        Write-Host 'If a Tailscale sign-in window is already open, finish there instead;'
        Write-Host 'either way, answer yes below and the installer re-checks automatically.'
        $signIn = Read-Choice 'Sign in to Tailscale now (may briefly open a browser tab that closes itself)?' $true
      }
      if ($signIn) {
        try {
          $selfName = Get-TailscaleSelf $tailscale
          if ($null -eq $selfName) {
            & $tailscale up
            $selfName = Get-TailscaleSelf $tailscale
          } else {
            Write-Host 'Already signed in - continuing.'
          }
        } catch {
          Write-Host "Sign-in did not complete: $($_.Exception.Message)"
        }
      }
      if ($null -eq $selfName) {
        Write-Host "Run 'tailscale up', finish the sign-in, then re-run this installer."
        $manualSteps += "Sign in to Tailscale ('tailscale up') and re-run the installer."
      }
    }
    if ($null -ne $selfName) {
      if (-not (Test-TailscaleServing $tailscale $Port)) {
        Write-Host "Publishing the helper on your private network (port $Port)..."
        try {
          & $tailscale serve --bg --yes $Port
          if ($LASTEXITCODE -ne 0) { throw 'tailscale serve was refused.' }
        } catch {
          Write-Host "Could not publish automatically: $($_.Exception.Message)"
          Write-Host "Run by hand: tailscale serve --bg --yes $Port"
          $manualSteps += "Publish the helper ('tailscale serve --bg --yes $Port')."
        }
      } else {
        Write-Host 'The helper is already published on your private network.'
      }
      if (Test-TailscaleServing $tailscale $Port) {
        $serveUrl = "https://$selfName"
        $values = Read-DotEnv $envPath
        $existingOrigin = ''
        if ($values.ContainsKey('OPERATOR_PUBLIC_ORIGIN')) {
          $existingOrigin = $values['OPERATOR_PUBLIC_ORIGIN']
        }
        # Never overwrite a custom (non-Tailscale) address the owner set by hand.
        if ($existingOrigin -eq '' -or $existingOrigin.EndsWith('.ts.net') -or
            $existingOrigin.EndsWith('.ts.net/')) {
          if ($existingOrigin -ne $serveUrl) {
            Set-DotEnvValue $envPath 'OPERATOR_PUBLIC_ORIGIN' $serveUrl
            $originChanged = $true
          }
        } else {
          Write-Host "Keeping your hand-written web address ($existingOrigin)."
          $serveUrl = $existingOrigin.TrimEnd('/')
        }
      }
    }
  } elseif ($NoTailscale) {
    Write-TailscaleSkipped $guideRef
    $manualSteps += 'Phone access is not set up (private-network step skipped).'
  }

  # 5. Automatic start entry -------------------------------------------
  Write-Step 'Registering the automatic start entry'
  $wrapperResult = Write-StartWrapper $root $Port $venvPython
  Register-ServeTask $TaskName $root $wrapperResult.Path
  Write-Host "The helper will start automatically each time you sign in (entry '$TaskName')."
  $restarted = Restart-ServeTask $TaskName $root `
    ($wrapperResult.Changed -or $originChanged -or $tokenResult.IsNew)
  if ($restarted -eq 'start-failed') {
    $manualSteps += ("The helper did not start. Open '.runtime\\serve.log' in the " +
      'project folder to see why, then re-run the installer.')
  }

  # A launched task is not proof: verify the helper actually answers.
  Write-Host 'Waiting for the helper to answer (up to 30 seconds)...'
  $answered = $false
  for ($i = 0; $i -lt 15; $i++) {
    if (Test-ServePort $Port) { $answered = $true; break }
    Start-Sleep -Seconds 1
  }
  if ($answered) {
    Write-Host "The helper answers at http://127.0.0.1:$Port."
  } else {
    Write-Host 'The helper did not answer within 30 seconds.'
    $manualSteps += ("Start the helper by hand from the project folder: " +
      'powershell -ExecutionPolicy Bypass -File .\.runtime\start-serve.ps1 ' +
      "(then open http://127.0.0.1:$Port). If it fails, the reason is in " +
      "'.runtime\serve.log'.")
  }

  # 6. Summary ----------------------------------------------------------
  Write-Step 'Done'
  Write-Host ''
  if ($null -ne $serveUrl) {
    Write-Host 'PHONE WEB ADDRESS (open on your phone):'
    Write-Host "  $serveUrl"
  } else {
    Write-Host 'No phone web address yet (see the manual steps below).'
    Write-Host 'On THIS PC the helper answers at:'
    Write-Host "  http://127.0.0.1:$Port"
  }
  Write-Host ''
  if ($tokenResult.IsNew) {
    Write-Host 'PASSWORD: the new password printed above. Type it on the phone'
    Write-Host 'when the page asks for it, then choose Connect.'
  } else {
    Write-Host 'PASSWORD: your existing password (unchanged, not shown here).'
    Write-Host "Type it on the phone when the page asks for it. To change it, see $guideRef,"
    Write-Host "section 'Changing the password'."
  }
  Write-Host ''
  Write-Host 'The helper also starts by itself every time you sign in to Windows.'
  Write-Host 'START IT YOURSELF (any time, from the project folder):'
  Write-Host '  powershell -ExecutionPolicy Bypass -File .\.runtime\start-serve.ps1'
  Write-Host "If something looks wrong, check '.runtime\\serve.log' in the project folder."
  if ($null -ne $serveUrl) {
    Write-Host ''
    Write-Host 'First-time phone notes: install the Tailscale app on the phone and'
    Write-Host 'sign in to the same account, then open the address above in the'
    Write-Host 'phone browser. If the page will not load, your private network'
    Write-Host 'may need the secure-address switch first - see the guide section'
    Write-Host "'If the page will not load'. Details: $guideRef."
  }
  if ($manualSteps.Count -gt 0) {
    Write-Host ''
    Write-Host 'STILL TO DO BY HAND:'
    $n = 1
    foreach ($step in $manualSteps) {
      Write-Host "  $n. $step"
      $n++
    }
  }
  Write-Host ''
  Write-Host "Full plain-language guide: $guideRef"
}

if ($MyInvocation.InvocationName -ne '.') {
  Invoke-Installer
}


