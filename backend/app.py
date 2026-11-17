"""智慧健康日誌 — Flask 後端

啟動時載入一次 YOLOv9 模型，提供：
  註冊 / 登入 / 登出      POST /api/register, /api/login, /api/logout；GET /api/me
  新增健康日誌（含照片）  POST /api/logs
  歷史統計               GET  /api/history?days=7
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

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"

CONFIG = {
    "DB_PATH": Path(os.environ.get("SHD_DB", BACKEND_DIR / "instance" / "health.db")),
    "UPLOAD_DIR": Path(os.environ.get("SHD_UPLOADS", BACKEND_DIR / "uploads")),
    "WEIGHTS": Path(os.environ.get("SHD_WEIGHTS", PROJECT_DIR / "weights" / "best.pt")),
    "DATA_YAML": PROJECT_DIR / "training" / "food.yaml",
    "DEVICE": os.environ.get("SHD_DEVICE", "cpu"),
    "IMGSZ": int(os.environ.get("SHD_IMGSZ", 352)),  # 與訓練時的 350 px 一致
}
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MEAL_TYPES = {"早餐", "午餐", "晚餐"}

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


def stored_kcal(calories):
    """有辨識到菜色但全部都沒有熱量資料時存 NULL，避免顯示成 0 大卡。"""
    if not calories:
        return None
    known = [d for d in calories["dishes"] if d["kcal"] is not None]
    if calories["dishes"] and not known:
        return None
    return calories["total_kcal"]


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
                    "display_name": user["display_name"]})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"message": "已登出"})


@app.get("/api/me")
@login_required
def me():
    user = get_db().execute("SELECT username, display_name, created_at FROM users WHERE id = ?",
                            (session["user_id"],)).fetchone()
    if user is None:
        session.clear()
        return error("請先登入", 401)
    return jsonify(dict(user) | {"detector_ready": detector is not None})


# ---------- 健康日誌 ----------
@app.post("/api/logs")
@login_required
def create_log():
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
    detections, calories, photo_filename = [], None, None
    if photo and photo.filename:
        ext = Path(photo.filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            return error("只接受 JPG、PNG、WEBP、BMP 圖片")
        if detector is None:
            return error("伺服器未載入辨識模型，暫時無法分析照片", 503)
        image_bytes = photo.read()
        try:
            detections = detector.detect(image_bytes)
        except ValueError as e:
            return error(str(e))
        calories = detector.estimate_calories(detections)
        CONFIG["UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
        photo_filename = f"{uuid.uuid4().hex}{ext}"
        (CONFIG["UPLOAD_DIR"] / photo_filename).write_bytes(image_bytes)

    fields = [glucose_pre, glucose_post, parse_bool(form.get("exercise")),
              parse_bool(form.get("veggie")), photo_filename]
    if all(v is None for v in fields):
        return error("請至少填寫一項資料或上傳一張照片")

    db = get_db()
    cur = db.execute(
        """INSERT INTO health_logs (user_id, log_date, meal_type, exercise_done, glucose_pre,
                                    glucose_post, veggie_done, photo_filename, total_kcal)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session["user_id"], log_date, meal_type, fields[2], glucose_pre, glucose_post,
         fields[3], photo_filename, stored_kcal(calories)))
    db.executemany(
        "INSERT INTO detections (log_id, label, label_zh, confidence, x1, y1, x2, y2) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(cur.lastrowid, d["label"], d["label_zh"], d["confidence"], *d["box"]) for d in detections])
    db.commit()
    return jsonify({"message": "已儲存", "log_id": cur.lastrowid,
                    "detections": detections, "calories": calories}), 201


@app.get("/api/history")
@login_required
def history():
    days = min(max(request.args.get("days", 7, type=int), 1), 90)
    end = date.today()
    start = end - timedelta(days=days - 1)
    db = get_db()
    rows = db.execute(
        """SELECT * FROM health_logs WHERE user_id = ? AND log_date BETWEEN ? AND ?
           ORDER BY log_date, created_at""",
        (session["user_id"], start.isoformat(), end.isoformat())).fetchall()

    # 逐日彙整：血糖取平均；運動 / 蔬菜當天只要有一筆「是」即算達成
    daily = {(start + timedelta(days=i)).isoformat(): {
        "pre": [], "post": [], "exercise": None, "veggie": None} for i in range(days)}
    for r in rows:
        d = daily[r["log_date"]]
        if r["glucose_pre"] is not None:
            d["pre"].append(r["glucose_pre"])
        if r["glucose_post"] is not None:
            d["post"].append(r["glucose_post"])
        for key, col in (("exercise", "exercise_done"), ("veggie", "veggie_done")):
            if r[col] is not None:
                d[key] = max(d[key] or 0, r[col])

    def avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    def rate(key):
        answered = [d[key] for d in daily.values() if d[key] is not None]
        return {"done": sum(answered), "answered_days": len(answered)}

    recent = db.execute(
        """SELECT l.id, l.log_date, l.meal_type, l.total_kcal,
                  GROUP_CONCAT(d.label_zh, '、') AS dishes
           FROM health_logs l LEFT JOIN detections d ON d.log_id = l.id
           WHERE l.user_id = ? AND l.photo_filename IS NOT NULL
           GROUP BY l.id ORDER BY l.log_date DESC, l.created_at DESC LIMIT 10""",
        (session["user_id"],)).fetchall()

    return jsonify({
        "start": start.isoformat(), "end": end.isoformat(),
        "dates": list(daily.keys()),
        "glucose_pre": [avg(d["pre"]) for d in daily.values()],
        "glucose_post": [avg(d["post"]) for d in daily.values()],
        "exercise": rate("exercise"),
        "veggie": rate("veggie"),
        "recent_meals": [dict(r) for r in recent],
    })


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
