from pathlib import Path

import pytest

from tbmcp.policy import FolderRule, allows, grants, load_rules


def test_load_rules(tmp_path: Path):
    assert load_rules(tmp_path / "missing.toml") == ()
    path = tmp_path / "config.toml"
    path.write_text('[[folders]]\npath = "@BKToDo/"\naccount = "account1"\nallow = ["move_in"]\n')
    assert load_rules(path) == (FolderRule("/@BKToDo", frozenset({"move_in"}), "account1"),)

    for bad, message in (
        ('[[folders]]\npath = "/x"\nallow = ["typo"]', "typo"),
        ('[[folders]]\nallow = ["move_in"]', "path"),
        ("[[folders]\n", "config.toml"),
        ('[[folders]]\npath = " "\nallow = []', "path"),
        ('[[folders]]\npath = "/x"\nallow = "move_in"', "allow"),
        ('[[folders]]\npath = "/x"\naccount = " "\nallow = []', "account"),
    ):
        path.write_text(bad)
        with pytest.raises(SystemExit, match=message):
            load_rules(path)


def test_allows_uses_segments_and_account():
    rules = (FolderRule("/@BKToDo", frozenset({"move_in"}), "account1"),)
    assert grants(rules, "move_in")
    assert allows(rules, "move_in", {"path": "/@BKToDo", "accountId": "account1"})
    assert allows(rules, "move_in", {"path": "/@BKToDo/sub", "accountId": "account1"})
    assert not allows(rules, "move_in", {"path": "/@BKToDoOld", "accountId": "account1"})
    assert not allows(
        rules, "move_in", {"path": "/@BKToDo", "accountId": "account1"}, strictly_below=True
    )
    assert not allows(rules, "move_in", {"path": "/@BKToDo", "accountId": "account2"})
    assert not allows(rules, "move_in", {"accountId": "account1"})
    assert not allows(rules, "move_out", {"path": "/@BKToDo", "accountId": "account1"})
    assert allows((FolderRule("/", frozenset({"move_in"})),), "move_in", {"path": "/Inbox"})
