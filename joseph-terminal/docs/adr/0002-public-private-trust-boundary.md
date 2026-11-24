# ADR-0002: Public frontend / private execution trust boundary

- **Status:** Accepted (2026-11-22)
- **Context:** The terminal runs in users' browsers and can be published on its own; strategy IP, user data and any signing capability must never ship in it. *Public* here means what reaches the browser and *private* means server side, not repository visibility.
- **Decision:** The browser holds only a short-lived session token and talks to versioned HTTP/WS contracts. Venue credentials are envelope-encrypted inside the execution engine and decrypted only in its adapters. The market engine never holds trading credentials. Service-to-service calls (signal forwarding) use a separate service token.
- **Alternatives:** Browser-side signing (rejected: key exfiltration, no server risk); a single backend holding both market and trading concerns (rejected: larger blast radius).
- **Consequences:** Architecture tests fail if browser code imports Node built-ins or private engine paths; secret scanning runs in CI. Production needs a BFF that turns a user session into backend tokens (dev uses `NEXT_PUBLIC_DEV_TOKEN`, documented as dev-only).
- **Migration:** Not applicable.
- **Rollback:** Disable the execution endpoints at the gateway; the terminal degrades to read-only.
