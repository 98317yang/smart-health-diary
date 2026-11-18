"""YOLOv9 菜色偵測器：伺服器啟動時載入一次模型，之後每次請求直接推論。"""
import io
import sys
import threading
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageOps, UnidentifiedImageError

BACKEND_DIR = Path(__file__).resolve().parent
YOLO_DIR = BACKEND_DIR / "yolov9"
if str(YOLO_DIR) not in sys.path:
    sys.path.append(str(YOLO_DIR))

from models.common import DetectMultiBackend  # noqa: E402
from utils.augmentations import letterbox  # noqa: E402
from utils.general import check_img_size, non_max_suppression, scale_boxes  # noqa: E402
from utils.torch_utils import select_device  # noqa: E402

from nutrition import load_food_info  # noqa: E402


class FoodDetector:
    def __init__(self, weights, data_yaml, device="cpu", imgsz=640, conf_thres=0.25, iou_thres=0.45):
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

    @staticmethod
    def decode(image_bytes):
        """讀取圖片並依 EXIF 轉正（手機照片常以 EXIF 記錄方向），回傳 BGR 陣列。

        轉正後的座標系與瀏覽器顯示照片時一致，前端才能把框畫在正確位置。
        """
        try:
            pil = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
        except (UnidentifiedImageError, OSError):
            raise ValueError("無法讀取圖片，請確認檔案格式")
        return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)

    def detect(self, image_bytes, return_size=False):
        """回傳偵測到的每個物件：英文/中文標籤、信心值、框座標（轉正後的原圖像素）。

        return_size=True 時另外回傳圖片尺寸 (寬, 高)。
        """
        img0 = self.decode(image_bytes)

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
        if return_size:
            return results, [int(img0.shape[1]), int(img0.shape[0])]
        return results

    def summarize(self, detections):
        """把偵測結果依食物類別彙整，附上預設份量與可選份量（份量需由使用者確認）。"""
        groups = []
        for key, info in self.food_info.items():
            count = sum(1 for d in detections if d["label"] == key)
            if count == 0:
                continue
            groups.append({
                "group": key, "group_zh": info["zh"], "dishes": count,
                "serving_hint": info["serving_hint"],
                "per_serving": info["per_serving"],
                "portion_options": info["portion_options"],
                "servings": info["default_servings"] * count,
            })
        return groups
