"""智慧健康日誌 — Flask 後端

啟動時載入一次 YOLOv9 模型，提供：
  註冊 / 登入 / 登出      POST /api/register, /api/login, /api/logout；GET /api/me
  新增健康日誌（含照片）  POST /api/logs
  確認／修改餐點份數      PUT  /api/logs/<id>/servings
  歷史統計               GET  /api/history?days=7
  餐點照片               GET  /api/photos/<檔名>（本人或醫護人員才能取用）
  醫護端                 GET  /api/staff/patients、/api/staff/patients/<id>
並同時提供 frontend/ 的網頁。
"""
import logging
import os
import secrets
import sqlite3
import uuid
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from nutrition import nutrition

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"

CONFIG = {
    "DB_PATH": Path(os.environ.get("SHD_DB", BACKEND_DIR / "instance" / "health.db")),
    "UPLOAD_DIR": Path(os.environ.get("SHD_UPLOADS", BACKEND_DIR / "uploads")),
    "WEIGHTS": Path(os.environ.get("SHD_WEIGHTS", PROJECT_DIR / "weights" / "foodgroups_gelan_s.pt")),
    "DATA_YAML": BACKEND_DIR / "model_classes.yaml",
    "DEVICE": os.environ.get("SHD_DEVICE", "cpu"),
    "IMGSZ": int(os.environ.get("SHD_IMGSZ", 640)),  # 與訓練時的輸入大小一致
}
GROUP_COLUMNS = {"grains_starches": "grain_servings", "vegetables": "veg_servings", "protein": "protein_servings"}
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MEAL_TYPES = {"早餐", "午餐", "晚餐"}
# 血糖參考範圍（mg/dL）：僅用於在醫護端標示偏高或偏低，不是診斷依據。
# 採中華民國糖尿病學會《糖尿病臨床照護指引》成人糖尿病的控制目標
# （餐前 80–130、餐後 1–2 小時 80–160），而非健康成人的診斷正常值。
# 目標值需個別化：年齡、共病與低血糖風險都會影響，65 歲以上通常放寬，
# 導入時應由醫療團隊逐項確認後調整。
GLUCOSE_REF = {"pre_low": 80, "pre_high": 130, "post_low": 80, "post_high": 160}
# 超過這個天數沒有任何紀錄，就在病人清單標示出來
INACTIVE_DAYS = 3

log = logging.getLogger("shd")
app = Flask(__name__, static_folder=None)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 單張照片上限 10 MB
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ---------- 資料庫 ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(CONFIG["DB_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    CONFIG["DB_PATH"].parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(CONFIG["DB_PATH"]) as db:
        db.executescript((BACKEND_DIR / "schema.sql").read_text(encoding="utf-8"))
        # 舊版資料庫沒有份數與醣類欄位時補上
        cols = {r[1] for r in db.execute("PRAGMA table_info(health_logs)")}
        for col in ("grain_servings", "veg_servings", "protein_servings", "carbs_g"):
            if col not in cols:
                db.execute(f"ALTER TABLE health_logs ADD COLUMN {col} REAL")
        # 舊版資料庫沒有角色欄位時補上，既有帳號一律視為病人
        if "role" not in {r[1] for r in db.execute("PRAGMA table_info(users)")}:
            db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'patient'")


# ---------- 模型（只載入一次） ----------
detector = None


def load_detector():
    global detector
    if not CONFIG["WEIGHTS"].exists():
        log.warning("找不到模型權重 %s，照片辨識功能停用（其他功能仍可使用）", CONFIG["WEIGHTS"])
        return
    from detector import FoodDetector
    detector = FoodDetector(CONFIG["WEIGHTS"], CONFIG["DATA_YAML"],
                            device=CONFIG["DEVICE"], imgsz=CONFIG["IMGSZ"])
    log.info("模型已載入：%s", CONFIG["WEIGHTS"])


# ---------- 工具 ----------
def error(message, status=400):
    return jsonify({"error": message}), status


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return error("請先登入", 401)
        return view(*args, **kwargs)
    return wrapped


def current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def staff_required(view):
    """醫護端專用：非醫護帳號一律擋下，避免病人看到其他人的資料。"""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            session.clear()
            return error("請先登入", 401)
        if user["role"] != "staff":
            return error("這個頁面僅供醫護人員使用", 403)
        return view(*args, **kwargs)
    return wrapped


def parse_bool(value):
    if value in (None, ""):
        return None
    return 1 if str(value).lower() in ("1", "yes", "true", "是") else 0


def parse_glucose(value, field):
    if value in (None, ""):
        return None
    try:
        v = float(value)
    except ValueError:
        raise ValueError(f"{field}必須是數字")
    if not 20 <= v <= 600:
        raise ValueError(f"{field}數值不合理（應介於 20–600 mg/dl）")
    return v


def servings_dict(groups):
    return {g["group"]: g["servings"] for g in groups}


# ---------- 帳號 ----------
@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip() or None
    if not 3 <= len(username) <= 30:
        return error("帳號長度需為 3–30 個字元")
    if len(password) < 6:
        return error("密碼至少需要 6 個字元")
    db = get_db()
    try:
        db.execute("INSERT INTO users (username, password_hash, display_name) VALUES (?, ?, ?)",
                   (username, generate_password_hash(password), display_name))
        db.commit()
    except sqlite3.IntegrityError:
        return error("此帳號已被使用", 409)
    return jsonify({"message": "註冊成功"}), 201


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    # 帳號不存在與密碼錯誤回傳相同訊息，避免被探測帳號
    if user is None or not check_password_hash(user["password_hash"], data.get("password") or ""):
        return error("帳號或密碼錯誤", 401)
    session.clear()
    session["user_id"] = user["id"]
    return jsonify({"message": "登入成功", "username": user["username"],
                    "display_name": user["display_name"], "role": user["role"]})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"message": "已登出"})


