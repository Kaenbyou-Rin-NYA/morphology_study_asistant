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

若出现 `address already in use`，说明 8000 端口已有服务：运行 `ss -ltnp '( sport = :8000 )'` 查明进程，关闭旧服务后重试；也可以把启动命令的 `--port 8000` 改成其它空闲端口（若使用 Vite 开发服务器，同时更新 `frontend/vite.config.ts` 的代理端口）。

## 演示切片

项目中已下载 [OpenSlide 测试库的主动脉切片 JP2K-33003-1.svs](https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/JP2K-33003-1.svs) 到 `backend/data/slides/JP2K-33003-1.svs`（约 61 MiB）；文件不纳入版本控制。若缺失，运行：

```bash
curl -L --fail -o backend/data/slides/JP2K-33003-1.svs https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/JP2K-33003-1.svs
```

来源目录为 https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/ ，目录对该文件的授权说明为 **Free to use and distribute, with or without modification**。目标默认填写“认识主动脉组织区域”。导览框仅是未经专家复核的粗略定位示范，不构成主动脉壁各层标注；判题为落在框内的坐标测试，不是组织学识别。其它已收录在词表中的目标需先安装并配置相应模型，再点击“分析切片”。

可上传 SVS/TIFF/NDPI/MRXS 格式切片，默认上限 **10 GiB**，可通过 `MAX_UPLOAD_GIB` 调整；页面按 4 MiB 分片上传并显示进度。断线后重新选择同一文件可根据服务器偏移量续传；保持至少文件大小加 256 MiB 的磁盘可用空间。传统 `/api/slides/upload` 兼容接口仍为 multipart，会整体缓冲请求且不支持续传，建议大文件使用页面的分片上传。部分 MRXS 依赖同名伴随目录，单文件上传可能无法解析。上传清单、任务与会话由 SQLite 保存；上传文件保留在本地。进程重启中断的分析任务需重新提交。

已添加可选的本地模型分析流水线（CONCH 候选召回、UNI 分类头、InstanSeg/Cellpose 细胞核分割、MedSAM 候选轮廓），配置与能力边界见 [模型说明](docs/MODELS.md)。没有本地权重时，仍可使用主动脉粗框演示；上传切片可以浏览，分析任务会显示缺少权重的原因。**其他结构目前仅可显示暂定候选，不能做可靠的点击正误评分。**核实例分割配置并成功推理后，点击任一同类核实例可判对；不确定的点击不计入命中率。自测在切片上以绿/红/灰标记历史点击位置。当前没有 RAG。

## 验证

测试需要 `pytest`、`httpx`（供 `fastapi.testclient` 使用）与 `numpy`，不在运行时依赖内，需另行安装：

```bash
.venv/bin/pip install pytest httpx numpy
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
cd frontend && npm run build
```

若使用 `--target backend/.deps` 安装，测试命令改为 `PYTHONPATH=backend/.deps:backend python3 -m pytest backend/tests -q`（同样需先安装测试依赖）。演示流程相关测试要求 `backend/data/slides/JP2K-33003-1.svs` 已就位。

阶段性下一步：取得带专家标注的组织学 WSI，建立标注版本及教学引用，评估 GPU 上的候选检索、实例分割与逐类结构识别精度。上传和分析任务目前采用本地线程及 SQLite，正式多进程/多机器部署需换成可恢复任务队列。
