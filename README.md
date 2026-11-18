# 智慧健康日誌：以影像辨識輔助糖尿病飲食紀錄

國立臺東大學資訊管理學系 114 級畢業專題（獨立完成，2023/10–2024/11），與台東聖母醫院國科會計畫「健康促進，科技包容」合作。

年長的糖尿病病友常常因為操作麻煩而無法持續紀錄飲食。本系統讓使用者在記錄血糖、運動的同時，上傳一張餐點照片，由 YOLOv9 模型辨識餐盤中的**全穀雜糧類、蔬菜類、豆魚蛋肉類**，使用者確認份數後換算醣類與熱量，資料存入資料庫後可在歷史頁面查看趨勢。

## 功能

- 帳號註冊、登入、登出（密碼以雜湊儲存，使用 session 驗證）
- 健康日誌：運動是否達 30 分鐘、飯前／飯後血糖、三碗蔬菜自評、三餐照片
- 食物類別辨識：依國健署每日飲食指南辨識三類食物，在照片上框出辨識結果；使用者以加減按鈕調整份數後，依食物代換表換算醣類與熱量
- 歷史資料：近 7 日統計（運動與蔬菜達成天數、平均血糖、平均每日醣類）、每日達成情形、血糖折線圖、醣類長條圖、最近的餐點紀錄
- 介面以年長使用者為對象：字級與按鈕放大，是／否以整塊按鈕點選，手機與電腦皆可使用
- 醫護端（唯讀）：病人清單標示久未記錄與血糖超出參考範圍者，可逐一查看該病人的血糖趨勢、每日達成情形與餐點照片

## 系統架構

```
瀏覽器 (frontend/)  ──HTTP/JSON──▶  Flask (backend/app.py) ──▶ SQLite
                                         │
                                         └──▶ FoodDetector (backend/detector.py)
                                               YOLOv9，伺服器啟動時載入一次
```

| 路徑 | 說明 |
|---|---|
| `backend/app.py` | Flask 後端：帳號、健康日誌、歷史統計、醫護端 API，並提供前端網頁 |
| `backend/detector.py` | 載入 YOLOv9 模型、偵測食物類別並整理為各類份數 |
| `backend/nutrition.py` | 依食物代換表換算醣類與熱量（不相依 PyTorch，便於單獨測試） |
| `backend/food_info.json` | 三類食物的每份營養素、份量說明與預設份數 |
| `backend/manage.py` | 命令列工具：建立醫護帳號、變更帳號角色 |
| `backend/schema.sql` | 資料表結構 |
| `backend/yolov9/` | YOLOv9 官方原始碼（僅保留推論與訓練所需部分） |
| `frontend/` | 網頁介面（以 Medicio 模板修改）；`staff*.html` 為醫護端 |
| `training/` | 資料集設定與訓練紀錄 |

## 執行方式

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# 將模型權重放到 weights/foodgroups_gelan_s.pt（見下方「模型權重」）
python app.py
```

開啟 http://127.0.0.1:5000 ，先註冊帳號再登入。沒有放權重檔時，系統仍可正常執行，只是照片辨識功能會停用。

可用的環境變數：`SECRET_KEY`（正式部署時必填）、`SHD_WEIGHTS`、`SHD_DB`、`SHD_DEVICE`（`cpu` 或 `0` 代表第一張 GPU）、`SHD_IMGSZ`。

### 模型權重

請從 Releases 下載 `foodgroups_gelan_s.pt`（約 15 MB），放到 `weights/` 資料夾。也可以用環境變數 `SHD_WEIGHTS` 指定其他位置。

## API

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/api/register` | 註冊（username、password、display_name） |
| POST | `/api/login` / `/api/logout` | 登入／登出 |
| GET | `/api/me` | 目前登入者資訊 |
| POST | `/api/logs` | 新增日誌（multipart，可附 `photo`），回傳辨識結果、各類別預設份數與醣類 |
| PUT | `/api/logs/<id>/servings` | 使用者確認或修改各類別份數，重新換算醣類與熱量 |
| DELETE | `/api/logs/<id>` | 刪除一筆餐點紀錄（同次填寫的血糖、運動保留） |
| GET | `/api/history?days=7` | 逐日血糖平均、每日醣類、運動與蔬菜達成情形、最近餐點 |
| GET | `/api/photos/<檔名>` | 餐點照片；僅上傳者本人或醫護帳號可取用 |
| GET | `/api/staff/patients?days=7` | 醫護端：病人清單與近 n 天摘要 |
| GET | `/api/staff/patients/<id>?days=14` | 醫護端：單一病人的統計、達成情形與餐點（含辨識框） |

## 角色與權限

帳號分為「病人」與「醫護人員」兩種角色，記錄在 `users.role`。

- 從註冊頁註冊的帳號一律是病人，**無法自行成為醫護**，避免任何人註冊後就能看到所有病人的資料。
- 醫護帳號由系統管理者在伺服器上建立：

