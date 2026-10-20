"""FlowMask 桌面入口：双击 exe（或 ``python desktop.py``）即启动本地一体机。

做的事很朴素：
1. 选一个可用端口（默认 8765，被占用就顺延）；
2. 起 FastAPI（uvicorn，同进程线程内）；
3. 等 ``/api/v1/health`` 就绪后，用 Edge/Chrome 的 "app 模式" 打开一个无地址栏的窗口
   （失败则回落到默认浏览器）；
4. 控制台保持运行，``Ctrl+C`` 退出；打不开浏览器时给出可点击的地址。

打包（PyInstaller）后资源/数据路径由 ``app/core/paths.py`` 决定：
资源在解包目录、数据在 ``%LOCALAPPDATA%\\FlowMask\\data``、模型权重放 exe 同级 ``models/``。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import socket
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


def _open_window(url: str) -> str:
    """优先用 Chromium 系浏览器的 app 模式打开（像独立程序窗口），否则退回默认浏览器。"""
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
                import subprocess

                subprocess.Popen(
                    [exe, f"--app={url}", "--window-size=1440,960"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return exe
            except OSError:
                continue
    webbrowser.open(url)
    return "default-browser"


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
    parser.add_argument("--self-check", action="store_true", help="打印/落盘一份运行环境自检报告后退出（排障用）")
    args = parser.parse_args(argv)

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
        else:
            opened_with = _open_window(url)
            print(f"[FlowMask] 已用 {opened_with} 打开应用窗口：{url}", flush=True)

    threading.Thread(target=_open_when_ready, daemon=True).start()

    print("", flush=True)
    print("  FlowMask · 网络与文档敏感数据分级脱敏与审计溯源", flush=True)
    print(f"  本机访问：{url}", flush=True)
    print(f"  内置单页：http://{host}:{port}/ui/", flush=True)
    print(f"  接口文档：http://{host}:{port}/docs", flush=True)
    print("  按 Ctrl+C 退出", flush=True)
    print("", flush=True)

    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
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
