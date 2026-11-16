"""行业分类关键词与判定（唯一来源，issue #149）。

此前同一组关键词有 3 份：`pr_calculator.py`（周期/科技/成长）、
`sustainability_calculator.py`（周期/防御/金融）、`site/js/calculator.js`（镜像）。
副本已经漂移过一次：JS 用 `'电力'` 做防御性关键词，Python 用 `'电力行业'`
（#42 L3 的专名规则）——东财标签「电力设备-电源设备-太阳能」在 JS 侧被判成
防御性行业，于是同一只股票双端结论不同。

关键词匹配一律是**子串匹配**（东财标签是「公用事业-电力-水电」这类复合名），
所以词条要取「能唯一指认该行业的最短专名」，不要取泛词：
- `'电力行业'` 而不是 `'电力'`：否则 `'电力设备'` 被误判防御
- `'化工'` 保留：东财 `'基础化工-化学制品'` 需要它；`'化学制药'` 由医药侧处理

新增关键词只需改这里；双端一致性由 scripts/verify_js_vs_python.py 的
policy parity 校验兜底（JS 镜像在 site/js/calculator.js 顶部）。
"""
from typing import Tuple

CYCLICAL_INDUSTRIES = frozenset({
    "煤炭", "钢铁", "有色金属", "石油", "化工", "航运", "建材",
    "水泥", "玻璃", "造纸", "养殖", "房地产", "工程机械", "船舶",
    "化肥", "农药", "化纤", "橡胶", "塑料",
    "证券", "券商", "保险",
})

TECH_INDUSTRIES = frozenset({
    "半导体", "软件", "互联网", "计算机", "通信", "电子",
    "芯片", "人工智能", "云计算", "大数据",
})

# 成长行业：高利润增速 + 高再投资（低分红）。修正市赚率对成长股不适用——
# 高成长需留存利润，分红率低导致 N 因子被压到 2.0、修正 PR 失真（丁宁原版定义）。
# 与 TECH 去重（半导体/芯片/人工智能等归科技）。
GROWTH_INDUSTRIES = frozenset({
    "新能源", "光伏", "太阳能", "锂电", "储能", "风电", "氢能", "新能源汽车",
    "军工", "国防", "机器人", "工业机器人", "智能驾驶", "卫星导航",
    "生物医药", "创新药", "CXO", "医疗器械", "医美",
    "新材料", "碳纤维", "复合材料",
    "算力", "数据中心", "大模型",
})

# 防御性行业（现金流稳定，高分红可持续性更强）
DEFENSIVE_INDUSTRIES = frozenset({
    "公用事业", "电力行业", "水务", "燃气", "高速公路", "铁路", "港口", "机场",
    "食品饮料", "白酒", "乳品", "家电", "医药", "超市", "运营商", "电信",
})

FINANCE_INDUSTRIES = frozenset({"银行", "保险"})


def _matches(industry: str, keywords) -> bool:
    return any(kw in industry for kw in keywords)


def classify_pr_industry(industry: str) -> Tuple[bool, bool, bool]:
    """市赚率页面用：(is_cyclical, is_tech, is_growth)。

    优先级由调用方决定（页面按 周期 > 科技 > 成长 报最优先的一类）。
    """
    return (
        _matches(industry, CYCLICAL_INDUSTRIES),
        _matches(industry, TECH_INDUSTRIES),
        _matches(industry, GROWTH_INDUSTRIES),
    )


def classify_sustainability_industry(industry: str) -> Tuple[bool, bool, bool]:
    """可持续性用：(is_bank, is_cyclical, is_defensive)。"""
    return (
        _matches(industry, FINANCE_INDUSTRIES),
        _matches(industry, CYCLICAL_INDUSTRIES),
        _matches(industry, DEFENSIVE_INDUSTRIES),
    )
