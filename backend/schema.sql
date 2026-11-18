-- 智慧健康日誌資料庫結構（SQLite）
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    display_name  TEXT,
    role          TEXT    NOT NULL DEFAULT 'patient',   -- patient 病人 / staff 醫護人員
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 每一筆健康日誌：日期、餐別、運動、血糖、蔬菜自評、餐點照片
CREATE TABLE IF NOT EXISTS health_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id),
    log_date       TEXT    NOT NULL,             -- YYYY-MM-DD
    meal_type      TEXT,                         -- 早餐 / 午餐 / 晚餐
    exercise_done  INTEGER,                      -- 1 是 / 0 否 / NULL 未填
    glucose_pre    REAL,                         -- 飯前血糖 mg/dl
    glucose_post   REAL,                         -- 飯後血糖 mg/dl
    veggie_done    INTEGER,                      -- 使用者自評是否吃滿三碗蔬菜
    photo_filename TEXT,
    grain_servings   REAL,                       -- 全穀雜糧類份數（使用者確認）
    veg_servings     REAL,                       -- 蔬菜類份數
    protein_servings REAL,                       -- 豆魚蛋肉類份數
    carbs_g        REAL,                         -- 依份數換算的醣類（公克）
    total_kcal     REAL,                         -- 依份數換算的熱量（大卡）
    created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 餐點照片中偵測到的每一個食物區塊（label 為食物類別）
CREATE TABLE IF NOT EXISTS detections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id     INTEGER NOT NULL REFERENCES health_logs(id),
    label      TEXT    NOT NULL,
    label_zh   TEXT    NOT NULL,
    confidence REAL    NOT NULL,
    x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER
);

CREATE INDEX IF NOT EXISTS idx_logs_user_date ON health_logs(user_id, log_date);
