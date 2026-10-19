"""
历史分配记录

每次「订单处理」和「修改分配」完成后，把结果存一份到 程序目录/history/：
  history/index.json            —— 列表（时间、文件夹、金额等，打开历史页时只读这个）
  history/20261011_085200.json.gz —— 当次完整结果（结果页要显示的全部数据 + 可修改分配需要的数据）

保留时间在设置里选（半年 / 一年 / 永久），超过的自动删除。
"""
import os
import json
import gzip
import threading
from datetime import datetime, timedelta

from settings import app_dir

HISTORY_DIR = os.path.join(app_dir(), "history")
INDEX_PATH = os.path.join(HISTORY_DIR, "index.json")
KEEP_OPTIONS = [(180, "半年"), (365, "一年"), (0, "永久保留")]
DEFAULT_KEEP_DAYS = 180

_lock = threading.Lock()


# ---------- 序列化 ----------
def _int_keys_to_str(st):
    st = dict(st)
    for k in ("assignment", "baseline", "sources"):
        if isinstance(st.get(k), dict):
            st[k] = {str(r): v for r, v in st[k].items()}
    if isinstance(st.get("scenario_assigns"), list):
        st["scenario_assigns"] = [{str(r): v for r, v in a.items()} for a in st["scenario_assigns"]]
    return st


def _str_keys_to_int(st):
    for k in ("assignment", "baseline", "sources"):
        if isinstance(st.get(k), dict):
            st[k] = {int(r): v for r, v in st[k].items()}
    if isinstance(st.get("scenario_assigns"), list):
        st["scenario_assigns"] = [{int(r): v for r, v in a.items()} for a in st["scenario_assigns"]]
    return st


def _file_of(hid):
    return os.path.join(HISTORY_DIR, f"{hid}.json.gz")


