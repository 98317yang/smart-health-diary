"""把菜色資料集轉成「食物類別」資料集（全穀雜糧類／蔬菜類／豆魚蛋肉類）。

來源：
  1. 原本的 8 類菜色資料（YOLO 格式，單一菜色特寫）
  2. Chinese Cafeteria 資料集（Pascal VOC 格式，含整盤照片，但部分食物未標註）

處理方式（原因見 LABELING.md）：
  - 類別依 LABELING.md 的對應表轉換，混合菜依「主要成分」歸類
  - Chinese Cafeteria 的「自助餐現場拍攝」系列（檔名 MMDD_YYMMDD_NNNN）
    全部保留給整盤測試集，不用於訓練，避免同一場景同時出現在訓練與測試
  - 其他 Chinese Cafeteria 圖片：
      * 只有一個標註框、且目視確認畫面中沒有其他未標註食物 → 整張使用
      * 其餘（多個標註框，或列在 cc_excluded.tsv）→ 只裁切出每個已標註的區域
  - 以檔案內容 MD5 去除重複圖片
  - 以「來源圖片」為單位分組，依主要類別分層切成 train / val / test_single（70/15/15），
    同一張原圖裁出來的多個區域一定落在同一組

用法：
  python prepare_foodgroups.py --own <原本資料集 train 資料夾> --cc <Chinese Cafeteria data 資料夾> --out <輸出資料夾>
"""
import argparse
import glob
import hashlib
import os
import random
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

GROUPS = ["全穀雜糧類", "蔬菜類", "豆魚蛋肉類"]
GROUPS_EN = ["grains_starches", "vegetables", "protein"]

# 原本 8 類菜色 → 食物類別
OWN_MAP = {
    0: 1,  # 炒青菜
    1: 1,  # 娃娃菜
    2: 0,  # 炒馬鈴薯絲（馬鈴薯屬全穀雜糧類）
    3: 2,  # 紅燒雞腿
    4: 2,  # 紅燒魚
    5: 1,  # 清炒小黃瓜
    6: 1,  # 肉末茄子（主要成分：茄子）
    7: 2,  # 番茄炒蛋（主要成分：蛋）
}

# Chinese Cafeteria 12 類 → 食物類別
CC_MAP = {
    "rice": 0,
    "triangle_hash_brown": 0,  # 薯餅（馬鈴薯）
    "fried_dumplings": 0,      # 煎餃（主要成分：餃子皮）
    "curry": 0,                # 咖哩（主要成分：馬鈴薯與勾芡醬汁）
    "mung_bean_sprouts": 1,
    "water_spinach": 1,
    "chinese_cabbage": 1,      # 高麗菜
    "carrot_eggs": 1,          # 紅蘿蔔炒蛋（主要成分：紅蘿蔔）
    "fried_chicken": 2,
    "fried_eggs": 2,
    "chinese_sausage": 2,
    "chicken_nuggets": 2,
}

CAFETERIA_SERIES = re.compile(r"\d+_\d+_\d+")
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
CROP_MARGIN = 0.04  # 裁切時在標註框外多留的比例


def md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def find_image(folder, stem):
    for ext in IMG_EXT + tuple(e.upper() for e in IMG_EXT):
        p = Path(folder) / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def read_voc(xml_path):
    root = ET.parse(xml_path).getroot()
    boxes = []
    for o in root.findall("object"):
        name = o.find("name").text.strip()
        b = o.find("bndbox")
        boxes.append((name, *[float(b.find(k).text) for k in ("xmin", "ymin", "xmax", "ymax")]))
    return boxes


def to_yolo(cls, x1, y1, x2, y2, w, h):
    x1, x2 = max(0, min(x1, x2)), min(w, max(x1, x2))
    y1, y2 = max(0, min(y1, y2)), min(h, max(y1, y2))
    return f"{cls} {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} {(x2 - x1) / w:.6f} {(y2 - y1) / h:.6f}"


