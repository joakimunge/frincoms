# Download this single file from the private repo and run it in PowerShell.
# Installs prerequisites for the current Windows user, clones/updates the repo,
# and creates a standalone dist\Frincoms.exe.
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $env:USERPROFILE 'frincoms')
)

$ErrorActionPreference = 'Stop'
$repo = 'joakimunge/frincoms'

function Assert-Success([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed (exit code $LASTEXITCODE)."
    }
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user;$env:Path"
}

function Require-Command([string]$Name, [string]$WingetId) {
    $found = Get-Command $Name -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "Install 'App Installer' (winget) from Microsoft Store, then rerun this script."
    }
    Write-Host "Installing $WingetId..."
    & winget.exe install --id $WingetId --exact --source winget --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity
    Assert-Success "Installing $WingetId"
    Refresh-Path
    $found = Get-Command $Name -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    # Some installers do not add their executable to the current process PATH.
    $locations = if ($Name -eq 'git.exe') {
        @((Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe'),
          (Join-Path $env:ProgramFiles 'Git\cmd\git.exe'))
    } else {
        @((Join-Path $env:LOCALAPPDATA 'Programs\GitHub CLI\gh.exe'),
          (Join-Path $env:ProgramFiles 'GitHub CLI\gh.exe'))
    }
    foreach ($location in $locations) {
        if (Test-Path -LiteralPath $location) { return $location }
    }
    throw "$WingetId installed, but $Name was not found. Open a new PowerShell window and rerun the script."
}

function Get-Python312 {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            & $launcher.Source -3.12 -c 'import tkinter, struct; assert struct.calcsize("P") == 8' *> $null
            if ($LASTEXITCODE -eq 0) { return @{ Command = $launcher.Source; Arguments = @('-3.12') } }
        } catch { }
    }
    $paths = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe')
    )
    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path) {
            try {
                & $path -c 'import tkinter, struct; assert struct.calcsize("P") == 8' *> $null
                if ($LASTEXITCODE -eq 0) { return @{ Command = $path; Arguments = @() } }
            } catch { }
        }
    }
    return $null
}

if ($env:OS -ne 'Windows_NT') { throw 'Run this script on Windows.' }

$git = Require-Command 'git.exe' 'Git.Git'
$gh = Require-Command 'gh.exe' 'GitHub.cli'

$signedIn = $false
try {
    & $gh auth status *> $null
    $signedIn = $LASTEXITCODE -eq 0
} catch { }
if (-not $signedIn) {
    Write-Host 'Sign in to GitHub in the browser to access the private Frincoms repository.'
    & $gh auth login --hostname github.com --web --git-protocol https
    Assert-Success 'GitHub sign-in'
}
& $gh auth setup-git
Assert-Success 'Configuring GitHub access for Git'

Refresh-Path
$python = Get-Python312
if (-not $python) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "Install 'App Installer' (winget) from Microsoft Store, then rerun this script."
    }
    Write-Host 'Installing 64-bit Python 3.12...'
    & winget.exe install --id Python.Python.3.12 --exact --source winget --scope user --architecture x64 --accept-package-agreements --accept-source-agreements --disable-interactivity
    Assert-Success 'Installing Python 3.12'
    Refresh-Path
    $python = Get-Python312
    if (-not $python) { throw 'Python 3.12 with Tk was not found. Open a new PowerShell window and rerun this script.' }
}

$Destination = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $Destination) {
    if (-not (Test-Path -LiteralPath (Join-Path $Destination '.git'))) {
        throw "$Destination exists but is not a Git checkout. Choose another destination with -Destination."
    }
    $origin = & $git -C $Destination remote get-url origin
    Assert-Success 'Reading repository origin'
    $origin = "$origin".Trim()
    if ($origin -notmatch '^((https://github\.com/)|(git@github\.com:))joakimunge/frincoms(\.git)?$') {
        throw "$Destination is not the joakimunge/frincoms repository."
    }
    $changes = & $git -C $Destination status --porcelain
    Assert-Success 'Checking local changes'
    if ($changes) { throw "There are local changes in $Destination. Commit or stash them before updating." }
    Write-Host 'Updating Frincoms...'
    & $git -C $Destination pull --ff-only origin main
    Assert-Success 'Updating Frincoms'
} else {
    $parent = Split-Path -Parent $Destination
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Write-Host 'Cloning private Frincoms repository...'
    & $git clone "https://github.com/$repo.git" $Destination
    Assert-Success 'Cloning Frincoms'
}

Push-Location -LiteralPath $Destination
try {
    $venvPython = Join-Path $Destination '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $pythonExe = $python.Command
        $pythonArgs = $python.Arguments
        & $pythonExe @pythonArgs -m venv .venv
        Assert-Success 'Creating Python environment'
    }
    & $venvPython -m pip install -r requirements.txt pyinstaller
    Assert-Success 'Installing build dependencies'
    & $venvPython -m unittest -q test_app
    Assert-Success 'Running tests'
    & $venvPython -m PyInstaller --noconfirm frincoms.spec
    Assert-Success 'Building Frincoms.exe'
    $exe = Join-Path $Destination 'dist\Frincoms.exe'
    if (-not (Test-Path -LiteralPath $exe)) { throw "Build finished without $exe." }
    Write-Host "`nReady: $exe" -ForegroundColor Green
} finally {
    Pop-Location
}
