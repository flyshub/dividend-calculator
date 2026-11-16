"""逐只取数的失败隔离（单一实现）。

股息/财务/可持续性三条路径原先各写一份「超时 → 单只跳过 → 连续失败计数 →
放弃门槛」，副本已经漂移：财务那份多了「零写入」护栏，可持续性那份连计数器
都没有（数据源整体失效时永远降级为「未评估」，不会中止）。

本模块把这段实现收进一个 interface：

    policy = FetchPolicy(timeout_s=90, max_consecutive_failures=30)
    outcome = fetch_each(codes, fetch_one, label="股息", policy=policy,
                         cached=cache.get_x, is_empty=..., save=...)

调用方只交「怎么取一只」「什么算空响应」「往哪写」，不再各自实现放弃判据。
"""
import sys
from dataclasses import dataclass, field
from typing import Callable, Generic, List, Optional, TypeVar

T = TypeVar("T")   # 取数原始返回
R = TypeVar("R")   # 写入后保留的行（缓存快照）

# 连续 N 只取数失败视为数据源不可用，中止本轮（避免几千只逐个超时耗光 CI 时长）
DEFAULT_MAX_CONSECUTIVE_FAILURES = 30

# 「零写入」护栏的下限：批次再小也要试到这么多只才判定数据源失效
MIN_MASS_FAILURE_MIN = 20


def effective_mass_failure_min(total_codes: int, configured: int = 200) -> int:
    """把零写入护栏的门槛按本轮代码数缩放，返回实际生效的只数。

    固定 200 只在分片里永不触发：CI 每片只有 ~86 只财务代码，2026-11-14
    run 43593390858 财务零写入、8 片全绿并照常上传了未变的数据。缩放到
    「本轮的一半（不少于 20 只）」，小批次也能识别数据源整体失效。

    注意两点语义：`configured` 是**上限**（设 500 会被缩到代码数/2，方向保守：
    宁可早中止也不白跑）；本轮代码数 < 20 时门槛 = 代码数/2，等于「这批里超过
    一半都拿不到数据才中止」——护栏不会在小批次上误报。
    """
    if total_codes <= 0:
        return configured
    return min(configured, max(MIN_MASS_FAILURE_MIN, total_codes // 2))


@dataclass(frozen=True)
class FetchPolicy:
    """一轮逐只取数的放弃判据。"""

    timeout_s: float
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES
    # 0 = 不启用零写入护栏；>0 时按本轮规模缩放（见 effective_mass_failure_min）
    mass_failure_min: int = 0

    def mass_failure_threshold(self, total_codes: int) -> int:
        if self.mass_failure_min <= 0:
            return 0
        return effective_mass_failure_min(total_codes, self.mass_failure_min)


@dataclass
class FetchOutcome(Generic[R]):
    """一轮取数的结果：items 同时含缓存复用与本次写入的行。"""

    items: List[T] = field(default_factory=list)
    attempted: int = 0   # 真正发起取数的只数（不含缓存复用）
    failures: int = 0    # 抛异常/超时的只数
    written: int = 0     # 成功写入的只数


def fetch_each(
    codes: List[str],
    fetch_one: Callable[[str], T],
    *,
    label: str,
    policy: FetchPolicy,
    cached: Optional[Callable[[str], Optional[R]]] = None,
    is_empty: Optional[Callable[[T], bool]] = None,
    save: Optional[Callable[[T], R]] = None,
    before_request: Optional[Callable[[], None]] = None,
) -> FetchOutcome[R]:
    """逐只取数并写入，返回本轮结果。

    契约：
    - `cached(code)` 返回可复用值（未过期）→ 直接计入 items，不发请求、不计 attempted
    - `before_request()` 在每次真实请求前调用（限流）
    - `fetch_one(code)` 抛异常/超时 → 计失败并跳过；连续 `max_consecutive_failures`
      次失败 → RuntimeError（含 label，便于定位是哪条路径炸的）
    - 成功返回后重置连续失败计数；`is_empty(result)` 为真 → 视为「有响应但无数据」，
      不写、不重置（同旧行为），并检查零写入护栏
    - `save(result)` 写入成功行并返回保留的行（缓存快照），计入 written
    """
    empty = is_empty or (lambda _r: False)
    outcome: FetchOutcome[R] = FetchOutcome()
    consecutive_failures = 0
    mass_threshold = policy.mass_failure_threshold(len(codes))

    for code in codes:
        if cached is not None:
            existing = cached(code)
            if existing is not None:
                outcome.items.append(existing)
                continue
        if before_request is not None:
            before_request()
        outcome.attempted += 1
        try:
            result = _call(fetch_one, code, label, policy.timeout_s)
        except Exception as exc:  # noqa: BLE001 — 单只失败不影响整批
            consecutive_failures += 1
            outcome.failures += 1
            _log_failure(label, code, exc)
            if consecutive_failures >= policy.max_consecutive_failures:
                raise RuntimeError(
                    f"{label}取数连续 {consecutive_failures} 只失败，判定数据源不可用，中止本轮"
                ) from exc
            continue

        # 有任何响应都说明数据源活着 → 重置连续失败计数（旧两条路径都是这个语义）。
        consecutive_failures = 0
        if empty(result):
            # 有响应但无数据：不写假值（数据铁律），但可能说明数据源整体失效。
            if mass_threshold and outcome.attempted >= mass_threshold and outcome.written == 0:
                raise RuntimeError(
                    f"{label}取数已尝试 {outcome.attempted} 只、无一成功写入"
                    f"（门槛 {mass_threshold}），判定数据源不可用，中止本轮"
                )
            continue

        saved = save(result) if save is not None else result  # type: ignore[assignment]
        outcome.written += 1
        outcome.items.append(saved)
    return outcome


def fetch_or_default(
    code: str,
    fetch_one: Callable[[], T],
    *,
    label: str,
    timeout_s: float,
    default: T,
):
    """单只取数的降级形状（漏斗④ 逐候选评估）：超时/异常 → default。

    与 fetch_each 共用同一套超时与日志文案，避免第三份副本。
    """
    try:
        return _call(lambda _code: fetch_one(), code, label, timeout_s)
    except Exception as exc:  # noqa: BLE001 — 单只失败降级
        _log_failure(label, code, exc)
        return default


def _call(fetch_one: Callable[[str], T], code: str, label: str, timeout_s: float) -> T:
    from src.screener_rate_limit import call_with_timeout

    return call_with_timeout(lambda: fetch_one(code), timeout_s, what=f"{code} {label}")


def _log_failure(label: str, code: str, exc: Exception) -> None:
    print(f"  [{label}] {code} 取数失败: {exc}", file=sys.stderr)