```bash
python backend/manage.py create-staff nurse --name "王護理師"   # 會詢問密碼
python backend/manage.py list                                   # 列出所有帳號與角色
python backend/manage.py set-role <帳號> staff                  # 將既有帳號改為醫護
```

- 登入後會依角色導向：病人到健康日誌，醫護到病人清單；開到不屬於自己角色的頁面會被導回。
- 醫護端一律唯讀，不能代替病人新增或修改紀錄；醫護帳號也不能記錄自己的健康日誌。
- 餐點照片不是公開路徑，必須經過 `/api/photos/<檔名>` 並通過身分檢查才取得。

病人清單上的「需要關注」標記，依據是 `backend/app.py` 裡的兩個設定：超過 `INACTIVE_DAYS`（預設 3 天）沒有任何紀錄，或平均血糖超出 `GLUCOSE_REF`（預設飯前 80–130、飯後 180 mg/dL）。**這組數值只用來在介面上標色，不是診斷依據**；血糖目標值因人而異，實際導入時應由醫療團隊確認後調整。

## 模型

| 項目 | 內容 |
|---|---|
| 架構 | GELAN-s（YOLOv9 系列，約 720 萬參數），以官方 `gelan-s.pt` 預訓練權重微調 |
| 類別 | 全穀雜糧類、蔬菜類、豆魚蛋肉類（分類規則見 `training/LABELING.md`） |
| 資料 | 原 8 類菜色資料 + Chinese Cafeteria 資料集，轉換為三類後共 1,354 張，再加入 100 張自行標註的整盤照片；以來源圖片分組、分層切分為 train 1,015 / val 202 / test_single 207 / test_tray_holdout 30 張 |
| 訓練 | Google Colab T4 GPU，100 epochs，輸入 640 px，batch size 32，約 1.25 小時 |
| 推論 | 伺服器啟動時載入一次；T4 上每張約 9 ms |

### 測試結果（test_single，207 張，未參與訓練與模型選擇）

| 類別 | Precision | Recall | mAP50 | mAP50-95 |
|---|---|---|---|---|
| 整體 | 0.910 | 0.916 | 0.958 | 0.823 |
| 全穀雜糧類 | 0.949 | 0.846 | 0.973 | 0.919 |
| 蔬菜類 | 0.945 | 0.949 | 0.976 | 0.788 |
| 豆魚蛋肉類 | 0.836 | 0.953 | 0.924 | 0.761 |

這組測試集大多是單一菜色的特寫，**不代表模型在整盤照片上的表現**（見「限制與未來方向」第 1 點）。

### 測試結果（test_tray_holdout，30 張整盤照片，獨立標註、未參與訓練）

整盤照片（自助餐餐盤、一盤多道菜）原本是這個模型最弱的場景。這一輪把 100 張自行標註的整盤照片中的 70 張加入訓練集，另外 30 張依拍攝梯次分組保留、完全不參與訓練，用來比較加入前後的差異。兩個模型都在同一組 30 張上評估：

| 類別 | 加入整盤資料前 mAP50 | 加入後 mAP50 | 加入前 mAP50-95 | 加入後 mAP50-95 |
|---|---|---|---|---|
| 整體 | 0.084 | **0.661** | 0.032 | **0.422** |
| 全穀雜糧類 | 0.077 | 0.563 | 0.019 | 0.299 |
| 蔬菜類 | 0.085 | 0.570 | 0.038 | 0.272 |
| 豆魚蛋肉類 | 0.091 | 0.850 | 0.040 | 0.695 |

加入後的 Precision / Recall：整體 0.627 / 0.646，全穀雜糧類 0.584 / 0.512，蔬菜類 0.554 / 0.682，豆魚蛋肉類 0.741 / 0.744。

整盤照片的表現仍明顯低於單一菜色測試集（mAP50 0.661 對 0.958），其中全穀雜糧類與蔬菜類最弱；但只加入 70 張訓練照片就讓 mAP50 從 0.084 提升到 0.661，說明原本的瓶頸確實是「訓練資料裡幾乎沒有多道菜同時出現的畫面」，而不是類別本身難以區分。

### 這一版修正了原本訓練的兩個問題

- **預訓練權重沒有用上**：原本以 `yolov9.yaml`（約 7,000 萬參數）搭配 `yolov9-s` 權重（約 700 萬參數），兩者尺寸不同，1,411 層中只有 15 層能沿用，等於從零訓練。新版改用配對的 `gelan-s.yaml` 與 `gelan-s.pt`，1,339 層中 1,333 層成功沿用（只有輸出層因類別數不同而重新訓練）。
- **驗證集與訓練集重疊**：原本 16 張驗證圖全部也在訓練集中，當時的 mAP 不能代表模型的真實表現。新版以內容雜湊去除重複，並以來源圖片為單位切分。

### 重新訓練

