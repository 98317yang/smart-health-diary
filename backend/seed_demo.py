"""產生示範病人資料，讓醫護端的畫面有東西可看。

只用於開發與 demo：病人姓名為虛構，餐點照片取自專題自己拍的餐盤照片
（test_tray_todo，未進入訓練集），份數與辨識框由模型實際推論得到。
示範帳號的密碼是隨機產生且不留存，需要登入時用 manage.py set-password 自行設定。

    python3 seed_demo.py            # 新增示範病人（已存在就跳過）
    python3 seed_demo.py --reset    # 先清掉舊的示範資料再重建
"""
import argparse
import os
import json
import random
import shutil
import sqlite3
import secrets
import uuid
from datetime import date, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash

from nutrition import count_dishes, load_food_info, nutrition

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
# 與後端相同的位置與環境變數
DB_PATH = Path(os.environ.get("SHD_DB", BACKEND_DIR / "instance" / "health.db"))
UPLOAD_DIR = Path(os.environ.get("SHD_UPLOADS", BACKEND_DIR / "uploads"))
PHOTO_DIR = PROJECT_DIR / "training" / "foodgroups" / "test_tray_todo" / "images"
DETS_PATH = BACKEND_DIR / "demo_detections.json"

MEALS = ("早餐", "午餐", "晚餐")
MEAL_TIME = {"早餐": "08:00:00", "午餐": "12:30:00", "晚餐": "18:30:00"}  # 紀錄時間，讓同一天的排序合理

# 五位示範病人：帳號、姓名、生日、性別、血糖區間、達成率、最後一次記錄距今幾天
# 血糖以常態分布抽樣，刻意讓部分病人落在控制目標之外，醫護端才看得到需要關注的案例。
DEMO_PATIENTS = [
    dict(username="demo_chen", name="陳秀英", birthday="1956-03-12", gender="女",
         pre=(105, 10), post=(138, 15), exercise=0.8, veggie=0.7, days_back=0, meals_per_day=2),
    dict(username="demo_lin", name="林文雄", birthday="1968-07-25", gender="男",
         pre=(118, 12), post=(188, 22), exercise=0.3, veggie=0.4, days_back=0, meals_per_day=2),
    dict(username="demo_wang", name="王美玉", birthday="1961-11-02", gender="女",
         pre=(148, 14), post=(207, 25), exercise=0.2, veggie=0.3, days_back=1, meals_per_day=2),
    dict(username="demo_chang", name="張國棟", birthday="1954-01-18", gender="男",
         pre=(126, 13), post=(152, 18), exercise=0.5, veggie=0.5, days_back=4, meals_per_day=2),
    dict(username="demo_huang", name="黃麗華", birthday="1971-04-14", gender="女",
         pre=(74, 7), post=(132, 16), exercise=0.6, veggie=0.6, days_back=0, meals_per_day=2),
]
DAYS = 14        # 產生最近兩週的紀錄
MIN_DAYS = 3     # 每位病人至少要有這麼多天的紀錄落在醫護端的七天檢視範圍內
SKIP_CHANCE = 0.15  # 其餘日子偶爾漏記，比每天都滿分真實


def connect():
    # 剛 clone 下來、還沒啟動過後端時資料庫不存在，依 schema.sql 建立（與後端 init_db 相同）
    fresh = not DB_PATH.exists()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    if fresh:
        db.executescript((BACKEND_DIR / "schema.sql").read_text(encoding="utf-8"))
        print(f"已建立新的資料庫 {DB_PATH}")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def ensure_columns(db):
    cols = {r[1] for r in db.execute("PRAGMA table_info(users)")}
    for col in ("birthday", "gender"):
        if col not in cols:
            db.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")


def reset(db):
    """刪掉所有 demo_ 開頭的帳號與其紀錄、照片。"""
    ids = [r["id"] for r in db.execute("SELECT id FROM users WHERE username LIKE 'demo_%'")]
    if not ids:
        print("沒有舊的示範資料")
        return
    marks = ",".join("?" * len(ids))
    photos = [r["photo_filename"] for r in db.execute(
        f"SELECT photo_filename FROM health_logs WHERE user_id IN ({marks}) AND photo_filename IS NOT NULL", ids)]
    db.execute(f"DELETE FROM detections WHERE log_id IN "
               f"(SELECT id FROM health_logs WHERE user_id IN ({marks}))", ids)
    db.execute(f"DELETE FROM health_logs WHERE user_id IN ({marks})", ids)
    db.execute(f"DELETE FROM users WHERE id IN ({marks})", ids)
    removed = 0
    for name in photos:
        f = UPLOAD_DIR / name
        if f.exists():
            f.unlink()
            removed += 1
    print(f"已清除 {len(ids)} 個示範帳號、{removed} 張照片")


