from pathlib import Path

import pytest

from backend.app.core.events import EventType
from backend.app.graphs.state import SubAgentState
from backend.app.tools.files import FileToolkit
from backend.app.tools.results import tool_events


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    (tmp_path / "README.md").write_text("# Demo\nHello world\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("hello world")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\0\0data")
    return tmp_path


async def test_list_directory_skips_ignored(ws):
    result = await FileToolkit(ws).list_directory()
    assert result.ok
    assert "src/" in result.content and "app.py" in result.content
    assert "node_modules" not in result.content and ".git" not in result.content


async def test_list_directory_truncates(ws):
    result = await FileToolkit(ws).list_directory(max_entries=2)
    assert result.ok and result.data["truncated"]


async def test_grep_literal_and_regex(ws):
    tk = FileToolkit(ws)
    literal = await tk.grep_search("hello")
    assert literal.ok and "README.md:2:" in literal.content and "node_modules" not in literal.content
    regex = await tk.grep_search(r"def \w+\(", regex=True, glob="*.py")
    assert regex.data["matches"] == 2
    bad = await tk.grep_search("(", regex=True)
    assert not bad.ok and bad.error_code == "INVALID_REGEX"


async def test_read_file_lines_range_and_errors(ws):
    tk = FileToolkit(ws)
    result = await tk.read_file_lines("src/app.py", 5, 6)
    assert result.ok and "5| def sub(a, b):" in result.content
    assert (await tk.read_file_lines("src/app.py", 99)).error_code == "LINE_OUT_OF_RANGE"
    assert (await tk.read_file_lines("missing.py")).error_code == "NOT_FOUND"
    assert (await tk.read_file_lines("logo.png")).error_code == "BINARY_FILE"


async def test_read_file_lines_truncates_long_files(ws):
    (ws / "long.txt").write_text("\n".join(f"line {i}" for i in range(1, 1001)))
    result = await FileToolkit(ws).read_file_lines("long.txt")
    assert result.data["end_line"] == 400 and "start_line=401" in result.content


@pytest.mark.parametrize("target", ["../outside.txt", "/etc/passwd"])
async def test_path_escape_returns_error(ws, target):
    result = await FileToolkit(ws).read_file_lines(target)
    assert not result.ok and result.error_code == "PATH_OUTSIDE_WORKSPACE"
    assert "HOW TO FIX" in result.to_message_text()


async def test_search_replace_edits_and_emits_diff(ws):
    tk = FileToolkit(ws)
    result = await tk.apply_search_replace_diff("src/app.py", "    return a - b", "    return a - b  # subtract")
    assert result.ok, result.to_message_text()
    assert "# subtract" in (ws / "src" / "app.py").read_text()
    assert "+    return a - b  # subtract" in result.diff
    events = tool_events(result, "coder", "t1")
    assert [e.event_type for e in events] == [EventType.TOOL_CALL_RESULT, EventType.DIFF_PROPOSED]


async def test_search_replace_not_found_gives_closest_line(ws):
    result = await FileToolkit(ws).apply_search_replace_diff("src/app.py", "  return a+b", "x")
    assert result.error_code == "SEARCH_NOT_FOUND"
    assert "line 2" in result.hint
    assert (ws / "src" / "app.py").read_text().count("return a + b") == 1


async def test_search_replace_ambiguous(ws):
    result = await FileToolkit(ws).apply_search_replace_diff("src/app.py", "(a, b):", "(x, y):")
    assert result.error_code == "AMBIGUOUS_MATCH"
    ok = await FileToolkit(ws).apply_search_replace_diff("src/app.py", "(a, b):", "(x, y):", expected_occurrences=2)
    assert ok.ok and (ws / "src" / "app.py").read_text().count("(x, y):") == 2


async def test_search_replace_preserves_crlf(ws):
    (ws / "win.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    result = await FileToolkit(ws).apply_search_replace_diff("win.txt", "one\ntwo", "one\n2")
    assert result.ok
    assert (ws / "win.txt").read_bytes() == b"one\r\n2\r\nthree\r\n"


async def test_edits_inside_git_dir_blocked(ws):
    tk = FileToolkit(ws)
    assert (await tk.apply_search_replace_diff(".git/config", "[core]", "x")).error_code == "PROTECTED_PATH"
    assert (await tk.write_file(".git/hooks/pre-commit", "x")).error_code == "PROTECTED_PATH"


async def test_write_file_rules(ws):
    tk = FileToolkit(ws)
    created = await tk.write_file("docs/new.md", "# New\n")
    assert created.ok and created.data["created"] and (ws / "docs" / "new.md").exists()
    assert (await tk.write_file("docs/new.md", "x")).error_code == "FILE_EXISTS"
    assert (await tk.write_file("docs/new.md", "# Replaced\n", overwrite=True)).ok
    (ws / "big.py").write_text("\n".join(f"x = {i}" for i in range(150)))
    big = await tk.write_file("big.py", "x = 1\n", overwrite=True)
    assert big.error_code == "OVERWRITE_TOO_LARGE" and "apply_search_replace_diff" in big.hint
    assert (ws / "big.py").read_text().count("x =") == 150


async def test_error_signature_feeds_needs_human_help(ws):
    tk = FileToolkit(ws)
    state = SubAgentState(agent_id="coder", task_id="t1", goal="g")
    escalate = False
    for _ in range(3):
        result = await tk.apply_search_replace_diff("src/app.py", "nope", "x")
        escalate = state.record_error(result.error_signature)
    assert escalate
