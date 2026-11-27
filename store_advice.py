# -*- coding: utf-8 -*-
"""
门店扫码枪上的「系统建议箱数」（不依赖界面，2026-11-23 用户：建议直接给门店看，会影响他们怎么订，一定要准）。

DETERSIVO 一个星期订一次：门店在扫码枪上做清单，截止（默认星期天 10:00）后办公室当天下单，批发商过几天送到；
下一次到货是再过 7 天。所以这一次要订的货，要从「数据的时间」一直够卖到「下一次到货」：

    要备的天数 H = 数据到截止还有几天（gap）+ 截止到送到几天（L）+ 7 天（到下一次到货）

用的还是检查清单同一套新算法（forecast：加权日均 + 季节 + ABC 服务水平 + 安全库存最多 7 天），只是备货天数换成
DETERSIVO 真实的日历。以前给门店的建议按 min(供应商周期, 14 天) × ABC 系数（A 1.25 / C 0.75）+ 到货 0 天算：
A 17.5 天、B 14 天、C 10.5 天，不管今天星期几、离截止还有几天、批发商几天送到 —— 星期一看和星期六看建议一样，
星期一的偏少（这几天还要卖），星期六的偏多。现在：一律到下一次到货为止，A / B / C 的差别交给服务水平。

再加几道检查（规范「数量 / 金额一定要有合理性检查」）：
  ① 箱规和检查清单一致：数据库箱规 ≥ 3 倍于每家批发商的 → 用批发商的（price_check.apply_quotes / PACK_FAR），
     不然门店看到「1 箱 = 56 件」、审核时却是 1 件一箱；
  ② 在途按件数扣（订单跟踪里的箱是批发商的箱，不能直接当门店的箱减）；
  ③ 断货补算：库存只剩一点、最近 ≥ 2 周一件没卖、过去一年在卖 → 按一年平均日销（货架空了不等于不好卖）；
  ④ 封顶：订完扣掉最后一箱还够卖 H + 安全库存上限 + 7 天以上 → 那一箱不要（销量数据出错 / 一次大单时不会建议一大堆）；
  ⑥ 一个月的货可以接受（2026-11-27 用户：扫一圈基本都是「不订」）：建议是 0、但库存（含在途）够卖不到 MONTH_DAYS 天
     → 补 1 箱，只要这一箱到了以后总共够卖不超过 BOX_MAX_DAYS 天（一箱太大的慢卖商品还是 0、门店要写原因）；
     和办公室审核一致（检查清单库存够卖不到 45–90 天就照门店清单订），门店不用为这些商品写原因；
  ⑤ 数据太旧（> 3 天没同步）建议照算，扫码枪上写数据日期，日志 ⚠。
"""
import math
from datetime import date, datetime, timedelta

REVIEW = 7               # DETERSIVO 一周订一次
DEFAULT_LEAD = 3         # 星期天下单 → 大约星期三到（订单跟踪里收货记录够了就用实际的）
LEAD_MIN, LEAD_MAX = 1, 7
LEAD_SAMPLES = 4         # 至少这么多张有收货日期的单才用实际的到货天数
STALE_DAYS = 3
CAP_EXTRA = 7            # ④ 封顶：H + 安全库存上限 + 这么多天
MONTH_DAYS = 30          # ⑥ 库存够卖不到这么多天 → 至少 1 箱
BOX_MAX_DAYS = 60        # ⑥ 但这一箱到了以后总共够卖超过这么多天就不补（一箱太大）


def next_deadline(now, rule):
    """rule = {"day": 1–7（星期一 … 星期天）, "time": "HH:MM"}（预约平台的门店清单截止）→ 下一个截止的日期。
    今天就是截止那天、时间还没到 = 今天；过了 = 下星期"""
    day = int((rule or {}).get("day") or 7)
    hh, mm = (str((rule or {}).get("time") or "10:00") + ":0").split(":")[:2]
    for i in range(0, 8):
        d = now.date() + timedelta(days=i)
        if d.isoweekday() != day:
            continue
        if datetime.combine(d, datetime.min.time()).replace(hour=int(hh), minute=int(mm)) > now:
            return d
    return now.date() + timedelta(days=7)


def data_day(sales_latest):
    """数据库每天早上同步到前一天的销售：库存 / 销量是「最新销售那天的第二天早上」的样子"""
    if sales_latest is None:
        return date.today()
    d = sales_latest.date() if isinstance(sales_latest, datetime) else sales_latest
    return d + timedelta(days=1)


