#!/usr/bin/env python3
"""檢查資料庫裡的健康日誌是否完整、合理。

每一筆日誌都應該是完整的：運動、飯前血糖、飯後血糖、三碗蔬菜四項都要有值。
缺值的紀錄在醫護端只會變成無法判讀的空格，也會讓達成率的分母失真。

    python backend/check_data.py           # 檢查並列出問題
    python backend/check_data.py --fix     # 一併刪除完全空白的紀錄（會先詢問）

資料庫位置預設與後端相同，可用環境變數 SHD_DB 指定。
"""
import argparse
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("SHD_DB", BACKEND_DIR / "instance" / "health.db"))
UPLOAD_DIR = Path(os.environ.get("SHD_UPLOADS", BACKEND_DIR / "uploads"))
REQUIRED = {
    "exercise_done": "運動",
    "glucose_pre": "飯前血糖",
    "glucose_post": "飯後血糖",
    "veggie_done": "三碗蔬菜",
}
WINDOW = 7      # 醫護端預設檢視的天數
MIN_DAYS = 3    # 每位病人在該範圍內至少要有幾天紀錄


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="刪除四項都空白的紀錄")
    ap.add_argument("--days", type=int, default=WINDOW)
    args = ap.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"找不到資料庫 {DB_PATH}")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    problems = 0
    end = date.today()
    start = end - timedelta(days=args.days - 1)

    patients = db.execute(
        "SELECT id, username, display_name, birthday, gender FROM users WHERE role = 'patient' ORDER BY id"
    ).fetchall()
    print(f"檢查 {len(patients)} 位病人（檢視範圍 {start}～{end}）\n")

    for u in patients:
        name = u["display_name"] or u["username"]
        issues = []

        if not u["birthday"]:
            issues.append("沒有生日")
        if not u["gender"]:
            issues.append("沒有性別")

        rows = db.execute("SELECT * FROM health_logs WHERE user_id = ?", (u["id"],)).fetchall()

        # 1. 缺欄位的紀錄
        incomplete = {}
        blank = 0
        for r in rows:
            missing = [zh for col, zh in REQUIRED.items() if r[col] is None]
            if len(missing) == len(REQUIRED):
                blank += 1
            if missing:
                incomplete.setdefault(tuple(missing), []).append(r["log_date"])
        for missing, dates in sorted(incomplete.items(), key=lambda kv: -len(kv[1])):
            issues.append(f"{len(dates)} 筆缺「{'、'.join(missing)}」（{dates[0]} 起）")

        # 2. 檢視範圍內的天數
        in_window = {r["log_date"] for r in rows
                     if start.isoformat() <= r["log_date"] <= end.isoformat()}
        if rows and len(in_window) < MIN_DAYS:
            issues.append(f"最近 {args.days} 天只有 {len(in_window)} 天有紀錄（至少要 {MIN_DAYS} 天）")
        if not rows:
            issues.append("完全沒有紀錄")

        # 3. 照片檔案是否真的存在
        missing_files = [r["photo_filename"] for r in rows
                         if r["photo_filename"] and not (UPLOAD_DIR / r["photo_filename"]).exists()]
        if missing_files:
            issues.append(f"{len(missing_files)} 筆的照片檔案不見了")

        # 4. 有照片卻沒有辨識框，或份數與醣類對不起來
        for r in rows:
            if r["photo_filename"]:
                n = db.execute("SELECT COUNT(*) FROM detections WHERE log_id = ?", (r["id"],)).fetchone()[0]
                if n == 0:
                    issues.append(f"{r['log_date']} 的餐點有照片但沒有辨識結果")
                    break
        bad_carbs = [r["log_date"] for r in rows if r["photo_filename"] and r["carbs_g"] is None]
        if bad_carbs:
            issues.append(f"{len(bad_carbs)} 筆有照片卻沒有醣類換算（{bad_carbs[0]} 起）")

        # 5. 血糖數值是否在合理範圍
        odd = [r["log_date"] for r in rows
               if (r["glucose_pre"] is not None and not 20 <= r["glucose_pre"] <= 600)
               or (r["glucose_post"] is not None and not 20 <= r["glucose_post"] <= 600)]
        if odd:
            issues.append(f"{len(odd)} 筆血糖數值超出 20–600（{odd[0]} 起）")

        mark = "✗" if issues else "✓"
        extra = f"（{len(rows)} 筆、{len({r['log_date'] for r in rows})} 天）"
        print(f"{mark} {name:<8}{extra}")
        for i in issues:
            print(f"      - {i}")
        problems += len(issues)

        if args.fix and blank:
            ans = input(f"      刪除 {name} 的 {blank} 筆全空白紀錄？(y/N) ")
            if ans.lower() == "y":
                ids = [r["id"] for r in rows
                       if all(r[c] is None for c in REQUIRED) and not r["photo_filename"]]
                if ids:
                    marks = ",".join("?" * len(ids))
                    db.execute(f"DELETE FROM detections WHERE log_id IN ({marks})", ids)
                    db.execute(f"DELETE FROM health_logs WHERE id IN ({marks})", ids)
                    db.commit()
                    print(f"      已刪除 {len(ids)} 筆")

    # 孤兒資料
    orphan_det = db.execute(
        "SELECT COUNT(*) FROM detections WHERE log_id NOT IN (SELECT id FROM health_logs)").fetchone()[0]
    used = {r[0] for r in db.execute(
        "SELECT photo_filename FROM health_logs WHERE photo_filename IS NOT NULL")}
    on_disk = {f.name for f in UPLOAD_DIR.iterdir()} if UPLOAD_DIR.exists() else set()
    stray = on_disk - used
    print()
    if orphan_det:
        print(f"✗ {orphan_det} 筆辨識框沒有對應的紀錄"); problems += 1
    if stray:
        print(f"✗ uploads 有 {len(stray)} 張沒有任何紀錄在用的照片"); problems += 1
    if not orphan_det and not stray:
        print(f"✓ 照片與辨識框都對得上（{len(used)} 張）")

    print("\n" + ("全部通過" if problems == 0 else f"共 {problems} 個問題"))
    db.close()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
