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


# 同一類別的兩個框，若小框有這麼大的比例落在大框裡，視為同一處食物重複框到
NESTED_COVERAGE = 0.8


def count_dishes(detections):
    """依辨識框計算每一類有幾處食物，作為預設份數的依據。

    detections 為 (類別, [x1, y1, x2, y2]) 的序列。模型偶爾會在一大片白飯上框一個大框、
    裡面再框一個小框，直接數框數會把同一份飯算兩次；所以小框有 NESTED_COVERAGE 以上
    落在同類大框內時，只算一處。
    在 36 張整盤照片中，這種情況出現 4 次：2 次確實是重複框、1 次是兩種不同的全穀雜糧
    （薯餅與白飯）被合併、1 次難以判斷。份數最後仍由使用者確認。
    """
    def area(b):
        return max(0, b[2] - b[0]) * max(0, b[3] - b[1])

    def covered(small, big):
        ix = max(0, min(small[2], big[2]) - max(small[0], big[0]))
        iy = max(0, min(small[3], big[3]) - max(small[1], big[1]))
        return ix * iy / area(small) if area(small) else 1.0

    counts = {}
    by_label = {}
    for label, box in detections:
        by_label.setdefault(label, []).append(box)
    for label, boxes in by_label.items():
        boxes = sorted(boxes, key=area, reverse=True)  # 由大到小，小框才比對得到包住它的大框
        kept = []
        for b in boxes:
            if not any(covered(b, k) >= NESTED_COVERAGE for k in kept):
                kept.append(b)
        counts[label] = len(kept)
    return counts

