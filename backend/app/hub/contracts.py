"""The documents a calling system is built from, generated from what is published:
an OpenAPI 3 description and a WSDL 1.1 (document/literal, SOAP 1.1 and 1.2) - and
reading and answering the SOAP calls themselves.

What a client sees is what it may call: its documents list only its own services.
"""

from __future__ import annotations

import json
import re
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from app.hub import mapping
from app.hub.inbound import Refused, Service

SOAP11 = "http://schemas.xmlsoap.org/soap/envelope/"
SOAP12 = "http://www.w3.org/2003/05/soap-envelope"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
_XSD_TYPES = {"string": "xs:string", "number": "xs:decimal", "integer": "xs:integer", "boolean": "xs:boolean", "date": "xs:date"}
_JSON_TYPES = {"string": "string", "number": "number", "integer": "integer", "boolean": "boolean", "date": "string"}


def namespace(tenant_slug: str) -> str:
    return f"urn:brt360:{tenant_slug}:integration"


def operation_name(code: str) -> str:
    name = re.sub(r"\W", "_", code)
    return name if re.match(r"[A-Za-z_]", name) else f"S_{name}"


def _element_name(name: Any) -> str:
    cleaned = re.sub(r"[^\w.-]", "_", str(name))
    return cleaned if re.match(r"[A-Za-z_]", cleaned) else f"_{cleaned}"


# ---------------------------------------------------------------- OpenAPI


def _field_schema(service: Service) -> dict[str, Any]:
    properties = {}
    for f in service.fields:
        kind = str(f.get("type") or "string")
        schema: dict[str, Any] = {"type": _JSON_TYPES.get(kind, "string")}
        if kind == "date":
            schema["format"] = "date"
        if f.get("description"):
            schema["description"] = str(f["description"])
        properties[str(f["name"])] = schema
    required = sorted(str(f["name"]) for f in service.fields if f.get("required"))
    record = {"type": "object", "properties": properties, "additionalProperties": True}
    if required:
        record["required"] = required
    if service.target_type != "bim":
        return record
    return {"oneOf": [record, {"type": "array", "items": record}, {"type": "object", "required": ["records"],
                                                                  "properties": {"records": {"type": "array", "items": record}}}]}


