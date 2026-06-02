import pytest

from git_standup import clipboard


def test_clipboard_command_prefers_pbcopy_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/pbcopy")

    assert clipboard._clipboard_command() == ["pbcopy"]


def test_clipboard_command_finds_first_linux_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name if name == "xclip" else None)

    assert clipboard._clipboard_command() == ["xclip", "-selection", "clipboard"]


def test_clipboard_command_none_when_no_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)

    assert clipboard._clipboard_command() is None
    assert clipboard.clipboard_available() is False


def test_copy_to_clipboard_invokes_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    monkeypatch.setattr(clipboard, "_clipboard_command", lambda: ["pbcopy"])

    def fake_run(command, input, text, check):  # noqa: A002 - mirror subprocess signature
        calls["command"] = command
        calls["input"] = input
        return None

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)

    assert clipboard.copy_to_clipboard("hello") is True
    assert calls == {"command": ["pbcopy"], "input": "hello"}


def test_copy_to_clipboard_returns_false_without_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard, "_clipboard_command", lambda: None)

    assert clipboard.copy_to_clipboard("hello") is False


def test_read_single_key_falls_back_to_readline(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStdin:
        def isatty(self) -> bool:
            return False

        def readline(self) -> str:
            return "c\n"

    monkeypatch.setattr(clipboard.sys, "stdin", FakeStdin())

    assert clipboard.read_single_key() == "c"
