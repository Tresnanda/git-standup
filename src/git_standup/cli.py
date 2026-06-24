"""CLI entry point for git-standup."""

import argparse
import getpass
import importlib.metadata
import json
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Confirm, Prompt

from git_standup import __version__
from git_standup.ai import generate_standup, generate_standup_with_harness
from git_standup.ai_env import (
    CLI_HARNESS_SPECS,
    CONFIGURABLE_CLI_HARNESSES,
    PROVIDER_SPECS,
    detect_ai_environment,
    mask_secret,
    resolve_ai_connection,
)
from git_standup.author_aliases import (
    AuthorAliases,
    canonicalize_commit_authors,
    parse_alias_assignments,
)
from git_standup.checkpoint import (
    CheckpointUpdate,
    checkpoint_path,
    checkpoint_since,
    load_checkpoints,
    local_repository_id,
    remote_repository_id,
    update_checkpoints,
)
from git_standup.clipboard import clipboard_available, copy_to_clipboard, read_single_key
from git_standup.config import AIConfig, config_path, load_config, reset_config, save_config
from git_standup.env_persist import persist_env_var
from git_standup.formatter import (
    TEAM_DIGEST_TEMPLATES,
    build_changelog_output,
    build_insights_output,
    build_json_output,
    build_markdown_output,
    build_stats_output,
    build_team_digest_output,
    build_text_output,
    build_workflow_board_output,
    print_ai_standup,
    print_text_standup,
)
from git_standup.github_api import (
    GitHubApiRunCache,
    _normalize_repo_slug,
    get_remote_commits,
    validate_remote_api_options,
)
from git_standup.gitlog import (
    compute_stats,
    describe_commit_quality,
    get_commits,
    get_repo_root,
    group_by_author,
    group_by_date,
)
from git_standup.prs import enrich_commits_with_prs

APP_NAME = "git-standup"
DIST_NAME = "git-standup"
REPO_URL = "https://github.com/Tresnanda/git-standup.git"
REPO_SPEC = f"git+{REPO_URL}"
MIN_PYTHON = (3, 10)
REPOSITORIES_KEY = "_repositories"
_RAW_TERMINAL_FD: int | None = None
_RAW_TERMINAL_COOKED: Any = None
_ADD_CUSTOM_REPO_LABEL = "+ Add custom repo (URL or owner/name)…"


class _WizardCancelled(Exception):
    """Raised when the user cancels an interactive picker."""


@dataclass
class UpdateCheck:
    available: bool
    current_commit: str | None = None
    latest_commit: str | None = None


@dataclass(frozen=True)
class _CheckpointTarget:
    repository_id: str
    label: str


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
                if c.get("pull_request"):
                    item["pull_request"] = c["pull_request"]
                if c.get("github_api"):
                    item["github_api"] = c["github_api"]
                if c.get("issues"):
                    item["issues"] = c["issues"]
                quality = c.get("quality") or describe_commit_quality(c)
                if quality:
                    item["quality"] = quality
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


