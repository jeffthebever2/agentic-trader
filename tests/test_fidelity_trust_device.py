"""Trust-device persistence fix: tick the 'remember this device' checkbox across
ALL frames (the box often renders in an iframe → main-frame-only search missed it,
so the device was never trusted and silent re-login could never skip 2FA), plus
the optional autonomous-TOTP helper for unattended re-login.
"""
import asyncio

import pytest

import web.api.fidelity as f


# ── async Playwright fakes ────────────────────────────────────────────────────
class FakeLocator:
    def __init__(self, present: bool, checked: bool = False, cid: str = "dom-trust-device-checkbox"):
        self._present = present
        self._checked = checked
        self._cid = cid

    @property
    def first(self):
        return self

    async def wait_for(self, state="attached", timeout=0):
        if not self._present:
            raise RuntimeError("not found")

    async def is_checked(self):
        return self._checked

    async def check(self, force=False, timeout=0):
        if not self._present:
            raise RuntimeError("not found")
        self._checked = True

    async def get_attribute(self, name):
        return self._cid if name == "id" else None

    async def click(self, timeout=0):
        if not self._present:
            raise RuntimeError("not found")
        self._checked = True


class FakeFrame:
    """A frame whose trust checkbox is present only if `has_box`."""
    def __init__(self, has_box: bool, checked: bool = False):
        self.box = FakeLocator(has_box, checked)
        self._empty = FakeLocator(False)

    def locator(self, sel):
        # The id selector (and label[for=...]) resolve to the box; others empty.
        if "trust-device-checkbox" in sel or sel.startswith("label[for="):
            return self.box
        return self._empty

    def get_by_role(self, role, name=None):
        return self._empty


class FakePage:
    def __init__(self, frames):
        self._frames = frames

    @property
    def frames(self):
        return self._frames

    # main-frame surface (empty here — the box lives in a child frame)
    def locator(self, sel):
        return FakeLocator(False)

    def get_by_role(self, role, name=None):
        return FakeLocator(False)


# ── trust-device frame scanning ───────────────────────────────────────────────
def test_tick_trust_in_frame_force_checks():
    fr = FakeFrame(has_box=True, checked=False)
    assert asyncio.run(f._tick_trust_in_frame(fr)) is True
    assert fr.box._checked is True


def test_tick_trust_in_frame_already_checked():
    fr = FakeFrame(has_box=True, checked=True)
    assert asyncio.run(f._tick_trust_in_frame(fr)) is True


def test_tick_trust_in_frame_absent():
    fr = FakeFrame(has_box=False)
    assert asyncio.run(f._tick_trust_in_frame(fr)) is False


def test_check_trust_device_finds_box_in_child_iframe():
    # THE BUG: main frame has no checkbox; it lives in a child iframe.
    child = FakeFrame(has_box=True)
    page = FakePage(frames=[child])         # frames excludes the Page, like Playwright
    assert asyncio.run(f._check_trust_device(page)) is True
    assert child.box._checked is True


def test_check_trust_device_returns_false_when_no_frame_has_box():
    page = FakePage(frames=[FakeFrame(has_box=False)])
    assert asyncio.run(f._check_trust_device(page)) is False


# ── autonomous TOTP helper ─────────────────────────────────────────────────────
def test_totp_none_when_unset(monkeypatch):
    monkeypatch.delenv("FIDELITY_TOTP_SECRET", raising=False)
    assert f._fidelity_totp_code() is None


def test_totp_generates_six_digits(monkeypatch):
    monkeypatch.setenv("FIDELITY_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    code = f._fidelity_totp_code()
    assert code and code.isdigit() and len(code) == 6


def test_totp_tolerates_spaces(monkeypatch):
    monkeypatch.setenv("FIDELITY_TOTP_SECRET", "JBSW Y3DP EHPK 3PXP")
    assert f._fidelity_totp_code() is not None


def test_totp_invalid_secret_returns_none(monkeypatch):
    monkeypatch.setenv("FIDELITY_TOTP_SECRET", "not!base32!")
    assert f._fidelity_totp_code() is None
