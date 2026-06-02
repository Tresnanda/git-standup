param(
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$AppName = "git-standup"
$RepoSlug = "Tresnanda/git-standup"
$RepoUrl = "https://github.com/$RepoSlug"
$RepoSpec = "git+https://github.com/Tresnanda/git-standup.git"
$MinimumPythonMajor = 3
$MinimumPythonMinor = 10

function Confirm-Step($Prompt, $DefaultYes = $true) {
    if ($Yes) { return $true }
    $suffix = if ($DefaultYes) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "$Prompt $suffix"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $DefaultYes }
    return @("y", "yes") -contains $answer.ToLowerInvariant()
}

function Offer-StarRepo {
    if ($Yes) {
        Write-Host "Star it here: $RepoUrl"
        return
    }
    if (-not (Confirm-Step "If $AppName helps you, star the GitHub repo now?" $true)) {
        Write-Host "Star it here: $RepoUrl"
        return
    }
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        try {
            & gh auth status *> $null
            if ($LASTEXITCODE -eq 0) {
                & gh repo star $RepoSlug *> $null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "[ok] Starred $RepoUrl"
                    return
                }
            }
        } catch {}
    }
    if ($env:GITHUB_TOKEN) {
        try {
            Invoke-RestMethod `
                -Method Put `
                -Uri "https://api.github.com/user/starred/$RepoSlug" `
                -Headers @{
                    "Accept" = "application/vnd.github+json"
                    "Authorization" = "Bearer $env:GITHUB_TOKEN"
                    "X-GitHub-Api-Version" = "2022-11-28"
                } *> $null
            Write-Host "[ok] Starred $RepoUrl"
            return
        } catch {}
    }
    Write-Host "Couldn't auto-star from this terminal."
    Write-Host "Star it here: $RepoUrl"
}

function Read-Choice($Prompt, $Default) {
    if ($Yes) { return $Default }
    $answer = Read-Host "$Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer
}

function Invoke-PythonCandidate($Candidate, [string[]]$Arguments) {
    $exe = $Candidate[0]
    $allArgs = @()
    if ($Candidate.Count -gt 1) {
        $allArgs += $Candidate[1..($Candidate.Count - 1)]
    }
    $allArgs += $Arguments
    & $exe @allArgs
}

function Test-PythonVersion($Candidate) {
    try {
        Invoke-PythonCandidate $Candidate @("-c", "import sys; raise SystemExit(0 if sys.version_info >= ($MinimumPythonMajor, $MinimumPythonMinor) else 1)") *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Resolve-PythonExecutable($Candidate) {
    $output = Invoke-PythonCandidate $Candidate @("-c", "import sys; print(sys.executable)")
    return ($output | Select-Object -First 1).Trim()
}

function Invoke-Pipx([string[]]$Arguments) {
    $exe = $script:PipxCommand[0]
    $allArgs = @()
    if ($script:PipxCommand.Count -gt 1) {
        $allArgs += $script:PipxCommand[1..($script:PipxCommand.Count - 1)]
    }
    $allArgs += $Arguments
    & $exe @allArgs
}

function Get-DataHome {
    if ($env:LOCALAPPDATA) { return $env:LOCALAPPDATA }
    if ($env:XDG_DATA_HOME) { return $env:XDG_DATA_HOME }
    return (Join-Path $HOME ".local/share")
}

function Get-PipxBinDir {
    if ($env:PIPX_BIN_DIR) { return $env:PIPX_BIN_DIR }
    return (Join-Path $HOME ".local/bin")
}

function Initialize-PipxBootstrap {
    $venvDir = Join-Path (Join-Path (Get-DataHome) $AppName) "pipx-bootstrap"
    Write-Host "pipx was not found; installing a private pipx helper..."
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $venvDir) *> $null
    & $Python @("-m", "venv", $venvDir)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create a Python virtual environment for pipx. Install pipx manually, then rerun this installer."
    }
    $venvPython = Join-Path $venvDir "Scripts/python.exe"
    if (-not (Test-Path $venvPython)) {
        $venvPython = Join-Path $venvDir "bin/python"
    }
    & $venvPython @("-m", "pip", "install", "--upgrade", "pip", "pipx") *> $null
    $pipxExe = Join-Path $venvDir "Scripts/pipx.exe"
    if (-not (Test-Path $pipxExe)) {
        $pipxExe = Join-Path $venvDir "bin/pipx"
    }
    $script:PipxCommand = @($pipxExe)
}

function Test-PipxAppInstalled {
    try {
        $installed = Invoke-Pipx @("list", "--short") 2>$null
    } catch {
        return $false
    }
    foreach ($line in $installed) {
        if ($line -match "^$([regex]::Escape($AppName))(\s|$)") {
            return $true
        }
    }
    return $false
}

function Install-OrUpdateApp {
    if (Test-PipxAppInstalled) {
        Write-Host "Updating existing $AppName install..."
        Invoke-Pipx @("reinstall", $AppName, "--python", $Python)
    } else {
        Invoke-Pipx @("install", "--python", $Python, $RepoSpec)
    }
}

function Find-Python {
    $candidates = @(
        @("py", "-3.13"),
        @("py", "-3.12"),
        @("py", "-3.11"),
        @("py", "-3.10"),
        @("python3.13"),
        @("python3.12"),
        @("python3.11"),
        @("python3.10"),
        @("python3"),
        @("python")
    )
    foreach ($candidate in $candidates) {
        if ((Get-Command $candidate[0] -ErrorAction SilentlyContinue) -and (Test-PythonVersion $candidate)) {
            return Resolve-PythonExecutable $candidate
        }
    }
    throw "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ and rerun this installer."
}

function Read-SecretText($Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Save-UserSecret($Name, $Value) {
    [Environment]::SetEnvironmentVariable($Name, $Value, "User")
    Set-Item -Path "Env:$Name" -Value $Value
    Write-Host "[ok] Saved $Name as a user environment variable"
    Write-Host "Open a new terminal before using it in another session."
}

function Save-CodexConfig {
    $configDir = Join-Path $env:APPDATA $AppName
    New-Item -ItemType Directory -Force -Path $configDir *> $null
    $configPath = Join-Path $configDir "config.toml"
    @(
        "# $AppName AI defaults. Store API keys in environment variables, not here.",
        "harness = `"codex`""
    ) | Set-Content -Path $configPath -Encoding UTF8
    Write-Host "[ok] Saved Codex as the AI default"
}

