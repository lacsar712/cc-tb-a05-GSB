def weigh(aroma: float, taste: float, liquor: float) -> tuple[str, str, float]:
    score = round(aroma * 0.3 + taste * 0.5 + liquor * 0.2, 2)
    if score >= 7:
        return "通过", "加权分达到放行线", score
    return "不通过", "加权分低于放行线", score


# 汤色比色卡档位。规则表（color_rules）按 max_score 升序判定：
# 命中第一档即定档。默认：汤色分 < 5 浅档；5 ≤ 分 ≤ 7.5 中档；分 > 7.5 深档。
GRADE_ORDER = ("浅", "中", "深")
GRADE_LABELS = {"浅": "浅档", "中": "中档", "深": "深档"}
GRADE_SWATCHES = {"浅": "#eac67c", "中": "#c08a3e", "深": "#7c3f1d"}

DEFAULT_RULES = [
    {"grade": "浅", "max_score": 5.0, "max_inclusive": False},
    {"grade": "中", "max_score": 7.5, "max_inclusive": True},
    {"grade": "深", "max_score": 10.0, "max_inclusive": True},
]


def expected_grade(liquor: float, rules: list[dict]) -> str:
    """按规则表判定汤色分应勾的档位。rules 须按 max_score 升序。"""
    for rule in rules:
        hi = rule["max_score"]
        if liquor < hi or (rule["max_inclusive"] and liquor == hi):
            return rule["grade"]
    return rules[-1]["grade"]


def rule_ranges(rules: list[dict]) -> list[str]:
    """给模板用的每档区间文字，如 ['汤色分 < 5', '5 ≤ 汤色分 ≤ 7.5', '7.5 < 汤色分 ≤ 10']。"""
    texts = []
    prev = None
    for rule in rules:
        hi, hi_inc = rule["max_score"], rule["max_inclusive"]
        upper = f"≤ {hi:g}" if hi_inc else f"< {hi:g}"
        if prev is None:
            texts.append(f"汤色分 {upper}")
        else:
            lo, lo_inc = prev
            lower = f"{lo:g} ≤" if lo_inc else f"{lo:g} <"
            texts.append(f"{lower} 汤色分 {upper}")
        # 下一档的下限 = 本档上限的补集：不含上限则含，含则不含
        prev = (hi, not hi_inc)
    return texts
