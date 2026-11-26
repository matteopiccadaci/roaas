"""ROaaS: a robot-agnostic provider of recorded robot work, settled through Crow (testnet only)."""

__version__ = "0.1.0"
# Sent on every HTTP request. Cloudflare (our Worker, and Crow's API) answers 403 "error code: 1010" to the default
# "Python-urllib/3.x" user agent.
USER_AGENT = f"roaas/{__version__} (+https://github.com/matteopiccadaci/roaas)"
