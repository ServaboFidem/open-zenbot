import pytest

from src.memory.registry import OrgRegistry, PeopleRegistry


def test_owner_round_trips(tmp_path):
    p = tmp_path / "people.json"
    PeopleRegistry(p).set_owner("Alex Rivera", ["Alex"])
    owner = PeopleRegistry(p).owner()
    assert owner.name == "Alex Rivera"
    assert owner.is_owner
    assert owner.aliases == ["Alex"]
    assert owner.tier == "close"


def test_find_matches_aliases_case_insensitively(tmp_path):
    r = PeopleRegistry(tmp_path / "people.json")
    r.add("Dave Kim")
    r.add_alias("Dave Kim", "DK")
    assert r.find("dk").name == "Dave Kim"
    assert r.find("DAVE KIM").name == "Dave Kim"
    assert r.find("nobody") is None


def test_add_never_lowers_tier(tmp_path):
    r = PeopleRegistry(tmp_path / "people.json")
    r.add("Jamie Rivera", relation="spouse", tier="close")
    e = r.add("Jamie Rivera", tier="known")
    assert e.tier == "close"
    assert e.relation == "spouse"


def test_promote(tmp_path):
    r = PeopleRegistry(tmp_path / "people.json")
    r.add("Dave Kim")
    r.promote("Dave Kim")
    assert PeopleRegistry(tmp_path / "people.json").find("Dave Kim").tier == "close"


def test_for_text_scopes_known_people(tmp_path):
    r = PeopleRegistry(tmp_path / "people.json")
    r.add("Jamie Rivera", relation="spouse", tier="close")
    r.add("Dave Kim")
    r.add_alias("Dave Kim", "DK")

    def names(t):
        return {e.name for e in r.for_text(t)}

    assert names("nothing here") == {"Jamie Rivera"}
    assert names("lunch with Dave Kim") == {"Jamie Rivera", "Dave Kim"}
    assert names("ask dk about it") == {"Jamie Rivera", "Dave Kim"}
    assert names("Davenport called") == {"Jamie Rivera"}


def test_org_registry_slugs(tmp_path):
    p = tmp_path / "orgs.json"
    r = OrgRegistry(p)
    assert r.add("Acme Widgets", "owns").slug == "acme_widgets"
    r2 = OrgRegistry(p)
    assert r2.get("acme_widgets").name == "Acme Widgets"
    assert r2.find("acme widgets").slug == "acme_widgets"
    assert len(r2.all()) == 1


def test_corrupt_file_is_set_aside(tmp_path):
    p = tmp_path / "people.json"
    p.write_text("{garbage", encoding="utf-8")
    r = PeopleRegistry(p)
    assert r.all() == []
    assert (tmp_path / "people.json.bad").read_text(encoding="utf-8") == "{garbage"


def test_empty_names_and_aliases_rejected(tmp_path):
    r = PeopleRegistry(tmp_path / "people.json")
    with pytest.raises(ValueError):
        r.add("  ")
    with pytest.raises(ValueError):
        r.set_owner("Alex Rivera", [""])
    with pytest.raises(ValueError):
        r.set_owner(" ", [])
    r.add("Dave Kim")
    with pytest.raises(ValueError):
        r.add_alias("Dave Kim", " ")
    assert r.find("Dave Kim").aliases == []


def test_rename_owner_keeps_the_old_name_as_an_alias(tmp_path):
    r = PeopleRegistry(tmp_path / "people.json")
    assert r.rename_owner("Alex Rivera") is None  # no owner yet
    r.set_owner("Alex", [])
    r.add("Alex Lee")
    assert r.rename_owner("Alex Lee") is None and r.owner().name == "Alex"  # someone else's name
    e = r.rename_owner("Alex Rivera")
    assert e.name == "Alex Rivera" and e.aliases == ["Alex"] and r.find("Alex") is e
    assert sorted(p.name for p in PeopleRegistry(r.path).all()) == ["Alex Lee", "Alex Rivera"]


def test_non_ascii_orgs_do_not_collide(tmp_path):
    reg = OrgRegistry(tmp_path / "orgs.json")
    reg.add("株式会社", "owns")
    reg.add("会社株式", "runs")
    assert sorted(o.name for o in reg.all()) == ["会社株式", "株式会社"]
