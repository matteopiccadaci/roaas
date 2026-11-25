"""Crow deal terms generated from the CR3A adapter (phase 4). The adjudicator-code run is in test_robot_fake.py."""
import json
import re

import pytest

from conftest import KIT
from roaas.robots import get_adapter
from roaas.settlement import crow_terms as ct

CR3A = get_adapter("dobot_cr3a")
FP = "0e811c503e02aa59faa288365e0f80023603ee9cfcd64f3ac4e67200960289af"


def placeholders(node):
    text = json.dumps(node)
    return set(re.findall(r"\{\{([A-Za-z][A-Za-z0-9]*)(?:\.length)?\}\}", text))


def test_template_shape_and_limits():
    t = ct.template(CR3A, FP)
    assert t["deliverable"]["format"] == "application/json"
    assert 1 <= len(t["checks"]) <= 20 and all(c["required"] for c in t["checks"])
    assert len({c["id"] for c in t["checks"]}) == len(t["checks"])
    assert ct.floats_in(t) == []                                     # Crow ADR-0022: integers only
    assert placeholders(t) <= set(ct.SCOPE_RULES)                    # only fields the scope gives
    sig = next(c for c in t["checks"] if c["type"] == "signature_valid")
    assert sig["publicKey"]["spkiSha256"] == f"sha256:{FP}" and sig["message"]["jsonEquals"] == "/manifest"
    kinds = {c["type"] for c in t["checks"]}
    assert {"json_schema", "signature_valid", "equal_lengths", "monotonic_column", "hidden_characters_max"} <= kinds


def test_rehearsal_terms_only_accept_the_simulator():
    def simulator_const(t):
        run = next(c for c in t["checks"] if c["id"] == "run")
        return run["schema"]["properties"]["manifest"]["properties"]["recorder"]["properties"]["simulator"]["const"]
    assert simulator_const(ct.template(CR3A, FP)) is False
    r = ct.template(CR3A, FP, rehearsal=True)
    assert simulator_const(r) is True and r["deliverable"]["description"].startswith("REHEARSAL ONLY")


def test_bad_fingerprint_refused():
    with pytest.raises(ValueError):
        ct.template(CR3A, "ABC")


def test_adapter_config_guards_the_delivery_window():
    cfg = ct.adapter_config("https://roaas.example.app")
    assert cfg["sellerApi"]["quote"] == "https://roaas.example.app/api/crow/quote"
    assert cfg["timing"]["deliverWithinSec"] > 7200 and cfg["scope"] == ct.SCOPE_RULES
    with pytest.raises(ValueError):
        ct.adapter_config("https://x.app", timing={"deliverWithinSec": 7200})


def test_scope_for_orders():
    raw = (KIT / "example_order.json").read_bytes()
    scope = ct.scope_for_order(raw)
    assert scope["task"] == "joint_waypoints" and scope["orderText"] == raw.decode()
    assert scope["nonce"] == "3f9a6c1e8b2d47f0a5c9e1b7d3f20864" and len(scope["orderSha256"]) == 64
    with pytest.raises(ValueError):                                  # a 12-hex test nonce cannot go to Crow
        ct.scope_for_order((KIT / "order_microtest.json").read_bytes())


def test_fill_matches_the_adapter_semantics():
    scope = {"a": "x", "n": 3, "l": ["p", "q"]}
    assert ct.fill("{{n}}", scope) == 3                              # a whole placeholder keeps the value's type
    assert ct.fill("n={{n}}, l={{l}}, {{l.length}}", scope) == "n=3, l=p, q, 2"
    assert ct.fill("\\{{a}}", scope) == "{{a}}"                      # escaped
    assert ct.fill({"k": ["{{a}}"]}, scope) == {"k": ["x"]}


def test_terms_for_an_order():
    raw = (KIT / "example_order.json").read_bytes()
    terms = ct.terms_for(ct.template(CR3A, FP), ct.scope_for_order(raw))
    assert placeholders(terms) == set()
    assert terms["acceptance"]["rubric"] == {"passThreshold": 0, "criteria": []}
    order_check = next(c for c in terms["acceptance"]["deterministicChecks"] if c["id"] == "order-text")
    assert order_check["schema"]["properties"]["order_text"]["const"] == raw.decode()


def test_offer_catalogue_is_generated_from_the_adapter():
    cat = ct.offers_catalog(CR3A, {"token": "T"}, "https://roaas.example.app")
    assert [o["task"] for o in cat["offers"]] == ["joint_waypoints", "repeatability"]
    jw = cat["offers"][0]
    env = CR3A.public_limits()["allowed_envelope_deg"]
    assert jw["order"]["params_schema"]["properties"]["waypoints"]["items"]["prefixItems"][0] == \
        {"type": "number", "minimum": env[0][0], "maximum": env[0][1]}
    assert cat["offers"][1]["order"]["params_schema"]["properties"]["repetitions"]["maximum"] == 10
