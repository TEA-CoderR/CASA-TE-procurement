"""商品图片（product_images.py）：条码整理、服务器文件夹名、没有图的标记"""
import os
import time

import product_images as PI


def test_norm_ean_and_candidates():
    assert PI.norm_ean(" 8002910029470 ") == "8002910029470"
    assert PI.norm_ean(8002910029470.0) == "8002910029470"
    assert PI.norm_ean("ABC") == "" and PI.norm_ean(None) == "" and PI.norm_ean("123") == ""
    assert PI.candidates("8002910029470") == ["8002910029470"]
    assert PI.candidates("83887775320") == ["83887775320", "0083887775320"]     # 数据库去掉了前面的 0
    assert PI.DEFAULT_URL.format(ean="8002910029470") == "https://img.casaete.top/prodotti/8002910029470/01.webp"


def test_missing_marker_expires(tmp_path):
    st = PI.ImageStore(str(tmp_path), enabled=True)
    assert not st.known_missing("8002910029470")
    p = st._miss("8002910029470")
    open(p, "w").close()
    assert st.known_missing("8002910029470")
    old = time.time() - (PI.MISS_DAYS + 1) * 86400
    os.utime(p, (old, old))
    assert not st.known_missing("8002910029470")                                 # 过了 7 天再问一次
