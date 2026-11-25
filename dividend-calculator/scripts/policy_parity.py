"""双端 policy 一致性校验（issues #149 / #151）。

`verify_js_vs_python.py` 比对的是「用同一份数据跑出的字段值」——它能发现
公式实现漂移，但看不见：
- 关键词集合的成员差异（例如 Python `'电力行业'` vs JS `'电力'`：只有遇到
  「电力设备-电源设备-太阳能」这类标签才会分叉，抽样股票的字段值恰好一致）；
- 页面散文里的阈值数字（样例里数字对，文案写着旧阈值）。

这里把这两类做成可独立运行的结构性校验：解析 JS/HTML 源里的声明，与
Python 的唯一来源比对，返回问题清单（空 = 一致）。纯字符串处理，不联网。
"""
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Pattern

# Python 唯一来源 ←→ JS 镜像变量名
INDUSTRY_ARRAYS = {
    "CYCLICAL_INDUSTRIES": "CYCLICAL_INDUSTRIES",
    "TECH_INDUSTRIES": "TECH_INDUSTRIES",
    "GROWTH_INDUSTRIES": "GROWTH_INDUSTRIES",
    "DEFENSIVE_INDUSTRIES": "DEFENSIVE_INDUSTRIES",
    "FINANCE_INDUSTRIES": "FINANCE_INDUSTRIES",
}


def _js_array(source: str, name: str) -> Optional[set]:
    """取 `var NAME = [ 'a', 'b', ];` 里的字符串集合；取不到返回 None。"""
    m = re.search(rf"var\s+{name}\s*=\s*\[(.*?)\];", source, re.S)
    if not m:
        return None
    return {a or b for a, b in re.findall(r"'([^']+)'|\"([^\"]+)\"", m.group(1))}


def python_industry_sets() -> Dict[str, set]:
    """Python 侧的权威集合（来自 src/industry_policy.py）。"""
    import sys

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src import industry_policy

    return {
        "CYCLICAL_INDUSTRIES": set(industry_policy.CYCLICAL_INDUSTRIES),
        "TECH_INDUSTRIES": set(industry_policy.TECH_INDUSTRIES),
        "GROWTH_INDUSTRIES": set(industry_policy.GROWTH_INDUSTRIES),
        "DEFENSIVE_INDUSTRIES": set(industry_policy.DEFENSIVE_INDUSTRIES),
        "FINANCE_INDUSTRIES": set(industry_policy.FINANCE_INDUSTRIES),
    }


def check_industry_keywords(js_source: str,
                            py_sets: Optional[Dict[str, set]] = None) -> List[str]:
    """行业关键词集合双端比对，返回问题描述（空 = 一致）。"""
    py_sets = python_industry_sets() if py_sets is None else py_sets
    problems: List[str] = []
    for py_name, js_name in INDUSTRY_ARRAYS.items():
        js = _js_array(js_source, js_name)
        if js is None:
            problems.append(f"JS 缺少 {js_name} 声明（解析不到 var {js_name} = [...]）")
            continue
        py = py_sets.get(py_name, set())
        only_py = sorted(py - js)
        only_js = sorted(js - py)
        if only_py or only_js:
            detail = []
            if only_py:
                detail.append(f"仅 Python 有: {', '.join(only_py)}")
            if only_js:
                detail.append(f"仅 JS 有: {', '.join(only_js)}")
            problems.append(f"{py_name} 双端不一致（{'；'.join(detail)}）")
    return problems


def _js_number(source: str, name: str) -> Optional[float]:
    m = re.search(rf"{name}\s*:\s*([0-9.]+)", source)
    return float(m.group(1)) if m else None


def check_pr_cutoffs(js_source: str,
                     py_cutoffs: Optional[Dict[str, float]] = None) -> List[str]:
    """估值档位切点双端比对（JS 的 PR_ZONE_CUTOFFS ←→ Python 的 PR_*_MAX）。"""
    if py_cutoffs is None:
        import sys

        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from src.pr_calculator import PR_FAIR_LOW_MAX, PR_FAIR_MAX, PR_UNDERVALUED_MAX

        py_cutoffs = {
            "undervalued": PR_UNDERVALUED_MAX,
            "fair_low": PR_FAIR_LOW_MAX,
            "fair_max": PR_FAIR_MAX,
        }
    problems = []
    for key, expected in py_cutoffs.items():
        got = _js_number(js_source, key)
        if got is None:
            problems.append(f"JS 缺少切点 {key}（PR_ZONE_CUTOFFS 未定义或拼写不同）")
        elif abs(got - expected) > 1e-9:
            problems.append(f"切点 {key} 双端不一致：JS {got} / Python {expected}")
    return problems


def check_sustainability_threshold(js_source: str,
                                   py_threshold: Optional[float] = None) -> List[str]:
    """可持续性评估阈值双端比对（JS 的 SUS_THRESHOLD_YIELD ←→ Python THRESHOLD_YIELD，#28）。"""
    if py_threshold is None:
        import sys

        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from src.sustainability_calculator import THRESHOLD_YIELD

        py_threshold = THRESHOLD_YIELD
    m = re.search(r"var\s+SUS_THRESHOLD_YIELD\s*=\s*([0-9.]+)", js_source)
    if not m:
        return ["JS 缺少 SUS_THRESHOLD_YIELD 声明（可持续性阈值未镜像 Python THRESHOLD_YIELD）"]
    got = float(m.group(1))
    if abs(got - py_threshold) > 1e-9:
        return [f"可持续性评估阈值双端不一致：JS {got} / Python {py_threshold}"]
    return []


def check_html_thresholds(html: str, pattern: Pattern, expected: Iterable[str],
                          where: str) -> List[str]:
    """网页散文里必须出现的阈值片段（防止文案写死旧数字）。"""
    missing = [snip for snip in expected if snip not in html]
    return [f"{where} 文案缺少 {snip!r}（阈值改了但文案没同步）" for snip in missing]
