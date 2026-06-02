"""CLI entry point for git-standup."""

import argparse
import getpass
import importlib.metadata
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from rich.prompt import Confirm, Prompt

from git_standup import __version__
from git_standup.ai import generate_standup, generate_standup_with_harness
from git_standup.ai_env import PROVIDER_SPECS, detect_ai_environment, resolve_ai_connection
from git_standup.clipboard import clipboard_available, copy_to_clipboard, read_single_key
from git_standup.config import AIConfig, config_path, load_config, reset_config, save_config
from git_standup.env_persist import persist_env_var
from git_standup.formatter import (
    build_json_output,
    build_markdown_output,
    build_text_output,
    print_ai_standup,
    print_text_standup,
)
from git_standup.gitlog import (
    compute_stats,
    get_commits,
    group_by_author,
    group_by_date,
)

APP_NAME = "git-standup"
DIST_NAME = "git-standup"
REPO_URL = "https://github.com/Tresnanda/git-standup.git"
REPO_SPEC = f"git+{REPO_URL}"
MIN_PYTHON = (3, 10)


@dataclass
class UpdateCheck:
    available: bool
    current_commit: str | None = None
    latest_commit: str | None = None


def _build_commit_data(
    commits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the structured data dict grouped by author -> date -> commits."""
    by_author = group_by_author(commits)
    result: dict[str, Any] = {}

    for author, author_commits in by_author.items():
        by_date = group_by_date(author_commits)
        date_data: dict[str, Any] = {}
        for date_key, day_commits in by_date.items():
            stats = compute_stats(day_commits)
            commit_items: list[dict[str, Any]] = []
            for c in day_commits:
                item = {
                    "hash": c.get("hash", ""),
                    "subject": c.get("subject", ""),
                    "body": c.get("body", ""),
                    "files": c.get("files", []),
                }
                if c.get("truncated"):
                    item["truncated"] = c["truncated"]
                commit_items.append(item)
            date_data[date_key] = {
                "commits": commit_items,
                "stats": stats,
            }
        result[author] = date_data

    return result


def _apply_output_budget(
    commits: list[dict[str, Any]],
    *,
    max_commits: int | None,
    max_files_per_commit: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Apply optional output/AI-input limits without changing default output."""
    if max_commits is None and max_files_per_commit is None:
        return commits, None

    commits_truncated = max_commits is not None and len(commits) > max_commits
    included_commits = commits[:max_commits] if max_commits is not None else commits
    budgeted_commits: list[dict[str, Any]] = []
    files_omitted = 0
    commits_with_files_truncated = 0

    for commit in included_commits:
        budgeted_commit = dict(commit)
        files = list(commit.get("files", []))
        if max_files_per_commit is not None and len(files) > max_files_per_commit:
            omitted = len(files) - max_files_per_commit
            files = files[:max_files_per_commit]
            files_omitted += omitted
            commits_with_files_truncated += 1
            budgeted_commit["truncated"] = {
                "files": True,
                "files_omitted": omitted,
            }
        budgeted_commit["files"] = files
        budgeted_commits.append(budgeted_commit)

    files_truncated = files_omitted > 0
    metadata = {
        "truncated": commits_truncated or files_truncated,
        "limits": {
            "max_commits": max_commits,
            "max_files_per_commit": max_files_per_commit,
        },
        "commits_included": len(budgeted_commits),
        "commits_truncated": commits_truncated,
        "more_commits_available": commits_truncated,
        "files_truncated": files_truncated,
        "commits_with_files_truncated": commits_with_files_truncated,
        "files_omitted": files_omitted,
    }
    return budgeted_commits, metadata


def _with_budget_metadata(
    commit_data: dict[str, Any],
    budget_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Add JSON-only budgeting metadata when budgeting flags were supplied."""
    if budget_metadata is None:
        return commit_data
    return {"_metadata": budget_metadata, **commit_data}


def _positive_int(value: str) -> int:
    """Parse a positive integer for CLI arguments."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _date_string(value: str) -> str:
    """Parse an ISO date string for exact report windows."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD")
    return value


def _write_output(text: str, output_path: str | None) -> bool:
    """Write text to output_path when provided. Return True when handled."""
    if output_path is None:
        return False
    Path(output_path).write_text(text, encoding="utf-8")
    return True


def _maybe_offer_copy(content: str) -> None:
    """Offer to copy printed output to the clipboard (interactive TTY only)."""
    if not sys.stdout.isatty() or not clipboard_available():
        return
    print("\nPress (c) to copy, or Enter to continue: ", end="", flush=True)
    try:
        key = read_single_key()
    except (OSError, KeyboardInterrupt):
        print()
        return
    print()
    if key.lower() == "c":
        if copy_to_clipboard(content):
            print("Copied to clipboard ✓")
        else:
            print("Could not access the clipboard.")


def _emit(content: str, output_path: str | None, printer) -> None:
    """Write content to a file, or print it and offer a clipboard copy."""
    if _write_output(content, output_path):
        return
    printer()
    _maybe_offer_copy(content)


def _pipx_binary() -> str | None:
    pipx = shutil.which("pipx")
    if pipx:
        return pipx
    local_pipx = Path.home() / ".local" / "bin" / "pipx"
    if local_pipx.exists():
        return str(local_pipx)
    return None


def _is_app_pipx_python(path: str) -> bool:
    normalized = str(Path(path)).replace("\\", "/")
    return f"/pipx/venvs/{DIST_NAME}/" in normalized


def _python_version_ok(path: str) -> bool:
    code = (
        "import sys; "
        f"raise SystemExit(0 if sys.version_info >= {MIN_PYTHON!r} else 1)"
    )
    try:
        completed = subprocess.run(
            [path, "-c", code],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return completed.returncode == 0


def _host_python() -> str | None:
    for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3", "python"):
        candidate = shutil.which(name)
        if candidate and not _is_app_pipx_python(candidate) and _python_version_ok(candidate):
            return candidate
    if not _is_app_pipx_python(sys.executable) and _python_version_ok(sys.executable):
        return sys.executable
    return None


def _python_pipx_available(python: str) -> bool:
    completed = subprocess.run(
        [python, "-m", "pipx", "--version"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _pipx_binary_available(pipx: str) -> bool:
    try:
        completed = subprocess.run(
            [pipx, "--version"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return completed.returncode == 0


def _pipx_update_command() -> list[str]:
    python = _host_python()
    if not python:
        return []
    pipx = _pipx_binary()
    if pipx and _pipx_binary_available(pipx):
        return [pipx, "reinstall", DIST_NAME, "--python", python]
    if _python_pipx_available(python):
        return [python, "-m", "pipx", "reinstall", DIST_NAME, "--python", python]
    return []


def _data_home() -> Path:
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home)
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data)
    return Path.home() / ".local" / "share"


def _pipx_bootstrap_dir() -> Path:
    return _data_home() / APP_NAME / "pipx-bootstrap"


def _bootstrap_pipx(python: str) -> str:
    print("pipx was not available; installing a private pipx helper and retrying...")
    venv_dir = _pipx_bootstrap_dir()
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([python, "-m", "venv", str(venv_dir)], check=True)
    if os.name == "nt":
        venv_python = venv_dir / "Scripts" / "python.exe"
        pipx = venv_dir / "Scripts" / "pipx.exe"
    else:
        venv_python = venv_dir / "bin" / "python"
        pipx = venv_dir / "bin" / "pipx"
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "pipx"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return str(pipx)


def _reinstall_with_bootstrapped_pipx(python: str) -> None:
    pipx = _bootstrap_pipx(python)
    subprocess.run([pipx, "reinstall", DIST_NAME, "--python", python], check=True)


def run_update() -> int:
    """Install the latest git-standup from GitHub via pipx."""
    print(f"Updating {APP_NAME} from GitHub...")
    command = _pipx_update_command()
    if not command:
        python = _host_python()
        if not python:
            print("Update failed: could not find a usable Python or pipx.", file=sys.stderr)
            return 1
        try:
            _reinstall_with_bootstrapped_pipx(python)
        except subprocess.CalledProcessError as exc:
            print(f"Update failed with exit code {exc.returncode}.", file=sys.stderr)
            return exc.returncode or 1
        print(f"{APP_NAME} updated. Run `{APP_NAME}` again to use the latest version.")
        return 0
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        python = (
            command[0]
            if len(command) >= 3 and command[1:3] == ["-m", "pipx"]
            else _host_python()
        )
        if python:
            try:
                _reinstall_with_bootstrapped_pipx(python)
            except subprocess.CalledProcessError as retry_exc:
                print(f"Update failed with exit code {retry_exc.returncode}.", file=sys.stderr)
                return retry_exc.returncode or 1
            print(f"{APP_NAME} updated. Run `{APP_NAME}` again to use the latest version.")
            return 0
        print(f"Update failed with exit code {exc.returncode}.", file=sys.stderr)
        return exc.returncode or 1
    print(f"{APP_NAME} updated. Run `{APP_NAME}` again to use the latest version.")
    return 0


def _installed_git_commit() -> str | None:
    try:
        distribution = importlib.metadata.distribution(DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        return None

    for file in distribution.files or []:
        if str(file).endswith("direct_url.json"):
            try:
                data = json.loads(distribution.locate_file(file).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            commit = data.get("vcs_info", {}).get("commit_id")
            return commit if isinstance(commit, str) else None
    return None


def _latest_git_commit(timeout: float = 3.0) -> str | None:
    git = shutil.which("git")
    if not git:
        return None
    try:
        result = subprocess.run(
            [git, "ls-remote", REPO_URL, "HEAD"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    fields = result.stdout.strip().split()
    return fields[0] if fields else None


def check_for_update() -> UpdateCheck:
    """Best-effort update check for pipx installs from GitHub."""
    if os.environ.get("GIT_STANDUP_SKIP_UPDATE_CHECK"):
        return UpdateCheck(available=False)
    current_commit = _installed_git_commit()
    latest_commit = _latest_git_commit()
    if not current_commit or not latest_commit:
        return UpdateCheck(False, current_commit, latest_commit)
    return UpdateCheck(current_commit != latest_commit, current_commit, latest_commit)


def prompt_for_update_if_available() -> bool:
    """Prompt in interactive flows. Return True when an update was attempted."""
    check = check_for_update()
    if not check.available:
        return False
    if Confirm.ask(f"New {APP_NAME} update found. Update now?", default=False):
        run_update()
        return True
    return False


def build_wizard_args(answers: dict[str, object]) -> list[str]:
    """Build deterministic git-standup arguments from wizard answers."""
    args: list[str] = []
    repo = str(answers.get("repo") or ".")
    if repo != ".":
        args.extend(["--repo", repo])

    preset = str(answers.get("preset") or "week")
    if preset == "today":
        args.extend(["--since", str(answers.get("since") or _today_string())])
    elif preset == "me":
        args.extend(["--author", "me"])
    elif preset == "me_week":
        args.extend(["--days", "7", "--author", "me"])
    elif preset == "branch":
        args.extend(["--base-branch", str(answers.get("base_branch") or "main")])
    elif preset == "custom":
        days = answers.get("days")
        since = answers.get("since")
        until = answers.get("until")
        author = answers.get("author")
        if since:
            args.extend(["--since", str(since)])
        elif days:
            args.extend(["--days", str(days)])
        if until:
            args.extend(["--until", str(until)])
    else:
        args.extend(["--days", "7"])

    authors = answers.get("authors")
    if isinstance(authors, list) and authors and preset not in {"me", "me_week"}:
        args.extend(["--author", "|".join(str(author) for author in authors)])
    author = answers.get("author")
    if author and preset not in {"me", "me_week"}:
        args.extend(["--author", str(author)])

    output_format = str(answers.get("format") or "text")
    use_ai = bool(answers.get("ai", True))
    if output_format == "json":
        args.append("--json")
    elif output_format == "markdown":
        args.append("--markdown")
        if not use_ai:
            args.append("--no-ai")
    elif not use_ai:  # plain text without AI
        args.append("--no-ai")

    output = answers.get("output")
    if output:
        args.extend(["--output", str(output)])
    return args


def _today_string() -> str:
    return date.today().isoformat()


def _default_output_path(output_format: str) -> str:
    if output_format == "markdown":
        return "standup.md"
    if output_format == "json":
        return "standup.json"
    return "standup.txt"


def _choice(message: str, choices: list[str], default: str) -> str:
    return Prompt.ask(message, choices=choices, default=default)


def _numbered_choice(
    message: str,
    options: list[tuple[str, str, str]],
    default: str,
) -> str:
    print(f"\n{message}:")
    default_index = "1"
    allowed: list[str] = []
    for index, (value, label, description) in enumerate(options, start=1):
        number = str(index)
        allowed.append(number)
        if value == default:
            default_index = number
        print(f"  {number}) {label} - {description}")

    choice = Prompt.ask(f"{message} choice", choices=allowed, default=default_index)
    return options[int(choice) - 1][0]


def _recent_authors(repo: str) -> list[str]:
    cmd = ["git"]
    if repo != ".":
        cmd.extend(["-C", repo])
    cmd.extend(["log", "--format=%an", "-n", "100"])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    authors: list[str] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        author = line.strip()
        if author and author not in seen:
            authors.append(author)
            seen.add(author)
    return authors[:12]


def _parse_author_selection(raw: str, authors: list[str]) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for token in (item.strip() for item in raw.split(",")):
        if not token:
            continue
        author = token
        if token.isdigit():
            index = int(token) - 1
            if 0 <= index < len(authors):
                author = authors[index]
        if author not in seen:
            selected.append(author)
            seen.add(author)
    return selected


def _choose_authors(repo: str) -> list[str]:
    authors = _recent_authors(repo)
    if authors:
        print("\nChoose authors:")
        for index, author in enumerate(authors, start=1):
            print(f"  {index}) {author}")
        raw = Prompt.ask("Author choices (comma-separated numbers or names/emails)", default="")
        return _parse_author_selection(raw, authors)

    raw = Prompt.ask("Author names or emails (comma-separated)", default="")
    return _parse_author_selection(raw, [])


def _format_command(args: list[str]) -> str:
    return "git-standup " + " ".join(shlex.quote(item) for item in args)


def _provider_defaults(provider: str) -> tuple[str, str]:
    for spec in PROVIDER_SPECS:
        if spec.provider == provider:
            return spec.base_url, spec.text_model
    if provider == "custom":
        return "https://api.openai.com/v1", "gpt-4o-mini"
    raise ValueError(f"Unknown provider: {provider}")


def _harness_defaults(harness: str) -> tuple[str, str]:
    if harness == "ollama":
        return "http://localhost:11434/v1", "llama3.1"
    if harness == "lms":
        return "http://localhost:1234/v1", "local-model"
    return "", ""


def _provider_key_env(provider: str) -> str:
    """Return the environment variable name for a provider's API key."""
    for spec in PROVIDER_SPECS:
        if spec.provider == provider:
            return spec.key_names[0]
    return "OPENAI_API_KEY"


def _prompt_api_key(provider: str) -> None:
    """Prompt for an API key, set it for this run, and offer to persist it."""
    key_name = _provider_key_env(provider)
    key = getpass.getpass(f"{key_name} (hidden; leave blank to skip): ").strip()
    if not key:
        print(f"Skipped key entry. Set {key_name} in your environment to use AI mode.")
        return
    os.environ[key_name] = key
    if Confirm.ask(f"Save {key_name} to your shell profile for future runs?", default=False):
        target = persist_env_var(key_name, key)
        if target == "setx":
            print(f"Saved {key_name} via setx. Open a new terminal to load it.")
        elif target:
            print(f"Saved {key_name} to {target}. Restart your shell or run: source {target}")
        else:
            print(f"Could not persist {key_name}; it is set for this run only.")
    else:
        print(f"{key_name} is set for this run only. To persist it yourself, add:")
        print(f'  export {key_name}="{key}"')


def _ai_provider_available(ai_report: dict[str, Any]) -> bool:
    """Return True when any AI provider, CLI harness, or saved config exists."""
    if ai_report.get("api_keys") or ai_report.get("cli_harnesses"):
        return True
    try:
        config = load_config(config_path())
    except ValueError:
        config = None
    return bool(config and (config.provider or config.harness))


def configure_ai_interactive(
    path: Path,
    *,
    kind: str | None = None,
    provider: str | None = None,
    harness: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    allow_key: bool = True,
) -> AIConfig | None:
    """Configure and persist an AI provider or CLI harness.

    Prompts interactively for any value not supplied. When ``allow_key`` is set
    and a provider is chosen, also prompts for an API key, exports it for the
    current run, and offers to persist it. Returns the saved AIConfig.
    """
    if kind is None:
        if harness:
            kind = "cli"
        elif provider:
            kind = "provider"
        else:
            kind = _choice("Set up AI using", ["provider", "cli"], "provider")

    if kind == "cli":
        chosen = harness or _choice("CLI harness", ["codex", "ollama", "lms"], "codex")
        default_base_url, default_model = _harness_defaults(chosen)
        config = AIConfig(
            harness=chosen,
            base_url=base_url or default_base_url,
            model=model or default_model,
        )
    else:
        chosen = provider or _choice(
            "Provider",
            [spec.provider for spec in PROVIDER_SPECS] + ["custom"],
            "openai",
        )
        default_base_url, default_model = _provider_defaults(chosen)
        if chosen == "custom" and not base_url:
            default_base_url = Prompt.ask("OpenAI-compatible base URL", default=default_base_url)
        config = AIConfig(
            provider=chosen,
            base_url=base_url or default_base_url,
            model=model or default_model,
        )
        if allow_key:
            _prompt_api_key(chosen)

    written = save_config(path, config)
    print(f"Saved AI defaults to {written}")
    return config


def _print_config(config: AIConfig, path: Path) -> None:
    print(f"path: {path}")
    for field in ("provider", "base_url", "model", "harness"):
        value = getattr(config, field)
        if value:
            print(f"{field}: {value}")


def run_config_command(args: argparse.Namespace) -> int:
    """Show, reset, or update saved AI defaults."""
    path = config_path()
    action = args.config_action or "interactive"
    if action == "show":
        config = load_config(path)
        if config is None:
            print(f"No config found at {path}")
        else:
            _print_config(config, path)
        return 0

    if action == "reset":
        if reset_config(path):
            print(f"Removed config: {path}")
        else:
            print(f"No config found at {path}")
        return 0

    if action == "set-provider":
        configure_ai_interactive(
            path,
            kind="provider",
            provider=args.provider,
            base_url=args.base_url,
            model=args.model,
            allow_key=False,
        )
        return 0

    if action == "set-cli":
        configure_ai_interactive(
            path,
            kind="cli",
            harness=args.harness,
            base_url=args.base_url,
            model=args.model,
            allow_key=False,
        )
        return 0

    if action == "interactive":
        configure_ai_interactive(path, allow_key=False)
        return 0

    print(f"Unknown config action: {action}", file=sys.stderr)
    return 2


def run_wizard() -> int:
    """Interactive command builder for git-standup."""
    repo = Prompt.ask("Repository path", default=".")
    preset = _numbered_choice(
        "Review changes from",
        [
            ("today", "Today", "Changes since today began."),
            ("week", "This week", "Last 7 days."),
            ("custom", "Custom range", "Choose how many days to review."),
            ("branch", "Branch changes", "Compare this branch against a base branch."),
        ],
        "week",
    )
    author_choice = _numbered_choice(
        "By who",
        [
            ("all", "Everyone", "All contributors."),
            ("me", "Me", "Only commits authored by me."),
            ("custom", "Someone else", "Pick one or more authors."),
        ],
        "all",
    )
    ai_report = detect_ai_environment(os.environ)
    answers: dict[str, object] = {
        "repo": repo,
        "preset": preset,
    }
    if author_choice == "me":
        answers["author"] = "me"
    elif author_choice == "custom":
        authors = _choose_authors(repo)
        if authors:
            answers["authors"] = authors
    if ai_report["cli_harnesses"]:
        print("Detected AI CLIs: " + ", ".join(ai_report["cli_harnesses"]))
    if preset == "branch":
        answers["base_branch"] = Prompt.ask("Base branch", default="main")
    elif preset == "custom":
        answers["days"] = Prompt.ask("Days of history", default="7")

    answers["format"] = _numbered_choice(
        "Output format",
        [
            ("markdown", "Markdown", "Paste-ready for Slack, Notion, or GitHub."),
            ("text", "Plain text", "Simple terminal summary."),
            ("json", "JSON", "Structured data for scripts or automation."),
        ],
        "markdown",
    )

    if answers["format"] == "json":
        answers["ai"] = False
    elif _ai_provider_available(ai_report):
        answers["ai"] = Confirm.ask("Polish with AI?", default=True)
    else:
        ai_choice = _numbered_choice(
            "Polish with AI? No AI provider detected",
            [
                ("setup", "Set one up now", "Configure an AI provider or CLI."),
                ("skip", "Skip AI", "Use raw output without AI."),
            ],
            "setup",
        )
        if ai_choice == "setup":
            answers["ai"] = configure_ai_interactive(config_path()) is not None
        else:
            answers["ai"] = False

    if Confirm.ask("Save report to a file?", default=False):
        output_format = str(answers["format"])
        answers["output"] = Prompt.ask("Save as", default=_default_output_path(output_format))

    args = build_wizard_args(answers)
    print(f"\nGenerated command:\n  {_format_command(args)}\n")
    if Confirm.ask("Run it now", default=True):
        return main(args)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="git-standup",
        description="AI-powered weekly standup generator. Analyze git history and "
        "generate standup summaries.",
        epilog="Examples:\n"
        "  git-standup wizard              # Build the right command interactively\n"
        "  git-standup config              # Choose saved AI defaults\n"
        "  git-standup config show         # Show saved AI defaults\n"
        "  git-standup update              # Update git-standup from GitHub\n"
        "  git-standup                     # Last 7 days, all contributors\n"
        "  git-standup me                  # My commits, no AI required\n"
        "  git-standup branch              # Current branch vs main, no AI required\n"
        "  git-standup ../api --markdown   # Run against another repository\n"
        "  git-standup --days 1            # Yesterday only\n"
        "  git-standup --repo ../api       # Run against another repository\n"
        "  git-standup --since 2026-01-01 --until 2026-01-07\n"
        "  git-standup --author me         # My commits only\n"
        "  git-standup --no-ai             # Text summary without AI\n"
        "  git-standup --markdown          # AI-polished Markdown summary\n"
        "  git-standup --markdown --no-ai  # Raw Markdown summary without AI\n"
        "  git-standup --json              # Raw JSON output\n"
        "  git-standup --max-commits 20 --max-files-per-commit 10\n"
        "  git-standup --markdown --output standup.md\n"
        "  git-standup --api-key sk-...    # Custom API key\n"
        "  git-standup --model gpt-4       # Custom model\n"
        "  git-standup --base-url https://api.openai.com/v1  # Custom endpoint",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "tokens",
        nargs="*",
        help=(
            "Optional command, preset (wizard, config, update, me, week, branch), "
            "or repository path"
        ),
    )
    parser.add_argument(
        "--days",
        type=_positive_int,
        default=7,
        help="Number of days of git history to include (default: 7)",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Path to the git repository to analyze (default: current directory)",
    )
    parser.add_argument(
        "--since",
        type=_date_string,
        default=None,
        help="Start date for the report window (YYYY-MM-DD). Overrides --days.",
    )
    parser.add_argument(
        "--until",
        type=_date_string,
        default=None,
        help="End date for the report window (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--author",
        type=str,
        default=None,
        help="Filter commits by author. Use 'me' for the current git user.",
    )
    parser.add_argument(
        "--base-branch",
        type=str,
        default=None,
        help="Base branch for comparing changes (e.g., 'main'). Shows commits "
        "in current branch not in base.",
    )
    parser.add_argument(
        "--max-commits",
        type=_positive_int,
        default=None,
        help="Maximum commits to include in output and AI input",
    )
    parser.add_argument(
        "--max-files-per-commit",
        type=_positive_int,
        default=None,
        help="Maximum changed files to include per commit in output and AI input",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON (no AI processing)",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip AI; output a raw formatted summary",
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        help="Force AI mode (default for text/markdown unless --no-ai; ignored with --json)",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Output a Markdown summary (AI-polished unless --no-ai)",
    )
    parser.add_argument(
        "--output", "--out",
        type=str,
        default=None,
        help="Write the generated JSON, Markdown, text, or AI summary to a file",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key for the LLM provider (default: OPENAI_API_KEY env var)",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="Provider name for AI config or one-off AI resolution",
    )
    parser.add_argument(
        "--harness",
        type=str,
        default=None,
        help="CLI harness name for config set-cli, such as codex, ollama, or lms",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model name to use (default: gpt-4o-mini)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="Base URL for OpenAI-compatible API (default: https://api.openai.com/v1)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"git-standup {__version__}",
    )
    parser.add_argument(
        "--no-wizard",
        action="store_true",
        help="Run the default report instead of the interactive guide",
    )

    args = parser.parse_args(argv)
    args.command = None
    tokens = list(args.tokens)
    args.config_action = None
    if tokens:
        target = tokens.pop(0)
        if target == "wizard":
            args.command = "wizard"
            if tokens:
                parser.error("wizard does not accept extra arguments")
        elif target == "update":
            args.command = "update"
            if tokens:
                parser.error("update does not accept extra arguments")
        elif target == "config":
            args.command = "config"
            args.config_action = tokens.pop(0) if tokens else "interactive"
            if tokens:
                parser.error("config accepts at most one action")
        elif target == "me":
            args.author = "me"
            args.no_ai = True
        elif target == "week":
            args.days = 7
            args.no_ai = True
        elif target == "branch":
            if args.base_branch is None:
                args.base_branch = "main"
            args.no_ai = True
        else:
            if tokens:
                parser.error("expected at most one repository path")
            if args.repo is not None:
                parser.error(
                    "provide a repository path either positionally or with --repo, not both"
                )
            args.repo = target
    del args.tokens
    return args


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(argv)

    if args.command == "update":
        return run_update()

    if args.command == "wizard" or (
        not raw_argv
        and not args.no_wizard
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    ):
        if prompt_for_update_if_available():
            return 0
        return run_wizard()

    if args.command == "config":
        return run_config_command(args)

    try:
        commit_fetch_limit = (
            args.max_commits + 1 if args.max_commits is not None else None
        )
        commits = get_commits(
            days=args.days,
            author=args.author,
            base_branch=args.base_branch,
            repo_path=args.repo,
            since=args.since,
            until=args.until,
            max_commits=commit_fetch_limit,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not commits:
        print("No commits found in the specified time range.")
        return 0

    commits, budget_metadata = _apply_output_budget(
        commits,
        max_commits=args.max_commits,
        max_files_per_commit=args.max_files_per_commit,
    )
    commit_data = _build_commit_data(commits)

    if args.json:
        if args.ai:
            print(
                "Warning: --ai has no effect with --json; JSON is always raw.",
                file=sys.stderr,
            )
        output = build_json_output(_with_budget_metadata(commit_data, budget_metadata))
        _emit(output + "\n", args.output, lambda: print(output))
        return 0

    output_format = "markdown" if args.markdown else "text"

    def _emit_raw() -> None:
        """Emit the raw (non-AI) formatter output for the chosen format."""
        if output_format == "markdown":
            markdown = build_markdown_output(commit_data)
            _emit(markdown, args.output, lambda: print(markdown, end=""))
        else:
            _emit(
                build_text_output(commit_data),
                args.output,
                lambda: print_text_standup(commit_data),
            )

    if args.no_ai:
        _emit_raw()
        return 0

    # AI mode
    try:
        user_config = load_config(config_path())
    except ValueError as exc:
        print(f"Error: invalid AI config: {exc}", file=sys.stderr)
        return 1
    connection = resolve_ai_connection(
        api_key_arg=args.api_key,
        base_url_arg=args.base_url,
        model_arg=args.model,
        env=os.environ,
        config=user_config,
        provider_arg=args.provider,
    )
    try:
        if connection.provider == "codex":
            standup_text = generate_standup_with_harness(
                commit_data=commit_data,
                harness=connection.provider,
                model=connection.model,
                budget_metadata=budget_metadata,
                output_format=output_format,
            )
        else:
            standup_text = generate_standup(
                commit_data=commit_data,
                api_key=connection.api_key,
                model=connection.model,
                base_url=connection.base_url,
                budget_metadata=budget_metadata,
                output_format=output_format,
            )
    except RuntimeError as exc:
        # Fall back to the raw formatter for the chosen format if AI fails.
        print(
            f"Warning: AI generation failed ({exc}). Showing raw summary instead.\n",
            file=sys.stderr,
        )
        _emit_raw()
        return 1

    _emit(standup_text.rstrip() + "\n", args.output, lambda: print_ai_standup(standup_text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
