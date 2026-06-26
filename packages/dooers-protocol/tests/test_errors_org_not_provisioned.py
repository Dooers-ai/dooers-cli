from dooers.protocol.errors import ErrorCode


def test_org_not_provisioned_member_exists():
    assert ErrorCode.org_not_provisioned.value == "org_not_provisioned"
