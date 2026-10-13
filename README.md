# 形态学学习台 · 初版

Web 端 WSI 学习流程演示：选片 → 导览组织区域 → 隐藏答案自测 → 查看复习报告。使用 OpenSlide 按需提供 Deep Zoom 瓦片，切片不会整张传到浏览器。

## 运行

环境：Python 3.10+、Node.js 18+。安装后端依赖（推荐虚拟环境；本机若缺少 `python3-venv`，可以安装 `python3-venv`，或用 `pip install --target backend/.deps -r backend/requirements.txt`）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

如使用 `--target backend/.deps` 安装，启动命令改为 `PYTHONPATH=backend/.deps:backend python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000`。访问 http://127.0.0.1:8000 。开发前端可另启 `cd frontend && npm run dev`，访问 http://localhost:5173 （Vite 代理 `/api` 至 8000）。

## 演示切片

项目中已下载 [OpenSlide 测试库的主动脉切片 JP2K-33003-1.svs](https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/JP2K-33003-1.svs) 到 `backend/data/slides/JP2K-33003-1.svs`（约 61 MiB）；文件不纳入版本控制。若缺失，运行：

```bash
curl -L --fail -o backend/data/slides/JP2K-33003-1.svs https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/JP2K-33003-1.svs
```

来源目录为 https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/ ，目录对该文件的授权说明为 **Free to use and distribute, with or without modification**。目标默认填写“认识主动脉组织区域”。导览框仅是未经专家复核的粗略定位示范，不构成主动脉壁各层标注；判题为落在框内的坐标测试，不是组织学识别。其它学习目标会明确返回不支持。

可上传 SVS/TIFF/NDPI/MRXS 格式切片（上限 1 GiB）；目前上传后仅可浏览。部分 MRXS 依赖同名伴随目录，单文件上传可能无法解析。上传文件、会话清单仅在本机/进程存放，重启后会话丢失。当前没有真正的模型定位、RAG 或 GPU 推理。

## 验证

```bash
PYTHONPATH=backend/.deps:backend python3 -m pytest backend/tests -q
cd frontend && npm run build
```

阶段性下一步：取得带专家标注的组织学 WSI，建立标注版本及教学引用，再评估 GPU 上的候选检索与局部检测精度，最后接入异步推理任务与持久化。
