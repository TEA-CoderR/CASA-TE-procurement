"""
供应商起订量优化（MEGA CASH 凑单）

问题：
    按最低价分配后，某个有起订条件的供应商（如 MEGA CASH：200 箱 / 700 欧）
    分到的货不够，订单下不了。

目标：
    在满足起订条件的前提下，让采购总成本增加最少。

算法：
    0. 先按最低价分配（基线），统计目标供应商的箱数和金额。
       已满足起订条件 → 不做任何改动。
    1. 方案 A「凑单」：把别家的商品改到目标供应商，直到满足起订条件。
       每个候选商品 i 的代价 Δi = (目标价 - 当前价) × 箱数，贡献 = 箱数 / 金额。
       贪心：每轮选「单位进度代价」最小的商品（Δ=0 的同价商品最先选），
       然后反向清理：多选的、去掉后仍满足条件的商品退回原供应商（先退代价大的）。
       再和「单个商品就能补足缺口」的最便宜方案比较，取更便宜者。
    2. 方案 B「放弃该供应商」：把已分到目标供应商的商品全部改到各自第二便宜的供应商。
       如果有商品只有目标供应商有报价，方案 B 不可行。
    3. 比较 A、B 的成本增加，选更便宜的；两者都不可行则保持原分配并给出警告。

本模块只做计算，不读写 Excel，方便单独测试。
"""


def _qty(v):
    try:
        q = float(v)
    except (TypeError, ValueError):
        return 0.0
    return q if q > 0 else 0.0


def _colli(r, s):
    """这一行在供应商 s 那里订几箱（起订箱数用）：按件数订货时各家箱规不同，箱数也不同"""
    c = r.get("colli")
    if c is not None and c.get(s) is not None:
        return c[s]
    return _qty(r["qty"])


def _amt(r, s):
    """一箱在供应商 s 的金额（起订金额用）：有 amt（件价 × 这家每箱件数）就用它，没有就等于 prices"""
    a = r.get("amt")
    if a is not None and a.get(s) is not None:
        return a[s]
    return r["prices"][s]


def _best(prices, exclude=None):
    """返回 (供应商, 价格) 的最低价；prices 按列顺序排列，同价取靠前者（与原逻辑一致）"""
    best_s, best_p = None, None
    for s, p in prices.items():
        if s == exclude or p is None:
            continue
        if best_p is None or p < best_p:
            best_s, best_p = s, p
    return best_s, best_p


def effective_limits(min_colli, min_amount, mode):
    """
    规则里的条件 → 计算用的 (起订箱数, 起订金额, mode)：
      "colli"  只要达到起订箱数（金额不管）
      "amount" 只要达到起订金额（箱数不管）
      "all"    箱数和金额都要达到
      "any"    旧规则：达到其中一个即可（保留兼容）
    """
    c, a = float(min_colli or 0), float(min_amount or 0)
    if mode == "colli":
        return c, 0.0, "all"
    if mode == "amount":
        return 0.0, a, "all"
    return c, a, ("any" if mode == "any" else "all")


def _satisfied(colli, amount, min_colli, min_amount, mode):
    checks = []
    if min_colli:
        checks.append(colli >= min_colli - 1e-9)
    if min_amount:
        checks.append(amount >= min_amount - 1e-9)
    if not checks:
        return True
    return all(checks) if mode == "all" else any(checks)


