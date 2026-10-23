import io, json, logging, socket, sys, urllib.parse
import httpx, pytest, structlog
from app import logging_config as lc

K = "FAKEodds0123456789abcdef"; C = "FAKEcfbd0123456789abcdef"

@pytest.fixture(autouse=True)
def no_net(monkeypatch):
    def boom(*a, **k): raise RuntimeError("network blocked")
    monkeypatch.setattr(socket.socket, "connect", boom)

def root_capture():
    lc.configure_logging()
    buf = io.StringIO(); h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(name)s %(message)s"))
    logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.DEBUG)
    return buf

URL = f"https://api.the-odds-api.com/v4/sports/x/odds?apiKey={K}&regions=us"
CASES = {
  "query_apiKey": URL,
  "query_mixed_APIKEY": f"?APIKEY={K}",
  "query_api_key": f"?api_key={K}",
  "url_encoded_whole": urllib.parse.quote(URL, safe=""),
  "url_encoded_value_eq": f"apiKey%3D{K}",
  "bearer_mixed": f"authorization: bEaReR {C}",
  "bearer_bytes_tuple": str([(b"authorization", f"Bearer {C}".encode())]),
  "json_body_apiKey": json.dumps({"apiKey": K}),
  "x_api_key_header": f"x-api-key: {K}",
  "dict_repr_params": str({"apiKey": K, "regions": "us"}),
}

@pytest.mark.parametrize("name", CASES)
def test_regex(name):
    out = lc.redact_secrets(CASES[name])
    assert K not in out and C not in out, out

def test_stdlib_child_and_thirdparty_loggers():
    buf = root_capture()
    for n in ("httpx", "httpcore.http11", "urllib3.connectionpool", "uvicorn.access",
              "uvicorn.error", "sentry_sdk.errors", "app.some.child", "random3p"):
        logging.getLogger(n).warning("GET %s", URL)
    assert K not in buf.getvalue()

def test_httpx_real_info_line():
    buf = root_capture()
    t = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    with httpx.Client(transport=t) as c:
        c.get("https://api.the-odds-api.com/v4/x", params={"apiKey": K})
    v = buf.getvalue(); assert "apiKey=REDACTED" in v and K not in v

def test_exception_traceback_stdlib():
    buf = root_capture()
    req = httpx.Request("GET", URL); resp = httpx.Response(500, request=req)
    try: resp.raise_for_status()
    except httpx.HTTPStatusError: logging.getLogger("x").exception("boom")
    assert K not in buf.getvalue()

def test_exception_args_object():
    buf = root_capture()
    e = httpx.HTTPStatusError(f"err {URL}", request=httpx.Request("GET", URL), response=httpx.Response(401))
    logging.getLogger("x").error("fail %r", e)
    assert K not in buf.getvalue()

def test_uvicorn_access_formatter():
    from uvicorn.logging import AccessFormatter
    lc.configure_logging()
    buf = io.StringIO(); h = logging.StreamHandler(buf); h.setFormatter(AccessFormatter())
    lg = logging.getLogger("uvicorn.access"); lg.addHandler(h); lg.setLevel(logging.INFO)
    lg.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4", "GET", f"/api?apiKey={K}", "1.1", 200)
    assert K not in buf.getvalue()

def _structlog_out(capsys, fn):
    lc.configure_logging(); structlog.reset_defaults(); lc.configure_logging()
    fn(structlog.get_logger("app.x")); return capsys.readouterr().out

def test_structlog_kwarg(capsys):
    out = _structlog_out(capsys, lambda l: l.warning("odds_fail", error=f"Client error for url {URL}"))
    assert K not in out, "structlog kwarg leaked"

def test_structlog_event_and_exc(capsys):
    def f(l):
        try: raise RuntimeError(URL)
        except RuntimeError: l.exception("boom")
    out = _structlog_out(capsys, f)
    assert K not in out, "structlog traceback leaked"

def test_sentry_event_scrub():
    import sentry_sdk
    events = []
    class T(sentry_sdk.transport.Transport):
        def capture_envelope(self, env):
            events.append(env.serialize().decode("utf-8", "replace"))
    sentry_sdk.init(dsn="https://a@b.invalid/1", transport=T(), default_integrations=True)
    lc.install_http_secret_redaction()
    try: raise RuntimeError(URL)
    except RuntimeError: logging.getLogger("x").exception("boom")
    sentry_sdk.flush()
    assert events and not any(K in e for e in events), "sentry event leaked"

def test_structlog_exception_kwarg(capsys):
    out = _structlog_out(capsys, lambda l: l.warning("odds_fail", error=RuntimeError(URL)))
    assert K not in out, "structlog exception kwarg leaked"
    assert "apiKey=REDACTED" in out

def test_structlog_httpx_url_kwarg(capsys):
    out = _structlog_out(capsys, lambda l: l.warning("odds_fail", url=httpx.URL(URL)))
    assert K not in out, "structlog httpx.URL kwarg leaked"
    assert "apiKey=REDACTED" in out
