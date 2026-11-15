import re

from src.memory.graph import SHARED
from src.memory.stores import SHARED_TAG, is_org, org_store, slugify


def test_org_store_and_is_org():
    assert org_store("acme") == "org:acme"
    assert is_org("org:acme")
    assert not is_org("personal")


def test_slugify():
    assert slugify("Acme Corp.") == "acme_corp"
    assert re.fullmatch(r"org_[0-9a-f]{8}", slugify("株式会社"))


def test_shared_tag_matches_graph():
    assert SHARED_TAG == SHARED