def collect(own_dir, cc_dir, excluded):
    """回傳 samples（訓練用）與 tray_pool（整盤測試候選）。

    sample = dict(group=來源圖片代號, kind=whole|crop, src=路徑, crop=None|(x1,y1,x2,y2),
                  labels=[(cls, x1,y1,x2,y2 以原圖像素)], primary=主要類別)
    """
    samples, tray_pool, seen = [], [], set()
    stats = Counter()

    # 1) 原本的 8 類資料（只用 train；原本的 val 全部與 train 重複）
    for lab in sorted(glob.glob(os.path.join(own_dir, "labels", "*.txt"))):
        stem = Path(lab).stem
        if stem == "classes":
            continue
        img = find_image(Path(own_dir) / "images", stem)
        if img is None:
            stats["own_缺圖"] += 1
            continue
        h5 = md5(img)
        if h5 in seen:
            stats["own_重複"] += 1
            continue
        seen.add(h5)
        w, h = Image.open(img).size
        labels = []
        for line in open(lab, encoding="utf-8"):
            p = line.split()
            if len(p) < 5 or not p[0].isdigit():
                continue
            c, x, y, bw, bh = int(p[0]), *map(float, p[1:5])
            labels.append((OWN_MAP[c], (x - bw / 2) * w, (y - bh / 2) * h, (x + bw / 2) * w, (y + bh / 2) * h))
        if not labels:
            stats["own_無標註"] += 1
            continue
        samples.append(dict(group=f"own:{stem}", kind="whole", src=img, crop=None, labels=labels,
                            primary=Counter(l[0] for l in labels).most_common(1)[0][0], source="own"))
        stats["own_整張"] += 1

    # 2) Chinese Cafeteria
    for split in ("train", "valid", "test"):
        for xml in sorted(glob.glob(os.path.join(cc_dir, split, "anns", "*.xml"))):
            stem = Path(xml).stem
            img = find_image(Path(cc_dir) / split / "imgs", stem)
            if img is None:
                stats["cc_缺圖"] += 1
                continue
            h5 = md5(img)
            if h5 in seen:
                stats["cc_重複"] += 1
                continue
            seen.add(h5)
            boxes = [b for b in read_voc(xml) if b[0] in CC_MAP]
            if not boxes:
                continue
            if CAFETERIA_SERIES.fullmatch(stem):
                tray_pool.append((img, boxes))
                continue
            gid = f"cc:{split}/{stem}"
            if len(boxes) == 1 and stem not in excluded:
                n, *b = boxes[0]
                samples.append(dict(group=gid, kind="whole", src=img, crop=None,
                                    labels=[(CC_MAP[n], *b)], primary=CC_MAP[n], source="cc"))
                stats["cc_整張"] += 1
            else:
                w, h = Image.open(img).size
                for n, x1, y1, x2, y2 in boxes:
                    if (x2 - x1) < 32 or (y2 - y1) < 32:
                        stats["cc_框太小略過"] += 1
                        continue
                    mx, my = (x2 - x1) * CROP_MARGIN, (y2 - y1) * CROP_MARGIN
                    crop = (max(0, x1 - mx), max(0, y1 - my), min(w, x2 + mx), min(h, y2 + my))
                    samples.append(dict(group=gid, kind="crop", src=img, crop=crop,
                                        labels=[(CC_MAP[n], x1, y1, x2, y2)], primary=CC_MAP[n], source="cc"))
                    stats["cc_裁切"] += 1
    return samples, tray_pool, stats


def split_groups(samples, seed):
    by_group = defaultdict(list)
    for s in samples:
        by_group[s["group"]].append(s)
    by_class = defaultdict(list)
    for g, ss in by_group.items():
        by_class[Counter(s["primary"] for s in ss).most_common(1)[0][0]].append(g)
    rng = random.Random(seed)
    assign = {}
    for cls, groups in sorted(by_class.items()):
        groups.sort()
        rng.shuffle(groups)
        n = len(groups)
        n_val, n_test = round(n * 0.15), round(n * 0.15)
        for i, g in enumerate(groups):
            assign[g] = "val" if i < n_val else "test_single" if i < n_val + n_test else "train"
    return {split: [s for g, ss in by_group.items() if assign[g] == split for s in ss]
            for split in ("train", "val", "test_single")}


