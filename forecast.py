"""
新订货建议算法（服务水平法，periodic review / order-up-to）

每个商品、每家门店：
  1. 需求：近 60 周每周销量 → 加权日均（近 4 周 50%、13 周 30%、26 周 20%；新品按实际在售周数）
            × 季节系数（去年同期接下来几周 ÷ 去年同期前 4 周，量太小不用）
  2. 波动：近 13 周周销量的标准差（至少按泊松 √均值 算）
  3. 目标库存 S（下次到货前要卖的量 + 安全库存）：
       覆盖天数 P = 下单间隔 R + 到货天数 L
       卖得慢（期间需求 < 20 件）：泊松分布的服务水平分位数
       其余：μ + z·σ·√P
       上限（快的慢的都一样，2026-11-03 起慢卖的也限）：安全库存最多够卖 max_safety_days 天（2026-10-22 用户定，默认 7 天；
         以前是「平均压货最多 max_stock_days 天」，和订货周期绑在一起，周期 30 天时快销品安全库存变成 0）
       有销量的商品至少保留 min_shelf 件陈列
  4. 建议件数 = S − 现有库存（负数当 0）− 在途
  5. 服务水平按 ABC：A = 目标 + 2.5%，B = 目标，C = 目标 − 5%（ABC 按本店近 13 周销售额帕累托：前 80% A，到 95% B，其余 C）
     动销兜底（2026-10-22 用户定，简单版 ABC×XYZ）：近 13 周里 ≥ abc_freq_hi 周有卖的最低算 B（天天卖的便宜货不被当 C 少订），
     ≤ abc_freq_lo 周有卖的最高算 C（偶尔才卖的不多备）；新品按上架以来的周数折算。
     2026-10-26：A 类还要近 13 周 ≥ abc_freq_a（6）周有卖，上架不到 4 周的新品最多 B；新品 / 老商品按全部 60 周判断
     订货周期也按 ABC 乘系数（cycle_a / cycle_b / cycle_c，默认 1.25 / 1 / 0.75）：
     A 类多备一点（订够 1.25 个周期），C 类不必订够一个周期（0.75 个），少压货。
     只影响「要备多少天的货」；判断会不会卖断仍按实际的订货周期
  7. 检查间隔（2026-11-07 用户定，默认 14 天）：采购部不是一个供应商周期才看一次，而是一两周就会再看、
       同一家供应商这周订一部分、下周再订另一部分 → 要备几天的货按「下次检查前」算：
       实际周期 = min(供应商周期（review_days，按配置文件可能是 30 / 60 / 90 天）, 检查间隔 check_days)。
       以前每天卖 1 件、每箱 6 件的 A 类，30 天周期建议 7 箱、60 天 13 箱；按 14 天是 4 箱。
       判断会不会卖断、凑整箱、订后够卖的周期都用实际周期
  6. 换成箱（2026-10-22 用户定）：件数凑不成整箱时，先看少订（往下取整）行不行——
       少订后的库存（现有 + 在途 + 整箱数 × 每箱件数）够卖「最低保障线」= 一个订货周期销量 × 系数
       （A 90% / B 70% / C 50%，safety_a / safety_b / safety_c）→ 少订，不为了一两件硬凑一箱；
       不够 → 多订一箱。没有销量（只保留陈列量）的不为它多订一箱
"""
import math
from statistics import NormalDist