def _greedy_fill(cands, colli, amount, min_colli, min_amount):
    """
    贪心凑单（mode=all，传入的 min_* 为 0 表示不考虑该约束）。
    cands: [(row, delta, q, a)]
    返回 (选中的列表, 总代价) 或 (None, None) 表示不可行。
    """
    def ok(c, a):
        return _satisfied(c, a, min_colli, min_amount, "all")

    chosen = []
    remaining = list(cands)
    C, A = colli, amount
    while not ok(C, A):
        rem_q = max(0.0, (min_colli or 0) - C)
        rem_a = max(0.0, (min_amount or 0) - A)
        best_idx, best_ratio, best_prog = None, None, 0.0
        for idx, (row, delta, q, a) in enumerate(remaining):
            progress = 0.0
            if rem_q > 0:
                progress += min(q, rem_q) / rem_q
            if rem_a > 0:
                progress += min(a, rem_a) / rem_a
            if progress <= 0:
                continue
            ratio = delta / progress
            # 同比率时优先进度更大的，减少改动行数
            if best_ratio is None or ratio < best_ratio - 1e-12 or (
                abs(ratio - best_ratio) <= 1e-12 and progress > best_prog
            ):
                best_idx, best_ratio, best_prog = idx, ratio, progress
        if best_idx is None:
            return None, None  # 所有候选都加上也不够
        item = remaining.pop(best_idx)
        chosen.append(item)
        C += item[2]
        A += item[3]

    # 反向清理：代价大的先尝试退回
    for item in sorted(chosen, key=lambda x: -x[1]):
        if ok(C - item[2], A - item[3]):
            chosen.remove(item)
            C -= item[2]
            A -= item[3]

    total = sum(x[1] for x in chosen)

    # 单个商品直接补足缺口的最便宜方案
    single = None
    for item in cands:
        if ok(colli + item[2], amount + item[3]) and (single is None or item[1] < single[1]):
            single = item
    if single is not None and single[1] < total - 1e-9:
        return [single], single[1]

    return chosen, total


