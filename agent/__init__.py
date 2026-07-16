"""SRE Agent package for Vertex AI Agent Engine."""

# Every Google reference agent (mortgage-agent, agent-dj, agent-weather,
# agent-datacommons) does this: ensures urllib3 does NOT use PyOpenSSL,
# which has a bug causing "ValueError: Context has already been used to
# create a Connection" when the OTEL span exporter pushes telemetry after
# an HTTP error. Defensive — a no-op if PyOpenSSL was never injected.
try:
    import urllib3.contrib.pyopenssl

    urllib3.contrib.pyopenssl.extract_from_urllib3()
except Exception:
    pass
