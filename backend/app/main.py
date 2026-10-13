"""WSI tile service and a small, explicitly annotated learning workflow."""
from __future__ import annotations

import io
import math
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import openslide
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from openslide.deepzoom import DeepZoomGenerator
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
SLIDES = ROOT / "data" / "slides"
UPLOADS = ROOT / "data" / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)
DEMO_ID = "aorta-demo"
DEMO_FILE = SLIDES / "JP2K-33003-1.svs"
MAX_UPLOAD = 1024 * 1024 * 1024
EXTENSIONS = {".svs", ".tif", ".tiff", ".ndpi", ".mrxs"}
executor = ThreadPoolExecutor(max_workers=2)
lock = threading.RLock()
slides: dict[str, dict] = {}
sessions: dict[str, dict] = {}
viewers: dict[str, tuple[openslide.OpenSlide, DeepZoomGenerator]] = {}

app = FastAPI(title="形态学学习助手 · 初版")


def register_demo():
    if DEMO_FILE.exists():
        slides[DEMO_ID] = {"id": DEMO_ID, "name": "主动脉组织切片 · 演示", "path": str(DEMO_FILE), "status": "ready", "demo": True}


register_demo()


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


def viewer_for_path(path: str):
    slide = openslide.OpenSlide(path)
    return slide, DeepZoomGenerator(slide, tile_size=254, overlap=1)


@app.post("/api/slides/upload", status_code=202)
async def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in EXTENSIONS:
        raise HTTPException(400, "仅支持 SVS/TIFF/NDPI/MRXS 格式")
    slide_id = uuid.uuid4().hex
    destination = UPLOADS / f"{slide_id}{suffix}"
    size = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "文件超过 1 GiB 上限")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    slides[slide_id] = {"id": slide_id, "name": Path(file.filename or "上传切片").name, "path": str(destination), "status": "processing", "demo": False}
    executor.submit(process_upload, slide_id)
    return public_slide(slides[slide_id])


class StartRequest(BaseModel):
    slide_id: str
    goal: str = Field(min_length=2, max_length=300)


# Coordinates below are fractions of the original level-0 image, NOT CSS pixels.
# This coarse box is a provisional demo ROI; it is not a validated histological mask.
ROI = [0.79, 0.34, 0.87, 0.53]
SOURCE = "OpenSlide test data: JP2K-33003-1.svs (aorta, free to use and distribute); teaching note is a local demo draft."


@app.post("/api/sessions")
def start(request: StartRequest):
    item = get_slide(request.slide_id)
    if item["status"] != "ready":
        raise HTTPException(409, "请等待切片处理完成")
    if not item["demo"]:
        raise HTTPException(422, "上传切片目前仅支持浏览；自动定位和自测需要先建立经过复核的标注")
    goal = request.goal.strip()
    if "主动脉" not in goal:
        raise HTTPException(422, "演示切片目前仅支持主动脉组织区域的学习目标，请尝试“认识主动脉组织区域”")
    slide, _ = viewer(request.slide_id)
    width, height = slide.dimensions
    bbox = [round(ROI[0] * width), round(ROI[1] * height), round(ROI[2] * width), round(ROI[3] * height)]
    session_id = uuid.uuid4().hex
    session = {"id": session_id, "slide_id": request.slide_id, "goal": goal, "started": time.monotonic(), "phase": "teacher", "attempts": [], "bbox": bbox}
    sessions[session_id] = session
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
    session["exam_started"] = time.monotonic()
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
    elapsed = round(time.monotonic() - session["exam_started"], 1)
    result = {"correct": correct, "elapsed": elapsed, "index": len(session["attempts"]) + 1, "reason": "命中演示组织区域；再放大检查局部层次。" if correct else "未命中演示框选区域；请回到组织主体寻找，注意不要把空白背景当成组织。"}
    session["attempts"].append(result)
    return result


@app.post("/api/sessions/{session_id}/review")
def review(session_id: str):
    session = get_session(session_id)
    if session["phase"] != "student" or not session["attempts"]:
        raise HTTPException(409, "请至少完成一次自测")
    session["phase"] = "review"
    attempts = session["attempts"]
    correct = sum(item["correct"] for item in attempts)
    return {"phase": "review", "goal": session["goal"], "count": len(attempts), "correct": correct, "accuracy": round(correct / len(attempts) * 100), "elapsed": round(time.monotonic() - session["exam_started"], 1), "bbox": session["bbox"], "note": "本次只评估粗粒度组织区域定位，不能代表对主动脉壁各层结构的识别能力。", "reinforcement": "复习时从低倍观察染色组织与背景边界，再放大寻找连续组织。若点击犹豫，先确认组织主体的位置，再核对局部形态。"}


DIST = ROOT.parent / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(DIST / "index.html")
