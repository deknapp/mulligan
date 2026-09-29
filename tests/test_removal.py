"""What counts as removal (the pick helper's tag, the site's filters)."""

from mulligan.site.tools import is_removal


def test_spells_triggers_and_abilities_that_answer_a_creature():
    assert is_removal({"targets": ["creature"], "effects": [{"op": "destroy"}]})
    assert is_removal({"targets": ["creature"],
                       "effects": [{"op": "pump", "power": -3, "toughness": -3}]})
    assert is_removal({"targets": ["creature"], "effects": [
        {"op": "set_base_pt", "power": 0, "toughness": 0, "to": "target"}]})
    assert is_removal({"triggers": [{"when": "etb", "targets": ["nonland:theirs"],
                                     "effects": [{"op": "exile"}]}]})
    assert is_removal({"abilities": [{"zone": "hand", "targets": ["creature:kw=flying"],
                                      "effects": [{"op": "destroy"}]}]})
    assert is_removal({"targets": ["any_target"], "effects": [{"op": "damage", "amount": 3}]})
    assert is_removal({"effects": [{"op": "sacrifice", "filter": "creature|planeswalker",
                                    "who": "each_opponent"}]})
    assert is_removal({"modes": [{"targets": ["creature"], "effects": [{"op": "damage"}]}]})


def test_your_own_things_and_noncreatures_are_not_removal():
    assert not is_removal({"abilities": [{"targets": ["permanent:yours,other"],
                                          "effects": [{"op": "bounce"}]}]})
    assert not is_removal({"targets": ["permanent:zone=graveyard,yours"],
                           "effects": [{"op": "bounce"}]})
    assert not is_removal({"triggers": [{"modes": [{"targets": ["nonland:!type=creature"],
                                                    "effects": [{"op": "destroy"}]}]}]})
    assert not is_removal({"targets": ["creature"],
                           "effects": [{"op": "pump", "power": 2, "toughness": 2}]})
