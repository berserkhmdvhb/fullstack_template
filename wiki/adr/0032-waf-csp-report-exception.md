# ADR-0032: A narrow WAF exception lets CSP reports through

## Status

Accepted.

## Date

2026-11-23

## Context

With the optional WAF (Envoy with Coraza and the OWASP CRS) and CSP reporting both on, CRS refused every browser
report: their content types (`application/csp-report`, `application/reports+json`) are not in CRS's allowed list
(rule 920420), and a report body, which quotes the policy and URLs, scores as XSS and SSRF. CSP reporting was useless
behind the WAF.

## Decision

One chained rule matches a `POST` to the exact report route (`/api/<version>/csp-report`) with one of the two report
content types. For that request alone it adds the two types to the allowed list and turns request-body inspection
off. The URI, the query and the headers are still inspected; any other method, path or content type is inspected as
before. The backend caps the body (`CSP_REPORT_MAX_BYTES`, 16 KiB by default, enforced while streaming), rate-limits and sanitises the report.

## Consequences

- Good: CSP violations reach the backend through the WAF, proven by a real browser on kind.
- Bad: on that one route the body is not inspected by the WAF; the backend's parser and caps are the control there.

## Alternatives considered

- Disable rule 920420 and the XSS and SSRF rules for the route: rejected, broader than turning off body access for
  two content types.
- Send reports around the WAF: rejected, a second path to the backend.
- Leave reporting off behind the WAF: rejected, it hides CSP breakage.

## Links

- [`charts/your-service-name/templates/waf-proxy.yaml`](../../charts/your-service-name/templates/waf-proxy.yaml)
- [Security: CSP violation reports](../security.md#csp-violation-reports)
- [`src/your_service_name/backend/api/routers/csp_report.py`](../../src/your_service_name/backend/api/routers/csp_report.py),
  [`tests/k8s/test_csp_report.py`](../../tests/k8s/test_csp_report.py)