@app.get("/api/session")
def session_status():
    """公開頁面用來判斷導覽列要顯示「登入」或「登出」；未登入也回 200，避免主控台出現錯誤。"""
    uid = session.get("user_id")
    if uid is None:
        return jsonify({"logged_in": False})
    user = get_db().execute("SELECT username, display_name, role FROM users WHERE id = ?", (uid,)).fetchone()
    if user is None:
        session.clear()
        return jsonify({"logged_in": False})
    return jsonify({"logged_in": True, "username": user["username"],
                    "display_name": user["display_name"], "role": user["role"]})


@app.get("/api/me")
@login_required
def me():
    user = get_db().execute("SELECT username, display_name, role, created_at FROM users WHERE id = ?",
                            (session["user_id"],)).fetchone()
    if user is None:
        session.clear()
        return error("請先登入", 401)
    return jsonify(dict(user) | {"detector_ready": detector is not None})


# ---------- 健康日誌 ----------
MEAL_COLUMNS = ("meal_type", "photo_filename", "grain_servings", "veg_servings",
                "protein_servings", "carbs_g", "total_kcal")


def remove_meal(db, row):
    """移除一筆紀錄中的餐點資料。

    同一次送出的血糖、運動、蔬菜自評會保留；如果這筆紀錄只有餐點，就整筆刪除。
    回傳要刪除的照片檔名（等資料庫 commit 之後再刪檔）。
    """
    db.execute("DELETE FROM detections WHERE log_id = ?", (row["id"],))
    has_other = any(row[c] is not None for c in ("exercise_done", "glucose_pre", "glucose_post", "veggie_done"))
    if has_other:
        db.execute(f"UPDATE health_logs SET {', '.join(c + ' = NULL' for c in MEAL_COLUMNS)} WHERE id = ?",
                   (row["id"],))
    else:
        db.execute("DELETE FROM health_logs WHERE id = ?", (row["id"],))
    return row["photo_filename"]


def delete_photos(filenames):
    for name in filenames:
        if name:
            (CONFIG["UPLOAD_DIR"] / name).unlink(missing_ok=True)


