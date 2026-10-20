# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把 FlowMask 主服务 + 正式前端打成一个 Windows 单文件 exe。

用法（在 server/ 目录下）：
    python -m PyInstaller flowmask.spec --noconfirm --distpath dist_exe --workpath build_exe

产物：``server/dist_exe/FlowMask.exe``（双击启动，自动开一个应用窗口）。
说明：
- 打包进去的只有「主服务」：L1 规则 + 解析 + 任务/审计/API + 前端静态资源 + 示例抓包；
- L2/L3/NER 的模型权重体积大（GB 级），**不打进 exe**，放 exe 同级的 ``l2_models/``、``models/`` 即可被识别；
- torch/onnxruntime/transformers 不打包（exe 只跑主服务，模型服务用 python 单独起，见 README 第 3/4 章）。
"""

from PyInstaller.utils.hooks import collect_submodules

# 只读资源：前端构建产物、自带单页控制台、示例抓包、评测集（体积都很小）
datas = [
    ("../web/dist", "web/dist"),
    ("app/webui", "app/webui"),
    ("samples", "samples"),
    ("eval", "eval"),
]

# app 内部模块动态导入较多（路由、解析器注册表），显式收集；uvicorn 的协议/循环实现靠 hiddenimports
hiddenimports = collect_submodules("app") + [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "multipart",
    "pdfplumber",
]

# 明确排除重依赖：它们只被 L2/L3/NER/训练侧使用，主服务运行时不 import
excludes = [
    "tkinter",
    "matplotlib",
    "torch",
    "transformers",
    "modelscope",
    "onnx",
    "onnxruntime",
    "onnxscript",
    "sentencepiece",
    "l2_service",
    "l3_service",
    "l2_training",
]

a = Analysis(
    ["desktop.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FlowMask",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 双击后不弹黑窗；日志写 %LOCALAPPDATA%\FlowMask\logs\desktop.log
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
