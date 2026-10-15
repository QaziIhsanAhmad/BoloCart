import httpx

# Replaced in tests with a MockTransport client.
HTTP = httpx.AsyncClient(timeout=60)