@app.post("/api/logs")
@login_required
def create_log():
    if current_user()["role"] == "staff":
        return error("醫護帳號不記錄個人日誌", 403)
    form = request.form
    try:
        log_date = date.fromisoformat(form.get("date") or date.today().isoformat()).isoformat()
        glucose_pre = parse_glucose(form.get("glucose_pre"), "飯前血糖")
        glucose_post = parse_glucose(form.get("glucose_post"), "飯後血糖")
    except ValueError as e:
        return error(str(e) if "血糖" in str(e) else "日期格式錯誤")
    meal_type = form.get("meal_type") or None
    if meal_type is not None and meal_type not in MEAL_TYPES:
        return error("餐別必須是早餐、午餐或晚餐")

    photo = request.files.get("photo")
    has_photo = bool(photo and photo.filename)
    replace = form.get("replace") == "1"
    db = get_db()
    existing = []
    if has_photo and meal_type:
        existing = db.execute(
            "SELECT * FROM health_logs WHERE user_id = ? AND log_date = ? AND meal_type = ? "
            "AND photo_filename IS NOT NULL", (session["user_id"], log_date, meal_type)).fetchall()
        if existing and not replace:
            return jsonify({"error": f"{log_date} 的{meal_type}已經記錄過了", "conflict": True,
                            "date": log_date, "meal_type": meal_type}), 409

    detections, groups, photo_filename, image_size = [], [], None, None
    if has_photo:
        ext = Path(photo.filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            return error("只接受 JPG、PNG、WEBP、BMP 圖片")
        if detector is None:
            return error("伺服器未載入辨識模型，暫時無法分析照片", 503)
        image_bytes = photo.read()
        try:
            detections, image_size = detector.detect(image_bytes, return_size=True)
        except ValueError as e:
            return error(str(e))
        groups = detector.summarize(detections)
        CONFIG["UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
        photo_filename = f"{uuid.uuid4().hex}{ext}"
        (CONFIG["UPLOAD_DIR"] / photo_filename).write_bytes(image_bytes)

    fields = [glucose_pre, glucose_post, parse_bool(form.get("exercise")),
              parse_bool(form.get("veggie")), photo_filename]
    if all(v is None for v in fields):
        return error("請至少填寫一項資料或上傳一張照片")

    servings = servings_dict(groups)
    totals = nutrition(servings) if photo_filename else None
    # 照片辨識成功後才移除舊的餐點，避免新照片失敗時舊紀錄也不見
    old_photos = [remove_meal(db, row) for row in existing]
    cur = db.execute(
        """INSERT INTO health_logs (user_id, log_date, meal_type, exercise_done, glucose_pre,
                                    glucose_post, veggie_done, photo_filename, total_kcal,
                                    grain_servings, veg_servings, protein_servings, carbs_g)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session["user_id"], log_date, meal_type, fields[2], glucose_pre, glucose_post,
         fields[3], photo_filename, totals and totals["kcal"],
         *[servings.get(g) for g in GROUP_COLUMNS], totals and totals["carbs_g"]))
    db.executemany(
        "INSERT INTO detections (log_id, label, label_zh, confidence, x1, y1, x2, y2) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(cur.lastrowid, d["label"], d["label_zh"], d["confidence"], *d["box"]) for d in detections])
    db.commit()
    delete_photos(old_photos)
    return jsonify({"message": "已取代原本的紀錄" if old_photos else "已儲存", "replaced": bool(old_photos),
                    "log_id": cur.lastrowid, "detections": detections,
                    "image_size": image_size,
                    "groups": groups, "nutrition": totals}), 201


@app.delete("/api/logs/<int:log_id>")
@login_required
def delete_meal(log_id):
    """刪除一筆餐點紀錄（同次送出的血糖、運動資料會保留）。"""
    db = get_db()
    row = db.execute("SELECT * FROM health_logs WHERE id = ? AND user_id = ?",
                     (log_id, session["user_id"])).fetchone()
    if row is None or row["photo_filename"] is None:
        return error("找不到這筆餐點紀錄", 404)
    photo = remove_meal(db, row)
    db.commit()
    delete_photos([photo])
    return jsonify({"message": "已刪除"})


@app.put("/api/logs/<int:log_id>/servings")
@login_required
def update_servings(log_id):
    """使用者確認或修改各類別的份數後，重新計算醣類與熱量。"""
    data = request.get_json(silent=True) or {}
    servings = {}
    for group in GROUP_COLUMNS:
        if group not in data or data[group] in (None, ""):
            continue
        try:
            v = float(data[group])
        except (TypeError, ValueError):
            return error("份數必須是數字")
        if not 0 <= v <= 20 or (v * 2) != int(v * 2):
            return error("份數需介於 0–20，且以 0.5 份為單位")
        servings[group] = v
    db = get_db()
    row = db.execute("SELECT * FROM health_logs WHERE id = ? AND user_id = ?",
                     (log_id, session["user_id"])).fetchone()
    if row is None:
        return error("找不到這筆紀錄", 404)
    if row["photo_filename"] is None:
        return error("這筆紀錄沒有餐點照片")
    merged = {g: (servings[g] if g in servings else row[col])
              for g, col in GROUP_COLUMNS.items()}
    merged = {g: v for g, v in merged.items() if v is not None}
    totals = nutrition(merged)
    db.execute(
        "UPDATE health_logs SET grain_servings = ?, veg_servings = ?, protein_servings = ?, "
        "carbs_g = ?, total_kcal = ? WHERE id = ?",
        (*[merged.get(g) for g in GROUP_COLUMNS], totals["carbs_g"], totals["kcal"], log_id))
    db.commit()
    return jsonify({"message": "份量已更新", "servings": merged, "nutrition": totals})


def history_payload(db, user_id, days):
    """整理某位使用者近 days 天的統計；病人看自己、醫護看病人都走這裡，確保兩邊數字一致。"""
    end = date.today()
    start = end - timedelta(days=days - 1)
    rows = db.execute(
        """SELECT * FROM health_logs WHERE user_id = ? AND log_date BETWEEN ? AND ?
           ORDER BY log_date, created_at""",
        (user_id, start.isoformat(), end.isoformat())).fetchall()

    # 逐日彙整：血糖取平均；運動 / 蔬菜當天只要有一筆「是」即算達成
    daily = {(start + timedelta(days=i)).isoformat(): {
        "pre": [], "post": [], "exercise": None, "veggie": None, "carbs": None} for i in range(days)}
    for r in rows:
        d = daily[r["log_date"]]
        if r["glucose_pre"] is not None:
            d["pre"].append(r["glucose_pre"])
        if r["glucose_post"] is not None:
            d["post"].append(r["glucose_post"])
        if r["carbs_g"] is not None:
            d["carbs"] = (d["carbs"] or 0) + r["carbs_g"]
        for key, col in (("exercise", "exercise_done"), ("veggie", "veggie_done")):
            if r[col] is not None:
                d[key] = max(d[key] or 0, r[col])

    def avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    def rate(key):
        answered = [d[key] for d in daily.values() if d[key] is not None]
        return {"done": sum(answered), "answered_days": len(answered)}

    recent = db.execute(
        """SELECT l.id, l.log_date, l.meal_type, l.total_kcal, l.carbs_g, l.photo_filename,
                  l.grain_servings, l.veg_servings, l.protein_servings,
                  COUNT(d.id) AS n_detected
           FROM health_logs l LEFT JOIN detections d ON d.log_id = l.id
           WHERE l.user_id = ? AND l.photo_filename IS NOT NULL
           GROUP BY l.id ORDER BY l.log_date DESC, l.created_at DESC LIMIT 10""",
        (user_id,)).fetchall()

    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "dates": list(daily.keys()),
        "glucose_pre": [avg(d["pre"]) for d in daily.values()],
        "glucose_post": [avg(d["post"]) for d in daily.values()],
        "carbs_g": [d["carbs"] for d in daily.values()],
        "exercise": rate("exercise"),
        "veggie": rate("veggie"),
        "exercise_daily": [d["exercise"] for d in daily.values()],
        "veggie_daily": [d["veggie"] for d in daily.values()],
        "recent_meals": [dict(r) for r in recent],
    }


@app.get("/api/history")
@login_required
def history():
    days = min(max(request.args.get("days", 7, type=int), 1), 90)
    return jsonify(history_payload(get_db(), session["user_id"], days))


@app.get("/api/photos/<path:filename>")
@login_required
def photo(filename):
    """餐點照片：只有上傳者本人或醫護人員能取用。"""
    owner = get_db().execute(
        "SELECT user_id FROM health_logs WHERE photo_filename = ?", (filename,)).fetchone()
    if owner is None:
        return error("找不到這張照片", 404)
    user = current_user()
    if user["role"] != "staff" and owner["user_id"] != user["id"]:
        return error("無權查看這張照片", 403)
    return send_from_directory(CONFIG["UPLOAD_DIR"], filename)


# ---------- 醫護端 ----------
def glucose_flags(pre, post):
    """判斷平均血糖是否超出參考範圍，回傳給前端標色用。"""
    flags = []
    if pre is not None:
        if pre > GLUCOSE_REF["pre_high"]:
            flags.append("飯前偏高")
        elif pre < GLUCOSE_REF["pre_low"]:
            flags.append("飯前偏低")
    if post is not None:
        if post > GLUCOSE_REF["post_high"]:
            flags.append("飯後偏高")
        elif post < GLUCOSE_REF["post_low"]:
            flags.append("飯後偏低")
    return flags


@app.get("/api/staff/patients")
@staff_required
def staff_patients():
    """病人清單：近 days 天的達成情形與平均血糖，供醫護掌握誰需要關心。"""
    days = min(max(request.args.get("days", 7, type=int), 1), 90)
    end = date.today()
    start = end - timedelta(days=days - 1)
    db = get_db()

    # 先以「使用者 × 日期」彙整，再於 Python 併成每位病人的摘要，避免逐一查詢
    per_day = db.execute(
        """SELECT user_id, log_date,
                  AVG(glucose_pre) AS pre, AVG(glucose_post) AS post,
                  MAX(exercise_done) AS exercise, MAX(veggie_done) AS veggie,
                  SUM(photo_filename IS NOT NULL) AS meals
           FROM health_logs WHERE log_date BETWEEN ? AND ?
           GROUP BY user_id, log_date""",
        (start.isoformat(), end.isoformat())).fetchall()
    stats = {}
    for r in per_day:
        d = stats.setdefault(r["user_id"], {"pre": [], "post": [], "exercise": [], "veggie": [], "meals": 0})
        for key in ("pre", "post"):
            if r[key] is not None:
                d[key].append(r[key])
        for key in ("exercise", "veggie"):
            if r[key] is not None:
                d[key].append(r[key])
        d["meals"] += r["meals"] or 0

    patients = db.execute(
        """SELECT u.id, u.username, u.display_name, u.created_at,
                  (SELECT MAX(log_date) FROM health_logs WHERE user_id = u.id) AS last_log_date,
                  (SELECT COUNT(*) FROM health_logs WHERE user_id = u.id) AS total_logs
           FROM users u WHERE u.role = 'patient' ORDER BY u.id""").fetchall()

    def avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    out = []
    for u in patients:
        d = stats.get(u["id"], {"pre": [], "post": [], "exercise": [], "veggie": [], "meals": 0})
        pre, post = avg(d["pre"]), avg(d["post"])
        days_since = (end - date.fromisoformat(u["last_log_date"])).days if u["last_log_date"] else None
        out.append({
            "id": u["id"], "username": u["username"], "display_name": u["display_name"],
            "created_at": u["created_at"], "last_log_date": u["last_log_date"],
            "days_since_log": days_since, "total_logs": u["total_logs"],
            "glucose_pre": pre, "glucose_post": post, "glucose_flags": glucose_flags(pre, post),
            "exercise_done": sum(d["exercise"]), "exercise_days": len(d["exercise"]),
            "veggie_done": sum(d["veggie"]), "veggie_days": len(d["veggie"]),
            "meals": d["meals"],
            "inactive": days_since is None or days_since >= INACTIVE_DAYS,
        })
    return jsonify({"days": days, "start": start.isoformat(), "end": end.isoformat(),
                    "reference": GLUCOSE_REF, "inactive_days": INACTIVE_DAYS, "patients": out})


@app.get("/api/staff/patients/<int:patient_id>")
@staff_required
def staff_patient_detail(patient_id):
    """單一病人的詳細紀錄；統計與病人自己看到的完全相同（共用 history_payload）。"""
    days = min(max(request.args.get("days", 14, type=int), 1), 90)
    db = get_db()
    user = db.execute("SELECT id, username, display_name, created_at, role FROM users WHERE id = ?",
                      (patient_id,)).fetchone()
    if user is None or user["role"] != "patient":
        return error("找不到這位病人", 404)

    data = history_payload(db, patient_id, days)
    # 補上每一餐的辨識框，醫護才能看出份量是怎麼來的
    meal_ids = [m["id"] for m in data["recent_meals"]]
    boxes = {}
    if meal_ids:
        rows = db.execute(
            f"""SELECT log_id, label, label_zh, confidence, x1, y1, x2, y2 FROM detections
                WHERE log_id IN ({','.join('?' * len(meal_ids))})""", meal_ids).fetchall()
        for r in rows:
            boxes.setdefault(r["log_id"], []).append(
                {"label": r["label"], "label_zh": r["label_zh"], "confidence": r["confidence"],
                 "box": [r["x1"], r["y1"], r["x2"], r["y2"]]})
    for m in data["recent_meals"]:
        m["detections"] = boxes.get(m["id"], [])

    last = data["recent_meals"][0]["log_date"] if data["recent_meals"] else None
    return jsonify({
        "patient": {"id": user["id"], "username": user["username"],
                    "display_name": user["display_name"], "created_at": user["created_at"]},
        "reference": GLUCOSE_REF, "last_meal_date": last, **data})


# ---------- 網頁 ----------
@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/<path:filename>")
def frontend(filename):
    return send_from_directory(FRONTEND_DIR, filename)


@app.errorhandler(413)
def too_large(_e):
    return error("照片太大，請小於 10 MB", 413)


def create_app():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()
    load_detector()
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)),
                     debug=os.environ.get("FLASK_DEBUG") == "1")
