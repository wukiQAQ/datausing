"""批量提交接口测试：多文件一次提交、坏文件不影响好文件、数量上限。"""

from __future__ import annotations

import time

ID_CARD = "11010519491231002X"


def _wait_done(client, task_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/tasks/{task_id}").json()
        if body["status"] in {"done", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError("任务在超时时间内未完成")


def _parts():
    """3 个文件：JSON（含手机号）、txt（含手机号）、伪造 docx（解析必失败）。"""
    order = ("order.json", b'{"name": "\xe5\xbc\xa0\xe4\xb8\x89", "phone": "13800138000"}')
    note = ("note.txt", "客户张伟，电话 13700137000。".encode("utf-8"))
    broken = ("page.docx", b"\x00\x01this-is-not-a-docx")
    return [("files", order), ("files", note), ("files", broken)]


def test_batch_tasks_partial_success(client) -> None:
    response = client.post("/api/v1/tasks/batch", files=_parts())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["total"] == 3
    assert body["accepted"] == 2, body

    ok = [item for item in body["items"] if item["task_id"]]
    bad = [item for item in body["items"] if not item["task_id"]]
    assert len(ok) == 2 and len(bad) == 1
    assert bad[0]["filename"] == "page.docx"
    assert bad[0]["error"], "解析失败的文件要给出原因"

    for item in ok:
        assert item["segments"] >= 1
        task = _wait_done(client, item["task_id"])
        assert task["status"] == "done"
        findings = client.get(f"/api/v1/tasks/{item['task_id']}/findings").json()
        assert findings["total"] >= 1, findings


def test_batch_tasks_rejects_too_many_files(client) -> None:
    files = [("files", (f"f{i}.txt", b"hello")) for i in range(21)]
    response = client.post("/api/v1/tasks/batch", files=files)
    assert response.status_code == 400
    assert "20" in response.json()["detail"]


def test_batch_tasks_rejects_empty(client) -> None:
    response = client.post("/api/v1/tasks/batch", files=[])
    assert response.status_code in {400, 422}
