"""
工具函数单元测试
运行: python -m pytest tests/test_utils.py -v
"""
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import clean_ean, normalize, parse_price


# =====================
# clean_ean 测试
# =====================
def test_clean_ean_normal():
    assert clean_ean("00123456") == "123456"


def test_clean_ean_no_leading_zero():
    assert clean_ean("123456") == "123456"


def test_clean_ean_none():
    assert clean_ean(None) == ""


def test_clean_ean_empty_string():
    assert clean_ean("") == ""


def test_clean_ean_with_spaces():
    assert clean_ean("  00123456  ") == "123456"


def test_clean_ean_number():
    assert clean_ean(123456) == "123456"


def test_clean_ean_float_integer():
    assert clean_ean(123456.0) == "123456"


# =====================
# normalize 测试
# =====================
def test_normalize_xlsx():
    assert normalize("Supplier A.xlsx") == "supplier a"


def test_normalize_xls():
    assert normalize("Supplier B.xls") == "supplier b"


def test_normalize_no_extension():
    assert normalize("Supplier C") == "supplier c"


def test_normalize_with_spaces():
    assert normalize("  Supplier D.xlsx  ") == "supplier d"


# =====================
# parse_price 测试
# =====================
def test_parse_price_plain():
    assert parse_price("12.50") == 12.50


def test_parse_price_with_euro():
    assert parse_price("€12.50") == 12.50


def test_parse_price_with_eur():
    assert parse_price("12,50 EUR") == 12.50


def test_parse_price_comma():
    assert parse_price("12,50") == 12.50


def test_parse_price_none():
    assert parse_price(None) is None


def test_parse_price_empty():
    assert parse_price("") is None


def test_parse_price_invalid():
    assert parse_price("abc") is None


def test_parse_price_integer():
    assert parse_price("100") == 100.0


def test_parse_price_number():
    assert parse_price(12.50) == 12.50


if __name__ == "__main__":
    # 简单运行测试
    tests = [
        test_clean_ean_normal,
        test_clean_ean_no_leading_zero,
        test_clean_ean_none,
        test_clean_ean_empty_string,
        test_clean_ean_with_spaces,
        test_clean_ean_number,
        test_clean_ean_float_integer,
        test_normalize_xlsx,
        test_normalize_xls,
        test_normalize_no_extension,
        test_normalize_with_spaces,
        test_parse_price_plain,
        test_parse_price_with_euro,
        test_parse_price_with_eur,
        test_parse_price_comma,
        test_parse_price_none,
        test_parse_price_empty,
        test_parse_price_invalid,
        test_parse_price_integer,
        test_parse_price_number,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
            print(f"  [PASS] {test.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {test.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  [ERROR] {test.__name__}: {e}")

    print(f"\n{passed} passed, {failed} failed")