def openapi(services: list[Service], origin: str, title: str) -> dict[str, Any]:
    """OpenAPI 3.0.3 for these services."""
    error = {"description": "Refused", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}
    paths: dict[str, Any] = {
        "/api/gateway/ping": {"get": {"summary": "Check your credentials", "operationId": "ping", "tags": ["Gateway"],
                                      "responses": {"200": {"description": "Who you are and what you may call"}, "401": error}}},
        "/api/gateway/messages/{correlation_id}": {"get": {
            "summary": "The outcome of an asynchronous call", "operationId": "messageStatus", "tags": ["Gateway"],
            "parameters": [{"name": "correlation_id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "Its status, and its result once it has one"}, "404": error}}},
    }
    for service in services:
        operation: dict[str, Any] = {
            "summary": service.name, "description": service.description or service.name, "operationId": operation_name(service.code), "tags": ["Services"],
            "parameters": [
                {"name": "Idempotency-Key", "in": "header", "required": False, "schema": {"type": "string"},
                 "description": "Send the same value when retrying: the work is done once and the first answer comes back"},
                {"name": "X-Correlation-ID", "in": "header", "required": False, "schema": {"type": "string"}, "description": "Your own id for this call"},
                {"name": "Prefer", "in": "header", "required": False, "schema": {"type": "string", "enum": ["respond-async"]},
                 "description": "respond-async: answer 202 at once and do the work in the queue"},
                {"name": "dry_run", "in": "query", "required": False, "schema": {"type": "boolean"}, "description": "Check everything, keep nothing"},
            ],
            "requestBody": {"required": True, "content": {"application/json": {"schema": _field_schema(service)}}},
            "responses": {
                "200": {"description": "Done", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Result"}}}},
                "202": {"description": "Accepted - read the outcome at status_url", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Accepted"}}}},
                "400": error, "401": error, "403": error, "404": error, "409": error, "422": {"description": "The work was refused or needs more", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Result"}}}},
                "429": error,
            },
        }
        if service.target_type == "bim":
            operation["responses"]["207"] = {"description": "Some records were loaded, some refused", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Result"}}}}
        paths[f"/api/gateway/services/{service.code}"] = {"post": operation}
    return {
        "openapi": "3.0.3",
        "info": {"title": title, "version": "1.0.0", "description": "Generated from the services published in Integration Hub."},
        "servers": [{"url": origin or "/"}],
        "security": [{"apiKey": []}, {"bearer": []}, {"basic": []}],
        "tags": [{"name": "Services"}, {"name": "Gateway"}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                "bearer": {"type": "http", "scheme": "bearer", "description": "Your key, or a token from /api/gateway/oauth/token"},
                "basic": {"type": "http", "scheme": "basic", "description": "Key id as user name, the key as password"},
            },
            "schemas": {
                "Result": {"type": "object", "properties": {"correlation_id": {"type": "string"}, "service": {"type": "string"}, "status": {"type": "string"},
                                                            "summary": {"type": "string"}, "result": {"type": "object", "additionalProperties": True},
                                                            "message": {"type": "string"}}, "additionalProperties": True},
                "Accepted": {"type": "object", "properties": {"correlation_id": {"type": "string"}, "service": {"type": "string"}, "status": {"type": "string", "enum": ["accepted"]},
                                                              "status_url": {"type": "string"}}},
                "Error": {"type": "object", "properties": {"error": {"type": "string"}, "message": {"type": "string"}, "correlation_id": {"type": "string"}}},
            },
        },
    }


# ---------------------------------------------------------------- WSDL


def _field_elements(service: Service, indent: str, all_optional: bool = False) -> str:
    out = []
    seen = {"callMode", "idempotencyKey"}  # the call's own elements
    for f in service.fields:
        name = _element_name(f["name"])
        if name in seen:
            continue
        seen.add(name)
        kind = _XSD_TYPES.get(str(f.get("type") or "string"), "xs:string")
        out.append(f'{indent}<xs:element name={quoteattr(name)} type="{kind}" minOccurs="{1 if f.get("required") and not all_optional else 0}"/>')
    return "\n".join(out)


def _request_element(service: Service) -> str:
    name = operation_name(service.code)
    mode = '            <xs:element name="callMode" type="tns:CallMode" minOccurs="0"/>\n'
    idempotency = '            <xs:element name="idempotencyKey" type="xs:string" minOccurs="0"/>\n'
    # extra fields in a namespace of the caller's own: ##any after optional elements would be ambiguous, and strict toolkits refuse that
    extras = '            <xs:any namespace="##other" processContents="lax" minOccurs="0" maxOccurs="unbounded"/>\n'
    if service.target_type == "bim":
        # each field optional in the schema: an update sends only what changed; a record missing what it needs is reported back row by row
        record = (f'            <xs:element name="record" minOccurs="1" maxOccurs="unbounded"><xs:complexType><xs:sequence>\n'
                  f'{_field_elements(service, "              ", all_optional=True)}\n'
                  f'              <xs:any namespace="##other" processContents="lax" minOccurs="0" maxOccurs="unbounded"/>\n'
                  f'            </xs:sequence></xs:complexType></xs:element>\n')
        inner = mode + idempotency + record
    else:
        fields = _field_elements(service, "            ")
        inner = mode + idempotency + (fields + "\n" if fields else "") + extras
    return (f'      <xs:element name="{name}Request"><xs:complexType><xs:sequence>\n{inner}'
            f'      </xs:sequence></xs:complexType></xs:element>\n'
            f'      <xs:element name="{name}Response" type="tns:CallResult"/>\n')


def wsdl(services: list[Service], tenant_slug: str, endpoint: str, title: str) -> str:
    ns = namespace(tenant_slug)
    elements = "".join(_request_element(s) for s in services)
    messages, port_ops, binding11, binding12 = [], [], [], []
    for service in services:
        name = operation_name(service.code)
        messages.append(f'  <wsdl:message name="{name}Input"><wsdl:part name="parameters" element="tns:{name}Request"/></wsdl:message>\n'
                        f'  <wsdl:message name="{name}Output"><wsdl:part name="parameters" element="tns:{name}Response"/></wsdl:message>\n')
        port_ops.append(f'    <wsdl:operation name="{name}"><wsdl:documentation>{escape(service.name)}</wsdl:documentation>'
                        f'<wsdl:input message="tns:{name}Input"/><wsdl:output message="tns:{name}Output"/></wsdl:operation>\n')
        action = f"{ns}:{name}"
        for target, prefix in ((binding11, "soap"), (binding12, "soap12")):
            target.append(f'    <wsdl:operation name="{name}"><{prefix}:operation soapAction={quoteattr(action)} style="document"/>'
                          f'<wsdl:input><{prefix}:body use="literal"/></wsdl:input><wsdl:output><{prefix}:body use="literal"/></wsdl:output></wsdl:operation>\n')
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions name={quoteattr(title)} targetNamespace={quoteattr(ns)} xmlns:tns={quoteattr(ns)}
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/" xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:soap12="http://schemas.xmlsoap.org/wsdl/soap12/" xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <wsdl:documentation>Generated from the services published in Integration Hub. Credentials: HTTP Basic (key id, key) or a WS-Security UsernameToken.</wsdl:documentation>
  <wsdl:types>
    <xs:schema targetNamespace={quoteattr(ns)} elementFormDefault="qualified">
      <xs:simpleType name="CallMode"><xs:restriction base="xs:string"><xs:enumeration value="sync"/><xs:enumeration value="async"/></xs:restriction></xs:simpleType>
      <xs:complexType name="CallResult"><xs:sequence>
        <xs:element name="correlationId" type="xs:string"/>
        <xs:element name="service" type="xs:string" minOccurs="0"/>
        <xs:element name="status" type="xs:string"/>
        <xs:element name="httpStatus" type="xs:int" minOccurs="0"/>
        <xs:element name="summary" type="xs:string" minOccurs="0"/>
        <xs:element name="message" type="xs:string" minOccurs="0"/>
        <xs:element name="resultJson" type="xs:string" minOccurs="0"/>
      </xs:sequence></xs:complexType>
{elements}    </xs:schema>
  </wsdl:types>
{"".join(messages)}  <wsdl:portType name="IntegrationPort">
{"".join(port_ops)}  </wsdl:portType>
  <wsdl:binding name="IntegrationSoap11" type="tns:IntegrationPort"><soap:binding transport="http://schemas.xmlsoap.org/soap/http" style="document"/>
{"".join(binding11)}  </wsdl:binding>
  <wsdl:binding name="IntegrationSoap12" type="tns:IntegrationPort"><soap12:binding transport="http://schemas.xmlsoap.org/soap/http" style="document"/>
{"".join(binding12)}  </wsdl:binding>
  <wsdl:service name="BRT360Integration">
    <wsdl:port name="IntegrationSoap11Port" binding="tns:IntegrationSoap11"><soap:address location={quoteattr(endpoint)}/></wsdl:port>
    <wsdl:port name="IntegrationSoap12Port" binding="tns:IntegrationSoap12"><soap12:address location={quoteattr(endpoint)}/></wsdl:port>
  </wsdl:service>
</wsdl:definitions>
'''


# ---------------------------------------------------------------- SOAP calls


class SoapRequest:
    def __init__(self, envelope_ns: str, operation: str, payload: Any, user_token: tuple[str, str] | None):
        self.version = "1.2" if envelope_ns == SOAP12 else "1.1"
        self.operation, self.payload, self.user_token = operation, payload, user_token


def parse_user_token(data: bytes) -> tuple[str, str] | None:
    """WS-Security UsernameToken from the header, read before the caller is known. Any XML problem: no token."""
    try:
        root = mapping.parse_xml(data)
    except ValueError:
        return None
    for element in root.iter():
        if mapping.local(element.tag) == "UsernameToken":
            values = {mapping.local(child.tag): (child.text or "").strip() for child in element}
            if values.get("Password"):
                return values.get("Username", ""), values["Password"]
    return None


def parse_request(data: bytes) -> SoapRequest:
    try:
        root = mapping.parse_xml(data)
    except ValueError as exc:
        raise Refused(400, str(exc), "Client") from exc
    if mapping.local(root.tag) != "Envelope":
        raise Refused(400, "That is not a SOAP envelope", "Client")
    envelope_ns = root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""
    if envelope_ns not in (SOAP11, SOAP12):
        raise Refused(400, f"Unknown SOAP version: {envelope_ns or 'no namespace'}", "VersionMismatch")
    body = next((c for c in root if mapping.local(c.tag) == "Body"), None)
    children = list(body) if body is not None else []
    if len(children) != 1:
        raise Refused(400, "The SOAP body must carry exactly one request element", "Client")
    name = mapping.local(children[0].tag)
    if not name.endswith("Request"):
        raise Refused(400, f"Unknown request element {name}", "Client")
    payload = mapping.to_dict(children[0]) or {}
    return SoapRequest(envelope_ns, name.removesuffix("Request"), payload if isinstance(payload, dict) else {}, None)


def service_for(services: list[Service], operation: str) -> Service | None:
    return next((s for s in services if operation_name(s.code) == operation), None)


def _envelope(version: str, inner: str) -> str:
    ns = SOAP12 if version == "1.2" else SOAP11
    return f'<?xml version="1.0" encoding="UTF-8"?><soap:Envelope xmlns:soap="{ns}"><soap:Body>{inner}</soap:Body></soap:Envelope>'


def content_type(version: str) -> str:
    return "application/soap+xml; charset=utf-8" if version == "1.2" else "text/xml; charset=utf-8"


def response(version: str, tenant_slug: str, operation: str, status: int, body: dict[str, Any]) -> str:
    ns = namespace(tenant_slug)
    parts = [("correlationId", body.get("correlation_id", "")), ("service", body.get("service", "")), ("status", body.get("status", "")), ("httpStatus", status)]
    if body.get("summary"):
        parts.append(("summary", body["summary"]))
    if body.get("message"):
        parts.append(("message", body["message"]))
    rest = {k: v for k, v in body.items() if k not in ("correlation_id", "service", "status", "summary", "message")}
    parts.append(("resultJson", json.dumps(rest, default=str)))
    inner = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in parts)
    return _envelope(version, f"<tns:{operation}Response xmlns:tns={quoteattr(ns)}>{inner}</tns:{operation}Response>")


def fault(version: str, code: str, message: str) -> str:
    """A SOAP fault. The code says whose problem it is: Client / Sender, or Server / Receiver."""
    sender = code in ("Client", "Sender", "unauthorized", "forbidden", "not_found", "invalid_request", "conflict", "rate_limited", "VersionMismatch")
    if version == "1.2":
        value = "soap:VersionMismatch" if code == "VersionMismatch" else "soap:Sender" if sender else "soap:Receiver"
        return _envelope(version, f'<soap:Fault><soap:Code><soap:Value>{value}</soap:Value></soap:Code>'
                                  f'<soap:Reason><soap:Text xml:lang="en">{escape(message)}</soap:Text></soap:Reason></soap:Fault>')
    value = "soap:VersionMismatch" if code == "VersionMismatch" else "soap:Client" if sender else "soap:Server"
    return _envelope(version, f"<soap:Fault><faultcode>{value}</faultcode><faultstring>{escape(message)}</faultstring></soap:Fault>")
