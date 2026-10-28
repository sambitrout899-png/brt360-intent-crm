"""The BIM service: bulk loads and the BRT_ID register (/api/bim) - in its
own container so a large load never slows the CRM down."""

from __future__ import annotations

from fastapi import APIRouter

PREFIXES = ("/api/bim/",)


def routers() -> list[APIRouter]:
    from app.experience import routes_bim

    return [routes_bim.router]
