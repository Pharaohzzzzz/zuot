"""Per-symbol trading state machine for a sell-first T."""

from __future__ import annotations

from enum import StrEnum


class State(StrEnum):
    IDLE = "IDLE"
    ARMED = "ARMED"
    SELL_SIGNAL = "SELL_SIGNAL"
    SOLD_UNCONFIRMED = "SOLD_UNCONFIRMED"
    SOLD = "SOLD"
    BUYBACK_SIGNAL = "BUYBACK_SIGNAL"
    BOUGHT_UNCONFIRMED = "BOUGHT_UNCONFIRMED"
    CLOSED = "CLOSED"
    PAUSED = "PAUSED"
    HALTED = "HALTED"
    UNWIND_FAILED = "UNWIND_FAILED"


ALLOWED: dict[State, set[State]] = {
    State.IDLE: {State.ARMED, State.PAUSED, State.HALTED},
    State.ARMED: {State.SELL_SIGNAL, State.IDLE, State.PAUSED, State.HALTED},
    State.SELL_SIGNAL: {State.SOLD_UNCONFIRMED, State.ARMED, State.PAUSED, State.HALTED},
    State.SOLD_UNCONFIRMED: {State.SOLD, State.ARMED, State.PAUSED, State.HALTED},
    State.SOLD: {
        State.BUYBACK_SIGNAL,
        State.PAUSED,
        State.HALTED,
        State.UNWIND_FAILED,
    },
    State.BUYBACK_SIGNAL: {
        State.BOUGHT_UNCONFIRMED,
        State.SOLD,
        State.PAUSED,
        State.HALTED,
    },
    State.BOUGHT_UNCONFIRMED: {State.CLOSED, State.UNWIND_FAILED, State.HALTED},
    State.CLOSED: {State.ARMED, State.IDLE, State.PAUSED, State.HALTED},
    State.PAUSED: {State.IDLE, State.ARMED, State.HALTED},
    State.HALTED: {State.IDLE},
    State.UNWIND_FAILED: {State.HALTED, State.IDLE},
}


class InvalidTransition(RuntimeError):
    pass


class StateMachine:
    def __init__(self, state: State = State.IDLE) -> None:
        self.state = state

    def can(self, target: State) -> bool:
        return target in ALLOWED[self.state]

    def transition(self, target: State) -> State:
        if not self.can(target):
            raise InvalidTransition(f"{self.state.value} -> {target.value} is not allowed")
        self.state = target
        return self.state
