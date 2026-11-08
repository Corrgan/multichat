# SPDX-License-Identifier: GPL-3.0-or-later
from multichat.core.logsetup import redact


def test_redacts_secrets():
    samples = {
        "Authorization: Bearer abc.def-123": "abc.def-123",
        "irc PASS oauth:abcdef123": "abcdef123",
        '{"access_token": "AT-xyz", "refresh_token": "RT-xyz"}': "AT-xyz",
        "client_secret=s3cr3t&grant_type=x": "s3cr3t",
        "GET /callback?code=THECODE&state=1": "THECODE",
        "Adding b'cookie'=b'YSC=abc; VISITOR=xyz' to the header table": "YSC=abc",
        "Cookie: SOCS=CAI; YSC=1": "SOCS=CAI",
    }
    for text, secret in samples.items():
        out = redact(text)
        assert secret not in out and "***" in out, (text, out)


def test_keeps_normal_text():
    assert redact("no cookies here, connected (#chan)") == "no cookies here, connected (#chan)"


def test_ca_certificates_fallback(monkeypatch, tmp_path):
    import ssl

    from multichat import app

    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    # OpenSSL built elsewhere: its default locations don't exist on this system
    monkeypatch.setattr(ssl, "get_default_verify_paths",
                        lambda: ssl.DefaultVerifyPaths("/nonexistent/cert.pem", "/nonexistent/certs", "", "", "", ""))
    monkeypatch.setattr(app, "CA_BUNDLES", ("/nonexistent/a.crt", str(bundle)))
    assert app.ensure_ca_certificates() == str(bundle)
    import os

    assert os.environ["SSL_CERT_FILE"] == str(bundle)


def test_damaged_config_is_kept_and_defaults_used(tmp_path, monkeypatch):
    from multichat.core import config

    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG", cfg_file)
    cfg_file.write_text('{"channels": {"twitch": "examplechan"')  # cut off while writing
    cfg = config.load_config()
    assert cfg["channels"]["twitch"] == "" and cfg["_warnings"]
    assert not cfg_file.exists() and list(tmp_path.glob("config.json.bad-*"))

    # wrong types are ignored field by field, valid ones kept
    cfg_file.write_text('{"channels": {"twitch": "examplechan", "kick": 5}, "video": {"volume": {"kick": 500}, '
                        '"sizes": "x"}, "overlay": {"port": 80}, "on_top": "yes"}')
    cfg = config.load_config()
    assert cfg["channels"] == {"twitch": "examplechan", "kick": "", "youtube": ""}
    assert cfg["video"]["volume"] == {"kick": 100} and cfg["video"]["sizes"] == [440, 860]
    assert cfg["overlay"]["port"] == config.OVERLAY_PORT and cfg["on_top"] is False and not cfg["_warnings"]

    config.save_config(cfg)  # internal keys are not written
    assert "_warnings" not in cfg_file.read_text()


def test_version_compare():
    from multichat.core.constants import version_tuple

    assert version_tuple("v0.30") > version_tuple("0.25") > version_tuple("0.3")
    assert version_tuple("garbage") == ()
