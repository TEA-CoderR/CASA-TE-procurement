"""
和卸货预约平台（Prenotazione_Scarico，prenota.casaete.top / .com）对接（2026-11-21 用户：预约平台不并进程序、做对接；
登记发货时在程序里就能选卸货时间，和平台同步；确认收货 / 拒收也写回平台）。不依赖界面。

平台那边的接口 /api/erp/*（PR #10），密钥 = 平台服务器 .env 里的 ERP_API_TOKEN：
  GET  /api/erp/bookings?order=FO-2111-3F9A   按订单号查预约（承运商写在文字里的订单号也认）
  GET  /api/erp/availability?entity=1&date=…&vehicle=van&pallets=2   采购部可选的时间（今天还没过的也行）
  POST /api/erp/bookings      采购部代约（电话预约 / 登记发货时选时间），直接「已确认」
  POST /api/erp/bookings/<id>/status   到场 / 完成 / 拒收（要原因）/ 取消

网址在设置 booking.url（随发布同步，没设用订单邮件里写的预约网址）；密钥只加密存在这台电脑（credentials booking.token）。
订单上记 o["booking"] = {id, code, status, date, time, carrier, phone, vehicle, pallets, parcels, by, synced}。
"""
from datetime import date, datetime

DEFAULT_URL = "https://prenota.casaete.top"
CRED_KEY = "booking.token"
TIMEOUT = 12