def _dp_fill(cands, colli, amount, min_colli, min_amount, max_states=3_000_000):
    """
    精确解（0/1 背包式动态规划）：在箱数、金额两个缺口都补足的前提下，总代价最小。
    箱数、金额按向下取整计算（保守，保证结果一定满足条件）。
    numpy 不可用或规模过大时返回 (None, None)，由调用方退回贪心。
    """
    try:
        import numpy as np
    except ImportError:
        return None, None
    import math

    need_q = max(0, math.ceil((min_colli or 0) - colli - 1e-9))
    need_a = max(0, math.ceil((min_amount or 0) - amount - 1e-9))
    if (need_q + 1) * (need_a + 1) > max_states:
        return None, None

    INF = float("inf")
    dp = np.full((need_q + 1, need_a + 1), INF)
    dp[0, 0] = 0.0
    rq = np.arange(need_q + 1)
    ra = np.arange(need_a + 1)
    takes = []
    for row, delta, q, a in cands:
        qi, ai = int(math.floor(q + 1e-9)), int(math.floor(a + 1e-9))
        if qi <= 0 and ai <= 0:
            takes.append(None)
            continue
        src = dp[np.ix_(np.maximum(rq - qi, 0), np.maximum(ra - ai, 0))] + delta
        take = src < dp - 1e-9
        dp = np.where(take, src, dp)
        takes.append((np.packbits(take), take.shape))

    if not np.isfinite(dp[need_q, need_a]):
        return None, None

    # 回溯
    chosen = []
    c, e = need_q, need_a
    for idx in range(len(cands) - 1, -1, -1):
        t = takes[idx]
        if t is None:
            continue
        bits, shape = t
        flat = c * shape[1] + e
        if (bits[flat // 8] >> (7 - flat % 8)) & 1:
            row, delta, q, a = cands[idx]
            chosen.append(cands[idx])
            c = max(c - int(math.floor(q + 1e-9)), 0)
            e = max(e - int(math.floor(a + 1e-9)), 0)
    return chosen, sum(x[1] for x in chosen)


def _best_fill(cands, colli, amount, min_colli, min_amount):
    """精确解与贪心解取更便宜的"""
    g, g_cost = _greedy_fill(cands, colli, amount, min_colli, min_amount)
    if g is None:
        return None, None
    # 代价超过贪心总代价的商品不可能出现在更优解里（所有代价都 >= 0），先剔除再做精确计算
    pruned = [c for c in cands if c[1] <= g_cost + 1e-9]
    d, d_cost = _dp_fill(pruned, colli, amount, min_colli, min_amount)
    if d is not None and _satisfied(colli + sum(x[2] for x in d), amount + sum(x[3] for x in d),
                                    min_colli, min_amount, "all"):
        if g is None or d_cost < g_cost - 1e-9:
            return d, d_cost
    return g, g_cost


def optimize_min_order(rows, target, min_colli=0, min_amount=0, mode="all", allow_drop=True):
    """
    Args:
        rows: [{'row': 行号, 'qty': 箱数, 'prices': {供应商: 价格或None}}]，
              prices 的键顺序 = 主表供应商列顺序
        target: 有起订条件的供应商名（主表列头）
        min_colli / min_amount: 起订箱数 / 起订金额（0 表示不限制）
        mode: "all" = 两个条件都要满足；"any" = 满足任一即可
        allow_drop: 是否允许「放弃该供应商」方案

    Returns:
        (assignment, report)
        assignment: {行号: 供应商}（只含有报价的行）
        report: dict，包含 action / extra_cost / changes 等
    """
    # 0. 基线：最低价
    assignment, base_price = {}, {}
    for r in rows:
        s, p = _best(r["prices"])
        if s is not None:
            assignment[r["row"]] = s
            base_price[r["row"]] = p

    if target is None:
        return assignment, {"action": "none"}

    def target_totals(assign):
        c = a = 0.0
        for r in rows:
            if assign.get(r["row"]) == target:
                c += _colli(r, target)
                a += _qty(r["qty"]) * _amt(r, target)
        return c, a

    colli, amount = target_totals(assignment)
    report = {
        "target": target, "before_colli": colli, "before_amount": amount,
        "min_colli": min_colli, "min_amount": min_amount, "mode": mode,
        "action": "none", "extra_cost": 0.0, "changes": [],
        "fill_cost": None, "drop_cost": None,
    }

    if colli == 0 or _satisfied(colli, amount, min_colli, min_amount, mode):
        # 没分到货（无需下单）或已经满足起订条件
        report["action"] = "ok" if colli > 0 else "none"
        report["after_colli"], report["after_amount"] = colli, amount
        return assignment, report

    # 1. 方案 A：凑单
    cands = []
    for r in rows:
        q = _qty(r["qty"])
        pt = r["prices"].get(target)
        cur = assignment.get(r["row"])
        if q <= 0 or pt is None or cur is None or cur == target:
            continue
        delta = (pt - base_price[r["row"]]) * q
        cands.append((r["row"], delta, _colli(r, target), _amt(r, target) * q))

    if mode == "all":
        fill, fill_cost = _best_fill(cands, colli, amount, min_colli, min_amount)
    else:
        # 满足任一条件即可：分别只凑箱数 / 只凑金额，取更便宜的
        options = []
        if min_colli:
            options.append(_best_fill(cands, colli, amount, min_colli, 0))
        if min_amount:
            options.append(_best_fill(cands, colli, amount, 0, min_amount))
        options = [o for o in options if o[0] is not None]
        fill, fill_cost = min(options, key=lambda o: o[1]) if options else (None, None)
    report["fill_cost"] = fill_cost

    # 2. 方案 B：放弃该供应商
    drop, drop_cost = None, None
    if allow_drop:
        drop, drop_cost = [], 0.0
        for r in rows:
            if assignment.get(r["row"]) != target:
                continue
            s2, p2 = _best(r["prices"], exclude=target)
            if s2 is None:  # 该商品只有目标供应商有
                drop, drop_cost = None, None
                break
            q = _qty(r["qty"])
            drop.append((r["row"], s2, (p2 - r["prices"][target]) * q))
            drop_cost += (p2 - r["prices"][target]) * q
    report["drop_cost"] = drop_cost

    # 3. 选更便宜的方案
    use_fill = fill is not None and (drop is None or fill_cost <= drop_cost)
    use_drop = drop is not None and not use_fill

    if use_fill:
        for row, delta, q, a in fill:
            report["changes"].append((row, assignment[row], target, delta))
            assignment[row] = target
        report["action"] = "fill"
        report["extra_cost"] = fill_cost
    elif use_drop:
        for row, s2, delta in drop:
            report["changes"].append((row, target, s2, delta))
            assignment[row] = s2
        report["action"] = "drop"
        report["extra_cost"] = drop_cost
    else:
        report["action"] = "infeasible"

    report["after_colli"], report["after_amount"] = target_totals(assignment)
    return assignment, report


# =====================================================================
# 多供应商起订量优化
# =====================================================================
def _fill_for(cands, colli, amount, min_colli, min_amount, mode):
    """按 mode 凑单，返回 (选中列表, 代价) 或 (None, None)"""
    if mode == "all":
        return _best_fill(cands, colli, amount, min_colli, min_amount)
    options = []
    if min_colli:
        options.append(_best_fill(cands, colli, amount, min_colli, 0))
    if min_amount:
        options.append(_best_fill(cands, colli, amount, 0, min_amount))
    options = [o for o in options if o[0] is not None]
    return min(options, key=lambda o: o[1]) if options else (None, None)


def _totals(rows, assign, target):
    c = a = 0.0
    for r in rows:
        if assign.get(r["row"]) == target:
            q = _qty(r["qty"])
            c += _colli(r, target)
            a += q * _amt(r, target)
    return c, a


def _total_cost(rows, assign):
    t = 0.0
    for r in rows:
        s = assign.get(r["row"])
        if s is not None:
            t += _qty(r["qty"]) * r["prices"][s]
    return t


def _base_assign(rows, closed):
    """在未关闭的供应商中按最低价分配；有数量的商品只有关闭的供应商有报价时返回 (None, 原因)"""
    assign = {}
    for r in rows:
        s, p = None, None
        for sup, pr in r["prices"].items():
            if sup in closed or pr is None:
                continue
            if p is None or pr < p:
                s, p = sup, pr
        if s is None:
            s0, _ = _best(r["prices"])
            if s0 is None:
                continue  # 本来就没报价
            if _qty(r["qty"]) > 0:
                return None, f"有商品只有 {normalize_name(s0)} 有报价，不能不在它那里下单"
            s = s0  # 数量为 0 的行保持原样，不影响成本
        assign[r["row"]] = s
    return assign, None


def _all_ok(rows, assign, rules, open_targets):
    for ru in rules:
        if ru["target"] in open_targets:
            c, a = _totals(rows, assign, ru["target"])
            if c > 0 and not _satisfied(c, a, ru["min_colli"], ru["min_amount"], ru["mode"]):
                return False
    return True


def _fill_sequence(rows, assign, seq, open_targets, lock_others, passes=3):
    """按 seq 顺序给各供应商凑单；lock_others=False 时允许从其他起订供应商挪货，之后再补"""
    assign = dict(assign)
    for _ in range(passes if not lock_others else 1):
        changed = False
        for ru in seq:
            t = ru["target"]
            c, a = _totals(rows, assign, t)
            if c == 0 or _satisfied(c, a, ru["min_colli"], ru["min_amount"], ru["mode"]):
                continue
            cands = []
            for r in rows:
                q = _qty(r["qty"])
                pt = r["prices"].get(t)
                cur = assign.get(r["row"])
                if q <= 0 or pt is None or cur is None or cur == t:
                    continue
                if lock_others and cur in open_targets:
                    continue
                cands.append((r["row"], (pt - r["prices"][cur]) * q, _colli(r, t), _amt(r, t) * q))
            fill, _ = _fill_for(cands, c, a, ru["min_colli"], ru["min_amount"], ru["mode"])
            if fill is None:
                return None
            for row, _, _, _ in fill:
                assign[row] = t
            changed = True
        if not changed:
            break
    return assign


def _evaluate(rows, rules, open_flags, max_perm=24):
    """
    给定每个起订供应商「下单 / 不下单」，算出该情况下成本最低的分配。
    返回 (assignment, None) 或 (None, 原因)。
    """
    import itertools
    closed = {ru["target"] for ru, o in zip(rules, open_flags) if not o}
    open_rules = [ru for ru, o in zip(rules, open_flags) if o]
    open_targets = {ru["target"] for ru in open_rules}

    base, why = _base_assign(rows, closed)
    if base is None:
        return None, why
    if _all_ok(rows, base, rules, open_targets):
        return base, None

    best, best_cost = None, None
    for i, seq in enumerate(itertools.permutations(open_rules)):
        if i >= max_perm:
            break
        for lock in (True, False):
            cand = _fill_sequence(rows, base, seq, open_targets, lock)
            if cand is None or not _all_ok(rows, cand, rules, open_targets):
                continue
            cost = _total_cost(rows, cand)
            if best_cost is None or cost < best_cost - 1e-9:
                best, best_cost = cand, cost
    if best is None:
        names = "、".join(normalize_name(ru["target"]) for ru in open_rules)
        return None, f"{names} 凑不够起订条件"
    return best, None


def normalize_name(name):
    s = str(name)
    for ext in (".xlsx", ".xls"):
        if s.lower().endswith(ext):
            s = s[: -len(ext)]
    return s


def _local_improve(rows, assign, rules, max_pair_rows=400):
    """
    局部改进：尝试把单行、或两行一起改到更便宜的供应商，
    只要所有起订供应商仍然「达标或为 0」就接受，直到不能再省钱。
    """
    assign = dict(assign)
    targets = {ru["target"]: ru for ru in rules}
    by_row = {r["row"]: r for r in rows}
    tot = {t: list(_totals(rows, assign, t)) for t in targets}

    def ok_after(changes):
        d = {t: [0.0, 0.0] for t in targets}
        for row, old, new in changes:
            r = by_row[row]
            q = _qty(r["qty"])
            if old in d:
                d[old][0] -= _colli(r, old)
                d[old][1] -= q * _amt(r, old)
            if new in d:
                d[new][0] += _colli(r, new)
                d[new][1] += q * _amt(r, new)
        for t, (dc, da) in d.items():
            if dc == 0 and da == 0:
                continue
            c, a = tot[t][0] + dc, tot[t][1] + da
            ru = targets[t]
            if c > 1e-9 and not _satisfied(c, a, ru["min_colli"], ru["min_amount"], ru["mode"]):
                return False
            # 必须下单的供应商不能被挪空
            if c <= 1e-9 and tot[t][0] > 1e-9 and not ru.get("allow_drop", True):
                return False
        return True

    def apply(changes):
        for row, old, new in changes:
            r = by_row[row]
            q = _qty(r["qty"])
            if old in tot:
                tot[old][0] -= _colli(r, old)
                tot[old][1] -= q * _amt(r, old)
            if new in tot:
                tot[new][0] += _colli(r, new)
                tot[new][1] += q * _amt(r, new)
            assign[row] = new

    def moves(r):
        cur = assign.get(r["row"])
        if cur is None or _qty(r["qty"]) <= 0:
            return []
        out = []
        for s, p in r["prices"].items():
            if s != cur and p is not None:
                out.append((r["row"], cur, s, (p - r["prices"][cur]) * _qty(r["qty"])))
        return out

    for _ in range(50):
        improved = False
        # 单行
        for r in rows:
            for row, old, new, d in sorted(moves(r), key=lambda m: m[3]):
                if d < -1e-9 and ok_after([(row, old, new)]):
                    apply([(row, old, new)])
                    improved = True
                    break
        # 两行（只在涉及起订供应商的行里找，数量太多时跳过）
        rel = [r for r in rows if assign.get(r["row"]) in targets or
               any(s in targets and p is not None for s, p in r["prices"].items())]
        if not improved and len(rel) <= max_pair_rows:
            all_moves = [m for r in rel for m in moves(r)]
            found = None
            for i in range(len(all_moves)):
                m1 = all_moves[i]
                for m2 in all_moves[i + 1:]:
                    if m2[0] == m1[0] or m1[3] + m2[3] >= -1e-9:
                        continue
                    ch = [m1[:3], m2[:3]]
                    if ok_after(ch) and (found is None or m1[3] + m2[3] < found[0]):
                        found = (m1[3] + m2[3], ch)
            if found:
                apply(found[1])
                improved = True
        if not improved:
            break
    return assign


def _milp_solve(rows, rules, baseline, time_limit=60):
    """
    精确求解（整数规划，scipy/HiGHS）。scipy 不可用或求解失败时返回 None。
    变量：x[i,s] 第 i 行是否给供应商 s；y[t] 是否在起订供应商 t 下单。
    """
    try:
        import numpy as np
        from scipy.optimize import milp, LinearConstraint, Bounds
        from scipy.sparse import lil_matrix
    except Exception:
        return None

    act = [r for r in rows if _qty(r["qty"]) > 0 and any(p is not None for p in r["prices"].values())]
    xs = []  # (row, supplier, cost, qty, amount)
    for r in act:
        q = _qty(r["qty"])
        for sup, p in r["prices"].items():
            if p is not None:
                xs.append((r["row"], sup, p * q, _colli(r, sup), _amt(r, sup) * q))
    nx = len(xs)
    tidx = {ru["target"]: k for k, ru in enumerate(rules)}
    ny = len(rules)
    nz = 2 * ny  # mode=any 时用：z1 达到箱数，z2 达到金额
    n = nx + ny + nz
    if nx == 0:
        return None

    c = np.zeros(n)
    c[:nx] = [x[2] for x in xs]

    rows_A, lo, hi = [], [], []
    A = lil_matrix((len(act) + 6 * ny, n))
    k = 0
    # 每行恰好一个供应商
    pos = {}
    for j, x in enumerate(xs):
        pos.setdefault(x[0], []).append(j)
    for row, js in pos.items():
        for j in js:
            A[k, j] = 1
        lo.append(1); hi.append(1); k += 1

    for ru in rules:
        t = ru["target"]
        y = nx + tidx[t]
        z1, z2 = nx + ny + 2 * tidx[t], nx + ny + 2 * tidx[t] + 1
        js = [j for j, x in enumerate(xs) if x[1] == t]
        big = sum(xs[j][3] for j in js) + 1
        # 有货 → 必须下单：sum x <= big * y
        for j in js:
            A[k, j] = xs[j][3]
        A[k, y] = -big
        lo.append(-np.inf); hi.append(0); k += 1
        mc, ma = ru["min_colli"] or 0, ru["min_amount"] or 0
        if ru["mode"] == "all":
            if mc:
                for j in js:
                    A[k, j] = xs[j][3]
                A[k, y] = -mc
                lo.append(0); hi.append(np.inf); k += 1
            if ma:
                for j in js:
                    A[k, j] = xs[j][4]
                A[k, y] = -ma
                lo.append(0); hi.append(np.inf); k += 1
        else:
            for j in js:
                A[k, j] = xs[j][3]
            A[k, z1] = -mc
            lo.append(0); hi.append(np.inf); k += 1
            for j in js:
                A[k, j] = xs[j][4]
            A[k, z2] = -ma
            lo.append(0); hi.append(np.inf); k += 1
            # z1 + z2 >= y（至少满足一个）；没设的条件不能算满足
            A[k, z1] = 1; A[k, z2] = 1; A[k, y] = -1
            lo.append(0); hi.append(np.inf); k += 1

    A = A[:k].tocsr()
    lb, ub = np.zeros(n), np.ones(n)
    for ru in rules:
        t = ru["target"]
        if not ru.get("allow_drop", True) and any(baseline.get(r["row"]) == t and _qty(r["qty"]) > 0 for r in rows):
            lb[nx + tidx[t]] = 1  # 不允许放弃：必须下单
        if ru["mode"] != "all":
            if not ru["min_colli"]:
                ub[nx + ny + 2 * tidx[t]] = 0
            if not ru["min_amount"]:
                ub[nx + ny + 2 * tidx[t] + 1] = 0
    try:
        res = milp(c, constraints=LinearConstraint(A, lo, hi), integrality=np.ones(n),
                   bounds=Bounds(lb, ub), options={"time_limit": time_limit, "disp": False})
    except Exception:
        return None
    if res.x is None:
        return None
    assign = dict(baseline)
    for j, x in enumerate(xs):
        if res.x[j] > 0.5:
            assign[x[0]] = x[1]
    return assign


def optimize_min_orders(rows, rules, max_rules=8):
    """
    多个供应商各自有起订条件时，求总采购成本最低的分配。

    做法：枚举每个起订供应商「下单 / 不下单」的所有组合（不允许放弃的供应商只能「下单」），
    每个组合里：不下单的供应商不参与分配，其余按最低价分配，
    再给未达标的下单供应商做最低代价凑单；最后取总成本最低的组合。

    Args:
        rows: [{'row', 'qty', 'prices': {供应商: 价格}}]
        rules: [{'target': 供应商列头, 'min_colli', 'min_amount', 'mode': 'all'/'any',
                 'allow_drop': bool}]
    Returns:
        (assignment, report)
        report = {'baseline_cost', 'final_cost', 'extra_cost', 'changes': [(row, 旧, 新, 增加成本)],
                  'rules': [{target, min_colli, min_amount, mode, before_colli, before_amount,
                             after_colli, after_amount, status}], 'warning': str|None}
    """
    import itertools

    baseline, _ = optimize_min_order(rows, None)
    base_cost = _total_cost(rows, baseline)
    rules = [ru for ru in rules if ru.get("target")][:max_rules]

    best_assign, best_cost, reasons = None, None, []
    for flags in itertools.product([True, False], repeat=len(rules)):
        if any((not o) and (not ru.get("allow_drop", True)) for ru, o in zip(rules, flags)):
            continue
        assign, why = _evaluate(rows, rules, flags)
        if assign is None:
            reasons.append(why)
            continue
        cost = _total_cost(rows, assign)
        if best_cost is None or cost < best_cost - 1e-9:
            best_assign, best_cost = assign, cost

    warning = None
    if best_assign is not None and rules:
        best_assign = _local_improve(rows, best_assign, rules)
        best_cost = _total_cost(rows, best_assign)

    # 精确解（有 scipy 时）：合法且更便宜就采用
    exact = _milp_solve(rows, rules, baseline) if rules else None
    if exact is not None:
        open_t = {ru["target"] for ru in rules}
        if _all_ok(rows, exact, rules, open_t):
            ec = _total_cost(rows, exact)
            if best_assign is None or ec < best_cost - 1e-9:
                best_assign, best_cost = exact, ec
    if best_assign is None:
        best_assign, best_cost = dict(baseline), base_cost
        warning = "所有方案都无法满足起订条件，已保持最低价分配，请人工处理。原因：" + \
                  "；".join(sorted(set(reasons))[:3])

    report_rules = []
    for ru in rules:
        t = ru["target"]
        bc, ba = _totals(rows, baseline, t)
        ac, aa = _totals(rows, best_assign, t)
        if bc == 0 and ac == 0:
            status = "未分到货，无需下单"
        elif ac == 0:
            status = "不在该供应商下单（更省钱）"
        elif _satisfied(ac, aa, ru["min_colli"], ru["min_amount"], ru["mode"]):
            before_ok = bc > 0 and _satisfied(bc, ba, ru["min_colli"], ru["min_amount"], ru["mode"])
            status = "已达标" if before_ok else "已凑单达标"
        else:
            status = "未达标"
        report_rules.append(dict(ru, before_colli=bc, before_amount=ba,
                                 after_colli=ac, after_amount=aa, status=status))

    changes = []
    for r in rows:
        old, new = baseline.get(r["row"]), best_assign.get(r["row"])
        if old != new and old is not None and new is not None:
            q = _qty(r["qty"])
            changes.append((r["row"], old, new, (r["prices"][new] - r["prices"][old]) * q))

    return best_assign, {
        "baseline_cost": base_cost, "final_cost": best_cost,
        "extra_cost": best_cost - base_cost, "changes": changes,
        "rules": report_rules, "warning": warning,
    }
