"""
界面语言：中文 / English / Italiano

用法：tr("中文原文 {0}", 参数) —— 原文就是键，找不到翻译时直接显示中文。
翻译表在 translations.py。
"""
LANGS = [("zh", "中文"), ("en", "English"), ("it", "Italiano")]
_lang = "zh"

try:
    from translations import TRANSLATIONS   # {中文: (English, Italiano)}
except Exception:  # 翻译表缺失时只显示中文
    TRANSLATIONS = {}


def set_language(code):
    global _lang
    _lang = code if code in dict(LANGS) else "zh"


def get_language():
    return _lang


def _lookup(text, lang):
    if lang == "zh":
        return text
    t = TRANSLATIONS.get(text)
    if not t:
        return text
    return (t[0] if lang == "en" else t[1]) or text


def tr(text, *args):
    s = _lookup(text, _lang)
    if args:
        try:
            return s.format(*args)
        except (IndexError, KeyError, ValueError):
            return text.format(*args)
    return s


# 状态 / 原因：程序内部一律用中文原词当代码，只在显示时翻译
STATUS_KIND = {
    "未达标": "err", "未找到文件": "err",
    "已放弃": "warn", "放弃供应商": "warn",
    "未分到货，无需下单": "info", "已停用": "info", "手动": "info",
    "启用": "ok", "已达标": "ok", "已凑单达标": "ok", "自动凑单": "ok",
}


def kind_of(text):
    """状态文字（任何语言）→ 颜色类别"""
    for raw, k in STATUS_KIND.items():
        if text == raw:
            return k
        t = TRANSLATIONS.get(raw)
        if t and text in t:
            return k
    return "ok"
