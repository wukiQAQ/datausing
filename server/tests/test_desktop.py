"""桌面入口与打包相关的轻量测试（不启动真实服务、不打开浏览器）。"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

import desktop
from app.core import paths


def _closed_port() -> int:
    """拿一个当前空闲的端口：先绑定再释放。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_free_port_prefers_requested_port() -> None:
    port = _closed_port()
    assert desktop._free_port(port) == port


def test_free_port_skips_busy_port() -> None:
    port = _closed_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind(("127.0.0.1", port))
        held.listen(1)
        assert desktop._free_port(port) == port + 1


def test_open_window_falls_back_to_default_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    # 没有任何 Chromium 系浏览器时，应回落到默认浏览器
    monkeypatch.setattr(desktop.Path, "is_file", lambda self: False)
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: called.append(url))

    assert desktop._open_window("http://127.0.0.1:8765/app/") == "default-browser"
    assert called == ["http://127.0.0.1:8765/app/"]


def test_wait_ready_returns_false_for_unreachable_port() -> None:
    assert desktop._wait_ready("http://127.0.0.1:%d/api/v1/health" % _closed_port(), timeout_s=0.6) is False


def test_writable_root_falls_back_when_localappdata_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """打包后若用户目录不可写（安全软件/组策略），必须回落到 exe 同级目录而不是启动失败。"""
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    # 让 blocked 变成"文件"，其下无法建目录 → 触发不可写分支
    blocked_file = tmp_path / "blocked-file"
    blocked_file.write_text("x", encoding="utf-8")

    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(paths, "executable_dir", lambda: exe_dir)
    monkeypatch.setenv("LOCALAPPDATA", str(blocked_file))

    assert paths.writable_root() == exe_dir
    assert paths.default_data_dir() == exe_dir / "data"


def test_writable_root_uses_localappdata_when_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.writable_root() == tmp_path / "FlowMask"
    assert paths.default_data_dir() == tmp_path / "FlowMask" / "data"


def test_help_works(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # 注意：必须把 "-h" 传给 parse_args（传空列表会忽略 sys.argv 并真的启动服务）
    with pytest.raises(SystemExit) as exc:
        desktop.main(["-h"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--port" in out and "--no-browser" in out and "FlowMask" in out


def test_frozen_log_path_uses_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    log_path = desktop._frozen_log_path()
    assert log_path == tmp_path / "FlowMask" / "logs" / "desktop.log"


def test_paths_dev_layout_points_at_server_dir() -> None:
    assert paths.is_frozen() is False
    server_dir = Path(__file__).resolve().parents[1]
    assert paths.resource_root() == server_dir
    assert paths.writable_root() == server_dir
    assert paths.frontend_dir() == server_dir.parent / "web" / "dist"
    # 开发态数据目录就是 server/data（可被 FLOWMASK_DATA_DIR 覆盖）
    assert paths.default_data_dir() == server_dir / "data"


def test_resolve_model_dir_searches_known_locations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 在"可执行文件目录"下造一个假权重目录，resolve_model_dir 应能找到
    monkeypatch.setattr(paths, "executable_dir", lambda: tmp_path)
    model_dir = tmp_path / "models" / "bert4ner"
    model_dir.mkdir(parents=True)
    assert paths.resolve_model_dir("models", "bert4ner") == model_dir


def test_spec_and_entrypoint_are_consistent() -> None:
    server_dir = Path(__file__).resolve().parents[1]
    spec = (server_dir / "flowmask.spec").read_text(encoding="utf-8")
    assert 'name="FlowMask"' in spec
    assert "console=False" in spec
    for marker in ("../web/dist", "samples", "eval", "desktop.py"):
        assert marker in spec, marker
    # 重依赖不能打进 exe（体积会爆炸，且主服务运行时不 import）
    for excluded in ('"torch"', '"onnxruntime"', '"transformers"'):
        assert excluded in spec, excluded


def test_build_script_exists_and_mentions_pyinstaller() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_exe.ps1"
    text = script.read_text(encoding="utf-8-sig")
    assert "PyInstaller" in text or "pyinstaller" in text
    assert "web/dist" in text or "dist\\index.html" in text
    assert "FlowMask.exe" in text
