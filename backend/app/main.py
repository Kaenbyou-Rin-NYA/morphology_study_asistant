"""WSI tile service and a small, explicitly annotated learning workflow."""
from __future__ import annotations

import io
import math
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import openslide
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from openslide.deepzoom import DeepZoomGenerator
from pydantic import BaseModel, Field
from . import analysis, ai, storage
from .catalog import CLASSES, resolve

ROOT = Path(__file__).resolve().parents[1]
SLIDES = ROOT / "data" / "slides"
UPLOADS = storage.DATA / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)
DEMO_ID = "aorta-demo"
DEMO_FILE = SLIDES / "JP2K-33003-1.svs"
MAX_UPLOAD = int(float(os.environ.get("MAX_UPLOAD_GIB", "10")) * 1024 ** 3)
EXTENSIONS = {".svs", ".tif", ".tiff", ".ndpi", ".mrxs"}
executor = ThreadPoolExecutor(max_workers=2)
lock = threading.RLock()
slides: dict[str, dict] = {item["id"]: item for item in storage.all_records("slide")}
sessions: dict[str, dict] = {item["id"]: item for item in storage.all_records("session")}
viewers: dict[str, tuple[openslide.OpenSlide, DeepZoomGenerator]] = {}

app = FastAPI(title="形态学学习助手 · 初版")


def register_demo():
    if DEMO_FILE.exists():
        slides[DEMO_ID] = {"id": DEMO_ID, "name": "主动脉组织切片 · 演示", "path": str(DEMO_FILE), "status": "ready", "demo": True}


register_demo()
analysis.recover()
for item in slides.values():
    if item["status"] == "processing":
        item.update(status="failed", error="处理被重启中断，请重新上传")
        storage.save("slide", item)


@app.get("/api/config")
def configuration():
    return {"max_upload_bytes": MAX_UPLOAD, "classes": CLASSES, "models": ai.model_status()}


class AnalysisRequest(BaseModel):
    goal: str = Field(min_length=2, max_length=300)


@app.post("/api/slides/{slide_id}/analyses", status_code=202)
def submit_analysis(slide_id: str, request: AnalysisRequest):
    item = get_slide(slide_id)
    if item["status"] != "ready":
        raise HTTPException(409, "切片未就绪")
    try:
        target = resolve(request.goal)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    states = ai.model_status()
    if target["id"] == "nuclei":
        if not (states["instanseg"]["configured"] or states["cellpose"]["configured"]):
            raise HTTPException(409, "请先配置 InstanSeg 或 Cellpose 的本地模型权重")
    elif not states["conch"]["configured"]:
        raise HTTPException(409, states["conch"]["message"])
    return analysis.create(item, target)


@app.get("/api/analyses/{task_id}")
def analysis_status(task_id: str):
    task = storage.get("analysis", task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@app.get("/api/analyses/{task_id}/candidates")
def candidates(task_id: str):
    return analysis_status(task_id)["candidates"]


def get_slide(slide_id: str) -> dict:
    item = slides.get(slide_id)
    if not item:
        raise HTTPException(404, "切片不存在")
    return item


def viewer(slide_id: str) -> tuple[openslide.OpenSlide, DeepZoomGenerator]:
    item = get_slide(slide_id)
    if item["status"] != "ready":
        raise HTTPException(409, "切片尚未就绪")
    with lock:
        if slide_id not in viewers:
            try:
                slide = openslide.OpenSlide(item["path"])
                viewers[slide_id] = (slide, DeepZoomGenerator(slide, tile_size=254, overlap=1, limit_bounds=False))
            except (openslide.OpenSlideError, OSError) as exc:
                raise HTTPException(422, f"无法读取切片：{exc}") from exc
        return viewers[slide_id]


def public_slide(item: dict) -> dict:
    result = {k: v for k, v in item.items() if k != "path"}
    if item["status"] == "ready":
        slide, _ = viewer(item["id"])
        result["width"], result["height"] = slide.dimensions
        result["mpp"] = slide.properties.get("openslide.mpp-x")
    return result


@app.get("/api/slides")
def list_slides():
    return [public_slide(item) for item in slides.values()]


@app.get("/api/slides/{slide_id}")
def slide_info(slide_id: str):
    return public_slide(get_slide(slide_id))


@app.get("/api/slides/{slide_id}/image.dzi")
def dzi(slide_id: str):
    _, dz = viewer(slide_id)
    return Response(dz.get_dzi("jpeg"), media_type="application/xml")


@app.get("/api/slides/{slide_id}/image_files/{level}/{col}_{row}.jpeg")
def tile(slide_id: str, level: int, col: int, row: int):
    _, dz = viewer(slide_id)
    try:
        image = dz.get_tile(level, (col, row)).convert("RGB")
    except (ValueError, IndexError) as exc:
        raise HTTPException(404, "瓦片不存在") from exc
    output = io.BytesIO()
    image.save(output, "JPEG", quality=82)
    return Response(output.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})


