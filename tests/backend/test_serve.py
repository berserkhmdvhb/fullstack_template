# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The image launcher hands Uvicorn the options the client-IP and keep-alive design relies on."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from your_service_name.backend import serve

ENTRY = "your_service_name.backend.main:app"


def test_the_defaults_are_the_safe_baseline() -> None:
    assert serve.build_options({}) == {
        "app": ENTRY,
        "host": "0.0.0.0",  # noqa: S104 - the container must listen on every interface
        "port": 8000,
        "log_level": "info",
        "proxy_headers": False,
        "server_header": False,
        "access_log": False,
        "timeout_keep_alive": 75,
        "timeout_graceful_shutdown": 30,
    }


def test_one_process_and_no_reload() -> None:
    options = serve.build_options({})
    assert not {"workers", "reload", "forwarded_allow_ips"} & options.keys()


def test_every_value_is_configurable() -> None:
    options = serve.build_options(
        {
            "BACKEND_ENTRY": "pkg.mod:application",
            "BACKEND_BIND": "127.0.0.1",
            "BACKEND_PORT": "9001",
            "BACKEND_GRACEFUL_TIMEOUT": "45",
            "BACKEND_KEEPALIVE_TIMEOUT": "120",
        }
    )
    assert options["app"] == "pkg.mod:application"
    assert options["host"] == "127.0.0.1"
    assert options["port"] == 9001  # noqa: PLR2004
    assert options["timeout_graceful_shutdown"] == 45  # noqa: PLR2004
    assert options["timeout_keep_alive"] == 120  # noqa: PLR2004


def test_an_empty_value_means_the_default() -> None:
    options = serve.build_options({"BACKEND_PORT": "", "BACKEND_BIND": ""})
    assert options["port"] == 8000  # noqa: PLR2004
    assert options["host"] == "0.0.0.0"  # noqa: S104


@pytest.mark.parametrize(
    "name", ["BACKEND_KEEPALIVE_TIMEOUT", "BACKEND_GRACEFUL_TIMEOUT", "BACKEND_PORT"]
)
@pytest.mark.parametrize("value", ["0", "-1", "soon", "1.5", " 5", "05"])
def test_a_bad_number_is_refused_by_name(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        serve.build_options({name: value})


def test_main_starts_uvicorn_in_process_with_the_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("uvicorn.run", lambda **kw: calls.append(kw))
    monkeypatch.setenv("BACKEND_KEEPALIVE_TIMEOUT", "99")
    assert serve.main() == 0
    assert calls == [serve.build_options({"BACKEND_KEEPALIVE_TIMEOUT": "99"})]
    out = capsys.readouterr().out
    assert ENTRY in out
    assert "keepalive: 99s" in out
    assert "/api/v1/health" in out


@pytest.mark.parametrize(
    "name", ["BACKEND_KEEPALIVE_TIMEOUT", "BACKEND_GRACEFUL_TIMEOUT", "BACKEND_PORT"]
)
def test_a_bad_value_exits_non_zero_without_starting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("uvicorn.run", lambda **kw: calls.append(kw))
    monkeypatch.setenv(name, "soon")
    assert serve.main() == 1
    assert name in capsys.readouterr().err
    assert not calls


def test_the_banner_keeps_the_wording_of_the_launcher_it_replaced(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("uvicorn.run", lambda **_: None)
    assert serve.main() == 0
    assert "▶️  Backend — FastAPI / Uvicorn" in capsys.readouterr().out


def test_main_does_not_validate_the_app_settings_a_second_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The app builds its Settings when uvicorn imports it; the launcher only needs the API version.
    monkeypatch.setattr("uvicorn.run", lambda **_: None)
    monkeypatch.delitem(sys.modules, "your_service_name.backend.settings", raising=False)
    assert serve.main() == 0
    assert "your_service_name.backend.settings" not in sys.modules


def test_the_banner_shows_the_api_version_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("uvicorn.run", lambda **_: None)
    monkeypatch.setenv("API_VERSION", "v7")
    assert serve.main() == 0
    assert "/api/v7/health" in capsys.readouterr().out
