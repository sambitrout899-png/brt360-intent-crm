"""Services split out of the one app (Phase 2 of the architecture plan).

Each runs from the same image as the API with its own command, e.g.
`gunicorn app.services.training:app`. docs/module-boundaries.md has the map.
"""
