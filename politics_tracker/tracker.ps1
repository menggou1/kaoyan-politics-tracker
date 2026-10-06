param([switch]$Stop)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 8501
$url = "http://127.0.0.1:$port"
$healthUrl = "$url/_stcore/health"
$pidFile = Join-Path $projectRoot '.tracker.pid'

function Test-TrackerRunning {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 2 -UseBasicParsing
        return $response.StatusCode -eq 200 -and $response.Content.Trim() -eq 'ok'
    } catch {
        return $false
    }
}

function Save-ListeningProcess {
    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
        if ($processInfo -and $processInfo.CommandLine -match 'streamlit.*run.*app\.py') {
            Set-Content -LiteralPath $pidFile -Value $listener.OwningProcess -Encoding ASCII
            return $true
        }
    }
    return $false
}

if ($Stop) {
    if (-not (Test-Path -LiteralPath $pidFile)) {
        if (Test-TrackerRunning) { Save-ListeningProcess | Out-Null }
        if (-not (Test-Path -LiteralPath $pidFile)) {
            Write-Host 'No server managed by this launcher was found.'
            exit 0
        }
    }
    $savedPid = [int](Get-Content -LiteralPath $pidFile -Raw)
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $savedPid" -ErrorAction SilentlyContinue
    if ($processInfo -and $processInfo.CommandLine -match 'streamlit.*run.*app\.py') {
        Stop-Process -Id $savedPid -Force
        Write-Host 'The local practice tracker has been stopped.'
    } else {
        Write-Host 'The saved server process is no longer running.'
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    exit 0
}

if (Test-TrackerRunning) {
    Save-ListeningProcess | Out-Null
    Write-Host 'The tracker is already running. Opening the page...'
    Start-Process $url
    exit 0
}

$python = $null
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    try {
        & py -c "import sys,streamlit; assert sys.version_info >= (3,11)" 2>$null
        if ($LASTEXITCODE -eq 0) { $python = 'py' }
    } catch { }
}

if (-not $python) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        try {
            & python -c "import sys,streamlit; assert sys.version_info >= (3,11)" 2>$null
            if ($LASTEXITCODE -eq 0) { $python = 'python' }
        } catch { }
    }
}

if (-not $python) {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        if ($launcher) {
            Write-Host 'Creating the project Python environment...'
            & py -m venv (Join-Path $projectRoot '.venv')
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the environment. Install Python 3.11 or later and retry.' }
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            Write-Host 'Creating the project Python environment...'
            & python -m venv (Join-Path $projectRoot '.venv')
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the environment. Install Python 3.11 or later and retry.' }
        } else {
            throw 'Python was not found. Install Python 3.11 or later, then double-click start.bat again.'
        }
    }
    $python = $venvPython
    Write-Host 'Installing required packages. Internet access is needed only for this first setup...'
    & $python -m pip install -r (Join-Path $projectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. Check the network connection and retry.' }
}

$logs = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$stdout = Join-Path $logs 'streamlit.out.log'
$stderr = Join-Path $logs 'streamlit.err.log'
$arguments = @('-m', 'streamlit', 'run', 'app.py', '--server.headless', 'true', '--server.port', "$port")
if ($python -eq 'py') {
    Start-Process -FilePath 'py' -ArgumentList $arguments -WorkingDirectory $projectRoot `
        -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
} elseif ($python -eq 'python') {
    Start-Process -FilePath 'python' -ArgumentList $arguments -WorkingDirectory $projectRoot `
        -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
} else {
    Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot `
        -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
}

Write-Host 'Starting the practice tracker...'
$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
    if (Test-TrackerRunning) {
        Save-ListeningProcess | Out-Null
        Start-Process $url
        Write-Host 'The tracker page is open.'
        exit 0
    }
    Start-Sleep -Seconds 1
}
throw "The server did not start. Check $stderr"
