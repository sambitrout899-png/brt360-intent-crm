"""The Integration Hub: BRT 360's connection to other systems (models/integration_hub.py).

  mapping    field mapping and transforms, {{templates}}, safe XML
  outbound   calling other systems: every pattern, retries, breaker, the SSRF wall
  inbound    other systems calling us: keys, published services, sync and async
  contracts  the OpenAPI and WSDL documents a service's callers build from
  worker     the queue: sends, retries, webhooks, scheduled pulls, housekeeping

See docs/integration-hub.md.
"""