def write_sample(s, out_dir, idx):
    img = Image.open(s["src"]).convert("RGB")
    name = f"{s['source']}_{idx:05d}"
    if s["crop"]:
        cx1, cy1, cx2, cy2 = s["crop"]
        img = img.crop((int(cx1), int(cy1), int(cx2), int(cy2)))
        ox, oy = int(cx1), int(cy1)
    else:
        ox = oy = 0
    w, h = img.size
    img.save(out_dir / "images" / f"{name}.jpg", quality=92)
    lines = [to_yolo(c, x1 - ox, y1 - oy, x2 - ox, y2 - oy, w, h) for c, x1, y1, x2, y2 in s["labels"]]
    (out_dir / "labels" / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--own", required=True, help="原本 8 類資料集的 train 資料夾（含 images/ labels/）")
    ap.add_argument("--cc", required=True, help="Chinese Cafeteria 的 data 資料夾（含 train/ valid/ test/）")
    ap.add_argument("--out", required=True, help="輸出資料夾（必須不存在）")
    ap.add_argument("--tray-n", type=int, default=100, help="整盤測試集要準備幾張")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"{out} 已存在，請換一個輸出位置或先移除")
    here = Path(__file__).resolve().parent
    excluded = {l.split("\t")[0] for l in (here / "cc_excluded.tsv").read_text(encoding="utf-8").splitlines()
                if l and not l.startswith("#")}

    samples, tray_pool, stats = collect(args.own, args.cc, excluded)
    splits = split_groups(samples, args.seed)

    idx = 0
    for split, ss in splits.items():
        d = out / split
        (d / "images").mkdir(parents=True)
        (d / "labels").mkdir()
        for s in ss:
            write_sample(s, d, idx)
            idx += 1

    # 整盤測試集：挑選有 2 個以上標註框的自助餐現場照片，附上已轉換的既有標註，等待人工補齊
    rng = random.Random(args.seed)
    multi = sorted([t for t in tray_pool if len(t[1]) >= 2], key=lambda t: str(t[0]))
    rng.shuffle(multi)
    todo = out / "test_tray_todo"
    (todo / "images").mkdir(parents=True)
    (todo / "labels").mkdir()
    for i, (img, boxes) in enumerate(multi[: args.tray_n]):
        name = f"tray_{i:03d}"
        w, h = Image.open(img).size
        shutil.copy(img, todo / "images" / f"{name}{img.suffix.lower()}")
        (todo / "labels" / f"{name}.txt").write_text(
            "\n".join(to_yolo(CC_MAP[n], x1, y1, x2, y2, w, h) for n, x1, y1, x2, y2 in boxes) + "\n",
            encoding="utf-8")
    (todo / "labels" / "classes.txt").write_text("\n".join(GROUPS_EN) + "\n", encoding="utf-8")
    (todo / "sources.tsv").write_text(
        "\n".join(f"tray_{i:03d}\t{img}" for i, (img, _) in enumerate(multi[: args.tray_n])) + "\n",
        encoding="utf-8")

    (out / "foodgroups.yaml").write_text(
        # YOLOv9 會以 backend/yolov9/ 為基準解析相對路徑，所以寫相對路徑，換電腦也能用
        f"path: {os.path.relpath(out.resolve(), here.parent / 'backend' / 'yolov9')}\n"
        "train: train/images\n"
        "val: val/images\n"
        "test: test_single/images\n"
        f"nc: {len(GROUPS)}\n"
        f"names: {GROUPS_EN}  # {' / '.join(GROUPS)}\n",
        encoding="utf-8")

    # 報告
    lines = ["# 資料集產生報告", "", "## 來源處理統計"]
    lines += [f"- {k}: {v}" for k, v in sorted(stats.items())]
    lines += ["", "## 各組圖片數與標註框數（依類別）", "",
              "| 組別 | 圖片數 | " + " | ".join(GROUPS) + " |", "|---|---|" + "---|" * len(GROUPS)]
    for split, ss in splits.items():
        c = Counter(l[0] for s in ss for l in s["labels"])
        lines.append(f"| {split} | {len(ss)} | " + " | ".join(str(c[i]) for i in range(len(GROUPS))) + " |")
    c = Counter(CC_MAP[b[0]] for _, boxes in multi[: args.tray_n] for b in boxes)
    lines.append(f"| test_tray（既有標註，待補齊） | {min(args.tray_n, len(multi))} | "
                 + " | ".join(str(c[i]) for i in range(len(GROUPS))) + " |")
    lines += ["", f"自助餐現場系列共 {len(tray_pool)} 張，其中有 2 個以上標註框的 {len(multi)} 張，"
              f"隨機選 {min(args.tray_n, len(multi))} 張作為整盤測試集。"]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
