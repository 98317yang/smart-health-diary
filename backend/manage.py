#!/usr/bin/env python3
"""管理帳號角色的命令列工具。

醫護帳號不開放從註冊頁建立，必須由系統管理者在伺服器上執行這個指令，
避免任何人自行註冊後就能看到所有病人的資料。

用法（在 smart-health-diary 資料夾裡執行）：
    python backend/manage.py list                     列出所有帳號與角色
    python backend/manage.py create-staff <帳號>       建立醫護帳號（會詢問密碼）
    python backend/manage.py set-role <帳號> staff     將既有帳號改為醫護
    python backend/manage.py set-role <帳號> patient   改回病人
    python backend/manage.py set-password <帳號>       重設某個帳號的密碼（會詢問密碼）

資料庫位置預設與後端相同，可用環境變數 SHD_DB 指定。
"""
import argparse
import getpass
import os
import sqlite3
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

BACKEND_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("SHD_DB", BACKEND_DIR / "instance" / "health.db"))
ROLES = ("patient", "staff")
ROLE_ZH = {"patient": "病人", "staff": "醫護"}


def connect():
    if not DB_PATH.exists():
        sys.exit(f"找不到資料庫 {DB_PATH}，請先啟動一次後端讓它建立。")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    if "role" not in {r[1] for r in db.execute("PRAGMA table_info(users)")}:
        sys.exit("資料庫還沒有 role 欄位，請先啟動一次後端完成升級。")
    return db


def cmd_list(_args, db):
    rows = db.execute("SELECT username, display_name, role, created_at FROM users ORDER BY role, id").fetchall()
    if not rows:
        print("目前沒有任何帳號。")
        return
    print(f"{'帳號':<16}{'姓名':<12}{'角色':<6}註冊日期")
    for r in rows:
        print(f"{r['username']:<16}{(r['display_name'] or '—'):<12}{ROLE_ZH[r['role']]:<6}{r['created_at']}")


def cmd_create_staff(args, db):
    if db.execute("SELECT 1 FROM users WHERE username = ?", (args.username,)).fetchone():
        sys.exit(f"帳號 {args.username} 已存在；若要把它改成醫護，請用 set-role。")
    if not 3 <= len(args.username) <= 30:
        sys.exit("帳號長度需為 3–30 個字元。")
    password = getpass.getpass("請輸入密碼（至少 6 個字元，輸入時不會顯示）：")
    if len(password) < 6:
        sys.exit("密碼至少需要 6 個字元。")
    if password != getpass.getpass("請再輸入一次："):
        sys.exit("兩次輸入的密碼不同。")
    db.execute("INSERT INTO users (username, password_hash, display_name, role) VALUES (?, ?, ?, 'staff')",
               (args.username, generate_password_hash(password), args.name))
    db.commit()
    print(f"已建立醫護帳號 {args.username}。")


def cmd_set_password(args, db):
    if db.execute("SELECT 1 FROM users WHERE username = ?", (args.username,)).fetchone() is None:
        sys.exit(f"找不到帳號 {args.username}。")
    password = getpass.getpass("請輸入新密碼（至少 6 個字元，輸入時不會顯示）：")
    if len(password) < 6:
        sys.exit("密碼至少需要 6 個字元。")
    if password != getpass.getpass("請再輸入一次："):
        sys.exit("兩次輸入的密碼不同。")
    db.execute("UPDATE users SET password_hash = ? WHERE username = ?",
               (generate_password_hash(password), args.username))
    db.commit()
    print(f"已更新 {args.username} 的密碼。")


def cmd_set_role(args, db):
    user = db.execute("SELECT role FROM users WHERE username = ?", (args.username,)).fetchone()
    if user is None:
        sys.exit(f"找不到帳號 {args.username}。")
    if user["role"] == args.role:
        print(f"{args.username} 已經是{ROLE_ZH[args.role]}，不需變更。")
        return
    if user["role"] == "staff" and args.role == "patient":
        remaining = db.execute("SELECT COUNT(*) FROM users WHERE role = 'staff'").fetchone()[0] - 1
        if remaining == 0 and input("這是最後一個醫護帳號，改掉後將沒有人能進入管理端。確定嗎？(y/N) ").lower() != "y":
            sys.exit("已取消。")
    db.execute("UPDATE users SET role = ? WHERE username = ?", (args.role, args.username))
    db.commit()
    print(f"已將 {args.username} 設為{ROLE_ZH[args.role]}。")


def main():
    parser = argparse.ArgumentParser(description="管理帳號角色")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="列出所有帳號與角色").set_defaults(func=cmd_list)
    p_create = sub.add_parser("create-staff", help="建立醫護帳號")
    p_create.add_argument("username")
    p_create.add_argument("--name", default=None, help="顯示名稱，例如：王護理師")
    p_create.set_defaults(func=cmd_create_staff)
    p_pw = sub.add_parser("set-password", help="重設某個帳號的密碼")
    p_pw.add_argument("username")
    p_pw.set_defaults(func=cmd_set_password)
    p_role = sub.add_parser("set-role", help="變更既有帳號的角色")
    p_role.add_argument("username")
    p_role.add_argument("role", choices=ROLES)
    p_role.set_defaults(func=cmd_set_role)

    args = parser.parse_args()
    db = connect()
    try:
        args.func(args, db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