def _entry(summary, hid, when, old=None):
    st = summary.get("state") or {}
    ordine = st.get("ordine_file") or ""
    root = os.path.dirname(os.path.dirname(ordine)) if ordine else ""
    sups = summary.get("suppliers") or []
    # 采购总额 = 各家实际采购金额之和（箱数 × 每箱件数 × 件价）
    final_cost = sum(s.get("amount", 0) or 0 for s in sups)
    mo = summary.get("min_order") or {}
    e = {
        "id": hid,
        "time": when.strftime("%Y-%m-%d %H:%M:%S"),
        "folder": root,
        "main_file": summary.get("main_file") or os.path.basename(ordine),
        "total_rows": summary.get("total_rows", 0),
        "priced_rows": summary.get("priced_rows", 0),
        "missing": summary.get("missing", 0),
        "suppliers": len([s for s in sups if (s.get("rows") or 0) > 0]),
        "final_cost": round(float(final_cost or 0), 2),
        "extra_cost": round(float(mo.get("extra_cost") or 0), 2),
        "changes": len(summary.get("changes") or []),
        "warning": bool(mo.get("warning")),
        "edits": 0,
        "edited_at": None,
        # 历史页按「日期 · 门店」分组；几种方案各一行，标出选用的
        "store": _store_of(summary),
        "scenario": summary.get("scenario"),
        "scenarios": [{"name": x.get("name"), "total": round(float(x.get("total") or 0), 2),
                       "missing": len(x.get("missing") or []), "priced": x.get("priced", 0),
                       "vs_first": round(float(x.get("vs_first") or 0), 2), "chosen": bool(x.get("chosen")),
                       "excluded": x.get("excluded") or []}
                      for x in (summary.get("scenarios") or [])],
    }
    if old:
        e["time"] = old.get("time", e["time"])
        e["edits"] = old.get("edits", 0) + 1
        e["edited_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return e


def _store_of(summary):
    """主订单文件名里的门店（确认订单_FOIANO_… / 订货建议_MARLIA_…），认不出返回空字符串"""
    try:
        from reorder_service import guess_store, STORES
        sid = guess_store(summary.get("main_file") or "")
        return STORES.get(sid, "") if sid else ""
    except Exception:
        return ""


# ---------- 索引 ----------
def _read_index():
    try:
        with open(INDEX_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (OSError, ValueError):
        pass
    return _rebuild_index()


def _write_index(entries):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    os.replace(tmp, INDEX_PATH)


def _rebuild_index():
    """index.json 丢了或坏了：从各次记录文件重建"""
    entries = []
    if not os.path.isdir(HISTORY_DIR):
        return entries
    for name in os.listdir(HISTORY_DIR):
        if not name.endswith(".json.gz"):
            continue
        hid = name[:-len(".json.gz")]
        try:
            with gzip.open(os.path.join(HISTORY_DIR, name), "rt", encoding="utf-8") as f:
                data = json.load(f)
            meta = data.get("_history") or {}
            when = _time_of(hid) or datetime.now()
            e = _entry(data, hid, when)
            e.update({k: meta[k] for k in ("time", "edits", "edited_at") if k in meta})
            entries.append(e)
        except Exception:
            continue
    entries.sort(key=lambda e: e["id"], reverse=True)
    try:
        _write_index(entries)
    except OSError:
        pass
    return entries


def _time_of(hid):
    try:
        return datetime.strptime(hid[:15], "%Y%m%d_%H%M%S")
    except ValueError:
        return None


# ---------- 对外 ----------
def save_run(summary, keep_days=None, when=None):
    """新的一次处理：存一条记录，返回记录编号，并写进 summary['history_id']"""
    with _lock:
        when = when or datetime.now()
        entries = _read_index()
        hid = when.strftime("%Y%m%d_%H%M%S")
        used = {e["id"] for e in entries}
        n = 1
        base = hid
        while hid in used or os.path.exists(_file_of(hid)):
            n += 1
            hid = f"{base}_{n}"
        summary["history_id"] = hid
        e = _entry(summary, hid, when)
        _write_file(summary, hid, e)
        entries = [x for x in entries if x["id"] != hid]
        entries.insert(0, e)
        entries.sort(key=lambda x: x["id"], reverse=True)
        _write_index(entries)
    if keep_days is not None:
        purge(keep_days)
    return hid


def update_run(summary):
    """修改分配后：覆盖同一条记录（记下修改次数和时间）。没有编号时当新记录存。"""
    hid = summary.get("history_id")
    if not hid:
        return save_run(summary)
    with _lock:
        entries = _read_index()
        old = next((x for x in entries if x["id"] == hid), None)
        e = _entry(summary, hid, _time_of(hid) or datetime.now(), old=old or {})
        _write_file(summary, hid, e)
        entries = [x for x in entries if x["id"] != hid]
        entries.append(e)
        entries.sort(key=lambda x: x["id"], reverse=True)
        _write_index(entries)
    return hid


def _write_file(summary, hid, entry):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    data = {k: v for k, v in summary.items() if k != "state"}
    if summary.get("state"):
        data["state"] = _int_keys_to_str(summary["state"])
    data["_history"] = {"time": entry["time"], "edits": entry["edits"], "edited_at": entry["edited_at"]}
    tmp = _file_of(hid) + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    os.replace(tmp, _file_of(hid))


def _new_names(d):
    """旧记录里的方案名「不含 X」显示成「排除 X」"""
    from scenarios import display_name
    if d.get("scenario"):
        d["scenario"] = display_name(d["scenario"])
    for x in d.get("scenarios") or []:
        if isinstance(x, dict) and x.get("name"):
            x["name"] = display_name(x["name"])
    return d


def list_runs():
    with _lock:
        return [_new_names(dict(e, scenarios=[dict(x) for x in e.get("scenarios") or []]))
                for e in _read_index()]


def load_run(hid):
    """读一条完整记录；读不了返回 None"""
    try:
        with gzip.open(_file_of(hid), "rt", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if data.get("state"):
        try:
            _str_keys_to_int(data["state"])
        except (ValueError, TypeError):
            data.pop("state", None)
    data["history_id"] = hid
    return _new_names(data)


def delete_run(hid):
    delete_runs([hid])


def delete_runs(ids):
    """删除几条记录（只删记录，不动订单和供应商文件）"""
    ids = set(ids)
    with _lock:
        for hid in ids:
            try:
                os.remove(_file_of(hid))
            except OSError:
                pass
        entries = [x for x in _read_index() if x["id"] not in ids]
        _write_index(entries)
    return len(ids)


def clear_all():
    """删除全部记录"""
    with _lock:
        ids = [x["id"] for x in _read_index()]
    return delete_runs(ids)


def purge(keep_days):
    """删除超过保留天数的记录；keep_days=0 表示永久保留。返回删除条数"""
    if not keep_days:
        return 0
    cutoff = datetime.now() - timedelta(days=int(keep_days))
    removed = 0
    with _lock:
        entries = _read_index()
        keep = []
        for e in entries:
            t = _time_of(e["id"])
            if t and t < cutoff:
                try:
                    os.remove(_file_of(e["id"]))
                except OSError:
                    pass
                removed += 1
            else:
                keep.append(e)
        if removed:
            _write_index(keep)
    return removed


def count_older(keep_days):
    """超过保留天数的记录有几条（改保留时间前先问一下）"""
    if not keep_days:
        return 0
    cutoff = datetime.now() - timedelta(days=int(keep_days))
    return sum(1 for e in list_runs() if (_time_of(e["id"]) or datetime.now()) < cutoff)


def disk_usage():
    total = 0
    if os.path.isdir(HISTORY_DIR):
        for name in os.listdir(HISTORY_DIR):
            try:
                total += os.path.getsize(os.path.join(HISTORY_DIR, name))
            except OSError:
                pass
    return total