DEFAULT_PARAMS = {
    "engine": "v2",            # v2 = 新算法；v1 = 原程序算法
    "review_days": 7,          # 下单间隔 R（天）
    "lead_days": 5,            # 到货天数 L（下单后 2–7 天到）
    "service": 0.95,           # 目标服务水平（不缺货的概率）
    "abc_service": True,       # 按 ABC 调整服务水平
    "max_stock_days": 14,      # （旧，已不用）平均库存最多够卖几天
    "max_safety_days": 7,      # 安全库存最多够卖几天（卖得快的、卖得慢的都限，2026-11-03）
    "season": True,            # 用去年同期做季节调整
    "pack_threshold": 0.3,     # （旧规则，已不用）零头 ≥ 一箱的 30% 才多订一箱
    "abc_freq_hi": 10,         # 动销兜底：近 13 周 ≥ 10 周有卖 → 最低 B
    "abc_freq_lo": 3,          # 近 13 周 ≤ 3 周有卖 → 最高 C
    "abc_freq_a": 6,           # A 类还要卖得稳：近 13 周至少 6 周有卖，不然最多 B（2026-10-26：只卖过一两次的不该是 A）
    "safety_a": 0.9,           # 凑不成整箱时：少订后的库存够卖一个周期的 90%（A 类）就少订
    "safety_b": 0.7,
    "safety_c": 0.5,
    "min_shelf": 1,            # 有销量的商品至少保留几件
    "cycle_a": 1.25,           # A 类订够几个周期（2026-10-22 用户定）
    "cycle_b": 1.0,
    "cycle_c": 0.75,           # C 类不必订够一个周期
    "check_days": 14,          # 检查间隔：最多几天后会再看一次（2026-11-07 用户定）；0 = 不用，照供应商周期
    "warn_boxes": 6,           # 建议超过几箱标黄提醒看一眼（只提醒，不砍）；0 = 不提醒
    "slowdown": True,          # 最近 4 周明显卖慢了（有货）→ 按最近 4 周算（2026-11-07 用户定，规则 A）
    "stockout_fill": True,     # 断货的那几周不拉低日均（2026-11-25 用户定）
}
SLOW_RATIO = 0.5               # 最近 4 周的日均不到加权日均的一半，才算「明显卖慢了」
SLOW_MIN_AGE = 8               # 上架至少 8 周（新品本来就在爬坡）
SLOW_STOCK_DAYS = 14           # 现在库存够加权日均卖 14 天以上 = 一直有货，卖得少不是断货造成的
STOCKOUT_REGULAR = 0.7         # 断货周：别的周里至少 70% 有卖（一直卖得动的商品）
STOCKOUT_MIN_RUN = 2           # 连续至少 2 周一件没卖才算
STOCKOUT_MAX_RUN = 8           # 连续超过 8 周没卖：多半是季节 / 不卖了，不算断货

WEEKS = 60                     # 查多少周（季节调整要一年以上）
_ND = NormalDist()


def z_of(p):
    p = min(max(p, 0.5), 0.999)
    return _ND.inv_cdf(p)


def service_for(tier, params):
    s = float(params.get("service", 0.95))
    if params.get("abc_service", True):
        s += {"A": 0.025, "B": 0.0, "C": -0.05}.get(tier, 0.0)
    return min(max(s, 0.5), 0.995)


def review_used(params, cycle=None):
    """实际按几天备货：这个商品的订货周期（cycle，没给用 review_days）和检查间隔 check_days 取小的"""
    R = float(cycle if cycle else params.get("review_days", 7))
    c = float(params.get("check_days") or 0)
    if c > 0:
        R = min(R, c)
    return max(1.0, R)


def cycle_factor(tier, params):
    """订货周期按 ABC 乘的系数（A 多备、C 少备）"""
    return max(0.1, float(params.get({"A": "cycle_a", "B": "cycle_b"}.get(tier, "cycle_c"), 1.0)))


def poisson_quantile(mean, p):
    """最小的 k，使 P(X ≤ k) ≥ p（X ~ 泊松(mean)）"""
    if mean <= 0:
        return 0
    term = math.exp(-mean)
    cdf, k = term, 0
    while cdf < p and k < 1000:
        k += 1
        term *= mean / k
        cdf += term
    return k


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def age_weeks(weekly, horizon=26):
    """近 horizon 周里，从第一次有销量到现在有几周（新品识别）；没销量 = 0"""
    last = -1
    for i in range(min(horizon, len(weekly))):
        if weekly[i] > 0:
            last = i
    return last + 1


def history_weeks(weekly):
    """查到的全部周数里（默认 60 周），第一次卖出到现在几周 —— 半年前就卖过的不是新品"""
    w = [max(0.0, float(x or 0)) for x in (weekly or [])]
    return age_weeks(w, len(w))


