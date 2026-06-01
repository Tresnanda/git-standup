param(
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$AppName = "git-standup"
$RepoSpec = "git+https://github.com/Tresnanda/git-standup.git"

function Confirm-Step($Prompt, $DefaultYes = $true) {
    if ($Yes) { return $true }
    $suffix = if ($DefaultYes) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "$Prompt $suffix"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $DefaultYes }
    return @("y", "yes") -contains $answer.ToLowerInvariant()
}

function Find-Python {
    foreach ($candidate in @("py", "python3", "python")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $candidate }
    }
    throw "Python 3 is required."
}

function Check-AIEnvironment {
    Write-Host "AI environment checks (Claude/Anthropic intentionally skipped):"
    $keys = @(
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "GEMINI_API_KEY", "GOOGLE_API_KEY",
        "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY", "TOGETHER_API_KEY",
        "PERPLEXITY_API_KEY", "XAI_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"
    )
    foreach ($key in $keys) {
        if ([Environment]::GetEnvironmentVariable($key)) {
            Write-Host "[ok] $key is set"
        } else {
            Write-Host "[info] $key not set"
        }
    }
    foreach ($cmd in @("openai", "gemini", "ollama", "lms", "gh", "vercel", "aider", "opencode", "codex")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            Write-Host "[ok] $cmd CLI found"
        }
    }
    foreach ($url in @("http://localhost:11434/v1/models", "http://localhost:1234/v1/models", "http://localhost:4000/v1/models")) {
        try {
            Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1 *> $null
            Write-Host "[ok] local endpoint: $($url -replace '/models$','')"
        } catch {
        }
    }
}

function Get-DefaultProvider {
    if ([Environment]::GetEnvironmentVariable("GEMINI_API_KEY") -or [Environment]::GetEnvironmentVariable("GOOGLE_API_KEY")) { return "gemini" }
    if ([Environment]::GetEnvironmentVariable("OPENROUTER_API_KEY")) { return "openrouter" }
    if ([Environment]::GetEnvironmentVariable("GROQ_API_KEY")) { return "groq" }
    if ([Environment]::GetEnvironmentVariable("MISTRAL_API_KEY")) { return "mistral" }
    if ([Environment]::GetEnvironmentVariable("TOGETHER_API_KEY")) { return "together" }
    if ([Environment]::GetEnvironmentVariable("PERPLEXITY_API_KEY")) { return "perplexity" }
    if ([Environment]::GetEnvironmentVariable("XAI_API_KEY")) { return "xai" }
    return "openai"
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
            $base = [Environment]::GetEnvironmentVariable("OPENAI_BASE_URL")
            if ($base) { return $base }
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

function Read-Defaulted($Prompt, $Default) {
    $answer = Read-Host "$Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer
}

function Save-AIDefaults {
    if ($Yes) { return }
    if (-not (Confirm-Step "Save default AI provider/model for $AppName?" $true)) { return }
    $provider = Read-Defaulted "Provider or CLI (codex/openai/gemini/openrouter/groq/mistral/together/perplexity/xai)" (Get-DefaultProvider)
    if ($provider -eq "codex") {
        $model = Read-Defaulted "Codex model (blank uses Codex default)" ""
        $configDir = Join-Path $env:APPDATA $AppName
        New-Item -ItemType Directory -Force -Path $configDir *> $null
        $configPath = Join-Path $configDir "config.toml"
        $lines = @(
            "# $AppName AI defaults. Store API keys in environment variables, not here.",
            "harness = `"codex`""
        )
        if (-not [string]::IsNullOrWhiteSpace($model)) {
            $lines += "model = `"$model`""
        }
        $lines | Set-Content -Path $configPath -Encoding UTF8
        Write-Host "[ok] Saved AI defaults to $configPath"
        return
    }
    $baseUrl = Read-Defaulted "Base URL" (Get-ProviderBaseUrl $provider)
    $model = Read-Defaulted "Model" (Get-ProviderModel $provider)
    $configDir = Join-Path $env:APPDATA $AppName
    New-Item -ItemType Directory -Force -Path $configDir *> $null
    $configPath = Join-Path $configDir "config.toml"
    @(
        "# $AppName AI defaults. Store API keys in environment variables, not here.",
        "provider = `"$provider`"",
        "base_url = `"$baseUrl`"",
        "model = `"$model`""
    ) | Set-Content -Path $configPath -Encoding UTF8
    Write-Host "[ok] Saved AI defaults to $configPath"
}

Write-Host "git-standup installer"
$Python = Find-Python
Write-Host "[ok] Python: $(& $Python --version 2>&1)"
if (Get-Command git -ErrorAction SilentlyContinue) {
    Write-Host "[ok] git found"
} else {
    Write-Host "[warn] git not found"
}

try {
    & $Python -m pipx --version *> $null
    Write-Host "[ok] pipx found"
} catch {
    if (Confirm-Step "Install pipx with this Python?" $true) {
        & $Python -m pip install --user pipx
        & $Python -m pipx ensurepath *> $null
    } else {
        throw "Install pipx and rerun this installer."
    }
}

Check-AIEnvironment
Save-AIDefaults

Write-Host "Installing $AppName from GitHub..."
& $Python -m pipx install --force $RepoSpec
if (Get-Command $AppName -ErrorAction SilentlyContinue) {
    & $AppName --help *> $null
    Write-Host "[ok] $AppName installed"
} else {
    Write-Host "[warn] $AppName installed, but pipx bin dir may not be on PATH."
    Write-Host "Run: python -m pipx ensurepath"
}

if (-not $Yes -and (Confirm-Step "Run $AppName wizard now?" $true)) {
    & $AppName wizard
}
