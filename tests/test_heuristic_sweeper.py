"""The heuristic values a -X/-X sweeper by what it kills on each side."""

from mulligan.agents import heuristic
from mulligan.engine import effects as fx


class _Perm:
    def __init__(self, toughness, value):
        self.toughness, self.damage, self.value, self.is_land = toughness, 0, value, False


class _View:
    seat, opponent = 0, 1
    step = None

    def __init__(self, mine, theirs):
        self._c = {0: mine, 1: theirs}

    def creatures(self, seat):
        return self._c[seat]

    def attackers(self):
        return []


def _agent():
    agent = heuristic.HeuristicAgent()
    agent._pv = lambda perm: perm.value
    return agent


def test_sweeper_prefers_killing_their_creatures():
    sweep = fx.Pump(power=-3, toughness=-3, to="all:creature")
    agent = _agent()
    theirs_die = agent._mass_value(_View([_Perm(5, 4)], [_Perm(2, 3), _Perm(3, 3)]),
                                   sweep, "all:creature")
    mine_die = agent._mass_value(_View([_Perm(2, 3), _Perm(3, 3)], [_Perm(5, 4)]),
                                 sweep, "all:creature")
    assert theirs_die > 0 > mine_die
