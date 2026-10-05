"""Typed product attributes + constraint rules (Phase 1 primitive #4).

set_attribute_value/get_attribute_values let admins (and seed data)
attach typed values to a ProductSpecification. evaluate_constraints is
the second constraint layer catalog_service.validate_composition runs,
alongside the existing line-level BundleCompatibilityRule check - that
one keys off product-line categories, this one off specific attribute
values (e.g. "a 500+ Mbps product requires a StaticIP add-on in the same
bundle").
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.attributes import AttributeConstraintRule, ProductAttributeDefinition, ProductAttributeValue
from app.models.catalog import ProductSpecification

_OPERATORS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
}


def _coerce(value: str, data_type: str) -> Any:
    if data_type == "number":
        return float(value)
    if data_type == "boolean":
        return value.strip().lower() in ("true", "1", "yes")
    return value  # string / enum - compared as-is


def set_attribute_value(db: Session, product_specification_id: int, attribute_definition_id: int, value: str) -> ProductAttributeValue:
    existing = (
        db.query(ProductAttributeValue)
        .filter(
            ProductAttributeValue.product_specification_id == product_specification_id,
            ProductAttributeValue.attribute_definition_id == attribute_definition_id,
        )
        .first()
    )
    if existing:
        existing.value = value
        db.flush()
        return existing
    row = ProductAttributeValue(
        product_specification_id=product_specification_id, attribute_definition_id=attribute_definition_id, value=value
    )
    db.add(row)
    db.flush()
    return row


def get_attribute_values(db: Session, product_specification_id: int) -> dict[str, Any]:
    """{attribute name: typed value} for one product spec."""
    rows = (
        db.query(ProductAttributeValue, ProductAttributeDefinition)
        .join(ProductAttributeDefinition, ProductAttributeValue.attribute_definition_id == ProductAttributeDefinition.id)
        .filter(ProductAttributeValue.product_specification_id == product_specification_id)
        .all()
    )
    return {defn.name: _coerce(val.value, defn.data_type) for val, defn in rows}


def evaluate_constraints(db: Session, tenant_id: int, specs_by_name: dict[str, ProductSpecification]) -> list[str]:
    """specs_by_name: {product name: ProductSpecification} for every
    product in the proposed composition (catalog_service.validate_composition
    already builds this). Returns a list of violation messages - empty
    means every active rule is satisfied (or didn't apply).
    """
    rules = (
        db.query(AttributeConstraintRule)
        .filter(AttributeConstraintRule.tenant_id == tenant_id, AttributeConstraintRule.is_active.is_(True))
        .all()
    )
    if not rules:
        return []

    spec_ids_in_bundle = {s.id: name for name, s in specs_by_name.items()}
    violations: list[str] = []

    for rule in rules:
        if rule.if_product_spec_id not in spec_ids_in_bundle:
            continue  # the triggering product isn't even in this bundle

        attribute = db.get(ProductAttributeDefinition, rule.if_attribute_id)
        value_row = (
            db.query(ProductAttributeValue)
            .filter(
                ProductAttributeValue.product_specification_id == rule.if_product_spec_id,
                ProductAttributeValue.attribute_definition_id == rule.if_attribute_id,
            )
            .first()
        )
        if value_row is None or attribute is None:
            continue  # no value set for this attribute - rule can't fire

        actual = _coerce(value_row.value, attribute.data_type)
        threshold = _coerce(rule.threshold, attribute.data_type)
        if not _OPERATORS[rule.operator](actual, threshold):
            continue  # condition not met - rule doesn't apply to this bundle

        trigger_name = spec_ids_in_bundle[rule.if_product_spec_id]
        then_in_bundle = rule.then_product_spec_id in spec_ids_in_bundle
        then_spec = db.get(ProductSpecification, rule.then_product_spec_id)
        then_name = then_spec.name if then_spec else f"#{rule.then_product_spec_id}"

        if rule.rule_type == "requires" and not then_in_bundle:
            violations.append(
                f"{trigger_name}'s {attribute.name} {rule.operator} {rule.threshold} requires '{then_name}', "
                f"which is not present in this bundle"
            )
        elif rule.rule_type == "excludes" and then_in_bundle:
            violations.append(
                f"{trigger_name}'s {attribute.name} {rule.operator} {rule.threshold} excludes '{then_name}', "
                f"which is present in this bundle"
            )

    return violations
