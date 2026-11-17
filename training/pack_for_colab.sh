#!/bin/bash
# 把訓練需要的檔案打包成一個 zip，上傳到 Google 雲端硬碟給 Colab 使用
# 用法（在 smart-health-diary 資料夾裡執行）：bash training/pack_for_colab.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(dirname "$ROOT")/smart-health-diary-colab.zip"
cd "$ROOT"
rm -f "$OUT"
zip -qr "$OUT" backend/yolov9 backend/requirements.txt backend/requirements-train.txt training \
  -x "*/runs/*" "*__pycache__*" "*.DS_Store" "training/foodgroups/test_tray_todo/*"
echo "已產生 ${OUT}（$(du -h "$OUT" | cut -f1)），請上傳到 Google 雲端硬碟的「我的雲端硬碟」根目錄。"
