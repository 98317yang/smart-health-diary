#!/bin/bash
# 在 Apple Silicon Mac 上訓練三類食物模型（使用 Mac 的 GPU：MPS）
#
# 用法（在 smart-health-diary 資料夾裡執行）：
#   bash training/train_mac.sh speedtest   # 只跑 1 個 epoch，看看要花多久
#   bash training/train_mac.sh full        # 正式訓練 100 個 epoch
#
# 第一次使用前請先建立環境：
#   python3 -m venv venv && source venv/bin/activate
#   pip install -r backend/requirements-train.txt
set -euo pipefail

MODE="${1:-speedtest}"
case "$MODE" in
  speedtest) EPOCHS=1;   NAME="speedtest" ;;
  full)      EPOCHS=100; NAME="foodgroups_gelan_s" ;;
  *) echo "用法：bash training/train_mac.sh [speedtest|full]"; exit 1 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEIGHTS="$ROOT/weights/gelan-s.pt"
DATA="$ROOT/training/foodgroups/foodgroups.yaml"

if [ ! -f "$DATA" ]; then
  echo "找不到 ${DATA}，請先執行 training/prepare_foodgroups.py 產生資料集"; exit 1
fi
if [ ! -f "$WEIGHTS" ]; then
  echo "下載 gelan-s 預訓練權重…"
  curl -L --fail -o "$WEIGHTS" https://github.com/WongKinYiu/yolov9/releases/download/v0.1/gelan-s.pt
fi

# 部分運算 MPS 不支援，讓它們自動改用 CPU
export PYTORCH_ENABLE_MPS_FALLBACK=1

cd "$ROOT/backend/yolov9"
echo "開始訓練（${MODE}，${EPOCHS} epochs）。訓練期間請接上電源；caffeinate 會讓 Mac 不進入睡眠。"
# 記憶體不足（out of memory，或 swap 用量暴增、速度變得極慢）時改用 batch 4：
#   BATCH=4 bash training/train_mac.sh full
BATCH="${BATCH:-8}"
echo "batch size：${BATCH}"
caffeinate -i python train.py \
  --cfg models/detect/gelan-s.yaml \
  --weights "$WEIGHTS" \
  --data "$DATA" \
  --hyp data/hyps/hyp.scratch-high.yaml \
  --img 640 --batch "$BATCH" --epochs "$EPOCHS" \
  --device mps --workers 2 \
  --close-mosaic 15 \
  --name "$NAME"

echo
echo "完成。結果在 backend/yolov9/runs/train/ 底下最新的 $NAME 資料夾。"
if [ "$MODE" = "speedtest" ]; then
  echo "上面「1 epochs completed in X hours」的 X 乘以 100，就是正式訓練大約要花的時間（小時）。"
fi
