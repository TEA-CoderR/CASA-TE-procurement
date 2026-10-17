import secure_store as S


def test_roundtrip_and_no_plaintext():
    for s in ("mysql_x1", "pässwörd €", ""):
        p = S.protect(s)
        assert S.unprotect(p) == s
        if s:
            assert s not in p and S.is_protected(p)
    assert S.unprotect("plain-old") == "plain-old"          # 旧版明文照样能用
    assert S.protect(S.protect("abc")) == S.protect("abc") or S.unprotect(S.protect(S.protect("abc"))) == "abc"


def test_bootstrap_password_not_in_source():
    import os
    import fenpei.config as C
    src = open(os.path.join(os.path.dirname(C.__file__), "config.py"), encoding="utf-8").read()
    assert C.DatabaseConfig.PASSWORD and C.DatabaseConfig.PASSWORD not in src


def test_configure_decrypts():
    from fenpei.config import DatabaseConfig as D
    old = D.PASSWORD
    try:
        D.configure(password=S.protect("secret-pw"))
        assert D.PASSWORD == "secret-pw"
    finally:
        D.PASSWORD = old
