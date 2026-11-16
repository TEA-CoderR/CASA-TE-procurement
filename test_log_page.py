"""运行日志页：按内容认「哪一块」、按级别 / 哪一块 / 搜索筛选（2026-11-16）"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import log_page as LP


def test_module_of_by_keywords():
    assert LP.module_of("✔ FOIANO 比价完成") == "比价"
    assert LP.module_of("❌ SELL OUT 读分类失败：连不上数据库") == "SELL OUT"
    assert LP.module_of("⚠ 数据库里最新的销售是 14/11") == "数据库"
    assert LP.module_of("✔ 订单跟踪：Excel 同步完成") == "订单跟踪"
    assert LP.module_of("[邮件] 邮件核对：6 个邮箱") == "邮件"
    assert LP.module_of("启动用时 6.5 秒") == LP.OTHER


def test_matches_level_module_and_query():
    e_err = ("err", "❌ 比价失败", None)
    e_ok = ("ok", "✔ 比价完成", None)
    e_warn = ("warn", "⚠ 邮件发不出去", None)
    assert LP.matches(e_ok, "all", "all", "")
    assert not LP.matches(e_ok, "warn", "all", "")
    assert LP.matches(e_warn, "warn", "all", "")
    assert not LP.matches(e_warn, "err", "all", "")
    assert LP.matches(e_err, "err", "比价", "")
    assert not LP.matches(e_err, "all", "邮件", "")
    assert LP.matches(e_ok, "all", None, "完成") and not LP.matches(e_ok, "all", None, "失败")