def servings_from(dets, food_info):
    """與使用者端相同的預設份數：每一處食物 × 該類別的預設份數（見 detector.summarize）。"""
    counts = count_dishes((d[0], d[3:7]) for d in dets)
    return {k: food_info[k]["default_servings"] * n for k, n in counts.items()}


def backfill(db, username, rng):
    """補齊既有紀錄缺的欄位。

    開發過程留下的紀錄常常只有照片、沒有填血糖與運動，在醫護端會變成一片空格。
    這裡依該帳號既有紀錄的血糖分布補值，讓示範資料前後一致；
    沒有既有數值可參考時，用控制良好的範圍。
    """
    u = db.execute("SELECT id, display_name FROM users WHERE username = ?", (username,)).fetchone()
    if u is None:
        print(f"找不到帳號 {username}，略過")
        return
    rows = db.execute(
        """SELECT id, log_date, exercise_done, glucose_pre, glucose_post, veggie_done
           FROM health_logs WHERE user_id = ? ORDER BY log_date, id""", (u["id"],)).fetchall()

    def spread(col, fallback):
        xs = [r[col] for r in rows if r[col] is not None]
        if len(xs) < 3:
            return fallback
        mean = sum(xs) / len(xs)
        var = sum((x - mean) ** 2 for x in xs) / len(xs)
        return (mean, max(var ** 0.5, 6))

    pre_spec = spread("glucose_pre", (108, 11))
    post_spec = spread("glucose_post", (142, 16))

    def rate(col, fallback):
        xs = [r[col] for r in rows if r[col] is not None]
        return sum(xs) / len(xs) if len(xs) >= 3 else fallback

    ex_rate = rate("exercise_done", 0.7)
    veg_rate = rate("veggie_done", 0.7)

    # 同一天的運動與蔬菜要一致，不然同一天的兩餐會互相矛盾
    per_day = {}
    filled = 0
    for r in rows:
        day = per_day.setdefault(r["log_date"], {})
        if r["exercise_done"] is not None:
            day["exercise"] = r["exercise_done"]
        if r["veggie_done"] is not None:
            day["veggie"] = r["veggie_done"]

    for r in rows:
        updates = {}
        day = per_day[r["log_date"]]
        if r["exercise_done"] is None:
            day.setdefault("exercise", 1 if rng.random() < ex_rate else 0)
            updates["exercise_done"] = day["exercise"]
        if r["veggie_done"] is None:
            day.setdefault("veggie", 1 if rng.random() < veg_rate else 0)
            updates["veggie_done"] = day["veggie"]
        if r["glucose_pre"] is None:
            updates["glucose_pre"] = round(min(600, max(20, rng.gauss(*pre_spec))))
        if r["glucose_post"] is None:
            updates["glucose_post"] = round(min(600, max(20, rng.gauss(*post_spec))))
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            db.execute(f"UPDATE health_logs SET {sets} WHERE id = ?", (*updates.values(), r["id"]))
            filled += 1
    db.commit()
    print(f"{u['display_name'] or username}：補齊 {filled} 筆紀錄"
          f"（飯前約 {pre_spec[0]:.0f}、飯後約 {post_spec[0]:.0f}）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="先清掉舊的示範資料")
    ap.add_argument("--backfill", metavar="帳號", action="append", default=[],
                    help="補齊這個帳號既有紀錄中缺的運動／血糖／蔬菜（可重複指定）")
    ap.add_argument("--seed", type=int, default=20261118, help="隨機種子，固定才能重現同一批資料")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    dets_by_photo = json.loads(DETS_PATH.read_text(encoding="utf-8"))
    food_info = load_food_info()
    # 照片來源資料夾不在 git 裡（training/foodgroups 被 .gitignore 排除）；
    # 沒有的話仍可建立示範病人，只是不附餐點照片
    photo_names = sorted(n for n in dets_by_photo if (PHOTO_DIR / n).exists())
    have_photos = bool(photo_names)
    if not have_photos:
        print(f"找不到示範照片（{PHOTO_DIR}），示範紀錄將只有血糖、運動、蔬菜，不含餐點照片。")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    db = connect()
    ensure_columns(db)
    if args.reset:
        reset(db)
    for username in args.backfill:
        backfill(db, username, rng)

    # Yang 本人的基本資料（使用者指定：45 歲、女）
    db.execute("UPDATE users SET birthday = ?, gender = ? WHERE username = 'test'",
               ("1981-05-03", "女"))

    today = date.today()
    photo_i = 0
    for spec in DEMO_PATIENTS:
        row = db.execute("SELECT id FROM users WHERE username = ?", (spec["username"],)).fetchone()
        if row:
            print(f"{spec['username']} 已存在，略過")
            continue
        cur = db.execute(
            """INSERT INTO users (username, password_hash, display_name, birthday, gender, role, created_at)
               VALUES (?, ?, ?, ?, ?, 'patient', ?)""",
            (spec["username"], generate_password_hash(secrets.token_urlsafe(32)),
             spec["name"], spec["birthday"], spec["gender"],
             (today - timedelta(days=rng.randint(20, 90))).isoformat() + " 09:00:00"))
        uid = cur.lastrowid

        if spec["days_back"] is None:
            print(f"建立 {spec['name']}（{spec['username']}）：尚未開始記錄")
            continue

        # 從最後一次記錄往前推；前 MIN_DAYS 天一定要有紀錄，
        # 否則隨機漏記可能讓醫護端的七天檢視只剩一兩天，統計看起來會很奇怪
        first = spec["days_back"]
        guaranteed = set(range(first, first + MIN_DAYS))
        record_days = [b for b in range(first, DAYS)
                       if b in guaranteed or rng.random() >= SKIP_CHANCE]

        n_logs = n_photos = 0
        for back in record_days:
            day = today - timedelta(days=back)
            exercise = 1 if rng.random() < spec["exercise"] else 0
            veggie = 1 if rng.random() < spec["veggie"] else 0
            for meal in rng.sample(MEALS, min(spec["meals_per_day"], len(MEALS))):
                pre = round(max(55, rng.gauss(*spec["pre"])))
                post = round(max(70, rng.gauss(*spec["post"])))

                # 有照片才產生餐點（餐別、份數、醣類、辨識框）；
                # 找不到照片時只留血糖、運動、蔬菜，不做出「有醣類卻沒有照片」的餐點
                stored, dets, counts, totals, meal_type = None, [], {}, None, None
                if have_photos:
                    photo = photo_names[photo_i % len(photo_names)]
                    photo_i += 1
                    stored = f"{uuid.uuid4().hex}.jpg"
                    shutil.copy2(PHOTO_DIR / photo, UPLOAD_DIR / stored)
                    n_photos += 1
                    dets = dets_by_photo[photo]["detections"]
                    counts = servings_from(dets, food_info)
                    totals = nutrition(counts)
                    meal_type = meal

                cur = db.execute(
                    """INSERT INTO health_logs
                       (user_id, log_date, meal_type, exercise_done, glucose_pre, glucose_post,
                        veggie_done, photo_filename, grain_servings, veg_servings, protein_servings,
                        carbs_g, total_kcal, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (uid, day.isoformat(), meal_type, exercise, pre, post, veggie, stored,
                     counts.get("grains_starches") if stored else None,
                     counts.get("vegetables") if stored else None,
                     counts.get("protein") if stored else None,
                     totals and totals["carbs_g"], totals and totals["kcal"],
                     f"{day.isoformat()} {MEAL_TIME[meal]}"))
                log_id = cur.lastrowid
                n_logs += 1
                if stored:
                    db.executemany(
                        """INSERT INTO detections (log_id, label, label_zh, confidence, x1, y1, x2, y2)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        [(log_id, d[0], d[1], d[2], d[3], d[4], d[5], d[6]) for d in dets])
        print(f"建立 {spec['name']}（{spec['username']}）：{n_logs} 筆紀錄、{n_photos} 張照片")

    db.commit()
    total = db.execute("SELECT COUNT(*) FROM users WHERE role = 'patient'").fetchone()[0]
    print(f"\n完成，目前共 {total} 位病人帳號")
    db.close()


if __name__ == "__main__":
    main()
