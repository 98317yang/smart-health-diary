"""食物類別的營養換算（國健署食物代換表）。不依賴 PyTorch，網站其他功能可單獨使用。"""
import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent


def load_food_info(path=BACKEND_DIR / "food_info.json"):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["groups"]


def nutrition(servings_by_group, food_info=None):
    """依各類別份數計算醣類、蛋白質與熱量（國健署食物代換表）。不需要載入模型。"""
    food_info = food_info or load_food_info()
    total = {"carbs_g": 0, "protein_g": 0, "kcal": 0}
    for key, servings in servings_by_group.items():
        per = food_info[key]["per_serving"]
        for k in total:
            total[k] += per[k] * servings
    return {k: round(v, 1) for k, v in total.items()}
