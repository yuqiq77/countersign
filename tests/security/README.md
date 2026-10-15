# Security Tests

The fetcher's network policy: private, loopback, link-local and metadata
destinations are refused, including after a redirect; each connection is
pinned to its vetted address; response bodies are capped; and session
cookies stay within one fetch and one host.
