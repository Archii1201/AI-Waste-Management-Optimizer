"""Business logic, deliberately free of FastAPI imports.

Keeping services framework-agnostic lets the MQTT bridge, the scheduler and the
CLI tools reuse exactly the same code paths as the HTTP API.
"""