def long_rate(weekly, max_weeks=52):
    """
    (过去一年的平均日销, 最近连续几周一件没卖)：一年 = 最近 52 周，上架不满一年的按上架以来算。
    断货判断用：库存只剩一两件、最近几周一件没卖的，多半是货架上没货（不是卖不动了），
    这时加权日均被最近的 0 拉低，要按一年平均看
    """
    w = [max(0.0, float(x or 0)) for x in (weekly or [])]
    zero = 0
    for x in w:
        if x > 0:
            break
        zero += 1
    n = min(max_weeks, history_weeks(w))
    return (sum(w[:n]) / (7.0 * n) if n else 0.0), (zero if n else 0)


def fill_stockouts(weekly, inv, params=None):
    """
    断货的周不拉低日均（2026-11-25 用户定）：近 26 周里一直卖得动的商品（别的周至少 70% 有卖），
    中间连续 2–8 周一件没卖 —— 多半是货架上没货（断货），不是没人买 → 这几周按有卖的那些周的中位数补上再算日均。
    最近几周就没卖、现在店里有货的不补（那是真的卖慢了，交给规则 A）；最多补 26 周的三分之一。
    返回 (补过的周销量, 补了几周)
    """
    w = [max(0.0, float(x or 0)) for x in (weekly or [])]
    if params is not None and not params.get("stockout_fill", True):
        return w, 0
    n = min(26, len(w))
    if n < 13 or history_weeks(w) < 13:
        return w, 0
    runs, i = [], 0
    while i < n:
        if w[i] == 0:
            j = i
            while j < n and w[j] == 0:
                j += 1
            if STOCKOUT_MIN_RUN <= j - i <= STOCKOUT_MAX_RUN and j < n and not (i == 0 and float(inv or 0) > 0):
                runs.append((i, j))            # 中间断了几周（后面又卖了）；最近几周的只在现在没货时算
            i = j
        else:
            i += 1
    if not runs:
        return w, 0
    inside = {k for a, b in runs for k in range(a, b)}
    rest = [w[k] for k in range(n) if k not in inside]
    sold = sorted(x for x in rest if x > 0)
    if not rest or len(sold) < STOCKOUT_REGULAR * len(rest) or len(inside) > n // 3:
        return w, 0
    fill = sold[len(sold) // 2]
    out = list(w)
    for k in inside:
        out[k] = fill
    return out, len(inside)


def demand_rate(weekly):
    """
    weekly[0] = 最近 7 天销量，weekly[1] = 再往前 7 天 …；返回 (日均, 用了几周)
    2026-10-26 修正：
      · 半年前（26 周以前）就卖过的是老商品，一律按 26 周加权算（以前只看 26 周内第一次卖出，
        老商品最近偶尔卖一件会被当成新品，1 件 ÷ 1 周 = 月销 4.3，严重高估）；
      · 真的新品不满 4 周：至少按 4 周平均（第一周卖 3 件不等于每周 3 件）
    """
    w = [max(0.0, float(x or 0)) for x in weekly] + [0.0] * max(0, 26 - len(weekly))
    age = age_weeks(w)
    if age == 0:
        return 0.0, 0
    if history_weeks(w) > 26:
        age = 26
    if age < 4:
        rate = sum(w[:age]) / 4.0
    elif age < 13:
        rate = 0.6 * _mean(w[:4]) + 0.4 * _mean(w[:age])
    else:
        rate = 0.5 * _mean(w[:4]) + 0.3 * _mean(w[:13]) + 0.2 * _mean(w[:26])
    return rate / 7.0, age


def weekly_sigma(weekly, age):
    n = max(4, min(13, age or 13))
    xs = [max(0.0, float(x or 0)) for x in weekly[:n]]
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def seasonal_factor(weekly, horizon_days):
    """去年同期：接下来 h 周的周均 ÷ 之前 4 周的周均；数据不够或量太小 → 1"""
    h = max(1, min(8, math.ceil(horizon_days / 7)))
    if len(weekly) < 56:
        return 1.0
    ly_next = [max(0.0, float(x or 0)) for x in weekly[52 - h:52]]
    ly_prev = [max(0.0, float(x or 0)) for x in weekly[52:56]]
    if sum(ly_next) + sum(ly_prev) < 8 or sum(ly_prev) <= 0:
        return 1.0
    f = (_mean(ly_next)) / (_mean(ly_prev))
    return min(1.8, max(0.6, f))


def target_stock(daily, sigma_w, inv, tier, params, season=1.0, has_recent_sales=True):
    """返回 dict：mu 期间需求、ss 安全库存、S 目标库存、svc 服务水平"""
    R = review_used(params) * cycle_factor(tier, params)    # 按 ABC：A 多备、C 少备；最多备到下次检查
    L = float(params.get("lead_days", 5))
    P = R + L
    d = daily * season
    svc = service_for(tier, params)
    if d <= 0:
        S = float(params.get("min_shelf", 0)) if has_recent_sales else 0.0
        return {"mu": 0.0, "ss": 0.0, "S": S, "svc": svc}
    mu = d * P
    sd_poisson = math.sqrt(mu)
    sd = max(sigma_w / math.sqrt(7.0) * math.sqrt(P) * season, sd_poisson)
    if mu < 20 and sd <= sd_poisson * 1.3:
        # 卖得慢：用泊松分布的服务水平分位数
        S = float(poisson_quantile(mu, svc))
    else:
        S = mu + z_of(svc) * sd
    # 安全库存最多够卖 max_safety_days 天（不和订货周期绑在一起；A/B/C 的差别交给服务水平）。
    # 2026-11-03 用户定：卖得慢的也一样（以前慢卖商品按泊松分位数不设上限，MARLIA 一个花洒每天 0.14 件、
    # 60 天周期，防断货多备了 7 件 ≈ 50 天，订完够卖 125 天；现在最多多备 7 天）
    cap = mu + d * max(0.0, float(params.get("max_safety_days", 7)))
    S = min(S, cap)
    if has_recent_sales:
        S = max(S, float(params.get("min_shelf", 1)))
    return {"mu": mu, "ss": max(0.0, S - mu), "S": S, "svc": svc}


def order_qty(S, inv, on_order=0.0):
    return max(0, int(math.ceil(S - max(0.0, inv) - max(0.0, on_order) - 1e-9)))


def safety_ratio(tier, params):
    """凑不成整箱时可以接受的最低库存：一个订货周期销量的几成（A 90% / B 70% / C 50%）"""
    return max(0.0, float(params.get({"A": "safety_a", "B": "safety_b"}.get(tier, "safety_c"), 0.7)))


def to_packs(qty, pack, inv, daily, params, tier="B", cycle=None, on_order=0.0):
    """
    件数 → 箱数。整箱正好就按整箱；凑不成整箱时：
      少订（往下取整）后的库存 ≥ 安全库存（一个订货周期销量 × A 90% / B 70% / C 50%）→ 少订；否则多订一箱。
    cycle：这个商品实际的订货周期（天，按配置文件周期时每家供应商不一样）；None = 算法设置里的
    """
    if qty <= 0:
        return 0
    pack = pack if pack and pack > 0 else 1
    if pack == 1:
        return int(math.ceil(qty - 1e-9))
    full = int(qty // pack)
    rem = qty - full * pack
    if rem <= 1e-9:
        return full
    if daily <= 0:
        return full                   # 没有销量（只为保留陈列量）不为零头多订一箱
    R = review_used(params, cycle)
    P = R + float(params.get("lead_days", 5))
    after = max(0.0, inv) + max(0.0, on_order) + full * pack
    return full if after >= safety_ratio(tier, params) * daily * P else full + 1


def abc_by_revenue(revenue):
    """{商品: 销售额} → {商品: 'A'/'B'/'C'}（帕累托：前 80% A，到 95% B）"""
    items = sorted(((v or 0.0, k) for k, v in revenue.items()), reverse=True)
    total = sum(v for v, _ in items if v > 0)
    out, cum = {}, 0.0
    for v, k in items:
        if v <= 0 or total <= 0:
            out[k] = "C"
            continue
        share_before = cum / total
        cum += v
        out[k] = "A" if share_before < 0.80 else "B" if share_before < 0.95 else "C"
    return out


def sold_weeks(weekly, n=13):
    """近 n 周里有几周有销量"""
    return sum(1 for x in (weekly or [])[:n] if (x or 0) > 0)


def adjust_tier(tier, weekly, params):
    """
    动销兜底：按金额分出的等级，再看近 13 周有几周卖过——
      ≥ abc_freq_hi 周 → 至少 B；≤ abc_freq_lo 周 → 最多 C。新品（上架不满 13 周）按上架以来的周数折算，不降级。
    返回 (等级, 原因：None / "up" / "down")
    """
    hi = float(params.get("abc_freq_hi", 10))
    lo = float(params.get("abc_freq_lo", 3))
    fa = float(params.get("abc_freq_a", 6))
    age = min(13, history_weeks(weekly))       # 第一次卖出到现在几周（看全部 60 周，最多算 13）
    if age <= 0:
        return tier, None
    n = sold_weeks(weekly, 13)
    ratio = n / age
    if tier == "C" and age >= 4 and ratio >= hi / 13.0:
        return "B", "up"
    if tier in ("A", "B") and age >= 4 and ratio <= lo / 13.0:
        return "C", "down"                     # 偶尔才卖（老商品看 13 周，新品按上架周数折算）
    if tier == "A" and (age < 4 or ratio < fa / 13.0):
        return "B", "down"                     # A 类要卖得稳；上架不到 4 周的新品还看不出来，最多 B
    return tier, None


def decide(daily, inv, qty, tier, params, total_inv=0, sold_26w=True):
    """判断（和建议量一致）：返回 (文字, 优先级)"""
    R = review_used(params)
    L = float(params.get("lead_days", 5))
    if daily <= 0:
        if not sold_26w and total_inv > 0:
            return "🟢【不动销】26 周没有销量", 1
        if qty > 0:
            return "🟠【可订】保留陈列量", 3
        return "⚪【无销量】", 0
    cover = max(0.0, inv) / daily
    if qty > 0 and cover < L:
        return "🔴【紧急】到货前会卖断", 10 if tier == "A" else 9 if tier == "B" else 8
    if qty > 0 and cover < R + L:
        return "🟡【建议订货】下次到货前会卖断", 6 if tier == "A" else 5
    if qty > 0:
        return "🟠【可订】补到目标库存", 3
    if cover > 2 * (R + L):
        return "🟢【库存多】", 1
    return "🟢【不用订】库存够", 2


def slowdown_rate(weekly, daily, age, inv, params):
    """
    规则 A（2026-11-07 用户定）：最近 4 周明显卖慢了、而且一直有货 → 返回最近 4 周的日均；不是 → None。
    例：开学季的文具 9–10 月卖得多，11 月一下子慢下来，加权日均还被高峰拉着（1.32 件/天，最近 30 天只卖 0.33）→ 多订。
    有货 = 现在的库存够按加权日均卖 SLOW_STOCK_DAYS 天以上（卖得少不是因为货架空了）
    """
    if not params.get("slowdown", True) or daily <= 0 or age < SLOW_MIN_AGE:
        return None
    w = [max(0.0, float(x or 0)) for x in (weekly or [])[:4]]
    if len(w) < 4:
        return None
    recent = sum(w) / 28.0
    if recent >= daily * SLOW_RATIO or inv < daily * SLOW_STOCK_DAYS:
        return None
    return recent


def analyze(weekly, inv, tier, params, total_inv=0, on_order=0.0):
    """一个商品一家店的完整计算；weekly[0] = 最近 7 天"""
    weekly, filled = fill_stockouts(weekly, inv, params)
    daily, age = demand_rate(weekly)
    slow_from = None
    slow = slowdown_rate(weekly, daily, age, inv, params)
    if slow is not None:
        slow_from, daily = daily, slow
    sig = weekly_sigma(weekly, age)
    P = review_used(params) + float(params.get("lead_days", 5))
    season = seasonal_factor(weekly, P) if params.get("season", True) and daily > 0 else 1.0
    recent = sum(max(0.0, float(x or 0)) for x in weekly[:13]) > 0
    t = target_stock(daily, sig, inv, tier, params, season, recent)
    q = order_qty(t["S"], inv, on_order)
    dec, pr = decide(daily * season, inv, q, tier, params, total_inv, age > 0)
    d_eff = daily * season
    return {"daily": d_eff, "daily_base": daily, "slow_from": slow_from, "sigma_w": sig, "season": season, "age_weeks": age,
            "mu": t["mu"], "safety": t["ss"], "target": t["S"], "service": t["svc"], "qty": q,
            "cover_days": (max(0.0, inv) / d_eff) if d_eff > 0 else float("inf"),
            "decision": dec, "priority": pr, "stockout_weeks": filled}
