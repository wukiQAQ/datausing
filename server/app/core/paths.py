"""运行路径解析：让同一套代码既能以「源码目录」运行，也能被 PyInstaller 打成单文件 exe。

为什么要单独一层：
- 源码运行时，资源（`web/dist`、`samples/`、`eval/`）在仓库里，可写数据放在 `server/data/`；
- 打成 exe 后，资源被解包到 ``sys._MEIPASS``（只读临时目录），
  可写数据必须落到用户目录（``%LOCALAPPDATA%\\FlowMask\\data``），否则升级/多用户会丢数据；
- 模型权重体积大，不打包进 exe，约定放在 exe 同级的 ``models/``、``l2_models/`` 下。

约定（打包后目录结构）::

    FlowMask/                 ← 用户解压后看到的目录
      FlowMask.exe
      models/                 ← 可选：L3 / NER 权重
      l2_models/              ← 可选：L2 ONNX 模型
      web/dist/               ← 也可外置覆盖（默认用 exe 内置的）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_FROZEN = bool(getattr(sys, "frozen", False))


def is_frozen() -> bool:
    """当前进程是否运行在 PyInstaller 打出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def executable_dir() -> Path:
    """exe 所在目录（源码运行时退化为仓库的 ``server/``）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    """只读资源根目录：源码运行 = ``server/``；打包运行 = PyInstaller 解包目录。"""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass)
        return executable_dir()
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    """拼接只读资源路径（如 ``resource_path("samples", "demo-traffic.pcap")``）。"""
    return resource_root().joinpath(*parts)


def _is_writable_dir(path: Path) -> bool:
    """探测目录可写：建目录 + 写一个临时文件再删掉。

    某些机器上 ``%LOCALAPPDATA%\\FlowMask`` 会被安全软件/组策略拒绝访问（WinError 5），
    不能假设"用户目录一定可写"，否则双击 exe 会直接建库失败。
    """
    probe = path / ".flowmask-write-probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def writable_root() -> Path:
    """可写根目录：打包后优先用户目录，不可写时回落到 exe 同级（便携模式）。"""
    if not is_frozen():
        return Path(__file__).resolve().parents[2]
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    preferred = Path(base) / "FlowMask"
    if _is_writable_dir(preferred):
        return preferred
    portable = executable_dir()
    if _is_writable_dir(portable):
        return portable
    return Path(os.environ.get("TEMP") or str(Path.home())) / "FlowMask"


def default_data_dir() -> Path:
    """默认数据目录（SQLite / 上传临时文件 / 实时旁路抓包）。"""
    return writable_root() / "data"


def frontend_dir() -> Path:
    """正式前端构建产物目录；不存在时服务端会回落到内置 ``/ui`` 单页。"""
    env = os.environ.get("FLOWMASK_FRONTEND_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if not is_frozen():
        # 源码运行：前端在仓库根的 web/dist（与 server/ 同级）
        return Path(__file__).resolve().parents[3] / "web" / "dist"
    # 打包后允许用户把 dist 放在 exe 旁边覆盖内置版本（便于只换前端不重打包）
    external = executable_dir() / "web" / "dist"
    if external.is_dir():
        return external
    return resource_path("web", "dist")


def model_search_dirs(*parts: str) -> list[Path]:
    """模型目录候选：先找 exe 同级（用户自己放权重），再找打包资源目录。"""
    dirs: list[Path] = [executable_dir().joinpath(*parts)]
    if is_frozen():
        dirs.append(resource_path(*parts))
    return dirs


def resolve_model_dir(*parts: str) -> Path:
    """返回第一个存在的模型目录，都不存在时返回 exe 同级路径（便于报错信息可读）。"""
    candidates = model_search_dirs(*parts)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]
