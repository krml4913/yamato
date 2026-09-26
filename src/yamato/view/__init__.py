"""zellij view layer (design §10): windows onto the ships' seats. Run as ``yamato view ...``.

- ``attach``: ``yamato view attach`` — follow a seat's current shift and ``claude attach`` to it
- ``layout``: ``yamato view layout`` — one zellij tab per ship, one pane per seat (KDL)
- ``cli``: the ``view`` subcommand's parser and runner (``yamato.cli`` only calls these two)

Nothing here reads the ship folder itself: ships, seats and the current shift come
from ``util.resolve_ship``, ``team.runtime_team`` and ``roster``.
"""
