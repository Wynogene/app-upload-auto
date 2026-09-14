"""Smoke tests for AAB meta parsing and error formatting (no network)."""

from __future__ import annotations

from pathlib import Path

from app.stores.aab_meta import parse_aab_meta
from app.stores.google_errors import format_google_upload_error, is_retryable_transport_error


UPLOAD_TEST = Path(r"F:\upload-test")


def test_parse_known_aabs() -> None:
    cases = {
        "com.blurams.ipc_5.1049.4.952_Android_20260828_1a1a169d93_release.aab": (
            "com.blurams.ipc",
            "1952",
            "5.1049.4.952",
        ),
        "com.vitec.easelifeEn_5.1054.4.420_Android_20260826_86ea6c2e1b_release.aab": (
            "com.vitec.easelifeEn",
            "10420",
            "5.1054.4.420",
        ),
        "com.boykeep.ipc1_5.1055.4.184_Android_20260807_ec1601457c_release.aab": (
            "com.boykeep.ipc1",
            "2184",
            "5.1055.4.184",
        ),
    }
    for name, (pkg, code, vname) in cases.items():
        path = UPLOAD_TEST / name
        if not path.exists():
            continue
        meta = parse_aab_meta(path)
        assert meta.package_name == pkg
        assert meta.version_code == code
        assert meta.version_name == vname


def test_format_version_used() -> None:
    msg = format_google_upload_error(
        Exception('HttpError 403 "Version code 2184 has already been used."')
    )
    assert "2184" in msg
    assert "已被使用" in msg
    assert "release" in msg


def test_format_ssl() -> None:
    msg = format_google_upload_error(
        Exception("SSLError(SSLEOFError(8, 'EOF occurred in violation of protocol'))")
    )
    assert "SSL" in msg
    assert "代理" in msg


def test_retryable() -> None:
    assert is_retryable_transport_error(Exception("SSLEOFError EOF"))
    assert not is_retryable_transport_error(Exception("Version code 1 has already been used"))


def test_primary_release_on_track() -> None:
    from app.stores.google_errors import primary_release_on_track

    payload = {
        "tracks": [
            {
                "track": "production",
                "releases": [
                    {
                        "versionCodes": ["2184"],
                        "status": "completed",
                        "name": "2184",
                    }
                ],
            }
        ]
    }
    rel = primary_release_on_track(payload, "production")
    assert rel is not None
    assert rel["versionCodes"] == ["2184"]
    assert primary_release_on_track(payload, "internal") is None


def test_proxy_refused_is_friendly() -> None:
    """代理没开是最高频故障，必须给出可执行提示，而不是原始堆栈。"""
    from app.stores.google_errors import describe_transport_error, format_google_status_error

    exc = Exception(
        "HTTPSConnectionPool(host='oauth2.googleapis.com', port=443): Max retries exceeded "
        "(Caused by ProxyError('Unable to connect to proxy', "
        "NewConnectionError(\"HTTPSConnection(host='127.0.0.1', port=7892): "
        "Failed to establish a new connection: [WinError 10061]\")))"
    )
    msg = format_google_status_error(exc)
    assert "代理不可用" in msg
    assert "HTTP_PROXY" in msg
    # 不应把嵌套异常原样丢出来
    assert "NewConnectionError" not in msg
    assert describe_transport_error(exc) is not None


def test_status_error_non_transport_still_reported() -> None:
    from app.stores.google_errors import describe_transport_error, format_google_status_error

    # 非网络类错误：保留精简后的原始信息便于排查，且不误判为代理问题
    exc = Exception("some unexpected google failure")
    assert describe_transport_error(exc) is None
    assert "状态查询失败" in format_google_status_error(exc)


if __name__ == "__main__":
    test_parse_known_aabs()
    test_format_version_used()
    test_format_ssl()
    test_retryable()
    test_primary_release_on_track()
    print("ok")
