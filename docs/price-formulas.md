# Price formulas - the price impact expression builder

Product Modeller → a product → **Pricing** tab.

A per-value impact ("Fiber: +500", "DSL: -10%", "*: 0.5 each") stays on the attribute. A **price formula** works an amount out of several attributes and child products at once - a discount for a combination, bandwidth tiers, a charge per unit above an allowance, a one-time fee when a child product is taken. Each formula is one line of the price breakdown on the configurator, quote and order.

## Building one

1. **Describe it** in plain English and press *✨ Build formula*. The tenant's AI model writes the formula when one is set up; otherwise (or when its answer doesn't check out) the built-in reader does. Examples:
   - `add 500 if Access Medium is Fiber`
   - `10% off when Contract Term is 36 months` / `10% off the total when ...`
   - `2 per Kbps above 2048`
   - `one-time 1500 installation if CPE is chosen`
   - `if Bandwidth is at most 2 Mbps then 0; if Bandwidth is at most 10 Mbps then 800; otherwise 2000` (first match wins)
2. **Formula** - edit it, or build it from the palette: attributes (click one to see its values), child products, `base` / `subtotal`, operators, functions and starting templates. It is checked as you type and said back in words.
3. **Line label** and **Monthly / One-time**.
4. **Impact preview** - the amount for every value worth seeing (each value of a list, range ends and the thresholds the formula compares against).
5. **Try it** - pick values, see the amount.

## The language

| | |
|---|---|
| `[Access Medium]`, `Bandwidth` | an attribute (one-word names need no brackets). A list attribute is its value; a ranged attribute is a number in its own unit (`Bandwidth > 2048` for Kbps - "2 Mbps" is rejected with the number to write) |
| `[CPE]` | a child product: how many are chosen (0 = not chosen) |
| `base`, `subtotal` | the base price; the monthly price worked out so far (base + child products + per-value impacts + earlier formulas) |
| `500`, `10%`, `"Fiber"` | numbers, percentages (10% = 0.10), text |
| `+ - * /`, `= != < <= > >=`, `and or not`, `x in ("A", "B")` | |
| `if(condition, then, otherwise)`, `min`, `max`, `round(x, digits)`, `ceil`, `floor`, `abs`, `between(x, low, high)` | |

A formula is never executed as code: it is parsed into a tree and worked out. Saving checks that every name exists and is a pricing attribute or child product, that text compared with a list attribute is one of its values, and that the result is an amount. At selling time it never fails: a value not chosen counts as 0, and dividing by 0 gives 0.

## Where it lives

Formulas are product rules of type **price** (`product_rules`, no migration). The design carries them as `price_rules`, so a **released model version freezes them**. Selling picks a formula up when the model is released, or at once for a product never released, and the product page shows "the workspace has changes not released yet". Product instances on quotes and orders keep the formulas they were priced with. The release QA check reports a formula that uses a name the product no longer has.

The spreadsheet **Rules** sheet carries them as rows of type `price` whose Rule is the formula (`one-time: ...` for a one-time charge) and whose Message is the line label, so export → import gives back the same formula.

API (administrators): `GET /api/admin/product-model/{product}/price-formulas/vocabulary`, `POST .../price-formulas/check` (`text`, `picks`, `use_ai`), `POST .../price-formulas` (`expression`, `label`, `charge`, `rule_id` to edit). Switching on/off and removal use the rule endpoints.
