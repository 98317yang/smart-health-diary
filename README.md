# 智慧健康日誌：以影像辨識輔助糖尿病飲食紀錄

國立臺東大學資訊管理學系 114 級畢業專題（獨立完成，2023/10–2024/11），與台東聖母醫院國科會計畫「健康促進，科技包容」合作。

年長的糖尿病病友常常因為操作麻煩而無法持續紀錄飲食。本系統讓使用者在記錄血糖、運動的同時，上傳一張餐點照片，由 YOLOv9 模型辨識自助餐菜色，並推估熱量，資料存入資料庫後可在歷史頁面查看趨勢。

## 功能

- 帳號註冊、登入、登出（密碼以雜湊儲存，使用 session 驗證）
- 健康日誌：運動是否達 30 分鐘、飯前／飯後血糖、三碗蔬菜自評、三餐照片
- 菜色辨識：8 類台式自助餐菜色，並以標準份量推估熱量
- 歷史資料：近 7 日血糖折線圖、運動與蔬菜達成率、最近的餐點辨識紀錄

## 系統架構

```
瀏覽器 (frontend/)  ──HTTP/JSON──▶  Flask (backend/app.py) ──▶ SQLite
                                         │
                                         └──▶ FoodDetector (backend/detector.py)
                                               YOLOv9，伺服器啟動時載入一次
```

| 路徑 | 說明 |
|---|---|
| `backend/app.py` | Flask 後端：帳號、健康日誌、歷史統計 API，並提供前端網頁 |
| `backend/detector.py` | 載入 YOLOv9 模型、偵測菜色、依對照表推估熱量 |
| `backend/food_info.json` | 菜色中文名稱、每 100 g 熱量、標準份量 |
| `backend/schema.sql` | 資料表結構 |
| `backend/yolov9/` | YOLOv9 官方原始碼（僅保留推論與訓練所需部分） |
| `frontend/` | 網頁介面（以 Medicio 模板修改） |
| `training/` | 資料集設定與訓練紀錄 |

## 執行方式

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# 將模型權重放到 weights/best.pt（見下方「模型權重」）
python app.py
```

開啟 http://127.0.0.1:5000 ，先註冊帳號再登入。沒有放權重檔時，系統仍可正常執行，只是照片辨識功能會停用。

可用的環境變數：`SECRET_KEY`（正式部署時必填）、`SHD_WEIGHTS`、`SHD_DB`、`SHD_DEVICE`（`cpu` 或 `0` 代表第一張 GPU）、`SHD_IMGSZ`。

### 模型權重

權重檔約 136 MB，超過 GitHub 單檔上限，請從 Releases 下載 `best.pt`，放到 `weights/` 資料夾。

## API

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/api/register` | 註冊（username、password、display_name） |
| POST | `/api/login` / `/api/logout` | 登入／登出 |
| GET | `/api/me` | 目前登入者資訊 |
| POST | `/api/logs` | 新增日誌（multipart，可附 `photo`），回傳辨識結果與熱量 |
| GET | `/api/history?days=7` | 逐日血糖平均、運動與蔬菜達成情形、最近餐點 |

## 模型

- 架構：YOLOv9，以 `yolov9-s` 預訓練權重微調
- 類別（8 類）：炒青菜、娃娃菜、炒馬鈴薯絲、紅燒雞腿、紅燒魚、清炒小黃瓜、肉末茄子、番茄炒蛋
- 資料：480 張訓練圖片（每類約 60 張，大多為單一菜色特寫），來源為公開資料集（原始類別名稱為簡體中文，已改為臺灣用語）
- 訓練：200 epochs、輸入 350 px、batch size 2、SGD
- 推論輸入大小設為 352 px，與訓練解析度一致；在一般筆電 CPU 上每張照片約 1–1.5 秒

## 重新訓練：改為辨識食物類別（進行中）

目前網站使用的是原本的 8 類菜色模型。新版改為依國健署每日飲食指南辨識**全穀雜糧類、蔬菜類、豆魚蛋肉類**，以便推估醣類份數；分類規則見 `training/LABELING.md`。

原本的訓練有兩個問題，這次一併修正：

