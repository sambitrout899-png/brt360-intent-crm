"""Configurable Customer 360 ("Configure 360", INT-33).

catalog    - domains, canonical datasets, the widget library, themes, templates,
             and layout validation (normalize_layout).
connectors - the tenant's external systems (billing, service, network,
             inventory): calling them, mapping their schema onto ours, demo systems.
data       - loading each dataset for one customer (BRT 360 itself or a
             connector), aggregating it per widget, insights, the full view.
agent      - turns plain words ("add a billing pane with a revenue trend,
             connect our network system") into layout operations.
"""