def lead_from_tracking(rows, default=DEFAULT_LEAD):
    """订单跟踪里 DETERSIVO 批发商单（程序比价生成的，origin wholesale）下单到收货几天：中位数，限 1–7 天；
    有收货日期的不到 LEAD_SAMPLES 张就用 default。返回 (天数, 用了几张单)"""
    days = []
    for r in rows or []:
        if (r.get("origin") or "") != "wholesale":
            continue
        d0, d1 = r.get("date"), r.get("arrived")
        if d0 and d1 and 0 <= (d1 - d0).days <= 21:
            days.append((d1 - d0).days)
    if len(days) < LEAD_SAMPLES:
        return default, len(days)
    days.sort()
    m = days[len(days) // 2] if len(days) % 2 else (days[len(days) // 2 - 1] + days[len(days) // 2]) / 2.0
    return int(min(LEAD_MAX, max(LEAD_MIN, round(m)))), len(days)


def context(sales_latest, rule, lead, now=None):
    """这一次建议的日历：{dataDate, deadline, gap, lead, horizon, stale}"""
    now = now or datetime.now()
    dd = data_day(sales_latest)
    dl = next_deadline(now, rule)
    gap = max(0, (dl - dd).days)
    lead = int(lead)
    return {"dataDate": dd.isoformat(), "deadline": dl.isoformat(), "gap": gap, "lead": lead,
            "horizon": gap + lead + REVIEW, "stale": (now.date() - dd).days > STALE_DAYS}


def algo_for(algo, ctx):
    """新算法参数换成 DETERSIVO 的节奏：周期 7 天（不和检查间隔取小、不按 ABC 放大），到货天数 = gap + L
    （forecast 里 P = R + L，正好 = H）"""
    a = dict(algo or {})
    a.update(engine="v2", review_days=REVIEW, check_days=0, cycle_a=1.0, cycle_b=1.0, cycle_c=1.0,
             lead_days=float(ctx["gap"] + ctx["lead"]))
    return a


def censored_rate(x, horizon_days):
    """③ 断货补算（和检查清单 censored_info 同一个意思，只是门店还没点名要）：一年平均日销 / None"""
    long_ = float(x.get("long_daily") or 0)
    weeks = int(x.get("zero_weeks") or 0)
    d = float(x.get("daily") or 0)
    inv = max(0.0, float(x.get("inv0") or 0))
    if long_ <= 0 or weeks < 2 or d >= long_ or inv > max(2.0, long_ * horizon_days):
        return None
    return long_


def advise(x, algo, ctx, transit_pcs=0.0):
    """
    一个商品（reorder_service.to_rows 的一行，compute_suggestions 用 algo_for 的参数算的；x["pack"] 已经按
    price_check.apply_quotes 换好）→ (建议箱数 或 None, 标记集合)。
    标记：censored 断货补算 · capped ④ 封顶砍过 · month ⑥ 不到一个月补 1 箱 · no_pack 没有箱规（不给建议）
    """
    import forecast as F
    flags = set()
    pack = float(x.get("pack") or 0)
    if pack <= 0:
        return None, {"no_pack"}
    H = float(ctx["horizon"])
    inv = max(0.0, float(x.get("inv0") or 0))
    tr = max(0.0, float(transit_pcs or 0))
    season = float(x.get("season") or 1.0) or 1.0
    d = float(x.get("daily") or 0) * season
    need = float(x.get("suggested") or 0)              # 到目标库存还差几件（没扣在途）
    cr = censored_rate(x, H)
    if cr is not None:
        flags.add("censored")
        d = max(d, cr * season)
        need = max(need, d * H - inv)
    qty = need - tr
    boxes = 0
    if qty > 0:
        boxes = F.to_packs(qty, pack, inv, d, algo, tier=x.get("tier") or "C", cycle=REVIEW, on_order=tr)
    if boxes <= 0:
        # ⑥ 一个月的货可以接受：够卖不到 MONTH_DAYS 天、补一箱后不超过 BOX_MAX_DAYS 天 → 1 箱
        if d > 0 and (inv + tr) / d < MONTH_DAYS and (inv + tr + pack) / d <= BOX_MAX_DAYS:
            flags.add("month")
            return 1, flags
        return 0, flags
    # ④ 封顶：扣掉最后一箱还够卖很久 → 那一箱不要（卖得快的不会碰到，只拦数据出错 / 一箱很大的情况下多出来的箱）
    if d > 0:
        limit = H + float(algo.get("max_safety_days", 7)) + CAP_EXTRA
        while boxes > 1 and (inv + tr + (boxes - 1) * pack) / d > limit:
            boxes -= 1
            flags.add("capped")
    return int(boxes), flags
