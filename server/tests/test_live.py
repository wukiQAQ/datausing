"""实时旁路测试：增量 pcap 跟随 + 外部推送 + 跟随窗口落库。

用「代码写 pcap 文件、按真实节奏追加」的方式模拟持续抓包，
所以用例不依赖管理员权限，也不依赖 Npcap。
"""

from __future__ import annotations

import struct
import time
from pathlib import Path

import pytest

from app.api.schemas import TrafficIngestOut
from app.core.config import get_settings
from app.traffic import LiveError, PcapFollower, write_pcap
from app.traffic.live import reset_live_manager_for_tests
from tests.test_traffic import demo_pcap, tcp_frame

PHONE = "13800138000"
ID_CARD = "11010519491231002X"


def _record(ts: float, frame: bytes) -> bytes:
    sec = int(ts)
    usec = int(round((ts - sec) * 1_000_000))
    return struct.pack("<IIII", sec, usec, len(frame), len(frame)) + frame


def _pcap_header() -> bytes:
    return struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)


def _synth_frame(port: int, body: str) -> bytes:
    return tcp_frame(port, body.encode("utf-8"))


@pytest.fixture()
def live_manager():
    reset_live_manager_for_tests()
    yield
    reset_live_manager_for_tests()


@pytest.fixture()
def data_dir(tmp_env) -> Path:
    return get_settings().data_dir


def _wait(predicate, timeout: float = 8.0, interval: float = 0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


# ---------------- 增量解析 ----------------


def test_follower_reads_incrementally(tmp_path: Path) -> None:
    path = tmp_path / "live.pcap"
    frame_a = _synth_frame(50001, '{"phone":"13800138000"}')
    frame_b = _synth_frame(50002, "客户张伟，电话 13700137000")
    path.write_bytes(_pcap_header() + _record(100.0, frame_a))

    follower = PcapFollower(path)
    first = follower.poll()
    assert len(first) == 1
    assert first[0].ts == pytest.approx(100.0)
    assert follower.linktype == 1

    # 半条记录：不能报错、也不产出
    payload = _record(101.0, frame_b)
    path.write_bytes(path.read_bytes() + payload[:20])
    assert follower.poll() == []

    # 补上剩余字节 → 解析出第二条
    path.write_bytes(path.read_bytes() + payload[20:])
    second = follower.poll()
    assert len(second) == 1
    assert second[0].ts == pytest.approx(101.0)


def test_follower_waits_for_file(tmp_path: Path) -> None:
    follower = PcapFollower(tmp_path / "not-yet.pcap")
    assert follower.poll() == []


def test_follower_rejects_pcapng(tmp_path: Path) -> None:
    path = tmp_path / "x.pcapng"
    path.write_bytes(b"\x0a\x0d\x0d\x0a" + b"\x00" * 40)
    with pytest.raises(LiveError, match="只支持经典 pcap"):
        PcapFollower(path).poll()


# ---------------- 外部推送 ----------------


def test_ingest_creates_task(client, database) -> None:
    response = client.post(
        "/api/v1/traffic/ingest",
        json={
            "source": "go-netmon",
            "segments": [
                {"path": "tcp 10.0.0.5:5001→203.0.113.10:80 POST /api/order", "text": '{"name":"张三","phone":"13800138000","id_card":"11010519491231002X"}'}
            ],
        },
    )
    assert response.status_code == 201, response.text
    out = TrafficIngestOut(**response.json())
    assert out.segments == 1 and out.task_id

    task = _wait(lambda: client.get(f"/api/v1/tasks/{out.task_id}").json()["status"] == "done")
    assert task, "任务没有在超时内完成"
    detail = client.get(f"/api/v1/tasks/{out.task_id}").json()
    assert detail["total_findings"] == 1
    assert detail["source"] == "ingest:go-netmon"

    findings = client.get(f"/api/v1/tasks/{out.task_id}/findings").json()["items"]
    assert findings[0]["level"] == "L3"
    assert "已屏蔽" in findings[0]["masked"]
    assert ID_CARD not in findings[0]["masked"]


# ---------------- 实时跟随 ----------------


def test_live_follow_and_stop(client, database, data_dir: Path, live_manager) -> None:
    pcap_path = data_dir / "live.pcap"
    pcap_path.parent.mkdir(parents=True, exist_ok=True)
    pcap_path.write_bytes(_pcap_header())

    started = client.post(
        "/api/v1/traffic/live/start",
        json={"pcap_path": "live.pcap", "interval_ms": 120, "window_s": 0.3},
    )
    assert started.status_code == 201, started.text
    live = started.json()
    live_id = live["live_id"]
    assert live["running"] is True
    assert live["pcap_path"].endswith("live.pcap")

    # 追加一帧带敏感正文的报文，等跟随线程发现它并生成任务
    body = f'{{"name":"张三","phone":"{PHONE}","id_card":"{ID_CARD}"}}'
    pcap_path.write_bytes(pcap_path.read_bytes() + _record(200.0, _synth_frame(51001, body)))

    def get_live() -> dict:
        sessions = client.get("/api/v1/traffic/live").json()["sessions"]
        return next((item for item in sessions if item["live_id"] == live_id), {})

    assert _wait(lambda: get_live().get("tasks", 0) >= 1), get_live()
    session = get_live()
    assert session["packets"] >= 1
    assert session["segments"] >= 1
    task_id = session["last_task_id"]
    assert task_id

    detail = _wait(lambda: client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "done")
    assert detail
    task = client.get(f"/api/v1/tasks/{task_id}").json()
    assert task["total_findings"] >= 1
    assert task["source"].startswith("live:")

    stopped = client.post(f"/api/v1/traffic/live/{live_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False
    assert stopped.json()["stopped_at"]

    # 已停止的会话可清理
    cleared = client.delete("/api/v1/traffic/live")
    assert cleared.status_code == 200
    assert "cleared" in cleared.json()


def test_live_rejects_path_outside_data_dir(client, database, live_manager) -> None:
    response = client.post(
        "/api/v1/traffic/live/start",
        json={"pcap_path": "C:/Windows/System32/drivers/etc/hosts"},
    )
    assert response.status_code == 400
    assert "数据目录" in response.json()["detail"]


def test_live_duplicate_start_conflict(client, database, data_dir: Path, live_manager) -> None:
    (data_dir / "dup.pcap").write_bytes(_pcap_header())
    first = client.post(
        "/api/v1/traffic/live/start",
        json={"pcap_path": "dup.pcap", "interval_ms": 120, "window_s": 1.0},
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/traffic/live/start",
        json={"pcap_path": "dup.pcap", "interval_ms": 120, "window_s": 1.0},
    )
    assert second.status_code == 409
    client.post(f"/api/v1/traffic/live/{first.json()['live_id']}/stop")


def test_live_stop_unknown_returns_404(client, database, live_manager) -> None:
    response = client.post("/api/v1/traffic/live/deadbeef/stop")
    assert response.status_code == 404


def test_demo_pcap_still_imports(client, database) -> None:
    """实时旁路不改变离线导入行为（回归）。"""
    response = client.post(
        "/api/v1/traffic/import",
        files={"pcap": ("demo.pcap", demo_pcap(), "application/vnd.tcpdump.pcap")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["sessions"] == 3
    # write_pcap 与测试里手工拼的 pcap 应能互相读取（帧结构约定一致）
    assert len(list(__import__("app.traffic", fromlist=["iter_packets"]).iter_packets(write_pcap([])))) == 0
