"""Retry, rate limit, pagination and audit behaviour of the shared HTTP client."""
import pytest

from careplatform.connectors.http import ApiClient, ApiError
from fakes import make_response


class ScriptedSession:
    """Returns responses in order, records what was asked."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(responses, now=lambda: 1000.0):
    waits = []
    s = ScriptedSession(responses)
    c = ApiClient("https://api.example.com", "test", run_id="run-1",
                  headers={"Authorization": "Bearer SECRET"}, session=s,
                  sleep=waits.append, now=now)
    return c, s, waits


def test_retries_server_error_then_succeeds():
    c, s, waits = client([make_response(503), make_response(502), make_response(200, {"ok": 1})])
    assert c.get_json("/x") == {"ok": 1}
    assert len(s.calls) == 3
    assert 1.0 <= waits[0] < 1.5 and 2.0 <= waits[1] < 2.5   # exponential backoff + jitter


def test_honours_retry_after_on_429():
    c, _, waits = client([make_response(429, headers={"Retry-After": "7"}), make_response(200, {})])
    c.get("/x")
    assert waits == [7.0]


def test_waits_for_rate_limit_reset():
    c, _, waits = client(
        [make_response(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1060"}),
         make_response(200, {})],
        now=lambda: 1000.0,
    )
    c.get("/x")
    assert waits == [61.0]   # reset - now + 1s safety


def test_rate_limit_too_far_away_fails_clearly():
    c, _, _ = client(
        [make_response(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "99999"})])
    with pytest.raises(ApiError, match="Rate limit exhausted"):
        c.get("/x")


def test_does_not_retry_client_errors():
    c, s, waits = client([make_response(404, {"message": "Not Found"})])
    with pytest.raises(ApiError) as e:
        c.get("/x")
    assert e.value.status_code == 404
    assert len(s.calls) == 1 and waits == []


def test_retries_connection_errors_and_gives_up():
    import requests
    c, s, _ = client([requests.ConnectionError("boom")] * 10)
    with pytest.raises(ApiError, match="after 6 attempts"):
        c.get("/x")
    assert len(s.calls) == 6   # 1 try + 5 retries (settings.yaml http.max_retries)


def test_every_request_has_a_timeout():
    c, s, _ = client([make_response(200, {})])
    c.get("/x")
    assert s.calls[0]["timeout"] == 30


def test_pagination_follows_link_header():
    page1 = make_response(200, [1, 2], headers={"Link": '<https://api.example.com/items?page=2>; rel="next"'})
    page2 = make_response(200, [3])
    c, s, _ = client([page1, page2])
    assert list(c.paginate("/items", params={"per_page": 2})) == [1, 2, 3]
    assert s.calls[1]["url"] == "https://api.example.com/items?page=2"
    assert s.calls[1]["params"] is None


def test_audit_log_records_every_attempt_and_never_the_token():
    c, _, _ = client([make_response(500), make_response(200, {"a": 1}, headers={"X-RateLimit-Remaining": "4999"})])
    c.get("/repos/x?secret=1")
    assert [x["attempt"] for x in c.calls] == [1, 2]
    assert c.calls[0]["error_type"] == "HTTP500"
    assert c.calls[1]["rate_limit_remaining"] == 4999
    assert c.calls[1]["endpoint"] == "/repos/x"          # no query string
    assert "SECRET" not in str(c.calls)