def process_upload(slide_id: str):
    item = slides[slide_id]
    try:
        slide, _ = viewer_for_path(item["path"])
        item["width"], item["height"] = slide.dimensions
        slide.close()
        item["status"] = "ready"
    except Exception as exc:
        item["status"] = "failed"
        item["error"] = str(exc)
    storage.save("slide", item)


def viewer_for_path(path: str):
    slide = openslide.OpenSlide(path)
    return slide, DeepZoomGenerator(slide, tile_size=254, overlap=1)


@app.post("/api/slides/upload", status_code=202)
def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in EXTENSIONS:
        raise HTTPException(400, "仅支持 SVS/TIFF/NDPI/MRXS 格式")
    slide_id = uuid.uuid4().hex
    destination = UPLOADS / f"{slide_id}{suffix}"
    staging = UPLOADS / f"{slide_id}{suffix}.partial"
    size = 0
    try:
        with staging.open("wb") as output:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, f"文件超过 {MAX_UPLOAD / 1024**3:g} GiB 上限")
                if shutil.disk_usage(UPLOADS).free < len(chunk) + 256 * 1024 ** 2:
                    raise HTTPException(507, "磁盘空间不足，上传已停止")
                output.write(chunk)
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()
    try:
        staging.replace(destination)
    except OSError:
        staging.unlink(missing_ok=True)
        raise
    slides[slide_id] = {"id": slide_id, "name": Path(file.filename or "上传切片").name, "path": str(destination), "status": "processing", "demo": False}
    storage.save("slide", slides[slide_id])
    executor.submit(process_upload, slide_id)
    return public_slide(slides[slide_id])


def parse_content_range(value: str, total: int):
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", value or "")
    if not match:
        raise HTTPException(400, "需要 Content-Range: bytes start-end/total")
    start, end, declared = map(int, match.groups())
    if declared != total or end < start or end >= total or end - start + 1 > 8 * 1024 ** 2:
        raise HTTPException(400, "分片范围无效或超过 8 MiB")
    return start, end


class UploadStart(BaseModel):
    filename: str
    size: int = Field(gt=0)


@app.post("/api/uploads", status_code=201)
def start_upload(request: UploadStart):
    suffix = Path(request.filename).suffix.lower()
    if suffix not in EXTENSIONS:
        raise HTTPException(400, "仅支持 SVS/TIFF/NDPI/MRXS 格式")
    if request.size > MAX_UPLOAD:
        raise HTTPException(413, f"文件超过 {MAX_UPLOAD / 1024**3:g} GiB 上限")
    if len(request.filename) > 255:
        raise HTTPException(400, "文件名过长")
    if shutil.disk_usage(UPLOADS).free < request.size + 256 * 1024 ** 2:
        raise HTTPException(507, "磁盘空间不足")
    identity = uuid.uuid4().hex
    record = {"id": identity, "name": Path(request.filename).name, "size": request.size, "offset": 0, "suffix": suffix, "status": "uploading"}
    storage.save("upload", record)
    (UPLOADS / f"{identity}{suffix}.partial").touch(exist_ok=False)
    return {"id": identity, "offset": 0, "size": request.size}


def upload_record(upload_id: str):
    record = storage.get("upload", upload_id)
    if not record:
        raise HTTPException(404, "上传任务不存在")
    return record


@app.get("/api/uploads/{upload_id}")
def upload_status(upload_id: str):
    record = upload_record(upload_id)
    return {"id": record["id"], "offset": record["offset"], "size": record["size"], "status": record["status"]}


