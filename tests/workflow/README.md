# Workflow Tests

Workflows under Temporal's time-skipping test server, always through
`support.time_skipping_env()`: strict contracts need the pydantic data
converter. Mocked Activities cover ordering, approval Signal dedupe,
expiry, retries and quarantine routing. Real Activities on a test database
cover crash-window idempotency, the evidence loop, Ashby verification and
the scheduled watch. History-replay tests are not written yet.
