"""Optional local model adapters. No downloads/authentication during API calls."""
import importlib.util
import json
import os
from functools import lru_cache
from pathlib import Path


def configured_path(name):
    value = os.environ.get(name, "")
    return Path(value) if value else None


def model_status():
    result = {}
    for name, module, env in [("conch", "conch", "CONCH_CHECKPOINT"), ("uni", "timm", "UNI_CHECKPOINT"), ("medsam", "segment_anything", "MEDSAM_CHECKPOINT")]:
        path = configured_path(env)
        try:
            installed = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            installed = False
        result[name] = {"configured": bool(installed and path and path.is_file()), "message": "本地权重已配置，推理时检查兼容性" if installed and path and path.is_file() else f"需要安装 {module} 并配置 {env}", "loaded": False}
    for name, module, env in [("instanseg", "instanseg", "INSTANSEG_MODEL"), ("cellpose", "cellpose", "CELLPOSE_CHECKPOINT")]:
        path = configured_path(env)
        available = bool(path and (path / "instanseg.pt").is_file()) if name == "instanseg" else bool(path and path.is_file())
        result[name] = {"configured": bool(importlib.util.find_spec(module) and available), "message": f"需安装 {module} 并配置本地 {env}，不自动下载", "loaded": False}
    head = configured_path("UNI_HEAD")
    result["uni"]["classifier_configured"] = bool(head and head.is_file())
    return result


def device():
    import torch
    requested = os.environ.get("MODEL_DEVICE", "cuda")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA 不可用；请安装适配 GPU 的 PyTorch，或设置 MODEL_DEVICE=cpu")
    return requested


class Conch:
    def __init__(self):
        from conch.open_clip_custom import create_model_from_pretrained, get_tokenizer
        path = configured_path("CONCH_CHECKPOINT")
        if not path or not path.is_file():
            raise RuntimeError("尚未配置 CONCH_CHECKPOINT：请申请 CONCH 权重并设置本地路径")
        self.device = device()
        self.model, self.transform = create_model_from_pretrained("conch_ViT-B-16", checkpoint_path=str(path))
        self.model = self.model.to(self.device).eval()
        self.tokenizer = get_tokenizer()

    def score(self, image, prompts):
        import torch
        from conch.open_clip_custom import tokenize
        with torch.inference_mode():
            images = self.transform(image).unsqueeze(0).to(self.device)
            text = tokenize(self.tokenizer, prompts).to(self.device)
            visual = self.model.encode_image(images, proj_contrast=True, normalize=True)
            linguistic = self.model.encode_text(text, normalize=True)
            scores = (visual @ linguistic.T)[0].float().cpu().tolist()
        return scores


class UniClassifier:
    """Frozen original UNI ViT-L + calibrated linear head exported as JSON."""
    def __init__(self):
        import timm
        import torch
        from timm.data import resolve_data_config, create_transform
        self.device = device()
        path = configured_path("UNI_CHECKPOINT")
        head = configured_path("UNI_HEAD")
        if not path or not head:
            raise RuntimeError("UNI 分类需要 UNI_CHECKPOINT 和已训练的 UNI_HEAD")
        self.model = timm.create_model("vit_large_patch16_224", img_size=224, init_values=1e-5, num_classes=0, dynamic_img_size=True)
        self.model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True), strict=True)
        self.model.to(self.device).eval()
        self.transform = create_transform(**resolve_data_config(self.model.pretrained_cfg, model=self.model))
        config = json.loads(head.read_text())
        self.classes = config["classes"]
        self.threshold = config["threshold"]
        self.validated = config.get("validated", False)
        self.weight = torch.tensor(config["weight"], dtype=torch.float32, device=self.device)
        self.bias = torch.tensor(config["bias"], dtype=torch.float32, device=self.device)

    def classify(self, image):
        import torch
        with torch.inference_mode():
            features = self.model(self.transform(image).unsqueeze(0).to(self.device)).float()
            probs = torch.softmax(features @ self.weight.T + self.bias, dim=-1)[0]
            index = int(probs.argmax())
            score = float(probs[index])
        return {"class_id": self.classes[index], "confidence": score, "accepted": score >= self.threshold, "validated": self.validated}


@lru_cache(maxsize=3)
def segment_model(kind):
    if kind == "instanseg":
        from instanseg import InstanSeg
        path = configured_path("INSTANSEG_MODEL")
        import torch
        # Load exactly the specified TorchScript file, without invoking the downloader.
        model = torch.jit.load(str(path / "instanseg.pt"), map_location="cpu")
        return InstanSeg(model, device=device(), verbosity=0)
    if kind == "cellpose":
        from cellpose import models
        return models.CellposeModel(gpu=device() == "cuda", pretrained_model=str(configured_path("CELLPOSE_CHECKPOINT")))
    from segment_anything import sam_model_registry
    return sam_model_registry["vit_b"](checkpoint=str(configured_path("MEDSAM_CHECKPOINT"))).to(device()).eval()


def segment(image, pixel_size, kind="medsam"):
    """Return local label mask. Nuclear adapters deliberately only handle nuclei."""
    import numpy as np
    import torch
    image_array = np.asarray(image)
    if kind == "instanseg":
        model = segment_model(kind)
        labels = model.eval_small_image(image_array, pixel_size=pixel_size, target="nuclei", return_image_tensor=False)
        return labels.squeeze().cpu().numpy().astype("int32")
    if kind == "cellpose":
        model = segment_model(kind)
        labels, *_ = model.eval(image_array)
        return np.asarray(labels, dtype="int32")
    import torch.nn.functional as F
    model = segment_model(kind)
    w, h = image.size
    normalized = np.asarray(image.resize((1024, 1024)), dtype="float32").transpose(2, 0, 1)
    tensor = torch.as_tensor(normalized[None], device=device())
    tensor = model.preprocess(tensor)
    with torch.inference_mode():
        embedding = model.image_encoder(tensor)
        box = torch.tensor([[[0., 0., 1024., 1024.]]], device=device())
        sparse, dense = model.prompt_encoder(points=None, boxes=box, masks=None)
        logits, _ = model.mask_decoder(image_embeddings=embedding, image_pe=model.prompt_encoder.get_dense_pe(), sparse_prompt_embeddings=sparse, dense_prompt_embeddings=dense, multimask_output=False)
        mask = F.interpolate(logits.sigmoid(), size=(h, w), mode="bilinear", align_corners=False)
    return (mask[0, 0].cpu().numpy() > .5).astype("int32")
