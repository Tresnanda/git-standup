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

Write-Host "Installing $AppName from GitHub..."
& $Python -m pipx install --force $RepoSpec
if (Get-Command $AppName -ErrorAction SilentlyContinue) {
    & $AppName --help *> $null
    Write-Host "[ok] $AppName installed"
} else {
    Write-Host "[warn] $AppName installed, but pipx bin dir may not be on PATH."
    Write-Host "Run: python -m pipx ensurepath"
}

if (Confirm-Step "Run $AppName wizard now?" $true) {
    & $AppName wizard
}