- **預訓練權重沒有用上**：原本以 `yolov9.yaml`（約 7,000 萬參數）搭配 `yolov9-s` 權重（約 700 萬參數），兩者尺寸不同，1,411 層中只有 15 層能沿用，等於從零訓練。新版改用配對的 `gelan-s.yaml` 與 `gelan-s.pt`，1,339 層中 1,333 層成功沿用（只有輸出層因類別數不同而重新訓練）。
- **驗證集與訓練集重疊**：原本 16 張驗證圖全部也在訓練集中。新版以內容雜湊去除重複，並以來源圖片為單位切分。

### 步驟

```bash
# 1. 產生三類資料集（需要原始資料，見 training/prepare_foodgroups.py 說明）
python training/prepare_foodgroups.py --own <原本資料集>/train --cc <Chinese Cafeteria>/data --out training/foodgroups

# 2. 建立訓練環境
python3 -m venv venv && source venv/bin/activate
pip install -r backend/requirements-train.txt

# 3a. 在 Apple Silicon Mac 上訓練（先測速，再正式訓練）
bash training/train_mac.sh speedtest
bash training/train_mac.sh full

# 3b. 或在 Google Colab 上訓練：執行 training/pack_for_colab.sh，再開啟 training/colab_train.ipynb

# 4. 評估（單一菜色測試集；整盤測試集補標完成後也會一起評估）
bash training/evaluate.sh backend/yolov9/runs/train/foodgroups_gelan_s/weights/best.pt mps
```

## 限制與未來方向

這一節是刻意寫清楚的，避免讓功能看起來比實際更完整。

1. **模型評估數字待更新**：原始訓練時的驗證集與訓練集有重疊，因此當時的 mAP 無法代表模型在新照片上的表現，這裡不列出。重新切分資料、重新訓練後會補上。
2. **份量並非由影像估算**：每偵測到一道菜，就以 `food_info.json` 中的「標準一份」（目前假設為 100 g）推估熱量。若要從照片估算實際份量，需要有重量標註的資料。未來規劃以分割模型計算食物面積，並以碗、盤或便當盒邊緣作為比例尺。
3. **熱量資料**：每 100 g 熱量取自 5 款熱門卡路里 App 的平均值；「肉末茄子」與「番茄炒蛋」尚未蒐集，會顯示「尚無資料」，不計入總熱量。
4. **「三碗蔬菜」為使用者自評**，不是由影像判斷。
5. 菜色只有 8 類，照片中其他食物不會被辨識。
6. 尚未實作：醫護人員的管理者介面、忘記密碼、首頁的「問題表單」（模板內建，未接後端）。

## 版本變更（相較於專題展出版本）

- 模型改為伺服器啟動時載入一次，不再每次請求都重新執行 `detect.py`
- 網頁的健康日誌、歷史資料、註冊登入都串接到後端與資料庫；原本的歷史圖表是寫死的示意數字
- 推論輸入大小由 640 改為 352，與訓練解析度一致，修正大量漏偵測的問題
- 熱量表補齊 8 個類別（未蒐集的數值明確標示，不再被當成 0 大卡）；每道菜都有自己的中文名稱
- 帳號不再使用身分證字號；資料庫改為 SQLite，設定改由環境變數讀取

## 第三方程式碼與授權

- [YOLOv9](https://github.com/WongKinYiu/yolov9)（`backend/yolov9/`），GPL-3.0，授權條款見 `backend/yolov9/LICENSE.md`。為相容較新版的 PyTorch、Pillow 與 NumPy，做了以下最小修改：載入權重時指定 `weights_only=False`（`train.py`、`models/experimental.py`、`utils/general.py`、`utils/loggers/__init__.py`）；Apple GPU 偵測改用 `torch.backends.mps.is_available()`（`utils/torch_utils.py`）；以 `getbbox` 取代已移除的 `getsize`（`utils/plots.py`）；`np.trapz` 改用 `np.trapezoid`（`utils/metrics.py`）；`np.float` 改為 `float`（`models/common.py`）。
- [Medicio](https://bootstrapmade.com/medicio-free-bootstrap-theme/) 網頁模板，BootstrapMade。修正了 `assets/js/main.js` 在沒有「回到頂端」按鈕的頁面會報錯的問題。
- [Chart.js](https://www.chartjs.org/)，MIT（`frontend/assets/vendor/chartjs/`）。

由於包含 GPL-3.0 的 YOLOv9 程式碼，本專案整體以 GPL-3.0 釋出。
