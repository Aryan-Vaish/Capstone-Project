"""Weak suite -- intended tier: medium (55-70% expected kill).

Deliberately omitted: apply_events and reachable_states entirely (so their
mutants land in the uncovered bucket), the None guard, and the terminal-state
short circuit.
"""
import pytest

from order_state_machine import can_transition, is_terminal, transition


def test_can_transition_allowed():
    assert can_transition("new", "paid") is True


def test_can_transition_disallowed():
    assert can_transition("new", "shipped") is False


def test_transition_returns_target():
    assert transition("new", "paid") == "paid"


def test_transition_illegal_raises():
    with pytest.raises(ValueError):
        transition("new", "delivered")


def test_is_terminal_true():
    assert is_terminal("delivered") is True
