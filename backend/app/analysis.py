"""Single-GPU background pipeline, persistent tasks, and original-pixel masks."""
import gc
import math
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import openslide

from . import ai, storage
from .catalog import CLASSES, NEGATIVES

worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu-analysis")
MASKS = storage.DATA / "masks"
MASKS.mkdir(exist_ok=True)


def recover():
    for task in storage.all_records("analysis"):
        if task["status"] in ("queued", "processing"):
            task.update(status="interrupted", error="服务重启使任务中断，请重新提交")
            storage.save("analysis", task)


def create(slide, target, point=None):
    task = {"id": uuid.uuid4().hex, "slide_id": slide["id"], "class_id": target["id"], "status": "queued", "progress": 0, "candidates": [], "point": point, "created": time.time(), "version": 1, "coverage": "local" if point else "sampled"}
    storage.save("analysis", task)
    worker.submit(run, task, slide, target)
    return task


def tissue(image):
    import numpy as np
    array = np.asarray(image.resize((64, 64))).astype("int16")
    return float(((array.max(axis=2)-array.min(axis=2) > 15) & (array.min(axis=2) < 220)).mean()) > .05


def positions(slide, target, point):
    width, height = slide.dimensions
    mpp = float(slide.properties.get("openslide.mpp-x", "0"))
    if mpp <= 0:
        raise RuntimeError("切片缺少有效 μm/px 元数据；需设置或补充扫描分辨率后才能进行结构推理")
    if point:
        # Scale choices are physical fields of view in micrometers.
        patches = []
        for size in target["scales"]:
            pixels = min(width, height, max(1, round(size/mpp)))
            x = max(0, min(width-pixels, round(point[0]-pixels/2)))
            y = max(0, min(height-pixels, round(point[1]-pixels/2)))
            patches.append((x, y, pixels))
        return patches, mpp
    limit = max(1, int(os.environ.get("ANALYSIS_MAX_PATCHES", "96")))
    grid = max(1, int(math.sqrt(limit / len(target["scales"]))))
    patches = []
    for size in target["scales"]:
        pixels = max(1, round(size / mpp))
        for row in range(grid):
            for col in range(grid):
                x = round(col * max(0, width-pixels) / max(1, grid-1))
                y = round(row * max(0, height-pixels) / max(1, grid-1))
                patches.append((x, y, pixels))
    return patches[:limit], mpp


