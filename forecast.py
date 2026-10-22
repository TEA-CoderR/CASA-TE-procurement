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
       上限（卖得快的）：平均库存（安全库存 + 半个下单间隔）不超过 max_stock_days 天
       有销量的商品至少保留 min_shelf 件陈列
  4. 建议件数 = S − 现有库存（负数当 0）− 在途
  5. 服务水平按 ABC：A = 目标 + 2.5%，B = 目标，C = 目标 − 5%（ABC 按本店近 13 周销售额帕累托：前 80% A，到 95% B，其余 C）
     订货周期也按 ABC 乘系数（cycle_a / cycle_b / cycle_c，默认 1.25 / 1 / 0.75）：
     A 类多备一点（订够 1.25 个周期），C 类不必订够一个周期（0.75 个），少压货。
     只影响「要备多少天的货」；判断会不会卖断、零头要不要多订一箱仍按实际的订货周期
  6. 换成箱：整箱以外的零头 ≥ 一箱的 pack_threshold 才多订一箱；
            一箱都不到时，只有下次到货前会卖断才订一箱
"""
import math
from statistics import NormalDist

DEFAULT_PARAMS = {
    "engine": "v2",            # v2 = 新算法；v1 = 原程序算法
    "review_days": 7,          # 下单间隔 R（天）
    "lead_days": 5,            # 到货天数 L（下单后 2–7 天到）
    "service": 0.95,           # 目标服务水平（不缺货的概率）
    "abc_service": True,       # 按 ABC 调整服务水平
    "max_stock_days": 14,      # 平均库存最多够卖几天（限制卖得快的商品的安全库存）
    "season": True,            # 用去年同期做季节调整
    "pack_threshold": 0.3,     # 零头 ≥ 一箱的 30% 才多订一箱
    "min_shelf": 1,            # 有销量的商品至少保留几件
    "cycle_a": 1.25,           # A 类订够几个周期（2026-10-22 用户定）
    "cycle_b": 1.0,
    "cycle_c": 0.75,           # C 类不必订够一个周期
}

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


def demand_rate(weekly):
    """weekly[0] = 最近 7 天销量，weekly[1] = 再往前 7 天 …；返回 (日均, 用了几周)"""
    w = [max(0.0, float(x or 0)) for x in weekly] + [0.0] * max(0, 26 - len(weekly))
    age = age_weeks(w)
    if age == 0:
        return 0.0, 0
    if age < 4:
        rate = _mean(w[:age])
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
    R = float(params.get("review_days", 7)) * cycle_factor(tier, params)    # 按 ABC：A 多备、C 少备
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
        # 卖得慢：直接用泊松分布的服务水平分位数（件数少，压货金额小，优先保证不断货）
        S = float(poisson_quantile(mu, svc))
    else:
        S = mu + z_of(svc) * sd
        # 平均库存 ≈ 安全库存 + 半个下单间隔的量；限制平均库存不超过 max_stock_days 天
        cap = mu + d * max(0.0, float(params.get("max_stock_days", 14)) - R / 2)
        S = min(S, cap)
    if has_recent_sales:
        S = max(S, float(params.get("min_shelf", 1)))
    return {"mu": mu, "ss": max(0.0, S - mu), "S": S, "svc": svc}


def order_qty(S, inv, on_order=0.0):
    return max(0, int(math.ceil(S - max(0.0, inv) - max(0.0, on_order) - 1e-9)))


def to_packs(qty, pack, inv, daily, params):
    """件数 → 箱数（智能取整，见模块说明）"""
    if qty <= 0:
        return 0
    pack = pack if pack and pack > 0 else 1
    if pack == 1:
        return int(qty)
    full = int(qty // pack)
    rem = qty - full * pack
    if rem <= 1e-9:
        return full
    thr = float(params.get("pack_threshold", 0.3))
    if rem >= thr * pack:
        return full + 1
    if full == 0:
        P = float(params.get("review_days", 7)) + float(params.get("lead_days", 5))
        return 1 if max(0.0, inv) < daily * P else 0     # 不订的话下次到货前会卖断 → 订一箱
    return full


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


def decide(daily, inv, qty, tier, params, total_inv=0, sold_26w=True):
    """判断（和建议量一致）：返回 (文字, 优先级)"""
    R = float(params.get("review_days", 7))
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
    if cover > 2 * (L + float(params.get("max_stock_days", 14))):
        return "🟢【库存多】", 1
    return "🟢【不用订】库存够", 2


def analyze(weekly, inv, tier, params, total_inv=0, on_order=0.0):
    """一个商品一家店的完整计算；weekly[0] = 最近 7 天"""
    daily, age = demand_rate(weekly)
    sig = weekly_sigma(weekly, age)
    P = float(params.get("review_days", 7)) + float(params.get("lead_days", 5))
    season = seasonal_factor(weekly, P) if params.get("season", True) and daily > 0 else 1.0
    recent = sum(max(0.0, float(x or 0)) for x in weekly[:13]) > 0
    t = target_stock(daily, sig, inv, tier, params, season, recent)
    q = order_qty(t["S"], inv, on_order)
    dec, pr = decide(daily * season, inv, q, tier, params, total_inv, age > 0)
    d_eff = daily * season
    return {"daily": d_eff, "daily_base": daily, "sigma_w": sig, "season": season, "age_weeks": age,
            "mu": t["mu"], "safety": t["ss"], "target": t["S"], "service": t["svc"], "qty": q,
            "cover_days": (max(0.0, inv) / d_eff) if d_eff > 0 else float("inf"),
            "decision": dec, "priority": pr}
