import pytest

from backend.app.tools.path_guard import PathEscapeError, resolve_in_workspace


def test_relative_path_inside(tmp_path):
    (tmp_path / "src").mkdir()
    assert resolve_in_workspace(tmp_path, "src") == (tmp_path / "src").resolve()


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "src/../../x"])
def test_escape_blocked(tmp_path, bad):
    with pytest.raises(PathEscapeError):
        resolve_in_workspace(tmp_path, bad)


def test_symlink_escape_blocked(tmp_path):
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "link").symlink_to(outside)
    with pytest.raises(PathEscapeError):
        resolve_in_workspace(ws, "link/file.txt")
