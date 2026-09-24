"""评分与汤色比色卡档位规则。

规则区间可在比色专页调整（仅审评员）。交评时按“当时规则表”校验档位，
台账落库后冻结，事后改规则只影响新交评，不回写旧台账。
"""

# tier 键名 → 中文名（展示顺序即区间从浅到深）
SHADE_TIERS = [
    ("light", "浅档"),
    ("medium", "中档"),
    ("dark", "深档"),
]
SHADE_LABELS = dict(SHADE_TIERS)
SHADE_ORDER = {tier: i for i, (tier, _) in enumerate(SHADE_TIERS)}

# 默认区间：汤色 < 5 为浅档，5 ≤ 汤色 ≤ 7.5 为中档，汤色 > 7.5 为深档。
# upper 为该档上界，None 表示无上界（最深档）；upper_inclusive 决定上界开闭。
DEFAULT_RULES = {
    "light": {"upper": 5.0, "upper_inclusive": False},
    "medium": {"upper": 7.5, "upper_inclusive": True},
    "dark": {"upper": None, "upper_inclusive": None},
}


def weigh(aroma: float, taste: float, liquor: float) -> tuple[str, str, float]:
    score = round(aroma * 0.3 + taste * 0.5 + liquor * 0.2, 2)
    if score >= 7:
        return "通过", "加权分达到放行线", score
    return "不通过", "加权分低于放行线", score


def expected_shade(liquor: float, rules) -> str:
    """按规则表（dict 或行序列）计算汤色应属档位。"""
    ordered = sorted(_as_rows(rules), key=lambda r: SHADE_ORDER[r["tier"]])
    for row in ordered:
        upper = row["upper"]
        if upper is None:
            return row["tier"]
        if row["upper_inclusive"]:
            if liquor <= upper:
                return row["tier"]
        elif liquor < upper:
            return row["tier"]
    return ordered[-1]["tier"]


def validate_shade(liquor: float, shade: str, rules) -> tuple[bool, str]:
    """交评勾选档位是否与汤色分相符。返回 (是否相符, 应选档位)。"""
    if shade not in SHADE_ORDER:
        return False, expected_shade(liquor, rules)
    expected = expected_shade(liquor, rules)
    return shade == expected, expected


def _as_rows(rules):
    if isinstance(rules, dict):
        return [
            {"tier": tier, "upper": cfg["upper"], "upper_inclusive": cfg["upper_inclusive"]}
            for tier, cfg in rules.items()
        ]
    return list(rules)


def _fmt(x) -> str:
    return f"{x:g}"


def band_text(tier: str, rules) -> str:
    """档位区间的中文文案，如 '5 ≤ 汤色 ≤ 7.5'。"""
    ordered = sorted(_as_rows(rules), key=lambda r: SHADE_ORDER[r["tier"]])
    by_tier = {r["tier"]: r for r in ordered}
    idx = SHADE_ORDER[tier]
    cur = by_tier[tier]
    upper_clause = (
        None
        if cur["upper"] is None
        else f"汤色 {'≤' if cur['upper_inclusive'] else '<'} {_fmt(cur['upper'])}"
    )
    if idx == 0:
        return upper_clause
    prev = ordered[idx - 1]
    lower_clause = f"{_fmt(prev['upper'])} {'≤' if not prev['upper_inclusive'] else '<'} 汤色"
    if upper_clause is None:
        return lower_clause
    return f"{lower_clause} 且 {upper_clause}"

