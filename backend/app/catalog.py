"""Teaching terms and English retrieval prompts. These are NOT trained classes."""
CLASSES = [
    {"id": "columnar", "name": "单层柱状上皮", "aliases": ["单层柱状上皮"], "prompt": "histology of simple columnar epithelium with a single layer of tall cells and basal nuclei", "scales": [256, 512], "explanation": "观察单层排列、细胞高度与宽度的关系以及核的位置。判断需要上皮游离面和邻近组织背景，不能仅凭一个细胞核。"},
    {"id": "cuboidal", "name": "单层立方上皮", "aliases": ["单层立方上皮"], "prompt": "histology of simple cuboidal epithelium with a single layer of cube shaped cells and round central nuclei", "scales": [256, 512], "explanation": "观察单层排列、近似相等的细胞高宽和居中的圆核，结合管腔方向与切面判断。"},
    {"id": "squamous", "name": "单层扁平上皮", "aliases": ["单层扁平上皮"], "prompt": "histology of simple squamous epithelium with a single layer of thin flat cells", "scales": [256, 512], "explanation": "观察薄而扁平的细胞层及沿表面排列的细胞核，结合切面避免与结缔组织混淆。"},
    {"id": "stratified", "name": "复层鳞状上皮", "aliases": ["复层鳞状上皮"], "prompt": "histology of stratified squamous epithelium with multiple layers and flattened surface cells", "scales": [512, 1024], "explanation": "从基底到表面观察多层细胞及表层扁平形态。"},
    {"id": "brush_border", "name": "纹状缘", "aliases": ["纹状缘", "刷状缘"], "prompt": "high magnification histology showing the apical brush border of intestinal epithelial cells", "scales": [128, 256], "explanation": "纹状缘位于上皮游离面，需高倍率观察窄带边界；低倍切块相似度不能证明具体点击处存在纹状缘。"},
    {"id": "elastic", "name": "弹性纤维", "aliases": ["弹性纤维", "弹力纤维"], "prompt": "histology showing branching elastic fibers with an elastic tissue stain", "scales": [256, 512], "explanation": "弹性纤维的辨认与染色相关，观察细丝、分支及组织背景；普通 H&E 中未必能可靠区分。"},
    {"id": "smooth_muscle", "name": "平滑肌", "aliases": ["平滑肌"], "prompt": "histology of smooth muscle bundles with elongated spindle shaped cells", "scales": [256, 512], "explanation": "结合束状排列、梭形细胞和长核观察，注意与纤维性结缔组织比较。"},
    {"id": "nuclei", "name": "细胞核", "aliases": ["细胞核"], "prompt": "histology showing cell nuclei", "scales": [128, 256], "explanation": "本目标仅寻找细胞核，不推断细胞亚型；实例轮廓由核分割模型产生。"},
]
NEGATIVES = ["histology of collagenous connective tissue", "histology of adipose tissue", "histology of blood and red blood cells", "blank background without tissue", "histology of damaged tissue and staining artifact"]


def resolve(goal):
    matches = [item for item in CLASSES if any(alias in goal for alias in item["aliases"])]
    if len(matches) != 1:
        raise ValueError("请每次指定一个目标结构。当前词表：" + "、".join(item["name"] for item in CLASSES))
    return matches[0]
