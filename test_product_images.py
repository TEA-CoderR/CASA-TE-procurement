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


def test_thumb_size_and_row_height():
    import product_images as PI
    assert PI.thumb_size({}) == 64
    assert PI.thumb_size({"image_size": "s"}) == 40
    assert PI.thumb_size({"image_size": "l"}) == 96
    assert PI.row_height({"image_size": "l"}) == 104
    assert PI.row_height({"show_images": False, "image_size": "l"}) == 34
    assert PI.row_height({"image_size": "s"}) == 48


def test_403_not_marked_missing_and_old_markers_cleared(tmp_path):
    d = str(tmp_path)
    open(os.path.join(d, "8002910029470.none"), "w").close()          # 旧版本记下的「没有图」
    st = PI.ImageStore(d, enabled=True)
    assert not os.path.exists(os.path.join(d, "8002910029470.none"))  # 作废一次
    PI.ImageStore(d, enabled=True)                                     # 第二次打开不再清

    class R:
        def __init__(self, code):
            self.status_code, self.content = code, b""

    class S:
        def __init__(self, code):
            self.code = code

        def get(self, url, timeout=0):
            return R(self.code)
    st._session = S(403)
    st._fetch("8002910029471")
    assert not st.known_missing("8002910029471")                       # 服务器拒绝：不当成没有图
    st._session = S(404)
    st._fetch("8002910029472")
    assert st.known_missing("8002910029472")                           # 真没有：记一天
