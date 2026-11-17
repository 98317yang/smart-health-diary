模型權重（檔案過大，不納入 git）

foodgroups_gelan_s.pt
  目前使用的三類模型（全穀雜糧／蔬菜／豆魚蛋肉）。
  訓練資料含 70 張整盤照片，後端預設載入這一個。

foodgroups_gelan_s_v1_no_tray.pt
  上一版三類模型，訓練資料不含整盤照片。
  保留作為 README 中「加入整盤資料前」對照數字的來源。

gelan-s.pt
  YOLOv9 官方預訓練權重，重新訓練時的起點。

legacy_8class_best.pt
  專題展出版本的 8 類菜色模型，已不使用。
