"""Regression: an expired Fidelity session redirects to a SIGN-IN url, not a
"login" one. Detecting only "login" produced a false "connected" status and made
the brain scrape the login page (503), so no trade alerts ever fired.
"""
from web.api.fidelity import _is_login_url, _is_authenticated_url

SIGNIN = "https://digital.fidelity.com/prgw/digital/signin/retail?AuthRedUrl=x"
LOGIN = "https://digital.fidelity.com/ftgw/digital/login/full-page"
POSITIONS = "https://digital.fidelity.com/ftgw/digital/portfolio/positions"
SUMMARY = "https://digital.fidelity.com/ftgw/digital/portfolio/summary"


def test_signin_url_is_login_not_authenticated():
    assert _is_login_url(SIGNIN)
    assert not _is_authenticated_url(SIGNIN)


def test_login_url_detected():
    assert _is_login_url(LOGIN)
    assert not _is_authenticated_url(LOGIN)


def test_authenticated_pages():
    for u in (POSITIONS, SUMMARY):
        assert not _is_login_url(u)
        assert _is_authenticated_url(u)


def test_offsite_not_authenticated():
    assert not _is_authenticated_url("https://www.google.com")
