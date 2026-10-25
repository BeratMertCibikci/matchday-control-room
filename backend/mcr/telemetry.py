"""OpenTelemetry for the crew: one span per moment, per agent call and per booth question.

Agent Framework adds its own GenAI spans (invoke_agent, chat, execute_tool) underneath ours, so a
trace viewer shows the whole hand-off chain: moment -> analyst -> narrator/persona -> producer.

Off by default. Turn it on with any of:
  MCR_OTEL=1                                   console exporter (prints spans)
  OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   e.g. the Aspire Dashboard
  APPLICATIONINSIGHTS_CONNECTION_STRING=...    Azure Monitor / Application Insights
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace

tracer = trace.get_tracer("mcr")
log = logging.getLogger(__name__)
_done = False


def setup() -> bool:
    global _done
    if _done:
        return True
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    otlp = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    console = os.getenv("MCR_OTEL", "0") not in ("0", "", "false")
    if not (conn or otlp or console):
        return False
    from agent_framework.observability import configure_otel_providers

    exporters = []
    if conn:
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

        exporters.append(AzureMonitorTraceExporter(connection_string=conn))
    configure_otel_providers(
        service_name=os.getenv("OTEL_SERVICE_NAME", "matchday-control-room"),
        otlp_protocol=os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf") if otlp else None,
        enable_console_exporters=console and not (conn or otlp),
        exporters=exporters or None,
    )
    _done = True
    log.info("OpenTelemetry enabled (%s)", "azure monitor" if conn else "otlp" if otlp else "console")
    return True
