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

function Get-HarnessLabel($Harness) {
    switch ($Harness) {
        "codex" { return "Codex CLI" }
        "cursor-agent" { return "Cursor Agent" }
        "agent" { return "Cursor Agent" }
        "opencode" { return "OpenCode" }
        "gemini" { return "Gemini CLI" }
        "aider" { return "Aider" }
        "goose" { return "Goose" }
        "copilot" { return "GitHub Copilot CLI" }
        "kiro-cli" { return "Kiro CLI" }
        "amp" { return "Amp" }
        "ollama" { return "Ollama" }
        "lms" { return "LM Studio" }
    }
}

function Save-HarnessConfig($Harness) {
    $configDir = Join-Path $env:APPDATA $AppName
    New-Item -ItemType Directory -Force -Path $configDir *> $null
    $configPath = Join-Path $configDir "config.toml"
    @(
        "# $AppName AI defaults. Store API keys in environment variables, not here.",
        "harness = `"$Harness`""
    ) | Set-Content -Path $configPath -Encoding UTF8
    Write-Host "[ok] Saved $(Get-HarnessLabel $Harness) as the AI default"
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

function Get-ProviderLabel($Provider) {
    switch ($Provider) {
        "openai" { return "OpenAI API" }
        "gemini" { return "Gemini API" }
        "openrouter" { return "OpenRouter API" }
        "groq" { return "Groq API" }
        "mistral" { return "Mistral API" }
        "together" { return "Together AI API" }
        "perplexity" { return "Perplexity API" }
        "xai" { return "xAI API" }
    }
}

function Get-ProviderEnvKeys($Provider) {
    switch ($Provider) {
        "openai" { return @("OPENAI_API_KEY") }
        "gemini" { return @("GEMINI_API_KEY", "GOOGLE_API_KEY") }
        "openrouter" { return @("OPENROUTER_API_KEY") }
        "groq" { return @("GROQ_API_KEY") }
        "mistral" { return @("MISTRAL_API_KEY") }
        "together" { return @("TOGETHER_API_KEY") }
        "perplexity" { return @("PERPLEXITY_API_KEY") }
        "xai" { return @("XAI_API_KEY") }
    }
}

function Get-ProviderBaseUrl($Provider) {
    switch ($Provider) {
        "gemini" { return "https://generativelanguage.googleapis.com/v1beta/openai/" }
        "openrouter" { return "https://openrouter.ai/api/v1" }
        "groq" { return "https://api.groq.com/openai/v1" }
        "mistral" { return "https://api.mistral.ai/v1" }
        "together" { return "https://api.together.xyz/v1" }
        "perplexity" { return "https://api.perplexity.ai" }
        "xai" { return "https://api.x.ai/v1" }
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
        "groq" { return "llama-3.3-70b-versatile" }
        "mistral" { return "mistral-small-latest" }
        "together" { return "meta-llama/Llama-3.3-70B-Instruct-Turbo" }
        "perplexity" { return "sonar-pro" }
        "xai" { return "grok-3-mini" }
        default { return "gpt-4o-mini" }
    }
}

function Ensure-ProviderKey($Provider) {
    $keyNames = Get-ProviderEnvKeys $Provider
    $primaryKey = $keyNames[0]
    foreach ($keyName in $keyNames) {
        if ([Environment]::GetEnvironmentVariable($keyName)) {
            Write-Host "[ok] $keyName already set"
            return
        }
    }
    if ($Yes) { return }
    Write-Host ""
    Write-Host "$primaryKey was not found."
    Write-Host "1) Paste API key now"
    Write-Host "2) Show me the env var command"
    Write-Host "3) Skip key setup"
    $choice = Read-Choice "Choice" "1"
    switch ($choice) {
        "1" {
            $apiKey = Read-SecretText "Enter $primaryKey"
            if (-not [string]::IsNullOrWhiteSpace($apiKey)) {
                Save-UserSecret $primaryKey $apiKey
            } else {
                Write-Host "[info] Empty key skipped"
            }
        }
        "2" {
            Write-Host "Run this later:"
            Write-Host "  [Environment]::SetEnvironmentVariable(`"$primaryKey`", `"your-api-key`", `"User`")"
        }
        default {
            Write-Host "[info] Skipped API key setup"
        }
    }
}

function Get-DefaultAIChoice {
    if (Get-Command codex -ErrorAction SilentlyContinue) { return "1" }
    if (Get-Command cursor-agent -ErrorAction SilentlyContinue) { return "2" }
    if (Get-Command agent -ErrorAction SilentlyContinue) { return "3" }
    if (Get-Command opencode -ErrorAction SilentlyContinue) { return "4" }
    if (Get-Command gemini -ErrorAction SilentlyContinue) { return "5" }
    if (Get-Command aider -ErrorAction SilentlyContinue) { return "6" }
    if (Get-Command goose -ErrorAction SilentlyContinue) { return "7" }
    if (Get-Command copilot -ErrorAction SilentlyContinue) { return "8" }
    if (Get-Command kiro-cli -ErrorAction SilentlyContinue) { return "9" }
    if (Get-Command amp -ErrorAction SilentlyContinue) { return "10" }
    if (Get-Command ollama -ErrorAction SilentlyContinue) { return "11" }
    if (Get-Command lms -ErrorAction SilentlyContinue) { return "12" }
    if ($env:OPENAI_API_KEY) { return "13" }
    if ($env:GEMINI_API_KEY -or $env:GOOGLE_API_KEY) { return "14" }
    if ($env:OPENROUTER_API_KEY) { return "15" }
    if ($env:GROQ_API_KEY) { return "16" }
    if ($env:MISTRAL_API_KEY) { return "17" }
    if ($env:TOGETHER_API_KEY) { return "18" }
    if ($env:PERPLEXITY_API_KEY) { return "19" }
    if ($env:XAI_API_KEY) { return "20" }
    return "21"
}

function Setup-AIDefaults {
    if ($Yes) { return }
    Write-Host ""
    Write-Host "Choose AI default:"
    Write-Host "1) $(Get-HarnessLabel "codex")"
    Write-Host "2) $(Get-HarnessLabel "cursor-agent")"
    Write-Host "3) $(Get-HarnessLabel "agent")"
    Write-Host "4) $(Get-HarnessLabel "opencode")"
    Write-Host "5) $(Get-HarnessLabel "gemini")"
    Write-Host "6) $(Get-HarnessLabel "aider")"
    Write-Host "7) $(Get-HarnessLabel "goose")"
    Write-Host "8) $(Get-HarnessLabel "copilot")"
    Write-Host "9) $(Get-HarnessLabel "kiro-cli")"
    Write-Host "10) $(Get-HarnessLabel "amp")"
    Write-Host "11) $(Get-HarnessLabel "ollama")"
    Write-Host "12) $(Get-HarnessLabel "lms")"
    Write-Host "13) $(Get-ProviderLabel "openai")"
    Write-Host "14) $(Get-ProviderLabel "gemini")"
    Write-Host "15) $(Get-ProviderLabel "openrouter")"
    Write-Host "16) $(Get-ProviderLabel "groq")"
    Write-Host "17) $(Get-ProviderLabel "mistral")"
    Write-Host "18) $(Get-ProviderLabel "together")"
    Write-Host "19) $(Get-ProviderLabel "perplexity")"
    Write-Host "20) $(Get-ProviderLabel "xai")"
    Write-Host "21) Skip AI setup"
    $choice = Read-Choice "Choice" (Get-DefaultAIChoice)
    switch ($choice) {
        "1" { Save-HarnessConfig "codex" }
        "2" { Save-HarnessConfig "cursor-agent" }
        "3" { Save-HarnessConfig "agent" }
        "4" { Save-HarnessConfig "opencode" }
        "5" { Save-HarnessConfig "gemini" }
        "6" { Save-HarnessConfig "aider" }
        "7" { Save-HarnessConfig "goose" }
        "8" { Save-HarnessConfig "copilot" }
        "9" { Save-HarnessConfig "kiro-cli" }
        "10" { Save-HarnessConfig "amp" }
        "11" { Save-HarnessConfig "ollama" }
        "12" { Save-HarnessConfig "lms" }
        "13" { Ensure-ProviderKey "openai"; Save-ProviderConfig "openai" (Get-ProviderBaseUrl "openai") (Get-ProviderModel "openai") }
        "14" { Ensure-ProviderKey "gemini"; Save-ProviderConfig "gemini" (Get-ProviderBaseUrl "gemini") (Get-ProviderModel "gemini") }
        "15" { Ensure-ProviderKey "openrouter"; Save-ProviderConfig "openrouter" (Get-ProviderBaseUrl "openrouter") (Get-ProviderModel "openrouter") }
        "16" { Ensure-ProviderKey "groq"; Save-ProviderConfig "groq" (Get-ProviderBaseUrl "groq") (Get-ProviderModel "groq") }
        "17" { Ensure-ProviderKey "mistral"; Save-ProviderConfig "mistral" (Get-ProviderBaseUrl "mistral") (Get-ProviderModel "mistral") }
        "18" { Ensure-ProviderKey "together"; Save-ProviderConfig "together" (Get-ProviderBaseUrl "together") (Get-ProviderModel "together") }
        "19" { Ensure-ProviderKey "perplexity"; Save-ProviderConfig "perplexity" (Get-ProviderBaseUrl "perplexity") (Get-ProviderModel "perplexity") }
        "20" { Ensure-ProviderKey "xai"; Save-ProviderConfig "xai" (Get-ProviderBaseUrl "xai") (Get-ProviderModel "xai") }
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
