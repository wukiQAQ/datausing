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

    opener, proc = desktop._open_window("http://127.0.0.1:8765/app/")
    assert opener == "default-browser"
    assert proc is None
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


# --------------------------------------------------------------------------- 停止入口 / 窗口关闭自动停


class _FakeProc:
    """假的窗口进程：``alive_polls`` 次 poll 返回 None，然后返回退出码。"""

    def __init__(self, alive_polls: int) -> None:
        self._left = alive_polls

    def poll(self) -> int | None:
        if self._left > 0:
            self._left -= 1
            return None
        return 0

    def wait(self) -> int:
        return 0


def test_parse_tasklist_csv_extracts_pids() -> None:
    text = (
        '"FlowMask.exe","1234","Console","1","50,000 K"\r\n'
        '"FlowMask.exe","5678","Console","1","50,000 K"\r\n'
    )
    assert desktop._parse_tasklist_csv(text) == [1234, 5678]
    assert desktop._parse_tasklist_csv(text, exclude=1234) == [5678]
    assert desktop._parse_tasklist_csv("") == []


def test_excluded_pids_covers_self_and_bootloader_parent() -> None:
    # onefile 的引导进程是父进程：--stop 时不能把它一起杀掉，否则退出码会变成 1
    import os

    excluded = desktop._excluded_pids()
    assert os.getpid() in excluded
    assert os.getppid() in excluded


def test_watch_window_delegated_when_process_exits_immediately() -> None:
    fired: list[str] = []
    # 立刻退出 = 浏览器把窗口交给了已有实例，不能把服务停掉
    assert desktop._watch_window(_FakeProc(0), lambda: fired.append("x"), grace_s=0.2, poll_s=0.02) == "delegated"
    assert fired == []


def test_watch_window_fires_when_window_closes_after_grace() -> None:
    fired: list[str] = []
    # 活过宽限期后退出 = 用户真的关掉了窗口
    assert desktop._watch_window(_FakeProc(5), lambda: fired.append("x"), grace_s=0.05, poll_s=0.02) == "closed"
    assert fired == ["x"]


def test_stop_running_without_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "_run_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_find_flowmask_pids", lambda: [])
    monkeypatch.setattr(desktop, "_read_pid_record", lambda: None)
    assert desktop._stop_running() == 1
    # 结果要落盘，便于"点了停止没反应"时排查
    assert (tmp_path / "last-stop.txt").read_text(encoding="utf-8").strip()


def test_stop_running_kills_recorded_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "_run_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_read_pid_record", lambda: {"pid": 4321})
    monkeypatch.setattr(desktop, "_find_flowmask_pids", lambda: [4321, 8765])
    killed: list[int] = []
    monkeypatch.setattr(desktop, "_terminate_pid", lambda pid: killed.append(pid) or True)
    assert desktop._stop_running() == 0
    assert killed == [4321, 8765]


def test_pid_file_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "_run_dir", lambda: tmp_path)
    written = desktop._write_pid_file(8765)
    assert written == tmp_path / "desktop.pid"
    record = desktop._read_pid_record()
    assert record is not None and record["port"] == 8765
    desktop._clear_pid_file()
    assert desktop._read_pid_record() is None


def test_stop_shortcut_script_exists() -> None:
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    stop_cmd = (scripts / "stop_flowmask.cmd").read_text(encoding="utf-8", errors="ignore")
    assert "--stop" in stop_cmd
    creator = (scripts / "create_shortcut.ps1").read_text(encoding="utf-8-sig")
    assert "ValidateSet(\"start\", \"stop\")" in creator
    assert "FlowMask 停止" in creator


def test_start_shortcut_uses_cmd_launcher() -> None:
    """启动快捷方式走 cmd 中转：切工作目录 + start 脱离控制台，行为可复现。"""
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    start_cmd = (scripts / "start_flowmask.cmd").read_text(encoding="utf-8", errors="ignore")
    assert "cd /d" in start_cmd
    assert "start \"\"" in start_cmd
    assert "dist_exe" in start_cmd
    creator = (scripts / "create_shortcut.ps1").read_text(encoding="utf-8-sig")
    assert "start_flowmask.cmd" in creator
    # 启动/停止两个快捷方式都应通过 cmd.exe 调用脚本，而不是直接指向 exe
    assert creator.count("System32\\cmd.exe") >= 2
