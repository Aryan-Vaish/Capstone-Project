"""Order lifecycle state machine.

Dict-dispatch control flow, identity checks against None, membership tests, and
a try/except/else block -- the exception-handler context flag has almost no
representation elsewhere in the corpus.
"""

TRANSITIONS = {
    "new": ("paid", "cancelled"),
    "paid": ("shipped", "refunded"),
    "shipped": ("delivered", "returned"),
    "delivered": (),
    "cancelled": (),
    "refunded": (),
    "returned": ("refunded",),
}

TERMINAL_STATES = {"delivered", "cancelled", "refunded"}


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def can_transition(current: str, target: str) -> bool:
    if current not in TRANSITIONS:
        return False
    return target in TRANSITIONS[current]


def transition(current: str, target: str) -> str:
    if current is None:
        raise ValueError("Current state is required")
    if not can_transition(current, target):
        raise ValueError("Illegal transition")
    return target


def apply_events(start: str, events: list) -> str:
    state = start
    for event in events:
        try:
            state = transition(state, event)
        except ValueError:
            continue
        else:
            if is_terminal(state):
                break
    return state


def reachable_states(start: str, depth: int = 2) -> set:
    seen = {start}
    frontier = [start]
    for _ in range(depth):
        nxt = []
        for state in frontier:
            for target in TRANSITIONS.get(state, ()):
                if target not in seen:
                    seen.add(target)
                    nxt.append(target)
        frontier = nxt
    return seen