function Save-ProviderConfig($Provider, $BaseUrl, $Model) {
    $configDir = Join-Path $env:APPDATA $AppName
    New-Item -ItemType Directory -Force -Path $configDir *> $null
    $configPath = Join-Path $configDir "config.toml"
    @(
        "# $AppName AI defaults. Store API keys in environment variables, not here.",
        "provider = `"$Provider`"",
        "base_url = `"$BaseUrl`"",
        "model = `"$Model`""
    ) | Set-Content -Path $configPath -Encoding UTF8
    Write-Host "[ok] Saved AI default to $configPath"
}

function Get-ProviderEnvKey($Provider) {
    switch ($Provider) {
        "openai" { return "OPENAI_API_KEY" }
        "gemini" { return "GEMINI_API_KEY" }
        "openrouter" { return "OPENROUTER_API_KEY" }
    }
}

function Get-ProviderBaseUrl($Provider) {
    switch ($Provider) {
        "gemini" { return "https://generativelanguage.googleapis.com/v1beta/openai/" }
        "openrouter" { return "https://openrouter.ai/api/v1" }
        default {
            if ($env:OPENAI_BASE_URL) { return $env:OPENAI_BASE_URL }
            return "https://api.openai.com/v1"
        }
    }
}

function Get-ProviderModel($Provider) {
    switch ($Provider) {
        "gemini" { return "gemini-3.5-flash" }
        "openrouter" { return "openai/gpt-4o-mini" }
        default { return "gpt-4o-mini" }
    }
}

function Ensure-ProviderKey($Provider) {
    $keyName = Get-ProviderEnvKey $Provider
    if ([Environment]::GetEnvironmentVariable($keyName)) {
        Write-Host "[ok] $keyName already set"
        return
    }
    if ($Yes) { return }
    Write-Host ""
    Write-Host "$keyName was not found."
    Write-Host "1) Paste API key now"
    Write-Host "2) Show me the env var command"
    Write-Host "3) Skip key setup"
    $choice = Read-Choice "Choice" "1"
    switch ($choice) {
        "1" {
            $apiKey = Read-SecretText "Enter $keyName"
            if (-not [string]::IsNullOrWhiteSpace($apiKey)) {
                Save-UserSecret $keyName $apiKey
            } else {
                Write-Host "[info] Empty key skipped"
            }
        }
        "2" {
            Write-Host "Run this later:"
            Write-Host "  [Environment]::SetEnvironmentVariable(`"$keyName`", `"your-api-key`", `"User`")"
        }
        default {
            Write-Host "[info] Skipped API key setup"
        }
    }
}

function Get-DefaultAIChoice {
    if (Get-Command codex -ErrorAction SilentlyContinue) { return "1" }
    if ($env:OPENAI_API_KEY) { return "2" }
    if ($env:GEMINI_API_KEY -or $env:GOOGLE_API_KEY) { return "3" }
    if ($env:OPENROUTER_API_KEY) { return "4" }
    return "5"
}

function Setup-AIDefaults {
    if ($Yes) { return }
    Write-Host ""
    Write-Host "Choose AI default:"
    Write-Host "1) Codex CLI - recommended if you use Codex"
    Write-Host "2) OpenAI API"
    Write-Host "3) Gemini API"
    Write-Host "4) OpenRouter API"
    Write-Host "5) Skip AI setup"
    $choice = Read-Choice "Choice" (Get-DefaultAIChoice)
    switch ($choice) {
        "1" { Save-CodexConfig }
        "2" { Ensure-ProviderKey "openai"; Save-ProviderConfig "openai" (Get-ProviderBaseUrl "openai") (Get-ProviderModel "openai") }
        "3" { Ensure-ProviderKey "gemini"; Save-ProviderConfig "gemini" (Get-ProviderBaseUrl "gemini") (Get-ProviderModel "gemini") }
        "4" { Ensure-ProviderKey "openrouter"; Save-ProviderConfig "openrouter" (Get-ProviderBaseUrl "openrouter") (Get-ProviderModel "openrouter") }
        default { Write-Host "[info] Skipped AI setup. You can run: $AppName config" }
    }
}

Write-Host "Install git-standup"
Write-Host "This checks Python/Git, installs with pipx, and can set an AI default."
$Python = Find-Python
Write-Host "[ok] Python: $(& $Python --version 2>&1)"
if (Get-Command git -ErrorAction SilentlyContinue) {
    Write-Host "[ok] git found"
} else {
    Write-Host "[warn] git not found"
}
if (Get-Command codex -ErrorAction SilentlyContinue) {
    Write-Host "[ok] Codex CLI found"
}

$script:PipxCommand = @()
$pipxExecutable = Get-Command pipx -ErrorAction SilentlyContinue
if ($pipxExecutable) {
    $script:PipxCommand = @($pipxExecutable.Source)
    try {
        Invoke-Pipx @("--version") *> $null
        Write-Host "[ok] pipx found"
    } catch {
        Write-Host "[info] Ignoring broken pipx at $($pipxExecutable.Source)"
        $script:PipxCommand = @()
    }
}
if ($script:PipxCommand.Count -eq 0) {
    $script:PipxCommand = @($Python, "-m", "pipx")
    try {
        Invoke-Pipx @("--version") *> $null
        Write-Host "[ok] pipx found"
    } catch {
        if (Confirm-Step "Install pipx with this Python?" $true) {
            Initialize-PipxBootstrap
        } else {
            throw "Install pipx and rerun this installer."
        }
    }
}

Setup-AIDefaults

Write-Host "Installing $AppName from GitHub..."
Install-OrUpdateApp
if (Get-Command $AppName -ErrorAction SilentlyContinue) {
    & $AppName --help *> $null
    Write-Host "[ok] $AppName installed"
} else {
    Write-Host "[warn] $AppName installed, but pipx bin dir may not be on PATH."
    Write-Host "Run: `$env:Path = `"$(Get-PipxBinDir);`$env:Path`""
}

Offer-StarRepo
Write-Host "Run git-standup in your terminal to start the guided report builder."
