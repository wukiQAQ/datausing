"""FlowMask 桌面入口：双击 exe（或 ``python desktop.py``）即启动本地一体机。

做的事很朴素：
1. 选一个可用端口（默认 8765，被占用就顺延）；
2. 起 FastAPI（uvicorn，同进程线程内），并把 pid 写进 ``run/desktop.pid`` 以便「停止」入口使用；
3. 等 ``/api/v1/health`` 就绪后，用 Edge/Chrome 的 "app 模式" 打开一个无地址栏的窗口
   （失败则回落到默认浏览器）；
4. 关掉那个应用窗口时默认**自动停服务**（``--keep-running`` 可关掉这个行为）；
5. 另有 ``--stop``：通知正在运行的实例退出（桌面「停止」快捷方式用），不启动新服务。

打包（PyInstaller）后资源/数据路径由 ``app/core/paths.py`` 决定：
资源在解包目录、数据在 ``%LOCALAPPDATA%\\FlowMask\\data``、模型权重放 exe 同级 ``models/``。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _free_port(preferred: int, host: str = "127.0.0.1", attempts: int = 20) -> int:
    """从 ``preferred`` 起找一个能绑定的端口；全占满时抛错。"""
    for offset in range(attempts):
        port = preferred + offset
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            # 不要设 SO_REUSEADDR：Windows 上它允许抢占已监听端口，探测会误判为可用
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise SystemExit(f"{host}:{preferred}~{preferred + attempts - 1} 都被占用，请用 --port 指定其它端口")


def _open_window(url: str) -> tuple[str, subprocess.Popen | None]:
    """优先用 Chromium 系浏览器的 app 模式打开（像独立程序窗口），否则退回默认浏览器。

    返回 ``(用哪种方式打开的, 进程句柄或 None)``；进程句柄用来"窗口关掉就停服务"。
    """
    candidates = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files") + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        + r"\Microsoft\Edge\Application\msedge.exe",
        os.environ.get("PROGRAMFILES", r"C:\Program Files") + r"\Microsoft\Edge\Application\msedge.exe",
    ]
    for exe in candidates:
        if Path(exe).is_file():
            try:
                proc = subprocess.Popen(
                    [exe, f"--app={url}", "--window-size=1440,960"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return exe, proc
            except OSError:
                continue
    webbrowser.open(url)
    return "default-browser", None


def _wait_ready(url: str, timeout_s: float = 60.0) -> bool:
    import urllib.request

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 - 只访问本机回环
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - 启动期任何异常都视作"还没好"
            time.sleep(0.3)
    return False


# --------------------------------------------------------------------------- 运行态：pid 文件与停止


def _run_dir() -> Path:
    """运行时目录（pid 文件放这里）；优先跟随数据目录策略。"""
    with contextlib.suppress(Exception):
        from app.core.paths import writable_root

        return writable_root() / "run"
    import tempfile

    return Path(tempfile.gettempdir()) / "flowmask-run"


def _pid_file_path() -> Path:
    return _run_dir() / "desktop.pid"


def _write_pid_file(port: int) -> Path | None:
    """记录当前实例的 pid/端口，供 ``--stop`` 与桌面「停止」快捷方式定位。"""
    path = _pid_file_path()
    record = {
        "pid": os.getpid(),
        "port": port,
        "exe": sys.executable,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return path
    except OSError:
        return None


def _clear_pid_file() -> None:
    with contextlib.suppress(OSError):
        _pid_file_path().unlink()


def _read_pid_record() -> dict[str, object] | None:
    try:
        raw = _pid_file_path().read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _parse_tasklist_csv(text: str, *, exclude: int | None = None) -> list[int]:
    """解析 ``tasklist /fo csv`` 输出里的 pid 列（csv 第一列是映像名）。"""
    pids: list[int] = []
    for line in (text or "").splitlines():
        line = line.strip().strip('"')
        if not line or "PID" in line:
            continue
        columns = [col.strip('"') for col in line.split('","')]
        if len(columns) < 2:
            continue
        with contextlib.suppress(ValueError):
            pid = int(columns[1].strip('"'))
            if pid and (exclude is None or pid != exclude):
                pids.append(pid)
    return pids


def _excluded_pids() -> set[int]:
    """不能杀自己，也不能杀 onefile 的引导进程（它是我们的父进程）。"""
    excluded = {os.getpid()}
    with contextlib.suppress(OSError, AttributeError):
        parent = os.getppid()
        if parent > 0:
            excluded.add(parent)
    return excluded


def _find_flowmask_pids() -> list[int]:
    """找出正在运行的 FlowMask 进程（不含自己与自己的引导进程）。"""
    excluded = _excluded_pids()
    try:
        result = subprocess.run(
            ["tasklist", "/fi", "imagename eq FlowMask.exe", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return [pid for pid in _parse_tasklist_csv(result.stdout) if pid not in excluded]
    except (OSError, subprocess.SubprocessError):
        return []


def _terminate_pid(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _stop_running() -> int:
    """停掉正在运行的实例：先按 pid 文件精确定位，再按进程名兜底。"""
    targets: list[int] = []
    record = _read_pid_record()
    if record:
        with contextlib.suppress(TypeError, ValueError):
            pid = int(record.get("pid") or 0)
            if pid and pid != os.getpid():
                targets.append(pid)
    for pid in _find_flowmask_pids():
        if pid not in targets:
            targets.append(pid)

    killed = [pid for pid in targets if _terminate_pid(pid)]
    _clear_pid_file()
    message = f"已停止 FlowMask（pid: {', '.join(map(str, killed))}）" if killed else "没有发现正在运行的 FlowMask 实例"
    print(message, flush=True)
    _report_stop(message)
    return 0 if killed else 1


def _report_stop(message: str) -> None:
    """无控制台时可落地一句结果，便于排查「点了停止没反应」。"""
    with contextlib.suppress(OSError):
        path = _run_dir() / "last-stop.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n", encoding="utf-8")


def _watch_window(process: subprocess.Popen, on_close, *, grace_s: float = 4.0, poll_s: float = 0.5) -> str:
    """等应用窗口进程退出后回调 ``on_close``。

    Chromium 系浏览器可能把新窗口交给**已存在的浏览器实例**，此时我们拿到的进程会立刻退出；
    这种"太快退出"视为无法跟踪，不触发停止，避免刚打开就把服务关掉。
    """
    deadline = time.time() + grace_s
    while time.time() < deadline:
        if process.poll() is not None:
            return "delegated"
        time.sleep(poll_s)
    process.wait()
    on_close()
    return "closed"


def _frozen_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "FlowMask" / "logs" / "desktop.log"


def _redirect_frozen_stdio() -> Path | None:
    """打包成无控制台 exe 后没有 stdout/stderr：统一写到日志文件，便于排障。

    依次尝试 ``paths.writable_root()``（会自动处理"用户目录不可写→便携模式"）、
    ``%LOCALAPPDATA%``、系统临时目录、exe 所在目录——任何一个可写就用它，
    避免"日志目录建不出来"导致启动失败原因彻底丢失。
    """
    import tempfile

    candidates: list[Path] = []
    with contextlib.suppress(Exception):
        from app.core.paths import writable_root

        candidates.append(writable_root() / "logs" / "desktop.log")
    candidates.append(_frozen_log_path())
    with contextlib.suppress(OSError):
        candidates.append(Path(tempfile.gettempdir()) / "flowmask-logs" / "desktop.log")
    with contextlib.suppress(OSError):
        candidates.append(Path(sys.executable).resolve().parent / "logs" / "desktop.log")
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = open(path, "a", encoding="utf-8", buffering=1)
        except OSError:
            continue
        sys.stdout = stream
        sys.stderr = stream
        return path
    return None


def _dump_error(detail: str, log_path: Path | None) -> str:
    """把启动异常同时写到所有可能的位置，返回写给用户看的提示。"""
    written: list[str] = []
    candidates = [log_path] + [
        Path(sys.executable).resolve().parent / "FlowMask-error.txt",
        Path(__file__).resolve().parent / "FlowMask-error.txt",
    ]
    for path in candidates:
        if path is None:
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(detail, encoding="utf-8")
            written.append(str(path))
        except OSError:
            continue
    return "；".join(written) if written else "（未能写日志）"


def _show_error(message: str) -> None:
    """无控制台环境下用系统弹窗告知失败原因（可用 FLOWMASK_NO_ERROR_DIALOG=1 关掉）。"""
    if os.environ.get("FLOWMASK_NO_ERROR_DIALOG") == "1":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "FlowMask 启动失败", 0x10)
    except Exception:  # noqa: BLE001 - 弹窗失败不影响日志
        pass


def _self_check_report() -> str:
    """自检：把打包/运行相关的关键路径与可写性打成一份报告（``--self-check`` 用）。

    用户双击没反应时，让他跑 ``FlowMask.exe --self-check`` 并把生成的
    ``FlowMask-selfcheck.txt`` 发回来，比"猜"快得多。
    """
    from app.core import paths

    lines = [
        "FlowMask 自检报告",
        f"frozen={bool(getattr(sys, 'frozen', False))}",
        f"executable={sys.executable}",
        f"meipass={getattr(sys, '_MEIPASS', '')}",
        f"resource_root={paths.resource_root()}",
        f"writable_root={paths.writable_root()}",
        f"default_data_dir={paths.default_data_dir()}",
        f"frontend_dir={paths.frontend_dir()} (存在: {paths.frontend_dir().is_dir()})",
        f"webui_dir={paths.resource_root() / 'app' / 'webui'} (存在: {(paths.resource_root() / 'app' / 'webui').is_dir()})",
        f"log_path={_frozen_log_path()}",
        f"pid_file={_pid_file_path()}",
    ]
    for candidate in (_frozen_log_path(), Path(sys.executable).resolve().parent / "logs" / "desktop.log"):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with open(candidate, "a", encoding="utf-8") as handle:
                handle.write("self-check\n")
            lines.append(f"可写: {candidate} -> 是")
        except OSError as exc:
            lines.append(f"可写: {candidate} -> 否（{exc}）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="FlowMask",
        description="FlowMask 分级脱敏与审计溯源系统（本机一体机）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机回环）")
    parser.add_argument("--port", type=int, default=8765, help="端口（默认 8765，被占用自动顺延）")
    parser.add_argument("--data-dir", default="", help="数据目录（默认写到系统 LOCALAPPDATA 下的 FlowMask\\data；源码运行时为 server/data）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开窗口（脚本/服务器用）")
    parser.add_argument("--no-app-window", action="store_true", help="用默认浏览器打开，不用 Chrome/Edge app 模式")
    parser.add_argument("--enable-l2", action="store_true", help="启用 L2 语义模型（需 l2_models/bert-sens-onnx）")
    parser.add_argument("--enable-l3", action="store_true", help="启用 L3 大模型复核（需 models/qwen2.5-1.5b-instruct 与已启动的 l3_service）")
    parser.add_argument("--enable-ner", action="store_true", help="启用自由文本 NER（需 models/bert4ner）")
    parser.add_argument("--keep-running", action="store_true", help="关掉应用窗口后服务继续在后台运行（默认关窗口即停）")
    parser.add_argument("--stop", action="store_true", help="停止正在运行的 FlowMask 实例后退出（桌面「停止」快捷方式用）")
    parser.add_argument("--self-check", action="store_true", help="打印/落盘一份运行环境自检报告后退出（排障用）")
    args = parser.parse_args(argv)

    if args.stop:
        return _stop_running()

    if args.self_check:
        report = _self_check_report()
        target = Path(sys.executable).resolve().parent / "FlowMask-selfcheck.txt"
        with contextlib.suppress(OSError):
            target.write_text(report, encoding="utf-8")
        print(report, flush=True)
        return 0

    # 环境变量必须在导入 app 之前设置：Settings 是进程内单例
    if args.data_dir:
        os.environ["FLOWMASK_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    if args.enable_l2:
        os.environ["FLOWMASK_ENABLE_L2"] = "1"
    if args.enable_l3:
        os.environ["FLOWMASK_ENABLE_L3"] = "1"
    if args.enable_ner:
        os.environ["FLOWMASK_ENABLE_NER"] = "1"
        os.environ.setdefault("FLOWMASK_NER_BACKEND", "token")
        if "FLOWMASK_NER_MODEL_DIR" not in os.environ:
            from app.core.paths import resolve_model_dir

            os.environ["FLOWMASK_NER_MODEL_DIR"] = str(resolve_model_dir("models", "bert4ner"))

    host = args.host
    port = _free_port(args.port, host)
    url = f"http://{host}:{port}/app/"

    import uvicorn

    from app.main import create_app

    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False))

    def _request_exit() -> None:
        print("[FlowMask] 应用窗口已关闭，正在停止服务…", flush=True)
        server.should_exit = True

    def _open_when_ready() -> None:
        if args.no_browser:
            return
        health = f"http://{host}:{port}/api/v1/health"
        if not _wait_ready(health):
            print(f"[FlowMask] 服务未在预期时间内就绪，请手动打开：{url}", flush=True)
            return
        if args.no_app_window:
            webbrowser.open(url)
            print(f"[FlowMask] 已请求默认浏览器打开：{url}", flush=True)
            return
        opened_with, window_proc = _open_window(url)
        print(f"[FlowMask] 已用 {opened_with} 打开应用窗口：{url}", flush=True)
        if window_proc is not None and not args.keep_running:
            outcome = _watch_window(window_proc, _request_exit)
            if outcome == "delegated":
                print("[FlowMask] 窗口交给已有浏览器实例，无法跟踪关闭事件；请用「停止」快捷方式退出", flush=True)

    threading.Thread(target=_open_when_ready, daemon=True).start()

    print("", flush=True)
    print("  FlowMask · 网络与文档敏感数据分级脱敏与审计溯源", flush=True)
    print(f"  本机访问：{url}", flush=True)
    print(f"  内置单页：http://{host}:{port}/ui/", flush=True)
    print(f"  接口文档：http://{host}:{port}/docs", flush=True)
    print("  关窗口即停服务（--keep-running 可保留后台）；按 Ctrl+C 也可退出", flush=True)
    print("", flush=True)

    _write_pid_file(port)
    try:
        server.run()
    finally:
        _clear_pid_file()
    return 0


if __name__ == "__main__":
    frozen = bool(getattr(sys, "frozen", False))
    log_path = _redirect_frozen_stdio() if frozen else None
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - exe 双击运行时必须给人看得懂的反馈
        import traceback

        detail = traceback.format_exc()
        with contextlib.suppress(Exception):
            print(detail, file=sys.stderr, flush=True)
        where = _dump_error(detail, log_path)
        _show_error(f"启动失败：{exc}\n\n详细日志：{where}")
        raise
