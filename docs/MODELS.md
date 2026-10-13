# 模型配置与能力边界

模型权重**不放进 Git**，也不使用用户的 Hugging Face 凭证自动登录。当前环境只有基础 Web 依赖，**尚未安装 PyTorch/CONCH/InstanSeg/MedSAM，也尚未下载任何模型权重**；`/api/config` 会报告本地模型是否配置。模型未配置时演示主动脉区域仍可用，其他目标的分析请求会返回清晰的缺失原因。以下地址供手动下载；拿到权重后仍需在目标机器上验证显存、版本和输出质量。

## 安装（独立 GPU 环境，Python 3.10–3.12）

先按 [PyTorch 官方安装页](https://pytorch.org/get-started/locally/) 安装兼容当前 GPU 的 CUDA 版 PyTorch，再安装 `backend/requirements-ai.txt`。CONCH 按[官方仓库](https://github.com/mahmoodlab/CONCH)安装；MedSAM 按[官方仓库](https://github.com/bowang-lab/MedSAM)安装其 `segment_anything` 模块。申请 [CONCH 权重](https://huggingface.co/MahmoodLab/conch)及 **原版 UNI** [权重](https://huggingface.co/MahmoodLab/uni)（当前适配器不是 UNI2-h），阅读并接受其非商业学术研究条款。凭证不写入代码或环境示例。模型与权重在本地离线加载，不在请求处理中下载。

手动下载清单：

| 模型 | 地址与文件 | 本地设置 |
|---|---|---|
| CONCH | https://huggingface.co/MahmoodLab/conch 的 `pytorch_model.bin`（需本人获准） | `CONCH_CHECKPOINT` |
| UNI | https://huggingface.co/MahmoodLab/uni 的 `pytorch_model.bin`（需本人获准） | `UNI_CHECKPOINT`；**还需要自训 `UNI_HEAD`** |
| MedSAM | https://github.com/bowang-lab/MedSAM 的 README 所列 checkpoint（vit_b，Google Drive 链接） | `MEDSAM_CHECKPOINT` |
| InstanSeg | https://github.com/instanseg/instanseg 的 `brightfield_nuclei` 模型，使用该项目 README 和模型索引给出的发布地址，解压后找到 `instanseg.pt` | `INSTANSEG_MODEL` 指向文件的父目录 |
| Cellpose | https://github.com/MouseLand/cellpose 的本地细胞核模型 checkpoint | `CELLPOSE_CHECKPOINT` |

使用 `.venv` 环境时，需要将相应源码依赖和模型依赖安装到**同一个** `.venv`；单有模型文件无法运行。GPU 版 PyTorch 的安装命令请依照 PyTorch 页面针对系统和驱动生成，勿安装 CPU 版后假定 GPU 已启用。下载完成后运行 `.venv/bin/python -c "import torch; print(torch.cuda.is_available())"` 核对。

本机试装 PyTorch 时，在下载约 555 MB 的 `torch` 主包阶段超时，未安装成功。若你的网络也有限速，请在浏览器/下载工具取得与你的 Python 版本、Linux 平台及 CUDA 对应的 wheel，再用 `.venv/bin/pip install /绝对路径/torch文件名.whl` 安装；还需安装其 CUDA 运行库与其余依赖，最稳妥的是网络条件允许时按 PyTorch 官方生成的完整 pip 命令安装。安装完执行 `.venv/bin/pip install -r backend/requirements-ai.txt`，再安装 CONCH 和 MedSAM 源码依赖。不能只拷贝 `.bin/.pth` 权重就运行。

启动前设置（路径仅供示例）：

```bash
export CONCH_CHECKPOINT=/absolute/path/to/conch/pytorch_model.bin
export UNI_CHECKPOINT=/absolute/path/to/uni/pytorch_model.bin
export UNI_HEAD=/absolute/path/to/your/trained-uni-head.json
export MEDSAM_CHECKPOINT=/absolute/path/to/medsam_vit_b.pth
export INSTANSEG_MODEL=/absolute/path/to/local/instanseg/model-directory-with-instanseg.pt
export CELLPOSE_CHECKPOINT=/absolute/path/to/local/cellpose/model
export MODEL_DEVICE=cuda
export ANALYSIS_MAX_PATCHES=96
```

`UNI_HEAD` **不是官方预训练权重的一部分**；需要自行用标注结构切块训练并验证。其 JSON 含 `classes`（与 `backend/app/catalog.py` 中 ID 一致）、`weight`（类别×特征维度）、`bias`、`threshold`（基于验证集校准）及 `validated` 布尔值。**没有训练头不会将 CONCH 相似度转换为正确率。**模型权重与代码版本若不匹配，任务会失败并保留错误状态，不会回退成假预测。

上述组件 API 会随上游版本变化；当前推理适配器仅经过代码审查与替身模型接口测试，尚未在实际权重上做过端到端 GPU 验证。建议收到权重后，先从一张小切片和 `ANALYSIS_MAX_PATCHES=4` 开始联调，再逐步提高抽样数量。8 GiB 显存可能容不下某些 UNI 权重与其他模型同时运行；任务顺序加载以降低峰值占用，最终仍须实测。

## 当前流水线

CONCH 对组织切块与英文结构描述进行相似度排序（含负例），返回多位置候选；UNI 分类头若已配置对候选进行类别复核；InstanSeg（或 Cellpose）给**细胞核**生成实例 Mask；MedSAM 使用候选框提示生成**暂定轮廓**。分块在原始像素尺度读取并保存 level-0 坐标和局部 Mask。默认抽样不覆盖全切片，单 GPU 串行任务；重启中断的任务可重新提交。

**判题边界：**细胞核点击可由已生成的实例 Mask 判对，点击切片另一个同类实例也判对。CONCH、UNI 的块级分类及 MedSAM 的整块提示轮廓**不能证明**点击到了纹状缘、弹性纤维或特定上皮边界，因此这些结构的结果目前只能用于暂定导览，点击返回“无法判定”，不被误判为错误。要将结构类别真正用于正误判题，仍须专家标注的结构级 Mask、已验证的结构定位/分割头、不同染色条件的逐类测评和阈值校准。模型能找到候选并不意味着已达到这些要求。

任务状态及候选接口：`POST /api/slides/{slide_id}/analyses`、`GET /api/analyses/{id}`、`GET /api/analyses/{id}/candidates`。分片上传：`POST /api/uploads` 建立任务，`GET /api/uploads/{id}` 查询偏移，`PUT /api/uploads/{id}/chunk` 写入分片（Content-Range），`POST /api/uploads/{id}/complete` 完成。请勿将零样本相似度解释为概率或准确率。
