"""部署相关文件的契约测试。

这些文件（依赖清单、环境变量示例、一键启动脚本）不参与运行时逻辑，
但一旦漏项就会让「换台机器跑不起来」，所以用测试固定住关键点：

1. 依赖清单可解析，且核心包都钉了版本；
2. ``.env.example`` 覆盖了所有需要人工配置的开关；
3. 一键启动脚本引用了正确的入口，停止脚本按 pid 文件工作；
4. 配置层确实支持环境变量覆盖（含空值回落默认）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_DIR.parent

REQUIRED_ENV_KEYS = (
    "FLOWMASK_DATA_DIR",
    "FLOWMASK_DB_PATH",
    "FLOWMASK_HOST",
    "FLOWMASK_PORT",
    "FLOWMASK_API_TOKEN",
    "FLOWMASK_AUDIT_SECRET",
    "FLOWMASK_ENABLE_L2",
    "FLOWMASK_L2_ENDPOINT",
    "FLOWMASK_ENABLE_L3",
    "FLOWMASK_L3_ENDPOINT",
    "FLOWMASK_L3_MODE",
    "FLOWMASK_L3_MODEL_DIR",
    "FLOWMASK_ENABLE_NER",
    "FLOWMASK_NER_MODEL_DIR",
    "FLOWMASK_MAX_UPLOAD_MB",
    "FLOWMASK_CORS_ORIGINS",
)

_REQUIREMENT_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.\-\[\]]+)==(?P<version>[0-9][^\s;]*)$")


def _parse_requirements(path: Path) -> list[tuple[str, str]]:
    """解析 requirements 文件，返回 ``[(name, version)]``，忽略注释与空行。"""
    pinned: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _REQUIREMENT_RE.match(line)
        assert match, f"{path.name} 里的依赖未钉版本：{raw!r}"
        pinned.append((match.group("name").lower(), match.group("version")))
    return pinned


def test_requirements_core_are_pinned() -> None:
    path = SERVER_DIR / "requirements.txt"
    assert path.is_file(), "缺少 requirements.txt"
    pinned = dict(_parse_requirements(path))
    expected = {"fastapi", "uvicorn", "pydantic", "python-multipart", "pdfplumber"}
    missing = expected - set(pinned)
    assert not missing, f"requirements.txt 缺少运行依赖：{sorted(missing)}"


def test_requirements_ml_and_dev_are_consistent() -> None:
    ml = dict(_parse_requirements(SERVER_DIR / "requirements-ml.txt"))
    # 真模型链路的关键依赖：ONNX 推理 + 训练/导出 + 权重下载
    for name in ("onnxruntime", "torch", "transformers", "onnx", "onnxscript", "modelscope"):
        assert name in ml, f"requirements-ml.txt 缺少 {name}"

    dev_text = (SERVER_DIR / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in dev_text
    dev = dict(_parse_requirements(SERVER_DIR / "requirements-dev.txt"))
    assert "pytest" in dev and "httpx" in dev


def test_env_example_covers_switches() -> None:
    path = SERVER_DIR / ".env.example"
    assert path.is_file(), "缺少 .env.example"
    text = path.read_text(encoding="utf-8")
    assigned = set(re.findall(r"^(FLOWMASK_[A-Z0-9_]+)=", text, re.MULTILINE))
    missing = set(REQUIRED_ENV_KEYS) - assigned
    assert not missing, f".env.example 缺少变量：{sorted(missing)}"


@pytest.mark.parametrize("script", ["start_local.ps1", "stop_local.ps1", "start_local.sh", "stop_local.sh"])
def test_scripts_exist(script: str) -> None:
    assert (SERVER_DIR / "scripts" / script).is_file(), f"缺少 scripts/{script}"


def test_start_scripts_reference_entrypoints() -> None:
    for script in ("start_local.ps1", "start_local.sh"):
        text = (SERVER_DIR / "scripts" / script).read_text(encoding="utf-8")
        assert "app.main:app" in text, f"{script} 没有用 uvicorn 启动主服务"
        assert "api/v1/health" in text, f"{script} 没有等待健康检查"
        assert "l2_service" in text, f"{script} 没有接入 L2 服务"
        assert "l3_service" in text, f"{script} 没有接入 L3 服务"


def test_stop_scripts_use_pid_files() -> None:
    ps = (SERVER_DIR / "scripts" / "stop_local.ps1").read_text(encoding="utf-8")
    sh = (SERVER_DIR / "scripts" / "stop_local.sh").read_text(encoding="utf-8")
    assert "*.pid" in ps and "Stop-Process" in ps
    assert "*.pid" in sh and "kill" in sh


def test_env_example_is_not_secret_leak() -> None:
    """.env.example 只能放占位值，不能出现真实 token/密钥。"""
    text = (SERVER_DIR / ".env.example").read_text(encoding="utf-8")
    # API Token 必须留空（不能把某个真实 token 抄进示例）
    assert re.search(r"^FLOWMASK_API_TOKEN=\s*$", text, re.MULTILINE)
    # 不允许出现 GitHub PAT / OpenAI 系密钥的形态（"mask-" 里的 sk- 不算）
    assert "ghp_" not in text
    assert not re.search(r"sk-[A-Za-z0-9]{16,}", text)


def test_settings_reads_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("FLOWMASK_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLOWMASK_API_TOKEN", "demo-token")
    monkeypatch.setenv("FLOWMASK_ENABLE_NER", "1")
    monkeypatch.setenv("FLOWMASK_L3_TIMEOUT_S", "2.5")
    monkeypatch.setenv("FLOWMASK_CORS_ORIGINS", "http://127.0.0.1:5173")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.data_dir == tmp_path.resolve()
        assert settings.db_path == tmp_path.resolve() / "flowmask.db"
        assert settings.api_token == "demo-token"
        assert settings.enable_ner is True
        assert settings.l3_timeout_s == 2.5
        assert settings.cors_origins == ("http://127.0.0.1:5173",)
    finally:
        get_settings.cache_clear()


def test_settings_blank_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """``.env`` 里留空的项必须回落默认值，而不是变成空字符串。"""
    from app.core.config import get_settings

    monkeypatch.setenv("FLOWMASK_API_TOKEN", "")
    monkeypatch.setenv("FLOWMASK_MAX_UPLOAD_MB", "")
    monkeypatch.setenv("FLOWMASK_ENABLE_L2", "")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.api_token == ""
        assert settings.max_upload_mb == 20
        assert settings.enable_l2 is False
    finally:
        get_settings.cache_clear()


def test_run_dir_is_gitignored() -> None:
    """启动脚本把 pid/日志写进 server/data/run，必须已被 gitignore。"""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "server/data/" in gitignore