# 程序的门店号 → 平台的收货主体（0 BMS、1 FOIANO … 5 S.CONCORDIO，和数据库门店号一样）
ENTITY = {"BMS": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
VEHICLES = (("parcel", "快递 / 零担"), ("van", "厢式货车"), ("truck", "卡车"), ("semi", "半挂"))
STATUS_TEXT = {"requested": "待平台确认", "confirmed": "已预约", "rejected": "平台已驳回", "cancelled": "已取消",
               "arrived": "已到场", "unloading": "卸货中", "completed": "已卸货", "no_show": "没来",
               "refused": "已拒收"}
LIVE = ("requested", "confirmed", "arrived", "unloading")


class BookingApiError(Exception):
    """平台返回的错误：code = 平台的错误码（SLOT_UNAVAILABLE…），给人看的写在 str() 里"""

    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


ERROR_TEXT = {
    "ERP_API_DISABLED": "预约平台还没开对接（平台服务器 .env 里没有 ERP_API_TOKEN，先运行一次 deploy/update.sh）",
    "UNAUTHENTICATED": "预约平台密钥不对（系统设置 → 数据连接 → 预约平台）",
    "SLOT_UNAVAILABLE": "这个时间刚被别人约走了，请换一个",
    "DATE_NOT_BOOKABLE": "这一天 / 这个时间不能约（已经过了，或太远）",
    "SITE_CLOSED": "这一天仓库不收货（节假日 / 关闭日）",
    "LOAD_NOT_ALLOWED": "这种车装不下这么多托盘，换大一点的车型",
    "DAILY_CAP_REACHED": "这一天仓库的托盘量已经满了",
    "INVALID_TRANSITION": "平台上这张预约的状态已经变了，刷新一下再试",
    "BOOKING_NOT_FOUND": "平台上找不到这张预约（可能被删了）",
    "VALIDATION": "填的内容不完整",
}


def cfg(settings):
    return (settings or {}).setdefault("booking", {})


def base_url(settings):
    u = (cfg(settings).get("url") or ((settings or {}).get("order_mail") or {}).get("booking_url") or DEFAULT_URL).strip()
    return fix_url(u).rstrip("/")


def fix_url(u):
    """2026-11-21 用户：网址是 prenota.casaete.top，以前写错成 casa-te.top（已经存进设置的也改过来）"""
    return (u or "").replace("casa-te.top", "casaete.top")


def token():
    try:
        import credentials
        return credentials.get(CRED_KEY) or ""
    except Exception:
        return ""


def set_token(value):
    import credentials
    credentials.set(CRED_KEY, (value or "").strip())


def ready(settings):
    """填了密钥才算连上（网址有默认值）"""
    return bool(token())


def entity_of(store):
    return ENTITY.get(str(store or ""))


# ===================================================================== 请求
def _call(settings, method, path, params=None, body=None, session=None, tok=None):
    import requests
    tok = tok if tok is not None else token()
    if not tok:
        raise BookingApiError("NO_TOKEN", "还没填预约平台的密钥（系统设置 → 数据连接 → 预约平台）")
    s = session or requests
    try:
        r = s.request(method, base_url(settings) + path, params=params, json=body, timeout=TIMEOUT,
                      headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
    except Exception as e:
        raise BookingApiError("NETWORK", f"连不上预约平台（{base_url(settings)}）：{e}")
    try:
        data = r.json()
    except Exception:
        data = {}
    if r.status_code >= 400:
        code = (data or {}).get("error") or f"HTTP {r.status_code}"
        msg = ERROR_TEXT.get(code) or (data or {}).get("message") or code
        raise BookingApiError(code, msg)
    return data


def test(settings, tok=None, session=None):
    """测试连接：查一下今天的预约（只读）。返回今天几张"""
    d = date.today().isoformat()
    data = _call(settings, "GET", "/api/erp/bookings", {"from": d, "to": d}, session=session, tok=tok)
    return len(data.get("bookings") or [])


def bookings_for(settings, order_nos, session=None):
    """这几个订单号在平台上的预约 → {订单号: [预约…]}（新的在后）"""
    nos = [n for n in dict.fromkeys(str(x).strip().upper() for x in order_nos or []) if n]
    out = {n: [] for n in nos}
    for k in range(0, len(nos), 80):                     # 网址别太长
        part = nos[k:k + 80]
        data = _call(settings, "GET", "/api/erp/bookings", [("order", n) for n in part], session=session)
        for b in data.get("bookings") or []:
            text = " ".join(str(b.get(f) or "") for f in ("orderNo", "notes", "ddtNo")).upper()
            for n in part:
                if n in text:
                    out[n].append(b)
    return out


def availability(settings, store, day, vehicle, pallets=0, parcels=0, session=None):
    """→ (卸货要几分钟, [「09:00」…])"""
    ent = entity_of(store)
    if ent is None:
        raise BookingApiError("VALIDATION", "这张单的门店在预约平台上没有对应的收货点")
    data = _call(settings, "GET", "/api/erp/availability",
                 {"entity": ent, "date": _iso(day), "vehicle": vehicle, "pallets": int(pallets or 0),
                  "parcels": int(parcels or 0)}, session=session)
    return int(data.get("durationMin") or 0), [s["time"] for s in data.get("slots") or []]


def create(settings, store, day, time, vehicle, pallets, parcels, carrier, by, order_no=None, supplier=None,
           phone=None, email=None, notes=None, ddt=None, session=None):
    """采购部代约：直接「已确认」。返回平台的 {id, code, status, time…}"""
    ent = entity_of(store)
    if ent is None:
        raise BookingApiError("VALIDATION", "这张单的门店在预约平台上没有对应的收货点")
    body = {"entityCode": ent, "date": _iso(day), "time": time, "vehicleType": vehicle,
            "pallets": int(pallets or 0), "parcels": int(parcels or 0), "carrierName": (carrier or "").strip(),
            "by": (by or "").strip() or "?", "orderNo": order_no or "", "supplier": supplier or "",
            "driverPhone": phone or "", "contactEmail": email or "", "notes": notes or "", "ddtNo": ddt or "",
            "lang": "it"}
    return _call(settings, "POST", "/api/erp/bookings", body=body, session=session)


def set_status(settings, booking_id, to, by, note=None, session=None):
    body = {"to": to, "by": (by or "").strip() or "?"}
    if note:
        body["note"] = str(note)[:500]
    return _call(settings, "POST", f"/api/erp/bookings/{int(booking_id)}/status", body=body, session=session)


def _iso(day):
    if isinstance(day, (date, datetime)):
        return day.strftime("%Y-%m-%d")
    return str(day)[:10]


# ===================================================================== 订单上的记录
def record(b, by=None, now=None):
    """平台的一张预约 → 订单上记的 o["booking"]"""
    out = {"id": b.get("id"), "code": b.get("code"), "status": b.get("status"), "date": b.get("date"),
           "time": b.get("time"), "carrier": b.get("carrierName") or b.get("carrier") or "",
           "phone": b.get("driverPhone") or "", "vehicle": b.get("vehicleType") or "",
           "pallets": b.get("pallets"), "parcels": b.get("parcels"),
           "synced": (now or datetime.now()).strftime("%Y-%m-%d %H:%M")}
    if by:
        out["by"] = by
    return out


def pick(bookings):
    """一个订单号对到几张预约时用哪张：还有效的（待确认 / 已约 / 到场 / 卸货）里最早的，没有就最新的"""
    live = [b for b in bookings or [] if b.get("status") in LIVE]
    if live:
        return sorted(live, key=lambda b: (b.get("date") or "", b.get("time") or ""))[0]
    return sorted(bookings or [], key=lambda b: (b.get("date") or "", b.get("time") or ""))[-1] if bookings else None


def changes_from(o, b, now=None):
    """平台上查到的预约 → 订单要改的字段（只在有变化时返回）：booking；约好的日期写进预计到货"""
    if not b:
        return {}
    cur = o.get("booking") or {}
    new = record(b, by=cur.get("by"), now=now)
    keys = ("id", "status", "date", "time", "carrier", "phone", "vehicle", "pallets", "parcels")
    f = {}
    if any(cur.get(k) != new.get(k) for k in keys):
        f["booking"] = new
    if new.get("status") in LIVE and new.get("date") and str(o.get("expected") or "")[:10] != new["date"] \
            and not o.get("arrived"):
        f["expected"] = new["date"]
    return f


def text(bk):
    """一行给人看：「24/11 09:00 · BRT · 已预约」"""
    if not bk:
        return ""
    d = str(bk.get("date") or "")
    when = f"{d[8:10]}/{d[5:7]}" if len(d) >= 10 else d
    from i18n import tr
    st = STATUS_TEXT.get(bk.get("status"))
    parts = [f"{when} {bk.get('time') or ''}".strip(), bk.get("carrier") or "", tr(st) if st else ""]
    return " · ".join(p for p in parts if p)


def receipt_status(result):
    """确认收货的结果 → 平台的状态：收到（全部 / 部分）= 卸完了；拒收 = refused"""
    return "refused" if result == "refused" else "completed"
