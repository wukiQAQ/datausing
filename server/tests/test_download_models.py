import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from scripts import download_models as dm


def _fake_model_dir(base: Path, target: dm.ModelTarget, *, weight_bytes: int = 2 * 1024 * 1024) -> Path:
    dest = dm.resolve_target_dir(target, base)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "config.json").write_text(json.dumps({"architectures": ["X"]}), encoding="utf-8")
    with (dest / "model.safetensors").open("wb") as handle:
        handle.write(b"\0" * weight_bytes)
    return dest


class _Recorder:
    """记录下载调用、并可模拟产物落地/缺依赖报错。"""

    def __init__(self, error: str = "") -> None:
        self.calls: list[tuple[str, Path]] = []
        self.error = error

    def __call__(self, target: dm.ModelTarget, dest: Path) -> Path:
        self.calls.append((target.name, dest))
        if self.error:
            raise RuntimeError(self.error)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "config.json").write_text("{}", encoding="utf-8")
        return dest


def test_plan_entries_are_well_formed() -> None:
    names = [target.name for target in dm.PLAN]
    assert len(names) == len(set(names)) == len(dm.BY_NAME)
    for target in dm.PLAN:
        assert target.source in dm.DOWNLOADERS
        assert target.approx_mb > 0
        assert target.target_dir.startswith("models/")
        assert target.model_id.count("/") == 1


def test_resolve_target_dir_default_and_custom_base(tmp_path: Path) -> None:
    target = dm.BY_NAME["ner"]
    assert dm.resolve_target_dir(target) == dm.SERVER_DIR / target.target_dir
    custom = dm.resolve_target_dir(target, tmp_path)
    assert custom == tmp_path / "bert4ner"


def test_is_downloaded_requires_config_and_weight(tmp_path: Path) -> None:
    target = dm.BY_NAME["l3"]
    assert dm.is_downloaded(target, tmp_path) is False
    dest = dm.resolve_target_dir(target, tmp_path)
    dest.mkdir(parents=True)
    (dest / "config.json").write_text("{}", encoding="utf-8")
    assert dm.is_downloaded(target, tmp_path) is False, "只有 config 不能算下完"
    with (dest / "model.safetensors").open("wb") as handle:
        handle.write(b"\0" * 8)
    assert dm.is_downloaded(target, tmp_path) is False, "权重过小视为占位"
    assert dm.is_downloaded(target, tmp_path, min_bytes=4) is True


def test_download_skips_when_already_present(tmp_path: Path) -> None:
    target = dm.BY_NAME["ner"]
    _fake_model_dir(tmp_path, target)
    recorder = _Recorder()
    dest = dm.download(target, tmp_path, downloader=recorder)
    assert recorder.calls == []
    assert dest == dm.resolve_target_dir(target, tmp_path)


def test_download_calls_downloader_when_missing(tmp_path: Path) -> None:
    target = dm.BY_NAME["l3"]
    recorder = _Recorder()
    dest = dm.download(target, tmp_path, downloader=recorder)
    assert recorder.calls == [("l3", dest)]
    assert dest.is_dir()


def test_download_force_redownloads(tmp_path: Path) -> None:
    target = dm.BY_NAME["l3"]
    _fake_model_dir(tmp_path, target)
    recorder = _Recorder()
    dm.download(target, tmp_path, force=True, downloader=recorder)
    assert len(recorder.calls) == 1


def test_select_targets_default_and_all() -> None:
    assert [target.name for target in dm.select_targets(None, False)] == ["l3", "ner"]
    assert [target.name for target in dm.select_targets(None, True)] == [target.name for target in dm.PLAN]
    assert [target.name for target in dm.select_targets(["ner"], False)] == ["ner"]
    with pytest.raises(SystemExit):
        dm.select_targets(["不存在"], False)


def test_render_plan_marks_downloaded(tmp_path: Path) -> None:
    _fake_model_dir(tmp_path, dm.BY_NAME["ner"])
    text = dm.render_plan(tmp_path)
    assert "Qwen2.5-1.5B-Instruct" in text
    assert "[ok] ner" in text
    assert "合计约" in text


def test_main_list_prints_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert dm.main(["--list"]) == 0
    printed = capsys.readouterr().out
    header, *rest = printed.splitlines()
    assert "名称" in header and "目录" in header
    assert any(line.strip().startswith("l3") for line in rest)
    assert "bert4ner-base-chinese" in printed
    assert "合计约" in printed


def test_main_dry_run_does_not_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setitem(dm.DOWNLOADERS, "modelscope", recorder)
    monkeypatch.setitem(dm.DOWNLOADERS, "huggingface", recorder)
    out = io.StringIO()
    with redirect_stdout(out):
        assert dm.main(["--only", "l3", "--dry-run", "--dest", str(tmp_path)]) == 0
    assert recorder.calls == []
    assert "[dry]" in out.getvalue()


def test_main_reports_missing_dependency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(error="缺少 modelscope，请先 pip install modelscope")
    monkeypatch.setitem(dm.DOWNLOADERS, "modelscope", recorder)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = dm.main(["--only", "l3", "--dest", str(tmp_path)])
    assert code == 2
    assert "缺少 modelscope" in err.getvalue()


def test_cli_entry_is_importable_as_module() -> None:
    """`python -m scripts.download_models --list` 能跑（打包/文档都指向这个入口）。"""
    from scripts import download_models

    assert callable(download_models.main)
    assert Path(download_models.__file__).name == "download_models.py"
    assert sys.version_info >= (3, 11)