def run(task, item, target):
    slide = None
    try:
        import numpy as np
        states = ai.model_status()
        nucleus = target["id"] == "nuclei"
        nuclear_backend = "instanseg" if states["instanseg"]["configured"] else "cellpose"
        if nucleus and not states[nuclear_backend]["configured"]:
            raise RuntimeError("细胞核分析需要本地 InstanSeg 或 Cellpose 权重")
        if not nucleus and not states["conch"]["configured"]:
            raise RuntimeError(states["conch"]["message"])
        task["status"] = "processing"
        task["models"] = {"retrieval": None if nucleus else "CONCH ViT-B/16", "segmentor": nuclear_backend if nucleus else ("MedSAM ViT-B" if states["medsam"]["configured"] else None), "classifier": "UNI ViT-L/16 + local head" if not nucleus and states["uni"]["configured"] and states["uni"]["classifier_configured"] else None}
        storage.save("analysis", task)
        slide = openslide.OpenSlide(item["path"])
        patches, mpp = positions(slide, target, task["point"])
        retriever = None if nucleus else ai.Conch()
        prompts = [entry["prompt"] for entry in CLASSES if entry["id"] != "nuclei"] + NEGATIVES
        target_index = [entry["id"] for entry in CLASSES if entry["id"] != "nuclei"].index(target["id"]) if not nucleus else None
        for index, (x, y, pixels) in enumerate(patches):
            # Choose native pyramid level; never materialize an entire WSI.
            level = slide.get_best_level_for_downsample(max(1, pixels / 512))
            downsample = slide.level_downsamples[level]
            w = min(pixels, slide.dimensions[0]-x)
            h = min(pixels, slide.dimensions[1]-y)
            if w <= 0 or h <= 0:
                continue
            image = slide.read_region((x, y), level, (max(1, math.ceil(w/downsample)), max(1, math.ceil(h/downsample)))).convert("RGB")
            if tissue(image):
                scores = retriever.score(image, prompts) if retriever else [1.]
                best = int(np.argmax(scores))
                # Keep the strongest patches even when the target is not top-1;
                # ranking is relative and no calibrated threshold exists yet.
                candidate = {"id": uuid.uuid4().hex, "slide_id": item["id"], "class_id": target["id"], "bbox": [x, y, x+w, y+h], "similarity": scores[best], "confidence": None, "validated": False, "status": "provisional", "polygon": None, "mask_ref": None, "model_versions": task["models"], "analysis_version": task["version"]}
                if not nucleus:
                    candidate["similarity"] = scores[target_index]
                    candidate["retrieval_rank"] = 1 + sum(score > scores[target_index] for score in scores)
                task["candidates"].append(candidate)
            del image
            task["progress"] = round((index+1)/len(patches)*50)
            storage.save("analysis", task)
        # Do not keep CONCH, UNI, and MedSAM in 8 GiB of GPU memory simultaneously.
        del retriever
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        if not nucleus:
            task["candidates"].sort(key=lambda c: c["similarity"], reverse=True)
            task["candidates"] = task["candidates"][:max(1, int(os.environ.get("ANALYSIS_TOP_K", "12")))]
            storage.save("analysis", task)
        classifier = ai.UniClassifier() if task["models"]["classifier"] and not nucleus and task["candidates"] else None
        for index, candidate in enumerate(task["candidates"]):
            x, y, x2, y2 = candidate["bbox"]
            w, h = x2-x, y2-y
            level = slide.get_best_level_for_downsample(max(1, w/512))
            downsample = slide.level_downsamples[level]
            image = slide.read_region((x, y), level, (max(1, math.ceil(w/downsample)), max(1, math.ceil(h/downsample)))).convert("RGB")
            if classifier:
                prediction = classifier.classify(image)
                candidate["confidence"] = prediction["confidence"]
                candidate["validated"] = bool(prediction["validated"])
                if prediction["class_id"] != target["id"]:
                    candidate["status"] = "conflict"
            del image
            task["progress"] = 50 + round((index+1)/max(1,len(task["candidates"]))*25)
            storage.save("analysis", task)
        del classifier
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        for index, candidate in enumerate(task["candidates"]):
            if candidate["status"] == "conflict":
                task["progress"] = 75 + round((index+1)/len(task["candidates"])*25)
                storage.save("analysis", task)
                continue
            x, y, x2, y2 = candidate["bbox"]
            w, h = x2-x, y2-y
            level = slide.get_best_level_for_downsample(max(1, w/512))
            downsample = slide.level_downsamples[level]
            image = slide.read_region((x, y), level, (max(1, math.ceil(w/downsample)), max(1, math.ceil(h/downsample)))).convert("RGB")
            if nucleus or states["medsam"]["configured"]:
                labels = ai.segment(image, mpp*downsample, nuclear_backend if nucleus else "medsam")
                ref = MASKS / f"{candidate['id']}.npy"
                np.save(ref, labels, allow_pickle=False)
                candidate["mask_ref"] = ref.name
                candidate["mask_width"], candidate["mask_height"] = labels.shape[1], labels.shape[0]
                import cv2
                contours, _ = cv2.findContours((labels > 0).astype("uint8"), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if contours:
                    contour = max(contours, key=cv2.contourArea)
                    contour = cv2.approxPolyDP(contour, 1., True).reshape(-1, 2)
                    candidate["polygon"] = [[x+float(px)*w/labels.shape[1], y+float(py)*h/labels.shape[0]] for px, py in contour]
                if nucleus and labels.any():
                    candidate["status"] = "predicted"
                # Full-patch MedSAM prompts are provisional and cannot establish structural identity.
            del image
            task["progress"] = 75 + round((index+1)/len(task["candidates"])*25)
            storage.save("analysis", task)
        task.update(status="completed", progress=100, completed=time.time())
        storage.save("analysis", task)
    except Exception as exc:
        task.update(status="failed", error=str(exc))
        storage.save("analysis", task)
    finally:
        if slide:
            slide.close()
        # Release Python objects before the next task; share the GPU serially.
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


def mask_hit(candidate, x, y):
    if not candidate.get("mask_ref"):
        return False
    import numpy as np
    x1, y1, x2, y2 = candidate["bbox"]
    if not (x1 <= x < x2 and y1 <= y < y2):
        return False
    labels = np.load(MASKS / candidate["mask_ref"], allow_pickle=False, mmap_mode="r")
    px = min(labels.shape[1]-1, int((x-x1)/(x2-x1)*labels.shape[1]))
    py = min(labels.shape[0]-1, int((y-y1)/(y2-y1)*labels.shape[0]))
    return int(labels[py, px])


def adjudicate(slide_id, class_id, x, y):
    for task in reversed(storage.all_records("analysis")):
        if task["slide_id"] != slide_id or task["class_id"] != class_id or task["status"] != "completed":
            continue
        for candidate in task["candidates"]:
            if candidate["status"] in ("predicted", "validated"):
                label = mask_hit(candidate, x, y)
                if label:
                    return {"result": "correct", "correct": True, "instance_id": f"{candidate['id']}:{label}", "reason": "命中同类别的模型实例（暂定预测）" if not candidate["validated"] else "命中已验证类别的模型实例"}
    # Absence in a sampled/uncertain prediction is never proof of a wrong answer.
    return {"result": "uncertain", "correct": None, "instance_id": None, "reason": "局部分析未提供足够的类别及轮廓证据，无法判定"}
