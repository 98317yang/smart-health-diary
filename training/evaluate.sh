#!/bin/bash
# 用訓練好的模型評估各個測試集
#
# 用法（在 smart-health-diary 資料夾裡執行）：
#   bash training/evaluate.sh <best.pt 的路徑> [device]
#   例：bash training/evaluate.sh backend/yolov9/runs/train/foodgroups_gelan_s/weights/best.pt mps
#
# 會依序評估：
#   1. test_single：單一菜色測試集（與訓練集不重疊）
#   2. test_tray_holdout：整盤測試集中保留、未參與訓練的 30 張
set -euo pipefail

W="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
DEVICE="${2:-cpu}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FG="$ROOT/training/foodgroups"
export PYTORCH_ENABLE_MPS_FALLBACK=1
cd "$ROOT/backend/yolov9"

echo "=== 單一菜色測試集（test_single）==="
python val.py --data "$FG/foodgroups.yaml" --weights "$W" --img 640 --task test \
  --device "$DEVICE" --name eval_test_single --exist-ok

if [ -d "$FG/test_tray_holdout" ]; then
  cat > "$FG/test_tray_holdout.yaml" <<EOF
path: ../../training/foodgroups
train: train/images
val: test_tray_holdout/images
nc: 3
names: ['grains_starches', 'vegetables', 'protein']
EOF
  echo
  echo "=== 整盤測試集（test_tray_holdout，30 張、未參與訓練）==="
  python val.py --data "$FG/test_tray_holdout.yaml" --weights "$W" --img 640 --task val \
    --device "$DEVICE" --name eval_test_tray --exist-ok
else
  echo
  echo "（尚未找到 training/foodgroups/test_tray_holdout，整盤測試集補標完成後再評估）"
fi
