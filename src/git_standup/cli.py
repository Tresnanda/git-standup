"""CLI entry point for git-standup."""

import argparse
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
from git_standup.config import AIConfig, config_path, load_config, reset_config, save_config
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
            date_data[date_key] = {
                "commits": [
                    {
                        "hash": c.get("hash", ""),
                        "subject": c.get("subject", ""),
                        "body": c.get("body", ""),
                        "files": c.get("files", []),
                    }
                    for c in day_commits
                ],
                "stats": stats,
            }
        result[author] = date_data

    return result


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

    author = answers.get("author")
    if author and preset not in {"me", "me_week"}:
        args.extend(["--author", str(author)])

    output_format = str(answers.get("format") or "text")
    if output_format == "markdown":
        args.append("--markdown")
    elif output_format == "json":
        args.append("--json")
    elif output_format == "text":
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


def _format_command(args: list[str]) -> str:
    return "git-standup " + " ".join(shlex.quote(item) for item in args)


def _provider_defaults(provider: str) -> tuple[str, str]:
    for spec in PROVIDER_SPECS:
        if spec.provider == provider:
            return spec.base_url, spec.text_model
    if provider == "custom":
        return "https://api.openai.com/v1", "gpt-4o-mini"
    raise ValueError(f"Unknown provider: {provider}")


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
        provider = args.provider
        if not provider:
            provider = _choice(
                "Provider",
                [spec.provider for spec in PROVIDER_SPECS] + ["custom"],
                "openai",
            )
        default_base_url, default_model = _provider_defaults(provider)
        if provider == "custom" and not args.base_url:
            default_base_url = Prompt.ask("OpenAI-compatible base URL", default=default_base_url)
        config = AIConfig(
            provider=provider,
            base_url=args.base_url or default_base_url,
            model=args.model or default_model,
        )
        written = save_config(path, config)
        print(f"Saved AI defaults to {written}")
        return 0

    if action == "set-cli":
        harness = args.harness or _choice("CLI harness", ["codex", "ollama", "lms"], "codex")
        default_model = ""
        default_base_url = ""
        if harness == "ollama":
            default_model = "llama3.1"
            default_base_url = "http://localhost:11434/v1"
        elif harness == "lms":
            default_model = "local-model"
            default_base_url = "http://localhost:1234/v1"
        config = AIConfig(
            harness=harness,
            base_url=args.base_url or default_base_url,
            model=args.model or default_model,
        )
        written = save_config(path, config)
        print(f"Saved AI defaults to {written}")
        return 0

    if action == "interactive":
        mode = _choice("Default type", ["provider", "cli"], "provider")
        args.config_action = "set-cli" if mode == "cli" else "set-provider"
        return run_config_command(args)

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
            ("custom", "Someone else", "Type an author name or email."),
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
        author = Prompt.ask("Author name or email", default="")
        if author:
            answers["author"] = author
    if ai_report["cli_harnesses"]:
        print("Detected AI CLIs: " + ", ".join(ai_report["cli_harnesses"]))
    if preset == "branch":
        answers["base_branch"] = Prompt.ask("Base branch", default="main")
    elif preset == "custom":
        answers["days"] = Prompt.ask("Days of history", default="7")

    answers["format"] = _numbered_choice(
        "Output style",
        [
            (
                "ai",
                "AI summary",
                "Use your configured AI provider or CLI for a polished draft.",
            ),
            (
                "markdown",
                "Markdown",
                "Paste-ready Markdown for Slack, Notion, GitHub, or a file.",
            ),
            ("text", "Plain text", "Simple terminal summary without AI."),
            ("json", "JSON", "Structured data for scripts, dashboards, or automation."),
        ],
        "ai",
    )

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
        "  git-standup --no-ai             # Text summary without LLM\n"
        "  git-standup --markdown          # Markdown summary without LLM\n"
        "  git-standup --json              # Raw JSON output\n"
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
        "--json",
        action="store_true",
        help="Output raw JSON (no AI processing)",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Output formatted text summary without AI",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Output a paste-ready Markdown summary without AI",
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
        commits = get_commits(
            days=args.days,
            author=args.author,
            base_branch=args.base_branch,
            repo_path=args.repo,
            since=args.since,
            until=args.until,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not commits:
        print("No commits found in the specified time range.")
        return 0

    commit_data = _build_commit_data(commits)

    if args.json:
        output = build_json_output(commit_data)
        if not _write_output(output + "\n", args.output):
            print(output)
        return 0

    if args.no_ai:
        if not _write_output(build_text_output(commit_data), args.output):
            print_text_standup(commit_data)
        return 0

    if args.markdown:
        output = build_markdown_output(commit_data)
        if not _write_output(output, args.output):
            print(output, end="")
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
            )
        else:
            standup_text = generate_standup(
                commit_data=commit_data,
                api_key=connection.api_key,
                model=connection.model,
                base_url=connection.base_url,
            )
    except RuntimeError as exc:
        # Fall back to text summary if AI fails
        print(
            f"Warning: AI generation failed ({exc}). Showing text summary instead.\n",
            file=sys.stderr,
        )
        if not _write_output(build_text_output(commit_data), args.output):
            print_text_standup(commit_data)
        return 1

    if not _write_output(standup_text.rstrip() + "\n", args.output):
        print_ai_standup(standup_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
