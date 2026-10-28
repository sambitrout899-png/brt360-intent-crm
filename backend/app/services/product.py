"""The product service: the catalog, Product Modeller, configurator and
product configuration, and the TMF product APIs. (The intent audit feed at
/api/audit lives in the catalog's routes, so it comes along.)"""

from __future__ import annotations

from fastapi import APIRouter

PREFIXES = (
    "/api/catalog", "/api/configurator/", "/api/audit", "/api/admin/product-model", "/api/admin/product-config",
    "/api/admin/product-classes",
)
# Served, but not published by the web container - the TMF APIs never were;
# other systems reach them on the server's network until they get an API gateway.
INTERNAL_PREFIXES = ("/tmf-api/productConfigurationManagement/", "/tmf-api/productOfferingQualification/")


def routers() -> list[APIRouter]:
    from app.experience import routes_catalog, routes_configurator, routes_product_config, routes_product_model

    return [
        routes_catalog.router,
        routes_product_config.router, routes_product_config.admin_router,
        routes_configurator.router, routes_configurator.tmf_router, routes_configurator.admin_router, routes_configurator.classes_router,
        routes_product_model.router, routes_product_model.admin_router,
    ]