@app.put("/api/uploads/{upload_id}/chunk")
async def upload_chunk(upload_id: str, request: Request):
    record = upload_record(upload_id)
    if record["status"] != "uploading":
        raise HTTPException(409, "上传任务已结束")
    start, end = parse_content_range(request.headers.get("content-range", ""), record["size"])
    if start != record["offset"]:
        raise HTTPException(409, f"分片位置不匹配，当前偏移量 {record['offset']}")
    if shutil.disk_usage(UPLOADS).free < end - start + 1 + 256 * 1024 ** 2:
        raise HTTPException(507, "磁盘空间不足")
    path = UPLOADS / f"{upload_id}{record['suffix']}.partial"
    length = 0
    try:
        with path.open("r+b") as output:
            output.seek(start)
            async for chunk in request.stream():
                length += len(chunk)
                if length > end - start + 1:
                    raise HTTPException(400, "分片实际长度超过 Content-Range")
                output.write(chunk)
            if length != end - start + 1:
                raise HTTPException(400, "分片实际长度不足 Content-Range")
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        with path.open("r+b") as output:
            output.truncate(start)
        raise
    record["offset"] = end + 1
    storage.save("upload", record)
    return upload_status(upload_id)


@app.post("/api/uploads/{upload_id}/complete", status_code=202)
def complete_upload(upload_id: str):
    record = upload_record(upload_id)
    if record["status"] != "uploading" or record["offset"] != record["size"]:
        raise HTTPException(409, "文件尚未上传完成")
    source = UPLOADS / f"{upload_id}{record['suffix']}.partial"
    destination = UPLOADS / f"{upload_id}{record['suffix']}"
    source.replace(destination)
    item = {"id": upload_id, "name": record["name"], "path": str(destination), "status": "processing", "demo": False}
    slides[upload_id] = item
    storage.save("slide", item)
    record["status"] = "completed"
    storage.save("upload", record)
    executor.submit(process_upload, upload_id)
    return public_slide(item)


class StartRequest(BaseModel):
    slide_id: str
    goal: str = Field(min_length=2, max_length=300)
    analysis_id: str | None = None


# Coordinates below are fractions of the original level-0 image, NOT CSS pixels.
# This coarse box is a provisional demo ROI; it is not a validated histological mask.
ROI = [0.79, 0.34, 0.87, 0.53]
SOURCE = "OpenSlide test data: JP2K-33003-1.svs (aorta, free to use and distribute); teaching note is a local demo draft."


@app.post("/api/sessions")
def start(request: StartRequest):
    item = get_slide(request.slide_id)
    if item["status"] != "ready":
        raise HTTPException(409, "请等待切片处理完成")
    goal = request.goal.strip()
    if request.analysis_id:
        task = analysis_status(request.analysis_id)
        try:
            definition = resolve(goal)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if task["slide_id"] != request.slide_id or task["class_id"] != definition["id"]:
            raise HTTPException(422, "分析目标或切片不匹配")
        if task["status"] != "completed" or not task["candidates"]:
            raise HTTPException(409, "分析尚未完成或没有找到候选结构")
        eligible = [c for c in task["candidates"] if c["status"] != "conflict"]
        if not eligible:
            raise HTTPException(409, "候选类别与分类器结果冲突，无法用于导览")
        candidate = max(eligible, key=lambda c: c["similarity"])
        session_id = uuid.uuid4().hex
        target = {"name": definition["name"], "bbox": candidate["bbox"], "polygon": candidate["polygon"], "annotation": "模型暂定候选；相似度不等于正确概率", "source": str(task["models"]), "explanation": definition["explanation"], "confusion": "其他位置的同类别实例也可判对；低置信度或证据不足时不计为错误。"}
        session = {"id": session_id, "slide_id": request.slide_id, "class_id": definition["id"], "analysis_id": task["id"], "goal": goal, "started": time.time(), "phase": "teacher", "attempts": [], "bbox": candidate["bbox"], "target": target}
        sessions[session_id] = session
        storage.save("session", session)
        return {"id": session_id, "phase": "teacher", "goal": goal, "target": target}
    if not item["demo"] or "主动脉" not in goal or any(alias in goal for c in CLASSES for alias in c["aliases"]):
        raise HTTPException(422, "该学习目标需要先进行模型分析，请点击“分析切片”")
    slide, _ = viewer(request.slide_id)
    width, height = slide.dimensions
    bbox = [round(ROI[0] * width), round(ROI[1] * height), round(ROI[2] * width), round(ROI[3] * height)]
    session_id = uuid.uuid4().hex
    session = {"id": session_id, "slide_id": request.slide_id, "goal": goal, "started": time.time(), "phase": "teacher", "attempts": [], "bbox": bbox}
    sessions[session_id] = session
    storage.save("session", session)
    return {"id": session_id, "phase": "teacher", "goal": goal, "target": {"name": "主动脉切片组织区域", "bbox": bbox, "annotation": "演示用粗略框选 · 未经组织学专家复核", "source": SOURCE, "explanation": "这是一张公开的主动脉组织切片。先在低倍镜下识别染色组织与白色背景的边界，再放大观察血管壁的层次。此演示框仅用于练习定位组织区域，不能用于识别具体内膜、中膜或外膜。", "confusion": "浅色间隙不一定是背景；判断时结合周围是否有连续染色组织。"}}


