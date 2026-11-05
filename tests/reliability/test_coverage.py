from reliability.coverage import CATEGORIES, TAGS, counts, gaps, load_sets, render


def case(category, *tags):
    return {"truth": {"category": category}, "tags": list(tags)}


def test_counts_categories_and_tags():
    categories, tags = counts(
        [case("bad_deploy", "clean"), case("bad_deploy", "clean", "regional")]
    )
    assert categories["bad_deploy"] == 2 and tags["clean"] == 2 and tags["regional"] == 1


def test_gaps_list_everything_under_the_minimum():
    cases = [case(category, *TAGS) for category in CATEGORIES for _ in range(3)]
    assert gaps(cases) == []
    assert "category traffic_surge: 2" in gaps([c for c in cases if c is not cases[-4]])


def test_assignment_tags_are_from_the_vocabulary():
    for c in load_sets()["assignment"]:
        assert set(c["tags"]) <= set(TAGS), c["incident"]["id"]


def test_report_has_a_row_per_category_and_tag():
    text = render({"a": [case("unknown", "clean")]})
    assert all(f"| {name} |" in text for name in (*CATEGORIES, *TAGS))
