"""YOLOv9 菜色偵測器：伺服器啟動時載入一次模型，之後每次請求直接推論。"""
import json
import sys
import threading
from pathlib import Path

import cv2
import numpy as np
import torch

BACKEND_DIR = Path(__file__).resolve().parent
YOLO_DIR = BACKEND_DIR / "yolov9"
if str(YOLO_DIR) not in sys.path:
    sys.path.append(str(YOLO_DIR))

from models.common import DetectMultiBackend  # noqa: E402
from utils.augmentations import letterbox  # noqa: E402
from utils.general import check_img_size, non_max_suppression, scale_boxes  # noqa: E402
from utils.torch_utils import select_device  # noqa: E402


def load_food_info(path=BACKEND_DIR / "food_info.json"):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["classes"]


class FoodDetector:
    def __init__(self, weights, data_yaml, device="cpu", imgsz=352, conf_thres=0.25, iou_thres=0.45):
        self.device = select_device(device)
        self.model = DetectMultiBackend(str(weights), device=self.device, data=str(data_yaml))
        self.names = self.model.names
        self.stride = self.model.stride
        self.imgsz = check_img_size((imgsz, imgsz), s=self.stride)
        self.conf_thres = conf_thres
        self.iou_thres = iou_thres
        self.food_info = load_food_info()
        self._lock = threading.Lock()
        self.model.warmup(imgsz=(1, 3, *self.imgsz))

    def detect(self, image_bytes):
        """回傳偵測到的每個物件：英文/中文標籤、信心值、框座標（原圖像素）。"""
        img0 = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img0 is None:
            raise ValueError("無法讀取圖片，請確認檔案格式")

        im = letterbox(img0, self.imgsz, stride=self.stride, auto=self.model.pt)[0]
        im = im.transpose((2, 0, 1))[::-1]  # HWC->CHW, BGR->RGB
        im = torch.from_numpy(np.ascontiguousarray(im)).to(self.device).float() / 255.0
        im = im[None]

        with self._lock, torch.no_grad():
            pred = self.model(im)
        det = non_max_suppression(pred, self.conf_thres, self.iou_thres)[0]

        results = []
        if len(det):
            det[:, :4] = scale_boxes(im.shape[2:], det[:, :4], img0.shape).round()
            for *xyxy, conf, cls in det.tolist():
                label = self.names[int(cls)]
                info = self.food_info.get(label, {})
                results.append({
                    "label": label,
                    "label_zh": info.get("zh", label),
                    "confidence": round(conf, 3),
                    "box": [int(v) for v in xyxy],
                })
        return results

    def estimate_calories(self, detections):
        """依標準份量對照表推估熱量（每個偵測框視為一份）。"""
        dishes = {}
        for d in detections:
            info = self.food_info.get(d["label"], {})
            item = dishes.setdefault(d["label"], {
                "label": d["label"], "label_zh": d["label_zh"], "servings": 0,
                "grams": 0, "kcal": 0, "kcal_known": info.get("kcal_per_100g") is not None,
            })
            serving_g = info.get("serving_g", 100)
            item["servings"] += 1
            item["grams"] += serving_g
            if item["kcal_known"]:
                item["kcal"] += round(info["kcal_per_100g"] * serving_g / 100)
        dishes = list(dishes.values())
        for item in dishes:
            if not item["kcal_known"]:
                item["kcal"] = None
        total = sum(i["kcal"] for i in dishes if i["kcal"] is not None)
        return {"dishes": dishes, "total_kcal": total,
                "incomplete": any(not i["kcal_known"] for i in dishes)}
