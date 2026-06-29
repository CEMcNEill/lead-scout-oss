"""Router dispatch and hard-stop checks. Both are deterministic, off-data."""

from pathlib import Path

import pytest

from engine.hardstops import HardStopConfig, check_hard_stops
from engine.router import Router

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "qualifiers" / "registry.yaml"
HARD_STOPS = REPO / "config" / "hard_stops.yaml"


# --- router ---------------------------------------------------------------


@pytest.fixture
def router() -> Router:
    return Router.from_yaml(REGISTRY)


def _rec(**lead) -> dict:
    base = {"lead": {"email": "a@acme.com", "domain": "acme.com"}}
    base.update({k: v for k, v in lead.items() if k != "lead"})
    if "lead" in lead:
        base["lead"].update(lead["lead"])
    return base


@pytest.mark.parametrize("signal", [
    "big_fish", "mrr_fit", "job_switcher", "spend_spike", "startup_rolloff",
    "new_customer", "recent_fundraise", "lookalike", "trust_center_nda",
    "unmanaged_ticket", "scale_activation", "eng_headcount_growth",
])
def test_route_product_led_signal_1to1(router, signal):
    r = router.route({"category": "product-led", "signal": signal, "lead": {}})
    assert r.qualifier == signal
    assert r.lead_type == signal


def test_route_product_led_unmapped_signal_to_fallback(router):
    # a product-led lead whose matching_criteria did not map -> fallback
    r = router.route({"category": "product-led", "signal": None, "lead": {}})
    assert r.qualifier == "plg_unclassified"


def test_route_onboarding(router):
    assert router.route({"category": "onboarding", "lead": {}}).qualifier == "onboarding"


def test_route_onboarding_referral_is_product_led_to_onboarding(router):
    # an onboarding referral is product-led (active customer) but handled by the
    # activation-led onboarding qualifier
    r = router.route({"category": "product-led", "signal": "onboarding_referral", "lead": {}})
    assert r.qualifier == "onboarding"
    assert r.lead_type == "onboarding"


def test_route_inbound_by_category(router):
    assert router.route({"category": "inbound", "lead": {}}).qualifier == "inbound"


def test_route_inbound_by_message_presence(router):
    r = router.route({"inbound_message": "we want to monitor LLM costs", "lead": {}})
    assert r.qualifier == "inbound"


def test_route_catch_all(router):
    # unknown category, no message -> falls through to the catch-all (inbound)
    r = router.route({"category": None, "lead": {}})
    assert r.qualifier == "inbound"
    assert r.lead_type == "inbound"


# --- hard stops -----------------------------------------------------------


@pytest.fixture
def hs_config() -> HardStopConfig:
    return HardStopConfig.from_yaml(HARD_STOPS)


def test_no_hard_stops_for_clean_lead(hs_config):
    assert check_hard_stops(_rec(), hs_config) == []


def test_do_not_contact(hs_config):
    assert "do_not_contact" in check_hard_stops(_rec(lead={"do_not_contact": True}), hs_config)


def test_competitor_by_domain(hs_config):
    rec = _rec(lead={"email": "p@mixpanel.com", "domain": "mixpanel.com"})
    assert "competitor" in check_hard_stops(rec, hs_config)


def test_competitor_by_flag(hs_config):
    assert "competitor" in check_hard_stops(_rec(lead={"is_competitor": True}), hs_config)


def test_personal_address(hs_config):
    rec = _rec(lead={"email": "someone@gmail.com", "domain": "gmail.com"})
    stops = check_hard_stops(rec, hs_config)
    assert "personal_address" in stops


def test_teammate_managed(hs_config):
    assert "teammate_managed" in check_hard_stops(_rec(lead={"owner_other_rep": True}), hs_config)


def test_multiple_stops_accumulate(hs_config):
    rec = _rec(lead={"email": "x@gmail.com", "domain": "gmail.com", "do_not_contact": True})
    stops = check_hard_stops(rec, hs_config)
    assert "do_not_contact" in stops and "personal_address" in stops
