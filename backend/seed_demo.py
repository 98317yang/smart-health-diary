"""產生示範病人資料，讓醫護端的畫面有東西可看。

只用於開發與 demo：病人姓名為虛構，餐點照片取自專題自己拍的餐盤照片
（test_tray_todo，未進入訓練集），份數與辨識框由模型實際推論得到。
示範帳號的密碼是隨機產生且不留存，需要登入時用 manage.py set-password 自行設定。

    python3 seed_demo.py            # 新增示範病人（已存在就跳過）
    python3 seed_demo.py --reset    # 先清掉舊的示範資料再重建
"""
import argparse
import json
import random
import shutil
import sqlite3
import secrets
import uuid
from datetime import date, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash

from nutrition import nutrition

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
DB_PATH = BACKEND_DIR / "instance" / "health.db"
UPLOAD_DIR = BACKEND_DIR / "uploads"
PHOTO_DIR = PROJECT_DIR / "training" / "foodgroups" / "test_tray_todo" / "images"
DETS_PATH = BACKEND_DIR / "demo_detections.json"

MEALS = ("早餐", "午餐", "晚餐")

# 每位示範病人：帳號、姓名、生日、性別、血糖區間、達成率、最後一次記錄距今幾天
# 血糖以常態分布抽樣，刻意讓部分病人落在控制目標之外，醫護端才看得到需要關注的案例。
DEMO_PATIENTS = [
    dict(username="demo_chen", name="陳秀英", birthday="1956-03-12", gender="女",
         pre=(105, 10), post=(138, 15), exercise=0.8, veggie=0.7, days_back=0, meals_per_day=2),
    dict(username="demo_lin", name="林文雄", birthday="1968-07-25", gender="男",
         pre=(118, 12), post=(188, 22), exercise=0.3, veggie=0.4, days_back=0, meals_per_day=2),
    dict(username="demo_wang", name="王美玉", birthday="1961-11-02", gender="女",
         pre=(148, 14), post=(207, 25), exercise=0.2, veggie=0.3, days_back=1, meals_per_day=2),
    dict(username="demo_chang", name="張國棟", birthday="1954-01-18", gender="男",
         pre=(126, 13), post=(152, 18), exercise=0.5, veggie=0.5, days_back=5, meals_per_day=1),
    dict(username="demo_li", name="李淑芬", birthday="1977-09-08", gender="女",
         pre=(98, 8), post=(126, 12), exercise=0.9, veggie=0.9, days_back=0, meals_per_day=3),
    dict(username="demo_huang", name="黃麗華", birthday="1971-04-14", gender="女",
         pre=(74, 7), post=(132, 16), exercise=0.6, veggie=0.6, days_back=0, meals_per_day=2),
    # 剛註冊、還沒開始記錄，醫護端會標示「從未記錄」
    dict(username="demo_wu", name="吳進財", birthday="1946-05-30", gender="男",
         pre=None, post=None, exercise=0, veggie=0, days_back=None, meals_per_day=0),
]
DAYS = 14  # 產生最近兩週的紀錄


def connect():
    db = sqlite3.connect(DB_PATH)
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


def servings_from(dets):
    """把辨識框數量換算成份數：同一類別每一塊算一份，與使用者端的預設一致。"""
    counts = {}
    for d in dets:
        counts[d[0]] = counts.get(d[0], 0) + 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="先清掉舊的示範資料")
    ap.add_argument("--seed", type=int, default=20261118, help="隨機種子，固定才能重現同一批資料")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    dets_by_photo = json.loads(DETS_PATH.read_text(encoding="utf-8"))
    photo_names = sorted(dets_by_photo)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    db = connect()
    ensure_columns(db)
    if args.reset:
        reset(db)

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

        n_logs = n_photos = 0
        for back in range(spec["days_back"], DAYS):
            day = today - timedelta(days=back)
            # 偶爾漏記一天，比每天都滿分真實
            if rng.random() < 0.15:
                continue
            exercise = 1 if rng.random() < spec["exercise"] else 0
            veggie = 1 if rng.random() < spec["veggie"] else 0
            for meal in rng.sample(MEALS, min(spec["meals_per_day"], len(MEALS))):
                pre = round(max(55, rng.gauss(*spec["pre"])))
                post = round(max(70, rng.gauss(*spec["post"])))

                photo = photo_names[photo_i % len(photo_names)]
                photo_i += 1
                stored = f"{uuid.uuid4().hex}.jpg"
                src = PHOTO_DIR / photo
                if src.exists():
                    shutil.copy2(src, UPLOAD_DIR / stored)
                    n_photos += 1
                else:
                    stored = None

                dets = dets_by_photo[photo]["detections"]
                counts = servings_from(dets)
                totals = nutrition(counts)

                cur = db.execute(
                    """INSERT INTO health_logs
                       (user_id, log_date, meal_type, exercise_done, glucose_pre, glucose_post,
                        veggie_done, photo_filename, grain_servings, veg_servings, protein_servings,
                        carbs_g, total_kcal, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (uid, day.isoformat(), meal, exercise, pre, post, veggie, stored,
                     counts.get("grains_starches", 0),
                     counts.get("vegetables", 0),
                     counts.get("protein", 0),
                     totals["carbs_g"], totals["kcal"], f"{day.isoformat()} 12:00:00"))
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
