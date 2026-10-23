"""Unit tests for the GitHub OAuth (3LO) config/URL helpers and the
create-or-adopt repo provisioning flow (httpx traffic mocked).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx
import pytest
from types import SimpleNamespace

from agents.github_agent import oauth3lo, provisioning


# ---------------------------------------------------------------------------
# oauth3lo.load_oauth_app_config / build_authorize_url / expires_at_from
# ---------------------------------------------------------------------------


class TestLoadOauthAppConfig:
    def test_raises_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("GITHUB_OAUTH_CLIENT_SECRET", raising=False)
        with pytest.raises(oauth3lo.OAuthNotConfigured):
            oauth3lo.load_oauth_app_config()

    def test_defaults_when_only_credentials_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "secret")
        config = oauth3lo.load_oauth_app_config()
        assert config.client_id == "cid"
        assert config.client_secret == "secret"
        assert config.scopes == oauth3lo.DEFAULT_SCOPES
        assert "api/github/oauth/callback" in config.redirect_uri

    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "secret")
        monkeypatch.setenv("GITHUB_OAUTH_REDIRECT_URI", "https://app.example/cb")
        monkeypatch.setenv("GITHUB_OAUTH_SCOPES", "repo")
        monkeypatch.setenv("GITHUB_OAUTH_SUCCESS_REDIRECT", "https://app.example/done")
        config = oauth3lo.load_oauth_app_config()
        assert config.redirect_uri == "https://app.example/cb"
        assert config.scopes == "repo"
        assert config.success_redirect_url == "https://app.example/done"


def test_build_authorize_url_carries_required_params() -> None:
    config = oauth3lo.OAuthAppConfig(
        client_id="cid",
        client_secret="secret",
        redirect_uri="https://app.example/cb",
        scopes="repo admin:repo_hook",
        success_redirect_url="https://app.example/done",
    )
    url = httpx.URL(oauth3lo.build_authorize_url(config, "state123"))
    params = dict(url.params)
    assert url.host == "github.com"
    assert params["client_id"] == "cid"
    assert params["scope"] == "repo admin:repo_hook"
    assert params["redirect_uri"] == "https://app.example/cb"
    assert params["state"] == "state123"
    assert params["allow_signup"] == "false"


class TestExpiresAtFrom:
    def test_none_without_expires_in(self) -> None:
        # GitHub OAuth App tokens don't expire by default.
        assert oauth3lo.expires_at_from({"access_token": "t"}) is None

    def test_computed_from_expires_in(self) -> None:
        before = datetime.utcnow()
        result = oauth3lo.expires_at_from({"expires_in": "3600"})
        assert result is not None
        assert timedelta(seconds=3595) <= (result - before) <= timedelta(seconds=3605)


# ---------------------------------------------------------------------------
# provisioning.parse_repo_url
# ---------------------------------------------------------------------------


class TestParseRepoUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://github.com/acme/widgets", ("acme", "widgets")),
            ("https://github.com/acme/widgets.git", ("acme", "widgets")),
            ("https://github.com/acme/widgets/", ("acme", "widgets")),
            ("git@github.com:acme/widgets.git", ("acme", "widgets")),
            ("acme/widgets", ("acme", "widgets")),
            ("  https://github.com/acme/widgets  ", ("acme", "widgets")),
        ],
    )
    def test_accepted_forms(self, url: str, expected: tuple[str, str]) -> None:
        assert provisioning.parse_repo_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "not a url",
            "https://gitlab.com/acme/widgets",
            "ftp://github.com/acme/widgets",
            "/missing-owner",
        ],
    )
    def test_rejects_garbage(self, url: str) -> None:
        with pytest.raises(ValueError):
            provisioning.parse_repo_url(url)


# ---------------------------------------------------------------------------
# provision_or_adopt_repo against a mocked GitHub API
# ---------------------------------------------------------------------------


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """Route every AsyncClient provisioning.py opens through MockTransport."""
    calls: list[httpx.Request] = []
    real_async_client = httpx.AsyncClient

    def tracking_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(tracking_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(provisioning, "httpx", SimpleNamespace(AsyncClient=fake_async_client))
    return calls


def _run(coro):
    return asyncio.run(coro)


_REPO_404 = httpx.Response(404, json={"message": "Not Found"})


def _created_repo(name: str) -> httpx.Response:
    return httpx.Response(
        201,
        json={"name": name, "default_branch": "main", "html_url": f"https://github.com/acme/{name}"},
    )


def _existing_repo() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "name": "legacy",
            "default_branch": "trunk",
            "html_url": "https://github.com/acme/legacy",
        },
    )


_PROTECTION_OK = httpx.Response(200, json={})
_HOOKS_EMPTY = httpx.Response(200, json=[])
_HOOK_CREATED = httpx.Response(201, json={"id": 1})


def test_creates_personal_repo_and_protects_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/repos/acme/widgets":
            return _REPO_404
        if request.method == "POST" and request.url.path == "/user/repos":
            body = request.read()
            assert b'"auto_init":true' in body or b'"auto_init": true' in body
            return _created_repo("widgets")
        if request.method == "PUT" and "/branches/main/protection" in request.url.path:
            return _PROTECTION_OK
        return httpx.Response(500)

    calls = _install_transport(monkeypatch, handler)
    result = _run(
        provisioning.provision_or_adopt_repo(
            access_token="tok",
            authenticated_login="acme",
            owner="acme",
            repo="widgets",
            default_branch="main",
            webhook_secret="whsec",
            webhook_target_url=None,
        )
    )
    assert result.mode == "created"
    assert result.default_branch == "main"
    assert result.warning is None
    assert [f"{c.method} {c.url.path}" for c in calls].count("POST /user/repos") == 1
    assert all(not c.url.path.endswith("/hooks") for c in calls)


def test_creates_under_org_when_owner_differs(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _REPO_404
        if request.method == "POST" and request.url.path == "/orgs/acme-org/repos":
            return _created_repo("widgets")
        if request.method == "PUT":
            return _PROTECTION_OK
        return httpx.Response(500)

    calls = _install_transport(monkeypatch, handler)
    result = _run(
        provisioning.provision_or_adopt_repo(
            access_token="tok",
            authenticated_login="someuser",
            owner="acme-org",
            repo="widgets",
            default_branch="main",
            webhook_secret="whsec",
            webhook_target_url=None,
        )
    )
    assert result.mode == "created"
    assert any(f"{c.method} {c.url.path}" == "POST /orgs/acme-org/repos" for c in calls)
    assert not any(c.url.path == "/user/repos" for c in calls)


def test_adopts_existing_repo_with_server_default_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/repos/acme/legacy":
            return _existing_repo()
        if request.method == "PUT" and "/branches/trunk/protection" in request.url.path:
            return _PROTECTION_OK
        return httpx.Response(500)

    calls = _install_transport(monkeypatch, handler)
    result = _run(
        provisioning.provision_or_adopt_repo(
            access_token="tok",
            authenticated_login="acme",
            owner="acme",
            repo="legacy",
            default_branch="main",
            webhook_secret="whsec",
            webhook_target_url=None,
        )
    )
    assert result.mode == "adopted"
    assert result.default_branch == "trunk"
    assert not any(c.method == "POST" and c.url.path.endswith("/repos") for c in calls)


def test_adoption_warns_instead_of_failing_on_protection_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _existing_repo()
        if request.method == "PUT":
            return httpx.Response(403, json={"message": "Forbidden"})
        return httpx.Response(500)

    _install_transport(monkeypatch, handler)
    result = _run(
        provisioning.provision_or_adopt_repo(
            access_token="tok",
            authenticated_login="acme",
            owner="acme",
            repo="legacy",
            default_branch="main",
            webhook_secret="whsec",
            webhook_target_url=None,
        )
    )
    assert result.warning is not None
    assert "lacks admin rights" in result.warning
    assert result.mode == "adopted"


def test_created_repo_still_fails_hard_on_protection_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _REPO_404
        if request.method == "POST":
            return _created_repo("widgets")
        if request.method == "PUT":
            return httpx.Response(403, json={"message": "Forbidden"})
        return httpx.Response(500)

    _install_transport(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        _run(
            provisioning.provision_or_adopt_repo(
                access_token="tok",
                authenticated_login="acme",
                owner="acme",
                repo="widgets",
                default_branch="main",
                webhook_secret="whsec",
                webhook_target_url=None,
            )
        )
    # The 403 must come from the protection PUT, not repo access itself.
    assert exc_info.value.response.status_code == 403
    assert "/branches/main/protection" in str(exc_info.value.request.url)


class TestWebhookEnsure:
    def _handler_existing_hook(self, hooks: list[dict], *, posted: list[bool]):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/repos/acme/legacy":
                return _existing_repo()
            if request.method == "PUT":
                return _PROTECTION_OK
            if request.method == "GET" and request.url.path == "/repos/acme/legacy/hooks":
                return httpx.Response(200, json=hooks)
            if request.method == "POST" and request.url.path == "/repos/acme/legacy/hooks":
                posted.append(True)
                return _HOOK_CREATED
            return httpx.Response(500)

        return handler

    def _adopt(self, monkeypatch: pytest.MonkeyPatch, handler, target_url: str | None):
        calls = _install_transport(monkeypatch, handler)
        return self._adopt_with_calls(calls, target_url)

    def _adopt_with_calls(self, calls, target_url: str | None):
        return _run(
            provisioning.provision_or_adopt_repo(
                access_token="tok",
                authenticated_login="acme",
                owner="acme",
                repo="legacy",
                default_branch="main",
                webhook_secret="whsec",
                webhook_target_url=target_url,
            ),
        )

    def test_no_post_without_target_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        posted: list[bool] = []
        calls = _install_transport(monkeypatch, self._handler_existing_hook([], posted=posted))
        self._adopt_with_calls(calls, None)
        assert not posted
        assert all(not c.url.path.endswith("/hooks") for c in calls)

    def test_skips_post_when_matching_hook_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        posted: list[bool] = []
        handler = self._handler_existing_hook(
            [{"config": {"url": "https://ci.example/hook"}}], posted=posted
        )
        self._adopt(monkeypatch, handler, "https://ci.example/hook")
        assert not posted

    def test_posts_when_no_matching_hook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        posted: list[bool] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/repos/acme/legacy":
                return _existing_repo()
            if request.method == "PUT":
                return _PROTECTION_OK
            if request.method == "GET" and request.url.path == "/repos/acme/legacy/hooks":
                return httpx.Response(200, json=[{"config": {"url": "https://other.example/x"}}])
            if request.method == "POST" and request.url.path == "/repos/acme/legacy/hooks":
                posted.append(True)
                assert b"whsec" in request.read()
                return _HOOK_CREATED
            return httpx.Response(500)

        self._adopt(monkeypatch, handler, "https://ci.example/hook")
        assert posted == [True]