def _with_commit_quality(commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach low-signal commit metadata to flat commit lists."""
    annotated: list[dict[str, Any]] = []
    for commit in commits:
        item = dict(commit)
        quality = describe_commit_quality(item)
        if quality:
            item["quality"] = quality
        annotated.append(item)
    return annotated


def _with_commit_quality_in_data(commit_data: dict[str, Any]) -> dict[str, Any]:
    """Attach low-signal commit metadata inside grouped commit data."""
    annotated: dict[str, Any] = {}
    for repo_name, repo_data in commit_data.items():
        if repo_name == REPOSITORIES_KEY and isinstance(repo_data, dict):
            annotated[repo_name] = {
                name: _with_commit_quality_in_data(data)
                for name, data in repo_data.items()
                if isinstance(data, dict)
            }
            continue
        if not isinstance(repo_data, dict):
            annotated[repo_name] = repo_data
            continue
        annotated_days: dict[str, Any] = {}
        for date_key, day_data in repo_data.items():
            if not isinstance(day_data, dict):
                annotated_days[date_key] = day_data
                continue
            annotated_day = dict(day_data)
            commits = day_data.get("commits", [])
            if isinstance(commits, list):
                annotated_day["commits"] = _with_commit_quality(commits)
            annotated_days[date_key] = annotated_day
        annotated[repo_name] = annotated_days
    return annotated


def _with_json_metadata(
    commit_data: dict[str, Any],
    budget_metadata: dict[str, Any] | None,
    provenance_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Add JSON-only provenance and optional budget metadata."""
    metadata: dict[str, Any] = dict(provenance_metadata)
    if budget_metadata is not None:
        metadata.update(budget_metadata)
    return {"_metadata": metadata, **commit_data}


def _resolve_author_aliases(
    config: AIConfig | None,
    cli_values: list[str] | None,
) -> AuthorAliases:
    """Merge saved author aliases with one-off CLI alias assignments."""
    aliases = AuthorAliases.from_mapping(config.author_aliases if config else {})
    return aliases.merge(parse_alias_assignments(cli_values))


def _generated_timestamp() -> str:
    """Return the JSON report generation time as a UTC ISO-8601 timestamp."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _build_json_provenance_metadata(args: argparse.Namespace) -> dict[str, Any]:
    """Build JSON-only report provenance metadata from parsed CLI options."""
    if args.remote_repos:
        repository_metadata = {
            "type": "remote",
            "repositories": list(args.remote_repos),
            "backend": args.remote_backend,
        }
    else:
        repository_metadata = {
            "type": "local",
            "path": args.repo or ".",
        }

    return {
        "generated_at": _generated_timestamp(),
        "query_window": {
            "days": args.days,
            "since": args.since,
            "until": args.until,
        },
        "author": args.author,
        "base_branch": args.base_branch,
        "exclude_merges": bool(args.exclude_merges),
        "include_prs": bool(args.include_prs),
        "pathspecs": list(args.pathspecs or []),
        "repository": repository_metadata,
    }


def _build_multi_repo_commit_data(
    repo_commits: list[tuple[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    return {
        REPOSITORIES_KEY: {
            repo_name: _build_commit_data(commits) for repo_name, commits in repo_commits
        }
    }


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
    """Parse a Git-compatible date or timestamp for exact report windows."""
    date_pattern = r"\d{4}-\d{2}-\d{2}"
    timestamp_pattern = rf"{date_pattern} \d{{2}}:\d{{2}}:\d{{2}} [+-]\d{{4}}"
    if not re.match(rf"^(?:{date_pattern}|{timestamp_pattern})$", value):
        raise argparse.ArgumentTypeError(
            "must use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS +0000"
        )
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


def _print_markdown(content: str) -> None:
    """Render Markdown for interactive terminals; keep raw Markdown for pipes."""
    if sys.stdout.isatty():
        Console().print(Markdown(content))
    else:
        print(content, end="")


def _emit_markdown(content: str, output_path: str | None) -> None:
    """Emit Markdown as rendered terminal output while preserving raw copy/file text."""
    _emit(content, output_path, lambda: _print_markdown(content))


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
    # Keep the updater prompt line-based so a broken old key reader cannot trap
    # users before they get a chance to install the fix.
    if Confirm.ask(f"New {APP_NAME} update found. Update now?", default=False):
        run_update()
        return True
    return False


def build_wizard_args(answers: dict[str, object]) -> list[str]:
    """Build deterministic git-standup arguments from wizard answers."""
    args: list[str] = []
    remote_repos = answers.get("remote_repos")
    if isinstance(remote_repos, list) and remote_repos:
        for repo in remote_repos:
            args.extend(["--remote-repo", str(repo)])
    else:
        repo = str(answers.get("repo") or ".")
        if repo != ".":
            args.extend(["--repo", repo])

    preset = str(answers.get("preset") or "week")
    if preset == "today":
        args.extend(["--since", str(answers.get("since") or _today_start_string())])
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
    elif output_format == "changelog":
        args.append("--changelog")
    elif output_format == "insights":
        args.append("--insights")
    elif output_format == "stats":
        args.append("--stats-only")
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


def _today_start_string(now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    today_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start.strftime("%Y-%m-%d %H:%M:%S %z")


def _checkpoint_timestamp(now: datetime | None = None) -> str:
    """Return the timestamp format stored for since-last checkpoints."""
    current = now or datetime.now().astimezone()
    return current.strftime("%Y-%m-%d %H:%M:%S %z")


def _remote_checkpoint_target(remote_repo: str) -> _CheckpointTarget:
    label = _remote_repo_label(remote_repo)
    return _CheckpointTarget(remote_repository_id(label), label)


def _checkpoint_targets(args: argparse.Namespace) -> list[_CheckpointTarget]:
    """Build repository checkpoint targets for local or remote report inputs."""
    if args.remote_repos:
        return [_remote_checkpoint_target(remote_repo) for remote_repo in args.remote_repos]
    repo_root = get_repo_root(args.repo)
    return [_CheckpointTarget(local_repository_id(repo_root), repo_root)]


def _since_last_by_target(targets: list[_CheckpointTarget]) -> dict[str, str]:
    """Load since-last timestamps for all targets, or raise when any are missing."""
    path = checkpoint_path()
    data = load_checkpoints(path)
    missing: list[str] = []
    since_by_id: dict[str, str] = {}
    for target in targets:
        since = checkpoint_since(data, target.repository_id)
        if since is None:
            missing.append(target.label)
        else:
            since_by_id[target.repository_id] = since
    if missing:
        missing_text = ", ".join(missing)
        raise RuntimeError(
            f"No since-last checkpoint found for {missing_text}. "
            "Run a report with --write-checkpoint first, or use --since."
        )
    return since_by_id


def _write_report_checkpoints(
    targets: list[_CheckpointTarget],
    timestamp: str,
) -> None:
    """Persist a successful report checkpoint for all report repositories."""
    update_checkpoints(
        [
            CheckpointUpdate(target.repository_id, timestamp, target.label)
            for target in targets
        ],
        checkpoint_path(),
    )


def _default_output_path(output_format: str) -> str:
    if output_format == "changelog":
        return "changelog.md"
    if output_format == "insights":
        return "standup-insights.md"
    if output_format == "stats":
        return "standup-stats.txt"
    if output_format == "markdown":
        return "standup.md"
    if output_format == "json":
        return "standup.json"
    return "standup.txt"


def _choice(message: str, choices: list[str], default: str) -> str:
    if sys.stdin.isatty() and sys.stdout.isatty():
        return _interactive_choice(
            message,
            [(choice, choice, "") for choice in choices],
            default,
        )
    return Prompt.ask(message, choices=choices, default=default)


def _numbered_choice(
    message: str,
    options: list[tuple[str, str, str]],
    default: str,
) -> str:
    if sys.stdin.isatty() and sys.stdout.isatty():
        return _interactive_choice(message, options, default)

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


def _read_terminal_key() -> str:
    if not sys.stdin.isatty():
        return read_single_key()

    try:  # Windows
        import msvcrt  # type: ignore[import-not-found]

        key = msvcrt.getwch()
        if key in {"\x00", "\xe0"}:
            return key + msvcrt.getwch()
        return key
    except ImportError:
        pass

    try:  # Unix
        import termios
        import tty
    except ImportError:
        return read_single_key()

    fd = sys.stdin.fileno()
    if _RAW_TERMINAL_FD == fd:
        return _read_terminal_key_from_fd(fd)

    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return _read_terminal_key_from_fd(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _read_terminal_key_from_fd(fd: int) -> str:
    key = os.read(fd, 1).decode(errors="ignore")
    if key == "\x1b":
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            return key
        key += os.read(fd, 1).decode(errors="ignore")
        if key[-1:] in {"[", "O"}:
            for _ in range(8):
                ready, _, _ = select.select([fd], [], [], 0.01)
                if not ready:
                    break
                next_char = os.read(fd, 1).decode(errors="ignore")
                key += next_char
                if next_char.isalpha() or next_char == "~":
                    break
    return key


@contextmanager
def _raw_terminal_session(enabled: bool):
    global _RAW_TERMINAL_FD, _RAW_TERMINAL_COOKED
    if not enabled or not sys.stdin.isatty() or sys.platform.startswith("win"):
        yield
        return

    try:
        import termios
        import tty
    except ImportError:
        yield
        return

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    previous_fd = _RAW_TERMINAL_FD
    previous_cooked = _RAW_TERMINAL_COOKED
    try:
        tty.setcbreak(fd)
        _RAW_TERMINAL_FD = fd
        _RAW_TERMINAL_COOKED = old_settings
        yield
    finally:
        _RAW_TERMINAL_FD = previous_fd
        _RAW_TERMINAL_COOKED = previous_cooked
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


@contextmanager
def _suspended_raw_terminal():
    """Temporarily restore cooked mode so a normal prompt can run mid-picker."""
    fd = _RAW_TERMINAL_FD
    if fd is None or _RAW_TERMINAL_COOKED is None:
        yield
        return

    import termios
    import tty

    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, _RAW_TERMINAL_COOKED)
        yield
    finally:
        tty.setcbreak(fd)


def _move_cursor_up(lines: int) -> None:
    if lines > 0:
        print(f"\x1b[{lines}F\x1b[J", end="")


def _terminal_lines() -> int:
    return shutil.get_terminal_size((80, 24)).lines


def _wizard_separator() -> None:
    """Print a dim full-width rule between wizard steps."""
    width = shutil.get_terminal_size((80, 24)).columns
    Console().print("─" * width, style="dim")


@contextmanager
def _spinner(message: str):
    """Show an animated spinner on a TTY, else print the message once."""
    if sys.stdout.isatty():
        with Console().status(message, spinner="dots"):
            yield
    else:
        print(message)
        yield


def _collapse_summary(title: str, summary: str, rendered_lines: int) -> None:
    """Erase a finished picker frame and leave a one-line summary behind."""
    _move_cursor_up(rendered_lines)
    print(f"{title}: \x1b[32m✓\x1b[0m {summary}")


def _picker_window(cursor: int, option_count: int) -> tuple[int, int, bool]:
    page_size = max(5, min(option_count, _terminal_lines() - 4))
    if option_count <= page_size:
        return 0, option_count, False

    start = min(
        max(0, cursor - page_size // 2),
        option_count - page_size,
    )
    return start, start + page_size, True


def _interactive_choice(
    title: str,
    options: list[tuple[str, str, str]],
    default: str,
    *,
    key_reader=_read_terminal_key,
) -> str:
    if not options:
        raise ValueError("options must not be empty")

    cursor = next(
        (index for index, (value, _label, _description) in enumerate(options) if value == default),
        0,
    )
    rendered_lines = 0

    def render() -> None:
        nonlocal rendered_lines
        _move_cursor_up(rendered_lines)
        start, end, paged = _picker_window(cursor, len(options))
        print(f"{title}:")
        print("Use Up/Down to move, Enter to choose, q to cancel.")
        for index in range(start, end):
            _value, label, description = options[index]
            pointer = ">" if index == cursor else " "
            suffix = f" - {description}" if description else ""
            print(f"{pointer} {label}{suffix}")
        footer_lines = 0
        if paged:
            print(f"Showing {start + 1}-{end} of {len(options)}.")
            footer_lines = 1
        rendered_lines = (end - start) + 2 + footer_lines

    with _raw_terminal_session(key_reader is _read_terminal_key):
        while True:
            render()
            key = key_reader()
            if key in {"\r", "\n"}:
                _collapse_summary(title, options[cursor][1], rendered_lines)
                return options[cursor][0]
            if key.lower() == "q":
                raise _WizardCancelled
            if key in {"\x1b[B", "\x1bOB", "\xe0P", "\x00P", "j"}:
                cursor = (cursor + 1) % len(options)
            elif key in {"\x1b[A", "\x1bOA", "\xe0H", "\x00H", "k"}:
                cursor = (cursor - 1) % len(options)


def _interactive_confirm(
    title: str,
    *,
    default: bool,
    key_reader=_read_terminal_key,
) -> bool:
    return (
        _interactive_choice(
            title,
            [
                ("yes", "Yes", ""),
                ("no", "No", ""),
            ],
            "yes" if default else "no",
            key_reader=key_reader,
        )
        == "yes"
    )


def _confirm(message: str, *, default: bool) -> bool:
    if sys.stdin.isatty() and sys.stdout.isatty():
        return _interactive_confirm(message, default=default)
    return Confirm.ask(message, default=default)


def _interactive_multi_select(
    title: str,
    options: list[str],
    *,
    key_reader=_read_terminal_key,
    add_label: str | None = None,
    add_prompt=None,
) -> list[str]:
    if not options and add_label is None:
        return []

    # When an add action is offered it occupies row 0; the real options follow.
    display: list[str] = ([add_label] if add_label else []) + list(options)
    add_index = 0 if add_label else None
    selected: set[int] = set()
    cursor = 1 if add_label and len(display) > 1 else 0
    rendered_lines = 0

    def render() -> None:
        nonlocal rendered_lines
        _move_cursor_up(rendered_lines)
        start, end, paged = _picker_window(cursor, len(display))
        print(f"{title}:")
        print("Use Up/Down to move, Space selects, Enter confirms, a selects all, q cancels.")
        for index in range(start, end):
            pointer = ">" if index == cursor else " "
            if index == add_index:
                print(f"{pointer} {display[index]}")
            else:
                mark = "[x]" if index in selected else "[ ]"
                print(f"{pointer} {mark} {display[index]}")
        footer_lines = 0
        if paged:
            print(f"Showing {start + 1}-{end} of {len(display)}. Selected: {len(selected)}.")
            footer_lines = 1
        rendered_lines = (end - start) + 2 + footer_lines

    def chosen() -> list[str]:
        return [display[index] for index in sorted(selected)]

    def trigger_add() -> None:
        nonlocal cursor, rendered_lines
        _move_cursor_up(rendered_lines)
        rendered_lines = 0
        with _suspended_raw_terminal():
            new_repos = add_prompt() if add_prompt else []
        for repo in new_repos:
            if repo not in display:
                display.append(repo)
                selected.add(len(display) - 1)
                cursor = len(display) - 1

    with _raw_terminal_session(key_reader is _read_terminal_key):
        while True:
            render()
            key = key_reader()
            on_add_row = add_index is not None and cursor == add_index
            if key in {"\r", "\n"}:
                if on_add_row:
                    trigger_add()
                    continue
                result = chosen()
                _collapse_summary(title, ", ".join(result) if result else "none", rendered_lines)
                return result
            if key.lower() == "q":
                raise _WizardCancelled
            if key.lower() == "a":
                selected = {index for index in range(len(display)) if index != add_index}
            elif key in {" ", "\t"}:
                if on_add_row:
                    trigger_add()
                elif cursor in selected:
                    selected.remove(cursor)
                else:
                    selected.add(cursor)
            elif key in {"\x1b[B", "\x1bOB", "\xe0P", "\x00P", "j"}:
                cursor = (cursor + 1) % len(display)
            elif key in {"\x1b[A", "\x1bOA", "\xe0H", "\x00H", "k"}:
                cursor = (cursor - 1) % len(display)


def _interactive_tabbed_multi_select(
    title: str,
    groups: dict[str, list[str]],
    *,
    key_reader=_read_terminal_key,
) -> list[str]:
    tabs = [(name, values) for name, values in groups.items() if values]
    if not tabs:
        return []
    tab_index = 0
    cursor_by_tab = {name: 0 for name, _values in tabs}
    selected: set[str] = set()
    rendered_lines = 0

    def current_tab() -> tuple[str, list[str]]:
        return tabs[tab_index]

    def render() -> None:
        nonlocal rendered_lines
        _move_cursor_up(rendered_lines)
        tab_name, options = current_tab()
        cursor = cursor_by_tab[tab_name]
        start, end, paged = _picker_window(cursor, len(options))
        print(f"{title}:")
        rendered_tabs = " | ".join(
            f"[{name}]" if index == tab_index else name
            for index, (name, _values) in enumerate(tabs)
        )
        print(f"Tabs: {rendered_tabs}")
        print(
            "Use Left/Right to switch tabs, Up/Down to move, "
            "Space selects, Enter confirms, a selects all, q cancels."
        )
        for index in range(start, end):
            repo = options[index]
            pointer = ">" if index == cursor else " "
            mark = "[x]" if repo in selected else "[ ]"
            print(f"{pointer} {mark} {repo}")
        footer_lines = 0
        if paged:
            print(f"Showing {start + 1}-{end} of {len(options)}. Selected: {len(selected)}.")
            footer_lines = 1
        rendered_lines = (end - start) + 3 + footer_lines

    with _raw_terminal_session(key_reader is _read_terminal_key):
        while True:
            render()
            key = key_reader()
            tab_name, options = current_tab()
            cursor = cursor_by_tab[tab_name]
            if key in {"\r", "\n"}:
                result = sorted(selected)
                _collapse_summary(title, ", ".join(result) if result else "none", rendered_lines)
                return result
            if key.lower() == "q":
                raise _WizardCancelled
            if key.lower() == "a":
                selected.update(options)
            elif key in {" ", "\t"}:
                repo = options[cursor]
                if repo in selected:
                    selected.remove(repo)
                else:
                    selected.add(repo)
            elif key in {"\x1b[C", "\x1bOC", "\xe0M", "\x00M", "l"}:
                tab_index = (tab_index + 1) % len(tabs)
            elif key in {"\x1b[D", "\x1bOD", "\xe0K", "\x00K", "h"}:
                tab_index = (tab_index - 1) % len(tabs)
            elif key in {"\x1b[B", "\x1bOB", "\xe0P", "\x00P", "j"}:
                cursor_by_tab[tab_name] = (cursor + 1) % len(options)
            elif key in {"\x1b[A", "\x1bOA", "\xe0H", "\x00H", "k"}:
                cursor_by_tab[tab_name] = (cursor - 1) % len(options)


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
        if sys.stdin.isatty() and sys.stdout.isatty():
            return _interactive_multi_select("Choose authors", authors)

        print("\nChoose authors:")
        for index, author in enumerate(authors, start=1):
            print(f"  {index}) {author}")
        raw = Prompt.ask("Author choices (comma-separated numbers or names/emails)", default="")
        return _parse_author_selection(raw, authors)

    raw = Prompt.ask("Author names or emails (comma-separated)", default="")
    return _parse_author_selection(raw, [])


def _dedupe_sorted_repos(lines: str) -> list[str]:
    repos: list[str] = []
    seen: set[str] = set()
    for line in lines.splitlines():
        name = line.strip()
        if name and name not in seen:
            seen.add(name)
            repos.append(name)
    return sorted(repos, key=str.lower)


def _remote_repository_groups() -> dict[str, list[str]]:
    gh = shutil.which("gh")
    if not gh:
        return {}
    queries = [
        ("Owned", "owner"),
        ("Organizations", "organization_member"),
        ("Collaborator", "collaborator"),
    ]
    groups: dict[str, list[str]] = {}
    seen_global: set[str] = set()
    try:
        with _spinner("Fetching your GitHub repositories…"):
            for label, affiliation in queries:
                result = subprocess.run(
                    [
                        gh,
                        "api",
                        f"user/repos?affiliation={affiliation}&per_page=100",
                        "--paginate",
                        "--jq",
                        ".[].full_name",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                )
                repos = [
                    repo
                    for repo in _dedupe_sorted_repos(result.stdout)
                    if repo not in seen_global
                ]
                seen_global.update(repos)
                groups[label] = repos
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {}
    return groups


def _remote_repositories() -> list[str]:
    repos: list[str] = []
    for values in _remote_repository_groups().values():
        repos.extend(values)
    return sorted(repos, key=str.lower)


def _parse_remote_repo_selection(raw: str, repos: list[str]) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for token in (item.strip() for item in raw.split(",")):
        if not token:
            continue
        repo = token
        if token.isdigit():
            index = int(token) - 1
            if 0 <= index < len(repos):
                repo = repos[index]
        if repo not in seen:
            selected.append(repo)
            seen.add(repo)
    return selected


def _prompt_custom_repos() -> list[str]:
    raw = Prompt.ask("Custom repo (URL or owner/name, comma-separated)", default="")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _choose_remote_repositories() -> list[str]:
    groups = _remote_repository_groups()
    repos = [repo for values in groups.values() for repo in values]
    if repos:
        if sys.stdin.isatty() and sys.stdout.isatty():
            selected = _interactive_tabbed_multi_select("Choose remote repositories", groups)
            if selected:
                return selected
            return _prompt_custom_repos()

        print("\nChoose remote repositories:")
        index = 1
        for label, values in groups.items():
            if not values:
                continue
            print(f"{label}:")
            for repo in values:
                print(f"  {index}) {repo}")
                index += 1
        raw = Prompt.ask(
            "Repository choices (comma-separated numbers, owner/name, or URL)",
            default="",
        )
        return _parse_remote_repo_selection(raw, repos)

    raw = Prompt.ask("Remote repositories (comma-separated owner/name or URL)", default="")
    return _parse_remote_repo_selection(raw, [])


def _remote_repo_label(repo: str) -> str:
    """Return a credential-free owner/repo label for remote display/checkpoints."""
    cleaned = repo.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]

    if cleaned.startswith("git@github.com:"):
        cleaned = cleaned.removeprefix("git@github.com:")
    elif "://" in cleaned:
        parsed = urlsplit(cleaned)
        if parsed.hostname != "github.com":
            raise RuntimeError(f"GitHub remote repositories must use github.com, got {repo!r}")
        if parsed.password is not None or (parsed.username not in (None, "git")):
            raise RuntimeError("Refusing credential-bearing GitHub remote URL")
        if parsed.query or parsed.fragment:
            raise RuntimeError("Refusing credential-bearing GitHub remote URL")
        cleaned = parsed.path.lstrip("/")
    elif "@" in cleaned or ":" in cleaned:
        raise RuntimeError("Refusing credential-bearing GitHub remote URL")

    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    parts = [part for part in cleaned.split("/") if part]
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise RuntimeError(f"GitHub remote repositories must be owner/repo, got {repo!r}")
    return f"{parts[0]}/{parts[1]}"


def _remote_repo_url(repo: str) -> str:
    if repo.startswith(("http://", "https://", "git@")):
        return repo
    return f"https://github.com/{repo}.git"


def _safe_repo_dir_name(repo: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", _remote_repo_label(repo)).strip("_") or "repo"


def _clone_remote_repo(repo: str, parent: Path) -> Path:
    target = parent / _safe_repo_dir_name(repo)
    gh = shutil.which("gh")
    # Clone blobs upfront: `git log --numstat` diffs every commit, and a blobless
    # (--filter=blob:none) clone would refetch each blob over the network on demand.
    if gh:
        cmd = [gh, "repo", "clone", repo, str(target)]
    else:
        cmd = ["git", "clone", _remote_repo_url(repo), str(target)]
    try:
        with _spinner(f"Cloning {_remote_repo_label(repo)}…"):
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Cloning {repo} timed out after 120s — check your network/VPN and retry"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or f"exit code {exc.returncode}"
        raise RuntimeError(f"Could not clone remote repository {repo}: {detail}") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not clone remote repository {repo}: {exc}") from exc
    return target


def _format_command(args: list[str]) -> str:
    return "git-standup " + " ".join(shlex.quote(item) for item in args)


def _provider_defaults(provider: str) -> tuple[str, str]:
    for spec in PROVIDER_SPECS:
        if spec.provider == provider:
            return spec.base_url, spec.text_model
    if provider == "azure-openai":
        return "", os.environ.get("AZURE_OPENAI_DEPLOYMENT") or os.environ.get(
            "AZURE_OPENAI_MODEL",
            "",
        )
    if provider == "custom":
        return "https://api.openai.com/v1", "gpt-4o-mini"
    raise ValueError(f"Unknown provider: {provider}")


def _harness_defaults(harness: str) -> tuple[str, str]:
    if harness == "ollama":
        return "http://localhost:11434/v1", "llama3.1"
    if harness == "lms":
        return "http://localhost:1234/v1", "local-model"
    for spec in CLI_HARNESS_SPECS:
        if spec.command == harness:
            return "", spec.default_model
    return "", ""


def _provider_key_env(provider: str) -> str:
    """Return the environment variable name for a provider's API key."""
    for spec in PROVIDER_SPECS:
        if spec.provider == provider:
            return spec.key_names[0]
    if provider == "azure-openai":
        return "AZURE_OPENAI_API_KEY"
    return "OPENAI_API_KEY"


def _prompt_api_key(provider: str) -> None:
    """Prompt for an API key, set it for this run, and offer to persist it."""
    key_name = _provider_key_env(provider)
    key = getpass.getpass(f"{key_name} (hidden; leave blank to skip): ").strip()
    if not key:
        print(f"Skipped key entry. Set {key_name} in your environment to use AI mode.")
        return
    os.environ[key_name] = key
    if _confirm(f"Save {key_name} to your shell profile for future runs?", default=False):
        target = persist_env_var(key_name, key)
        if target == "setx":
            print(f"Saved {key_name} via setx. Open a new terminal to load it.")
        elif target:
            print(f"Saved {key_name} to {target}. Restart your shell or run: source {target}")
        else:
            print(f"Could not persist {key_name}; it is set for this run only.")
    else:
        masked = mask_secret(key)
        print(f"{key_name} is set for this run only ({masked}).")
        print(
            "To persist it yourself, add the variable to your shell profile; "
            "the secret value is not printed here."
        )


def _prompt_model(default_model: str) -> str:
    """Ask which model to use, prefilling the provider/harness default."""
    return Prompt.ask("Model", default=default_model).strip()


def _supported_harness_text() -> str:
    return ", ".join(CONFIGURABLE_CLI_HARNESSES)


def _harness_label(harness: str) -> str:
    for spec in CLI_HARNESS_SPECS:
        if spec.command == harness:
            return spec.label
    return harness


def _detected_supported_harnesses(ai_report: dict[str, Any]) -> list[str]:
    detected = ai_report.get("cli_harnesses") or []
    if not isinstance(detected, list):
        return []
    return [str(item) for item in detected if str(item) in CONFIGURABLE_CLI_HARNESSES]


def _unsupported_harness_message(harness: str) -> str:
    return (
        f"Unsupported CLI harness: {harness}. "
        f"Supported harnesses: {_supported_harness_text()}."
    )


def _ai_provider_available(ai_report: dict[str, Any]) -> bool:
    """Return True when any AI provider, CLI harness, or saved config exists."""
    if ai_report.get("api_keys") or _detected_supported_harnesses(ai_report):
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
    try:
        existing_config = load_config(path)
    except ValueError:
        existing_config = None
    existing_author_aliases = existing_config.author_aliases if existing_config else {}

    if kind is None:
        if harness:
            kind = "cli"
        elif provider:
            kind = "provider"
        else:
            kind = _choice("Set up AI using", ["provider", "cli"], "provider")

    if kind == "cli":
        prompted = harness is None
        ai_report = detect_ai_environment(os.environ)
        detected = _detected_supported_harnesses(ai_report)
        if detected:
            print(
                "Detected ready AI harnesses: "
                + ", ".join(_harness_label(item) for item in detected)
            )
        if ai_report.get("unsupported_cli_tools"):
            print(
                "Detected other AI tools without a headless adapter yet: "
                + ", ".join(str(item) for item in ai_report["unsupported_cli_tools"])
            )
        default_harness = detected[0] if detected else CONFIGURABLE_CLI_HARNESSES[0]
        chosen = harness or _choice(
            "CLI harness",
            list(CONFIGURABLE_CLI_HARNESSES),
            default_harness,
        )
        if chosen not in CONFIGURABLE_CLI_HARNESSES:
            print(_unsupported_harness_message(chosen), file=sys.stderr)
            return None
        default_base_url, default_model = _harness_defaults(chosen)
        config = AIConfig(
            harness=chosen,
            base_url=base_url or default_base_url,
            model=model or (_prompt_model(default_model) if prompted else default_model),
            author_aliases=existing_author_aliases,
        )
    else:
        prompted = provider is None
        chosen = provider or _choice(
            "Provider",
            [spec.provider for spec in PROVIDER_SPECS] + ["azure-openai", "custom"],
            "openai",
        )
        default_base_url, default_model = _provider_defaults(chosen)
        if chosen == "custom" and not base_url:
            default_base_url = Prompt.ask("OpenAI-compatible base URL", default=default_base_url)
        api_key_env = ""
        if chosen == "custom":
            api_key_env = Prompt.ask("API key environment variable", default="OPENAI_API_KEY")
        config = AIConfig(
            provider=chosen,
            base_url=base_url or default_base_url,
            model=model or (_prompt_model(default_model) if prompted else default_model),
            api_key_env=api_key_env,
            author_aliases=existing_author_aliases,
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
    if config.author_aliases:
        print("author_aliases:")
        for canonical, aliases in config.author_aliases.items():
            print(f"  {canonical}: {', '.join(aliases)}")


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
        config = configure_ai_interactive(
            path,
            kind="cli",
            harness=args.harness,
            base_url=args.base_url,
            model=args.model,
            allow_key=False,
        )
        if config is None:
            return 2
        return 0

    if action == "interactive":
        try:
            configure_ai_interactive(path, allow_key=False)
        except _WizardCancelled:
            print("Cancelled.")
        return 0

    print(f"Unknown config action: {action}", file=sys.stderr)
    return 2


def run_wizard() -> int:
    """Interactive command builder for git-standup."""
    try:
        repo_source = _numbered_choice(
            "Repository source",
            [
                ("current", "Current directory", "Use this Git repository."),
                ("other", "Other directory", "Choose a local Git repository path."),
                ("remote", "Remote repository", "Pick one or more GitHub repositories."),
            ],
            "current",
        )
        repo = "."
        remote_repos: list[str] = []
        if repo_source == "other":
            repo = Prompt.ask("Repository path", default=".")
        elif repo_source == "remote":
            remote_repos = _choose_remote_repositories()

        _wizard_separator()
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
        _wizard_separator()
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
        if remote_repos:
            answers["remote_repos"] = remote_repos
        if author_choice == "me":
            answers["author"] = "me"
        elif author_choice == "custom":
            if remote_repos:
                author = Prompt.ask("Author name or email", default="")
                if author:
                    answers["author"] = author
            else:
                authors = _choose_authors(repo)
                if authors:
                    answers["authors"] = authors
        detected_supported = _detected_supported_harnesses(ai_report)
        if detected_supported:
            print(
                "Detected ready AI harnesses: "
                + ", ".join(_harness_label(item) for item in detected_supported)
            )
        if ai_report.get("api_keys"):
            providers = ", ".join(
                str(item.get("provider"))
                for item in ai_report["api_keys"]
                if isinstance(item, dict)
            )
            if providers:
                print("Detected ready API providers: " + providers)
        if ai_report.get("unsupported_cli_tools"):
            print(
                "Detected other AI tools: "
                + ", ".join(str(item) for item in ai_report["unsupported_cli_tools"])
                + " (no headless adapter yet)"
            )
        if ai_report.get("unsupported_api_keys"):
            names = ", ".join(
                str(item.get("name"))
                for item in ai_report["unsupported_api_keys"]
                if isinstance(item, dict)
            )
            if names:
                print(
                    "Detected unsupported AI credentials: "
                    + names
                    + " (missing setup details)"
                )
        if preset == "branch":
            answers["base_branch"] = Prompt.ask("Base branch", default="main")
        elif preset == "custom":
            answers["days"] = Prompt.ask("Days of history", default="7")

        _wizard_separator()
        answers["format"] = _numbered_choice(
            "Output format",
            [
                ("markdown", "Markdown", "Paste-ready for Slack, Notion, or GitHub."),
                ("text", "Plain text", "Simple terminal summary."),
                ("json", "JSON", "Structured data for scripts or automation."),
                ("stats", "Stats only", "Aggregate counts without per-commit details."),
                (
                    "changelog",
                    "Changelog",
                    "Release-note Markdown grouped by conventional commit type.",
                ),
                (
                    "insights",
                    "Planning insights",
                    "Themes, product areas, risks, and follow-ups for weekly planning.",
                ),
            ],
            "markdown",
        )

        _wizard_separator()
        if answers["format"] in {"json", "changelog", "insights", "stats"}:
            answers["ai"] = False
        elif _ai_provider_available(ai_report):
            answers["ai"] = _confirm("Polish with AI?", default=True)
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

        _wizard_separator()
        if _confirm("Save report to a file?", default=False):
            output_format = str(answers["format"])
            answers["output"] = Prompt.ask("Save as", default=_default_output_path(output_format))

        args = build_wizard_args(answers)
        _wizard_separator()
        print(f"Generated command:\n  {_format_command(args)}\n")
        if _confirm("Run it now", default=True):
            return main(args)
    except _WizardCancelled:
        print("Cancelled.")
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
        "  git-standup --remote-repo owner/api --remote-repo owner/web\n"
        "  git-standup --remote-repo owner/api --remote-backend api --json\n"
        "  git-standup --path src --path tests  # Only commits touching paths\n"
        "  git-standup --since 2026-01-01 --until 2026-01-07\n"
        "  git-standup --since-last --write-checkpoint --no-ai\n"
        "  git-standup --author me         # My commits only\n"
        "  git-standup --exclude-merges   # Hide merge commits\n"
        "  git-standup --include-prs      # Include PR numbers/titles/URLs\n"
        "  git-standup --include-prs --pr-status  # Include GitHub PR checks/reviews\n"
        "  git-standup --workflow-board   # PR handoff board by workflow status\n"
        "  git-standup --no-ai             # Text summary without AI\n"
        "  git-standup --markdown          # AI-polished Markdown summary\n"
        "  git-standup --markdown --no-ai  # Raw Markdown summary without AI\n"
        "  git-standup --stats-only       # Aggregate stats without commit details\n"
        "  git-standup --changelog        # Release-note Markdown without AI\n"
        "  git-standup --insights         # Planning insights without AI\n"
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
        "--remote-repo",
        dest="remote_repos",
        action="append",
        default=None,
        metavar="OWNER/NAME",
        help="GitHub repository to include in the report. Repeat for multiple repos.",
    )
    parser.add_argument(
        "--remote-backend",
        choices=("clone", "api"),
        default="clone",
        help=(
            "Backend for --remote-repo: clone repositories locally (default) or "
            "query GitHub through gh api without cloning"
        ),
    )
    parser.add_argument(
        "--since",
        type=_date_string,
        default=None,
        help="Start date for the report window (YYYY-MM-DD). Overrides --days.",
    )
    parser.add_argument(
        "--since-last",
        action="store_true",
        help=(
            "Start from this repository's last --write-checkpoint timestamp "
            "instead of remembering a --since date"
        ),
    )
    parser.add_argument(
        "--write-checkpoint",
        action="store_true",
        help=(
            "After a successful report, store the current timestamp for future "
            "--since-last runs"
        ),
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
        "--author-alias",
        action="append",
        default=None,
        metavar="CANONICAL=ALIAS[,ALIAS]",
        help=(
            "Merge commits from alternate names, emails, or logins into one author. "
            "Repeat or add [author_aliases] in config."
        ),
    )
    parser.add_argument(
        "--base-branch",
        type=str,
        default=None,
        help="Base branch for comparing changes (e.g., 'main'). Shows commits "
        "in current branch not in base.",
    )
    parser.add_argument(
        "--path",
        "--pathspec",
        dest="pathspecs",
        action="append",
        default=None,
        metavar="PATH",
        help="Only include commits touching this pathspec. Repeat for multiple paths.",
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
        "--exclude-merges",
        action="store_true",
        help="Exclude merge commits from git history",
    )
    parser.add_argument(
        "--include-prs",
        "--pr-digest",
        dest="include_prs",
        action="store_true",
        help=(
            "Enrich commits with PR numbers, titles, and URLs. May query GitHub "
            "through gh when available; otherwise uses local merge/squash metadata."
        ),
    )
    parser.add_argument(
        "--pr-status",
        action="store_true",
        help=(
            "When PR enrichment is enabled, also query GitHub for draft state, checks, "
            "reviews, mergeability, labels, linked issues, and ownership metadata"
        ),
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
        "--changelog",
        action="store_true",
        help=(
            "Output release-note Markdown grouped by conventional commit category "
            "(always no AI)"
        ),
    )
    parser.add_argument(
        "--team-digest",
        action="store_true",
        help=(
            "Output a team workflow digest with owner sections, risk radar, "
            "and follow-up questions (always no AI)"
        ),
    )
    parser.add_argument(
        "--workflow-board",
        action="store_true",
        help=(
            "Output a GitHub PR workflow status board grouped by needs review, "
            "ready to merge, rollout, and owner action (always no AI)"
        ),
    )
    parser.add_argument(
        "--stale-days",
        type=_positive_int,
        default=7,
        help="PR age in days since last update before the workflow board flags stale follow-up",
    )
    parser.add_argument(
        "--insights",
        action="store_true",
        help=(
            "Output concise planning insights with themes, likely product areas, "
            "review/rollout risks, and follow-ups (always no AI)"
        ),
    )
    parser.add_argument(
        "--template",
        choices=TEAM_DIGEST_TEMPLATES,
        default="slack",
        help="Team digest template style to label the output",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Output aggregate commit/file/line stats without per-commit details (always no AI)",
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
        help=(
            "Supported AI harness for config set-cli "
            f"({', '.join(CONFIGURABLE_CLI_HARNESSES)})"
        ),
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
            if args.remote_repos:
                parser.error("positional repository paths cannot be combined with --remote-repo")
            args.repo = target
    if args.changelog and args.json:
        parser.error("--changelog cannot be combined with --json")
    if args.since_last and args.since:
        parser.error("--since-last cannot be combined with --since")
    if args.changelog and args.markdown:
        parser.error("--changelog cannot be combined with --markdown; it already emits Markdown")
    if args.team_digest and args.json:
        parser.error("--team-digest cannot be combined with --json")
    if args.team_digest and args.markdown:
        parser.error("--team-digest cannot be combined with --markdown; it already emits Markdown")
    if args.team_digest and args.changelog:
        parser.error("--team-digest cannot be combined with --changelog")
    if args.workflow_board:
        if args.json:
            parser.error("--workflow-board cannot be combined with --json")
        if args.markdown:
            parser.error(
                "--workflow-board cannot be combined with --markdown; "
                "it already emits Markdown"
            )
        if args.changelog:
            parser.error("--workflow-board cannot be combined with --changelog")
        if args.stats_only:
            parser.error("--workflow-board cannot be combined with --stats-only")
        if args.insights:
            parser.error("--workflow-board cannot be combined with --insights")
        args.include_prs = True
        args.pr_status = True
    if args.pr_status:
        args.include_prs = True
    if args.insights and args.json:
        parser.error("--insights cannot be combined with --json")
    if args.insights and args.markdown:
        parser.error("--insights cannot be combined with --markdown; it already emits Markdown")
    if args.insights and args.changelog:
        parser.error("--insights cannot be combined with --changelog")
    if args.insights and args.team_digest:
        parser.error("--insights cannot be combined with --team-digest")
    if args.remote_repos and args.repo is not None:
        parser.error("--remote-repo cannot be combined with --repo or a positional repo path")
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

    checkpoint_targets: list[_CheckpointTarget] = []
    checkpoint_timestamp = _checkpoint_timestamp() if args.write_checkpoint else ""
    since_by_checkpoint_id: dict[str, str] = {}
    try:
        if args.since_last or args.write_checkpoint:
            checkpoint_targets = _checkpoint_targets(args)
        if args.since_last:
            since_by_checkpoint_id = _since_last_by_target(checkpoint_targets)
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    def _finish_success() -> int:
        if not args.write_checkpoint:
            return 0
        try:
            _write_report_checkpoints(checkpoint_targets, checkpoint_timestamp)
        except (OSError, ValueError) as exc:
            print(f"Error: could not write since-last checkpoint: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        user_config = load_config(config_path())
        author_aliases = _resolve_author_aliases(user_config, args.author_alias)
    except ValueError as exc:
        print(f"Error: invalid config: {exc}", file=sys.stderr)
        return 1

    try:
        commit_fetch_limit = (
            args.max_commits + 1 if args.max_commits is not None else None
        )
        budget_metadata: dict[str, Any] | None = None
        multi_repo_commit_data: dict[str, Any] | None = None
        if args.remote_repos:
            repo_commits: list[tuple[str, list[dict[str, Any]]]] = []
            repo_budget_metadata: dict[str, Any] = {}
            api_run_metadata: dict[str, Any] | None = None
            all_commits: list[dict[str, Any]] = []
            if args.remote_backend == "api":
                validate_remote_api_options(
                    base_branch=args.base_branch,
                    pathspecs=args.pathspecs,
                )
                api_cache = GitHubApiRunCache()
                for remote_repo in args.remote_repos:
                    repo_name = _remote_repo_label(remote_repo)
                    repo_since = args.since
                    if args.since_last:
                        repo_since = since_by_checkpoint_id[remote_repository_id(repo_name)]
                    fetched = get_remote_commits(
                        remote_repo,
                        days=args.days,
                        author=args.author,
                        since=repo_since,
                        until=args.until,
                        max_commits=commit_fetch_limit,
                        exclude_merges=args.exclude_merges,
                        include_prs=args.include_prs,
                        author_aliases=author_aliases,
                        cache=api_cache,
                    )
                    repo_stats = api_cache.repositories.get(_normalize_repo_slug(remote_repo))
                    if not fetched and repo_stats and repo_stats.get("rate_limited"):
                        continue
                    fetched, metadata = _apply_output_budget(
                        fetched,
                        max_commits=args.max_commits,
                        max_files_per_commit=args.max_files_per_commit,
                    )
                    canonicalize_commit_authors(fetched, author_aliases)
                    if metadata is not None:
                        repo_budget_metadata[repo_name] = metadata
                    if args.pr_status:
                        fetched = enrich_commits_with_prs(
                            fetched,
                            repo_slug=repo_name,
                            query_github=True,
                            include_status=True,
                        )
                    for commit in fetched:
                        commit["repository"] = repo_name
                    repo_commits.append((repo_name, fetched))
                    all_commits.extend(fetched)
                api_metadata = api_cache.metadata()
                if api_metadata is not None:
                    api_run_metadata = api_metadata
            else:
                with tempfile.TemporaryDirectory() as temp_dir:
                    parent = Path(temp_dir)
                    for remote_repo in args.remote_repos:
                        repo_path = _clone_remote_repo(remote_repo, parent)
                        repo_name = _remote_repo_label(remote_repo)
                        repo_since = args.since
                        if args.since_last:
                            repo_since = since_by_checkpoint_id[remote_repository_id(repo_name)]
                        fetched = get_commits(
                            days=args.days,
                            author=args.author,
                            base_branch=args.base_branch,
                            repo_path=str(repo_path),
                            since=repo_since,
                            until=args.until,
                            max_commits=commit_fetch_limit,
                            exclude_merges=args.exclude_merges,
                            pathspecs=args.pathspecs,
                            author_aliases=author_aliases,
                        )
                        fetched, metadata = _apply_output_budget(
                            fetched,
                            max_commits=args.max_commits,
                            max_files_per_commit=args.max_files_per_commit,
                        )
                        canonicalize_commit_authors(fetched, author_aliases)
                        if metadata is not None:
                            repo_budget_metadata[repo_name] = metadata
                        if args.include_prs:
                            enrich_kwargs: dict[str, Any] = {
                                "repo_path": str(repo_path),
                                "query_github": True,
                            }
                            if args.pr_status:
                                enrich_kwargs["include_status"] = True
                            fetched = enrich_commits_with_prs(fetched, **enrich_kwargs)
                            canonicalize_commit_authors(fetched, author_aliases)
                        for commit in fetched:
                            commit["repository"] = repo_name
                        repo_commits.append((repo_name, fetched))
                        all_commits.extend(fetched)
            commits = all_commits
            multi_repo_commit_data = _build_multi_repo_commit_data(repo_commits)
            metadata_parts: dict[str, Any] = {}
            if repo_budget_metadata:
                metadata_parts["repositories"] = repo_budget_metadata
            if api_run_metadata is not None:
                metadata_parts["github_api"] = api_run_metadata
            if metadata_parts:
                budget_metadata = metadata_parts
        else:
            repo_since = args.since
            if args.since_last:
                repo_since = since_by_checkpoint_id[checkpoint_targets[0].repository_id]
            commits = get_commits(
                days=args.days,
                author=args.author,
                base_branch=args.base_branch,
                repo_path=args.repo,
                since=repo_since,
                until=args.until,
                max_commits=commit_fetch_limit,
                exclude_merges=args.exclude_merges,
                pathspecs=args.pathspecs,
                author_aliases=author_aliases,
            )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not commits:
        print("No commits found in the specified time range.")
        return _finish_success()

    if multi_repo_commit_data is None:
        commits, budget_metadata = _apply_output_budget(
            commits,
            max_commits=args.max_commits,
            max_files_per_commit=args.max_files_per_commit,
        )
        if args.include_prs:
            enrich_kwargs = {"repo_path": args.repo, "query_github": True}
            if args.pr_status:
                enrich_kwargs["include_status"] = True
            commits = enrich_commits_with_prs(commits, **enrich_kwargs)
        canonicalize_commit_authors(commits, author_aliases)

    if args.changelog:
        if args.ai:
            print(
                "Warning: --ai has no effect with --changelog; changelog is always raw.",
                file=sys.stderr,
            )
        changelog = build_changelog_output(_with_commit_quality(commits), budget_metadata)
        _emit_markdown(changelog, args.output)
        return _finish_success()

    commit_data = multi_repo_commit_data or _build_commit_data(commits)

    if args.workflow_board:
        if args.ai:
            print(
                "Warning: --ai has no effect with --workflow-board; workflow board is always raw.",
                file=sys.stderr,
            )
        workflow_board = build_workflow_board_output(
            _with_commit_quality_in_data(commit_data),
            stale_days=args.stale_days,
        )
        _emit_markdown(workflow_board, args.output)
        return _finish_success()

    if args.team_digest:
        if args.ai:
            print(
                "Warning: --ai has no effect with --team-digest; team digest is always raw.",
                file=sys.stderr,
            )
        team_digest = build_team_digest_output(
            _with_commit_quality_in_data(commit_data),
            template=args.template,
            include_workflow_board=args.pr_status,
            stale_days=args.stale_days,
        )
        _emit_markdown(team_digest, args.output)
        return _finish_success()

    if args.insights:
        if args.ai:
            print(
                "Warning: --ai has no effect with --insights; insights are always raw.",
                file=sys.stderr,
            )
        insights = build_insights_output(_with_commit_quality_in_data(commit_data))
        _emit_markdown(insights, args.output)
        return _finish_success()

    output_format = "markdown" if args.markdown else "text"

    if args.stats_only:
        if args.ai:
            print(
                "Warning: --ai has no effect with --stats-only; stats output is always raw.",
                file=sys.stderr,
            )
        if args.json:
            print(
                "Warning: --json has no effect with --stats-only; "
                "use --markdown for Markdown stats.",
                file=sys.stderr,
            )
        stats_output = build_stats_output(commit_data, output_format=output_format)
        if output_format == "markdown":
            _emit_markdown(stats_output, args.output)
        else:
            _emit(stats_output, args.output, lambda: print(stats_output, end=""))
        return _finish_success()

    if args.json:
        if args.ai:
            print(
                "Warning: --ai has no effect with --json; JSON is always raw.",
                file=sys.stderr,
            )
        output = build_json_output(
            _with_json_metadata(
                commit_data,
                budget_metadata,
                _build_json_provenance_metadata(args),
            )
        )
        _emit(output + "\n", args.output, lambda: print(output))
        return _finish_success()

    def _emit_raw() -> None:
        """Emit the raw (non-AI) formatter output for the chosen format."""
        if output_format == "markdown":
            markdown = build_markdown_output(commit_data)
            _emit_markdown(markdown, args.output)
        else:
            text_output = build_text_output(commit_data)
            _emit(
                text_output,
                args.output,
                lambda: print(text_output, end="")
                if multi_repo_commit_data is not None
                else print_text_standup(commit_data),
            )

    if args.no_ai:
        _emit_raw()
        return _finish_success()

    # AI mode
    try:
        user_config = load_config(config_path())
    except ValueError as exc:
        print(f"Error: invalid AI config: {exc}", file=sys.stderr)
        return 1
    if (
        user_config
        and user_config.harness
        and user_config.harness not in CONFIGURABLE_CLI_HARNESSES
    ):
        print(
            f"Error: invalid AI config: {_unsupported_harness_message(user_config.harness)}",
            file=sys.stderr,
        )
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
        with _spinner("Polishing with AI…"):
            if connection.provider in CONFIGURABLE_CLI_HARNESSES and connection.provider not in {
                "ollama",
                "lms",
            }:
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

    output = standup_text.rstrip() + "\n"
    if output_format == "markdown":
        _emit_markdown(output, args.output)
    else:
        _emit(output, args.output, lambda: print_ai_standup(standup_text))
    return _finish_success()


if __name__ == "__main__":
    sys.exit(main())
