import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from domain_rules import DOMAIN_NAMES, DOMAINS, get_domain_prompt, render_domain_prompt  # noqa: E402


def test_counts_match_blog_table():
    assert len(DOMAINS) == 11
    assert sum(len(d["types"]) for d in DOMAINS.values()) == 40


def test_every_type_complete_and_unique():
    names = []
    for d in DOMAINS.values():
        assert d["cautions"]
        for t in d["types"]:
            assert t["checks"] and t["question"].endswith("?") and t["examples"], t["name"]
            names.append(t["name"])
    assert len(names) == len(set(names))


def test_render_and_unknown_domain():
    p = get_domain_prompt(["업무 운영", "협업·문서"])
    assert "예약·대여" in p and "알림·메시지" in p and "재무" not in p
    assert render_domain_prompt("재무").startswith("[업무 영역: 재무]")
    try:
        get_domain_prompt(["없는영역"])
    except KeyError:
        return
    raise AssertionError


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
