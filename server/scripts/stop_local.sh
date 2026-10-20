#!/usr/bin/env bash
# 停止由 scripts/start_local.sh 启动的所有 FlowMask 进程。
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$SERVER_DIR/data/run"

if [[ ! -d "$RUN_DIR" ]]; then
  echo "[flowmask] 没有运行记录（data/run 不存在），无需停止"
  exit 0
fi

stopped=0
shopt -s nullglob
for pid_file in "$RUN_DIR"/*.pid; do
  name="$(basename "$pid_file" .pid)"
  pid="$(head -n 1 "$pid_file" 2>/dev/null || true)"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    echo "[flowmask] $name 的 pid 文件内容无效，仅删除该文件" >&2
    rm -f "$pid_file"
    continue
  fi
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    echo "[flowmask] 已停止 $name pid=$pid"
    stopped=$((stopped + 1))
  else
    echo "[flowmask] $name pid=$pid 已退出，清理记录"
  fi
  rm -f "$pid_file"
done

echo "[flowmask] 完成：本次停止 $stopped 个进程"
