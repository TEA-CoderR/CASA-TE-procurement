"""卸货预约平台对接（2026-11-21）：按订单号查预约、代约时间、收货结果写回"""
from datetime import date, datetime

import pytest

import booking_api as BA


class _Resp:
    def __init__(self, code, data):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


class FakeSession:
    """记下每次请求，按 (方法, 路径) 返回预先放好的结果"""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def request(self, method, url, params=None, json=None, timeout=None, headers=None):
        path = url.split("://", 1)[1].split("/", 1)[1]
        self.calls.append((method, "/" + path, params, json, headers))
        code, data = self.answers.get((method, "/" + path), (404, {"error": "NOT_FOUND"}))
        return _Resp(code, data(params, json) if callable(data) else data)


@pytest.fixture(autouse=True)
def _cred(monkeypatch, tmp_path):
    monkeypatch.setenv("CASATE_CRED_DIR", str(tmp_path / "cred"))


def test_no_key_means_not_connected_and_clear_message():
    assert not BA.ready({})
    with pytest.raises(BA.BookingApiError) as e:
        BA.test({})
    assert e.value.code == "NO_TOKEN" and "预约平台" in str(e.value)


def test_url_from_settings_or_order_mail_and_key_sent_as_bearer():
    BA.set_token("k" * 40)
    assert BA.base_url({}) == "https://prenota.casa-te.top"
    assert BA.base_url({"order_mail": {"booking_url": "https://prenota.casa-te.com/"}}) == "https://prenota.casa-te.com"
    s = FakeSession({("GET", "/api/erp/bookings"): (200, {"bookings": [{"id": 1}]})})
    assert BA.test({"booking": {"url": "https://x.test"}}, session=s) == 1
    assert s.calls[0][4]["Authorization"] == "Bearer " + "k" * 40


def test_platform_errors_become_business_sentences():
    BA.set_token("k" * 40)
    s = FakeSession({("POST", "/api/erp/bookings"): (409, {"error": "SLOT_UNAVAILABLE", "message": "x"})})
    with pytest.raises(BA.BookingApiError) as e:
        BA.create({}, "1", date(2026, 11, 24), "09:00", "van", 2, 0, "BRT", "Marco", session=s)
    assert e.value.code == "SLOT_UNAVAILABLE" and "换一个" in str(e.value)
    body = s.calls[0][3]
    assert body["entityCode"] == 1 and body["date"] == "2026-11-24" and body["by"] == "Marco"
    with pytest.raises(BA.BookingApiError):          # 门店在平台上没有收货点
        BA.create({}, "9", date(2026, 11, 24), "09:00", "van", 2, 0, "BRT", "Marco", session=s)
    assert BA.entity_of("BMS") == 0 and BA.entity_of("5") == 5


def test_bookings_found_by_order_number_inside_carrier_text_and_written_on_order():
    BA.set_token("k" * 40)
    rows = [{"id": 7, "code": "P261124-AB", "status": "confirmed", "date": "2026-11-24", "time": "09:00",
             "carrierName": "BRT", "driverPhone": "333", "vehicleType": "van", "pallets": 2, "parcels": 0,
             "orderNo": "ordini fo-2311-3f9a + MA-2311-0001", "notes": None, "ddtNo": None},
            {"id": 8, "code": "P261120-CD", "status": "cancelled", "date": "2026-11-20", "time": "10:00",
             "carrierName": "GLS", "orderNo": "FO-2311-3F9A"}]
    s = FakeSession({("GET", "/api/erp/bookings"): (200, {"bookings": rows})})
    got = BA.bookings_for({}, ["FO-2311-3F9A", "MA-2311-0001", "SM-2311-FFFF"], session=s)
    assert [b["id"] for b in got["FO-2311-3F9A"]] == [7, 8] and [b["id"] for b in got["MA-2311-0001"]] == [7]
    assert got["SM-2311-FFFF"] == []
    assert ("order", "FO-2311-3F9A") in s.calls[0][2]
    pick = BA.pick(got["FO-2311-3F9A"])              # 还有效的那张，不是取消的
    assert pick["id"] == 7
    now = datetime(2026, 11, 21, 12, 0)
    f = BA.changes_from({"expected": "2026-11-28"}, pick, now=now)
    assert f["expected"] == "2026-11-24" and f["booking"]["status"] == "confirmed" and f["booking"]["carrier"] == "BRT"
    assert BA.text(f["booking"]) == "24/11 09:00 · BRT · 已预约"
    assert BA.changes_from({"booking": f["booking"], "expected": "2026-11-24"}, pick, now=now) == {}
    # 已经到货的单不改预计到货
    assert "expected" not in BA.changes_from({"arrived": "2026-11-24"}, pick, now=now)


def test_booking_sync_writes_platform_bookings_on_open_orders(tmp_path):
    import commands as CMD
    import mail_send as MS
    import order_tracking as OT
    BA.set_token("k" * 40)
    st = OT.Store(str(tmp_path), me="T").reload()
    a = st.add({"date": "2026-11-19", "store": "1", "supplier": "MELONI", "amount": 100})
    b = st.add({"date": "2026-11-19", "store": "2", "supplier": "BAMA", "amount": 50, "arrived": "2026-11-20"})
    no = MS.order_no(a, st.orders[a])
    rows = [{"id": 3, "code": "P1", "status": "requested", "date": "2026-11-25", "time": "11:15", "carrierName": "SDA",
             "orderNo": no}]
    s = FakeSession({("GET", "/api/erp/bookings"): (200, {"bookings": rows})})
    ch = CMD.booking_sync({}, st, log=lambda *_: None, session=s, now=datetime(2026, 11, 21, 10, 0))
    assert [oid for oid, _f in ch] == [a]
    asked = [v for k, v in s.calls[0][2]]
    assert no.upper() in asked and MS.order_no(b, st.orders[b]).upper() not in asked   # 到货了的不查
    st.reload()
    assert st.orders[a]["booking"]["time"] == "11:15" and st.orders[a]["expected"] == "2026-11-25"
    assert CMD.booking_sync({}, st, log=lambda *_: None, session=s, now=datetime(2026, 11, 21, 10, 5)) == []


def test_receipt_result_maps_to_platform_status():
    assert BA.receipt_status("full") == "completed" and BA.receipt_status("partial") == "completed"
    assert BA.receipt_status("refused") == "refused"