def get_session(session_id: str) -> dict:
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "学习会话不存在")
    return session


@app.post("/api/sessions/{session_id}/exam")
def exam(session_id: str):
    session = get_session(session_id)
    if session["phase"] != "teacher":
        raise HTTPException(409, "当前不能进入自测")
    session["phase"] = "student"
    session["exam_started"] = time.time()
    storage.save("session", session)
    return {"phase": "student", "message": "请在刚才的组织区域内定位并右键点击；触屏或触控板也可使用“定位作答”按钮。"}


class AnswerRequest(BaseModel):
    x: float
    y: float


@app.post("/api/sessions/{session_id}/answers")
def answer(session_id: str, request: AnswerRequest):
    session = get_session(session_id)
    if session["phase"] != "student":
        raise HTTPException(409, "请先进入自测")
    slide, _ = viewer(session["slide_id"])
    width, height = slide.dimensions
    if not all(math.isfinite(value) for value in (request.x, request.y)) or not (0 <= request.x < width and 0 <= request.y < height):
        raise HTTPException(422, "点击位置不在切片范围内")
    x1, y1, x2, y2 = session["bbox"]
    correct = x1 <= request.x <= x2 and y1 <= request.y <= y2
    elapsed = round(time.time() - session["exam_started"], 1)
    result = {"correct": correct, "result": "correct" if correct else "incorrect", "x": request.x, "y": request.y, "elapsed": elapsed, "index": len(session["attempts"]) + 1, "reason": "命中演示组织区域；再放大检查局部层次。" if correct else "未命中演示框选区域；请回到组织主体寻找，注意不要把空白背景当成组织。"}
    if session.get("class_id"):
        result.update(analysis.adjudicate(session["slide_id"], session["class_id"], request.x, request.y))
        models = ai.model_status()
        can_resolve = session["class_id"] == "nuclei" and (models["instanseg"]["configured"] or models["cellpose"]["configured"])
        if result["result"] == "uncertain" and can_resolve:
            task = analysis.create(get_slide(session["slide_id"]), next(c for c in CLASSES if c["id"] == session["class_id"]), [request.x, request.y])
            result.update(result="pending", task_id=task["id"], reason="正在分析点击附近的局部区域")
    with lock:
        result["index"] = len(session["attempts"]) + 1
        session["attempts"].append(result)
        storage.save("session", session)
    return result


@app.get("/api/sessions/{session_id}/answers")
def history(session_id: str):
    session = get_session(session_id)
    with lock:
        for result in session["attempts"]:
            if result.get("result") != "pending":
                continue
            task = analysis_status(result["task_id"])
            if task["status"] == "completed":
                result.update(analysis.adjudicate(session["slide_id"], session["class_id"], result["x"], result["y"]))
            elif task["status"] in ("failed", "interrupted"):
                result.update(result="uncertain", correct=None, reason=task.get("error", "分析未完成"))
        storage.save("session", session)
    return session["attempts"]


@app.post("/api/sessions/{session_id}/review")
def review(session_id: str):
    session = get_session(session_id)
    if session["phase"] != "student" or not session["attempts"]:
        raise HTTPException(409, "请至少完成一次自测")
    history(session_id)
    if any(a.get("result") == "pending" for a in session["attempts"]):
        raise HTTPException(409, "请等待局部分析完成再查看报告")
    session["phase"] = "review"
    storage.save("session", session)
    attempts = session["attempts"]
    correct = sum(item["correct"] is True for item in attempts)
    scored = sum(item["correct"] is not None for item in attempts)
    return {"phase": "review", "goal": session["goal"], "count": len(attempts), "scored": scored, "uncertain": len(attempts)-scored, "correct": correct, "accuracy": round(correct / scored * 100) if scored else None, "elapsed": round(time.time() - session["exam_started"], 1), "bbox": session["bbox"], "note": "命中率仅统计有明确正误的作答；模型候选和暂定结果仍需复核。", "reinforcement": "结合组织背景与局部形态复习，比较易混淆结构；无法判定的点击不计为错误。"}


DIST = ROOT.parent / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(DIST / "index.html")