```bash
# 1. 產生三類資料集（需要原始資料，見 training/prepare_foodgroups.py 說明）
python training/prepare_foodgroups.py --own <原本資料集>/train --cc <Chinese Cafeteria>/data --out training/foodgroups

# 2. 建立訓練環境
python3 -m venv venv && source venv/bin/activate
pip install -r backend/requirements-train.txt

# 3a. 在 Google Colab 上訓練（建議）：執行 training/pack_for_colab.sh，再開啟 training/colab_train.ipynb
# 3b. 或在 Apple Silicon Mac 上訓練；8 GB 記憶體的機器請用 BATCH=4（100 epochs 約 10 小時）
BATCH=4 bash training/train_mac.sh full

# 4. 評估（單一菜色測試集；整盤測試集補標完成後也會一起評估）
bash training/evaluate.sh <best.pt 的路徑> mps

# 4b. 在整盤測試集上評估（training/foodgroups/tray_eval.yaml）
python backend/yolov9/val.py --weights <best.pt 的路徑> --data training/foodgroups/tray_eval.yaml --task test --imgsz 640 --device mps
```

## 限制與未來方向

這一節是刻意寫清楚的，避免讓功能看起來比實際更完整。

1. **整盤照片仍明顯弱於單一菜色（目前最大的限制）**：加入 70 張整盤照片訓練後，整盤測試集的 mAP50 從 0.084 提升到 0.661，但與單一菜色的 0.958 仍有差距，全穀雜糧類（0.563）與蔬菜類（0.570）最弱。整盤照片的食物彼此緊鄰、互相遮擋，白飯又常被菜餚蓋住大半，是主要難點。整盤訓練資料目前只有 70 張，繼續增加標註量應該還有提升空間。
2. **份量並非由影像估算**：模型只判斷「有哪幾類食物」，份數由使用者依實際食量調整（系統預設值僅供參考）。若要從照片估算份量，需要有重量標註的資料；未來規劃以分割模型計算食物面積，並以碗、盤或便當盒邊緣作為比例尺。
3. **醣類換算是簡化的**：依國健署食物代換表，每份全穀雜糧類以 15 g 醣類、蔬菜類以 5 g 醣類計；全穀雜糧類的碗數換算以白飯為準（麵條等份量不同）。混合菜依主要成分歸為單一類別，次要成分的份量會被低估。
4. **全穀雜糧類的召回率偏低**：單一菜色測試集為 0.85，整盤測試集只有 0.51，代表整盤照片中約一半的全穀雜糧沒被偵測到，會低估醣類，是下一步最需要改善的地方。
5. **「三碗蔬菜」為使用者自評**，不是由影像判斷。
6. **醫護端只有唯讀功能**：可以查看病人紀錄，但還不能寫衛教備註、設定個別病人的血糖目標，也沒有匯出報表。血糖的參考範圍目前是全系統共用的固定值。
7. 尚未實作：忘記密碼（目前導向聯絡醫院）。

## 版本變更（相較於專題展出版本）

- 模型改為伺服器啟動時載入一次，不再每次請求都重新執行 `detect.py`
- 網頁的健康日誌、歷史資料、註冊登入都串接到後端與資料庫；原本的歷史圖表是寫死的示意數字
- 推論輸入大小與訓練解析度一致（舊模型原本訓練 350 px、推論卻用 640 px，造成大量漏偵測）
- 模型由 8 類菜色改為 3 類食物類別，以配對正確的預訓練權重重新訓練，並以獨立測試集評估
- 熱量改為依國健署食物代換表與使用者確認的份數換算，並增加醣類計算
- 帳號不再使用身分證字號；資料庫改為 SQLite，設定改由環境變數讀取
- 新增醫護端（病人清單與個別病人紀錄），並加入角色權限；餐點照片改為需驗證身分才能取得

## 第三方程式碼與授權

- [YOLOv9](https://github.com/WongKinYiu/yolov9)（`backend/yolov9/`），GPL-3.0，授權條款見 `backend/yolov9/LICENSE.md`。為相容較新版的 PyTorch、Pillow 與 NumPy，做了以下最小修改：載入權重時指定 `weights_only=False`（`train.py`、`models/experimental.py`、`utils/general.py`、`utils/loggers/__init__.py`）；Apple GPU 偵測改用 `torch.backends.mps.is_available()`（`utils/torch_utils.py`）；以 `getbbox` 取代已移除的 `getsize`（`utils/plots.py`）；`np.trapz` 改用 `np.trapezoid`（`utils/metrics.py`）；`np.float` 改為 `float`（`models/common.py`）。
- [Medicio](https://bootstrapmade.com/medicio-free-bootstrap-theme/) 網頁模板，BootstrapMade。修正了 `assets/js/main.js` 在沒有「回到頂端」按鈕的頁面會報錯的問題。
- [Chart.js](https://www.chartjs.org/)，MIT（`frontend/assets/vendor/chartjs/`）。

由於包含 GPL-3.0 的 YOLOv9 程式碼，本專案整體以 GPL-3.0 釋出。
