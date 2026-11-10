from __future__ import annotations

import pytest

from zuot.state import InvalidTransition, State, StateMachine


def test_happy_path() -> None:
    sm = StateMachine()
    for target in (
        State.ARMED,
        State.SELL_SIGNAL,
        State.SOLD_UNCONFIRMED,
        State.SOLD,
        State.BUYBACK_SIGNAL,
        State.BOUGHT_UNCONFIRMED,
        State.CLOSED,
    ):
        sm.transition(target)
    assert sm.state is State.CLOSED


def test_invalid_transition_raises() -> None:
    sm = StateMachine()
    with pytest.raises(InvalidTransition):
        sm.transition(State.SOLD)


def test_halt_is_terminal_except_reset() -> None:
    sm = StateMachine()
    sm.transition(State.HALTED)
    with pytest.raises(InvalidTransition):
        sm.transition(State.ARMED)
    sm.transition(State.IDLE)
    assert sm.state is State.IDLE


def test_unwind_failed_can_halt() -> None:
    sm = StateMachine(State.SOLD)
    sm.transition(State.UNWIND_FAILED)
    sm.transition(State.HALTED)
    assert sm.state is State.HALTED
