"""The deal's checks run by the lab before upload (roaas.settlement.crow_checks), on real deliveries of the fake CR3A.

With CROW_REPO set, every verdict is compared with Crow's own check-delivery.ts (the adjudicator's first stage), check
by check, so the lab and the adjudicator agree on what a delivery is worth before it leaves the lab.
"""
import base64
import json
import os
import subprocess

import pytest

from roaas.core.packaging import build_delivery
from roaas.settlement import crow_checks as cc
from roaas.settlement import crow_terms as ct
from test_robot_fake import CR3A, make_task, run

pytestmark = pytest.mark.robot
URL = "https://roaas-dev.example.workers.dev/t/job-000000000000000000000000.enc"


@pytest.fixture(scope="module")
def made(fake_robot, robot_info, signer, tmp_path_factory):
    task = make_task()
    ep = run(task, fake_robot, robot_info, signer, tmp_path_factory.mktemp("episodes"))
    doc, _ = build_delivery(ep.path, telemetry_url=URL)
    fp = json.loads(doc)["manifest"]["signer"]["pubkey_sha256"]
    scope = ct.scope_for_order(task.raw)
    terms = {name: ct.terms_for(ct.template(CR3A, fp, rehearsal=(name == "rehearsal")), scope)
             for name in ("rehearsal", "production")}
    return doc, terms, signer


def failing(checks, doc: bytes):
    return {r.id for r in cc.blocking(checks, cc.evaluate(checks, doc))}


def resign(d: dict, signer) -> bytes:
    """The delivery with its manifest changed AND signed again by the lab key (a lab bug, not a forgery)."""
    text = json.dumps(d["manifest"], indent=2, sort_keys=True)
    d["manifest_text"] = text
    d["manifest_sig"] = base64.b64encode(signer.sign(text.encode())).decode()
    return json.dumps(d).encode()


def variants(made):
    doc, terms, signer = made
    good = json.loads(doc)
    out = {"good": (doc, "rehearsal"), "production-terms-on-simulator": (doc, "production")}
    d = json.loads(doc)
    d["manifest"]["recorded_at_utc"] = "20990101T000000Z"                 # object no longer the signed text
    out["manifest-object-edited"] = (json.dumps(d).encode(), "rehearsal")
    d = json.loads(doc)
    t = d["manifest"]["preview"]["t_ms"]
    t[1], t[2] = t[2], t[1]                                              # rows out of order, signed by the lab
    out["preview-out-of-order"] = (resign(d, signer), "rehearsal")
    d = json.loads(doc)
    d["manifest"]["preview"]["q3_mdeg"].pop()                            # one series shorter, signed by the lab
    out["preview-ragged"] = (resign(d, signer), "rehearsal")
    d = json.loads(doc)
    d["order_text"] = d["order_text"].replace("{", "{​", 1)          # a hidden character
    out["hidden-character"] = (json.dumps(d).encode(), "rehearsal")
    assert good["format"] == "roaas-episode/1"
    return out


def test_good_delivery_passes_and_production_terms_refuse_the_simulator(made):
    doc, terms, _ = made
    assert failing(cc.deal_checks(terms["rehearsal"]), doc) == set()
    assert failing(cc.deal_checks(terms["production"]), doc) == {"run"}
    r = {x.id: x for x in cc.evaluate(cc.deal_checks(terms["production"]), doc)}["run"]
    assert "/manifest: /recorder: /simulator: not equal to const" in r.detail


def test_each_kind_catches_its_fault(made):
    _, terms, _ = made
    v = variants(made)
    checks = cc.deal_checks(terms["rehearsal"])
    assert "manifest-signature" in failing(checks, v["manifest-object-edited"][0])
    assert failing(checks, v["preview-out-of-order"][0]) == {"preview-time"}
    assert "preview-lengths" in failing(checks, v["preview-ragged"][0])
    assert "hidden" in failing(checks, v["hidden-character"][0])


def test_intake_and_unknown_checks_fail_closed(made):
    doc, terms, _ = made
    checks = cc.deal_checks(terms["rehearsal"])
    big = json.loads(doc)
    big["padding"] = "x" * cc.MAX_DELIVERY_BYTES
    assert "intake-size" in failing(checks, json.dumps(big).encode())
    assert failing(checks, b"not json") == {"intake-json"}
    odd = [{"id": "x", "type": "passes_through", "required": True}]
    assert failing(odd, doc) == {"x"}                                  # a kind the lab does not run holds it back
    weird = [{"id": "y", "type": "json_schema", "required": True, "schema": {"$ref": "#/x"}}]
    assert failing(weird, doc) == {"y"}
    optional = [dict(odd[0], required=False)]
    assert failing(optional, doc) == set()


def test_accepts_simulator_reads_the_terms(made):
    _, terms, _ = made
    assert cc.accepts_simulator(cc.deal_checks(terms["rehearsal"])) is True
    assert cc.accepts_simulator(cc.deal_checks(terms["production"])) is False
    assert cc.accepts_simulator([{"id": "f", "type": "json_schema", "required": True, "schema": {}}]) is None
    with pytest.raises(ValueError):
        cc.deal_checks({"terms": {"acceptance": {"deterministicChecks": []}}})


def test_js_equality_rules():
    assert cc._js_equal(1, 1.0) and not cc._js_equal(True, 1) and not cc._js_equal(0, False)
    assert cc._js_equal({"a": [1, {"b": None}]}, {"a": [1.0, {"b": None}]})
    assert not cc._js_equal({"a": 1}, {"a": 1, "b": 2})


@pytest.mark.skipif(not os.environ.get("CROW_REPO"), reason="set CROW_REPO to a built clone of Crow")
def test_same_verdicts_as_crows_adjudicator_code(made, tmp_path):
    _, terms, _ = made
    for name, (doc, which) in variants(made).items():
        (tmp_path / "terms.json").write_text(json.dumps(terms[which]))
        (tmp_path / "delivery.json").write_bytes(doc)
        r = subprocess.run(["node", "examples/check-delivery.ts", "--json", str(tmp_path / "terms.json"),
                            str(tmp_path / "delivery.json")], cwd=os.environ["CROW_REPO"], capture_output=True,
                           text=True, timeout=180)
        crow = json.loads(r.stdout)
        crow_failing = {o["checkId"] for o in crow["deterministic"] if not o["passed"] and o["required"]}
        checks = cc.deal_checks(terms[which])
        ours = cc.evaluate(checks, doc)
        assert failing(checks, doc) == crow_failing, (name, crow["deterministic"], [x.line() for x in ours])
        print(f"  {name:32s} crow={crow['result']:7s} failing={sorted(crow_failing)}")
        assert (crow["result"] == "accept") == (not crow_failing), (name, crow["result"])
