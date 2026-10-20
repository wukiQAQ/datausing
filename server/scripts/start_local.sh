#!/usr/bin/env bash
# FlowMask 本机一键启动（Linux / macOS）
#
# 用法：
#   ./scripts/start_local.sh                     # 只起主服务
#   ./scripts/start_local.sh --with-l2 --ner     # 叠加 L2 与 NER
#   ./scripts/start_local.sh --with-l2 --with-l3 --l3-device cuda
#   ./scripts/start_local.sh --port 8095 --no-browser
#
# 停止：./scripts/stop_local.sh
# 日志：server/data/run/*.log
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$SERVER_DIR/data/run"
PYTHON="${FLOWMASK_PYTHON:-python3}"

PORT=8090
BIND_HOST="127.0.0.1"
WITH_L2=0
WITH_L3=0
ENABLE_NER=0
L3_DEVICE="cpu"
OPEN_BROWSER=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --host) BIND_HOST="$2"; shift 2 ;;
    --with-l2) WITH_L2=1; shift ;;
    --with-l3) WITH_L3=1; shift ;;
    --ner) ENABLE_NER=1; shift ;;
    --l3-device) L3_DEVICE="$2"; shift 2 ;;
    --no-browser) OPEN_BROWSER=0; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

mkdir -p "$RUN_DIR"
cd "$SERVER_DIR"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "找不到 $PYTHON：请安装 Python 3.12+，或用 FLOWMASK_PYTHON 指定解释器" >&2
  exit 1
fi
echo "[flowmask] python = $(command -v "$PYTHON")"
echo "[flowmask] 工作目录 = $SERVER_DIR"

# 加载 .env（KEY=VALUE 形式，忽略注释）
if [[ -f "$SERVER_DIR/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ -z "${key// }" || "${key:0:1}" == "#" ]] && continue
    export "${key// }=${value}"
  done < "$SERVER_DIR/.env"
  echo "[flowmask] 已加载 .env"
fi

start_service() {
  local name="$1"; shift
  local out="$RUN_DIR/$name.log"
  local err="$RUN_DIR/$name.err.log"
  nohup "$@" >"$out" 2>"$err" &
  echo $! >"$RUN_DIR/$name.pid"
  echo "[flowmask] 启动 $name pid=$(cat "$RUN_DIR/$name.pid") 日志=$out"
}

if [[ "$WITH_L2" == "1" ]]; then
  L2_MODEL="$SERVER_DIR/l2_models/bert-sens-onnx"
  if [[ -d "$L2_MODEL" ]]; then
    FLOWMASK_L2_MODE=onnx FLOWMASK_L2_MODEL_DIR="$L2_MODEL" FLOWMASK_L2_PORT=8091 \
      start_service l2 "$PYTHON" -m l2_service
  else
    echo "[flowmask] 未找到 L2 模型 $L2_MODEL，跳过 L2" >&2
    WITH_L2=0
  fi
fi

if [[ "$WITH_L3" == "1" ]]; then
  L3_MODEL="$SERVER_DIR/models/qwen2.5-1.5b-instruct"
  if [[ -d "$L3_MODEL" ]]; then
    FLOWMASK_L3_MODE=instruct FLOWMASK_L3_MODEL_DIR="$L3_MODEL" FLOWMASK_L3_DEVICE="$L3_DEVICE" \
      FLOWMASK_L3_PORT=8092 start_service l3 "$PYTHON" -m l3_service
  else
    echo "[flowmask] 未找到 L3 模型 $L3_MODEL，跳过 L3" >&2
    WITH_L3=0
  fi
fi

export FLOWMASK_HOST="$BIND_HOST"
export FLOWMASK_PORT="$PORT"
export FLOWMASK_DATA_DIR="$SERVER_DIR/data"
[[ "$WITH_L2" == "1" ]] && export FLOWMASK_ENABLE_L2=1
[[ "$WITH_L3" == "1" ]] && export FLOWMASK_ENABLE_L3=1
if [[ "$ENABLE_NER" == "1" ]]; then
  if [[ -d "$SERVER_DIR/models/bert4ner" ]]; then
    export FLOWMASK_ENABLE_NER=1
    export FLOWMASK_NER_BACKEND=token
    export FLOWMASK_NER_MODEL_DIR="$SERVER_DIR/models/bert4ner"
  else
    echo "[flowmask] 未找到 NER 模型，保持关闭" >&2
  fi
fi

start_service main "$PYTHON" -m uvicorn app.main:app --host "$BIND_HOST" --port "$PORT"

BASE="http://$BIND_HOST:$PORT"
echo ""
echo "[flowmask] 等待主服务就绪……"
for _ in $(seq 1 90); do
  if curl -fsS "$BASE/api/v1/health" >/dev/null 2>&1; then
    echo ""
    echo "[flowmask] 就绪："
    echo "  正式前端（Vue3 + Element Plus）: $BASE/app/"
    echo "  内置单页（零依赖）            : $BASE/ui/"
    echo "  OpenAPI 文档                  : $BASE/docs"
    echo "  健康检查                      : $BASE/api/v1/health"
    echo ""
    echo "[flowmask] 停止：./scripts/stop_local.sh"
    if [[ "$OPEN_BROWSER" == "1" ]] && command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$BASE/app/" >/dev/null 2>&1 || true
    fi
    exit 0
  fi
  sleep 1
done

echo "[flowmask] 主服务未在 90s 内就绪，请查看 data/run/main.err.log" >&2
exit 1
