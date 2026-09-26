"""zellij view layer (design §10): windows onto the ships' seats.

- ``attach``: ``seat-attach`` — follow a seat's current shift and ``claude attach`` to it
- ``layout``: one zellij tab per ship, one pane per seat (KDL)
- ``shipfiles``: the only place that reads the ship folder (roster.json, team.yaml)

Kept independent of the P0 modules on purpose; wiring into the ``yamato`` CLI
comes after P0 is merged.
"""
