# packages/dooers-cli/tests/test_org.py
from dooers.cli.org import resolve_org

ORGS = [{"organizationId": "o1", "name": "A"}, {"organizationId": "o2", "name": "B"}]


def test_explicit_flag_wins():
    assert resolve_org(orgs=ORGS, explicit="o2", default=None, prompt=lambda o: "o1") == "o2"


def test_saved_default_used():
    assert resolve_org(orgs=ORGS, explicit=None, default="o1", prompt=lambda o: "o2") == "o1"


def test_single_org_auto():
    one = [ORGS[0]]
    assert resolve_org(orgs=one, explicit=None, default=None, prompt=lambda o: "x") == "o1"


def test_multiple_prompts():
    assert resolve_org(orgs=ORGS, explicit=None, default=None, prompt=lambda o: "o2") == "o2"
