# Agent Registry endpoint registrations (issue #33) -- Terraform is the source of
# truth for every registration the google-beta provider can represent.
#
# Adopted from the pre-existing live registrations via one-time `terraform import`
# (Terraform 1.4.7 CLI form -- not `import {}` blocks, which need >= 1.5). Nothing
# here was created fresh, so no endpoint was ever deregistered/re-registered and the
# Agent Gateway's default-deny egress never saw a gap. Values are copied verbatim from
# the live registrations rather than re-derived from the retired script's hostname
# heuristics, so config matches reality by construction.
#
# Replaces scripts/register_endpoints.py + scripts/googleapis.txt, whose "skip if the
# service ID already exists" logic could never correct a wrong URL on an existing
# entry -- the exact landmine that made issue #30 (Model Armor .rep. hostname) survive
# a "fix" and that left the stale trace-* entries below orphaned. Terraform reconciles
# these on every apply instead.
#
# NOT managed here, deliberately:
#   * 4 stale orphans (trace-api, trace-mtls, us-central1-trace, us-central1-trace-mtls)
#     -- pre-#130 rename leftovers, still live. Cleanup is a separate task; leaving them
#     unmanaged means Terraform will not touch them until we decide to remove them.
#   * sre-k8s-mcp -- the custom MCP registration. Dynamic tool-spec content and a
#     Cloud Run health-gated lifecycle; see agent_registry_mcp.tf.
locals {
  agent_registry_static_services = {
    "agentregistry" = {
      location         = "global"
      display_name     = "agentregistry.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://agentregistry.googleapis.com"
    }
    "agentregistry-mtls" = {
      location         = "global"
      display_name     = "agentregistry.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://agentregistry.mtls.googleapis.com"
    }
    "aiplatform" = {
      location         = "global"
      display_name     = "aiplatform.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://aiplatform.googleapis.com"
    }
    "aiplatform-mtls" = {
      location         = "global"
      display_name     = "aiplatform.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://aiplatform.mtls.googleapis.com"
    }
    "cloudresourcemanager" = {
      location         = "global"
      display_name     = "cloudresourcemanager.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://cloudresourcemanager.googleapis.com"
    }
    "cloudresourcemanager-mtls" = {
      location         = "global"
      display_name     = "cloudresourcemanager.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://cloudresourcemanager.mtls.googleapis.com"
    }
    "cloudtrace" = {
      location         = "global"
      display_name     = "cloudtrace.googleapis.com"
      protocol_binding = "GRPC"
      url              = "https://cloudtrace.googleapis.com"
    }
    "cloudtrace-mtls" = {
      location         = "global"
      display_name     = "cloudtrace.mtls.googleapis.com"
      protocol_binding = "GRPC"
      url              = "https://cloudtrace.mtls.googleapis.com"
    }
    "container" = {
      location         = "global"
      display_name     = "container.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://container.googleapis.com"
    }
    "container-mtls" = {
      location         = "global"
      display_name     = "container.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://container.mtls.googleapis.com"
    }
    "iamcredentials" = {
      location         = "global"
      display_name     = "iamcredentials.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://iamcredentials.googleapis.com"
    }
    "iamcredentials-mtls" = {
      location         = "global"
      display_name     = "iamcredentials.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://iamcredentials.mtls.googleapis.com"
    }
    "iap-api" = {
      location         = "global"
      display_name     = "iap.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://iap.googleapis.com"
    }
    "iap-mtls" = {
      location         = "global"
      display_name     = "iap.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://iap.mtls.googleapis.com"
    }
    "logging" = {
      location         = "global"
      display_name     = "logging.googleapis.com"
      protocol_binding = "GRPC"
      url              = "https://logging.googleapis.com"
    }
    "logging-mtls" = {
      location         = "global"
      display_name     = "logging.mtls.googleapis.com"
      protocol_binding = "GRPC"
      url              = "https://logging.mtls.googleapis.com"
    }
    "monitoring" = {
      location         = "global"
      display_name     = "monitoring.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://monitoring.googleapis.com"
    }
    "monitoring-mtls" = {
      location         = "global"
      display_name     = "monitoring.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://monitoring.mtls.googleapis.com"
    }
    "oauth2" = {
      location         = "global"
      display_name     = "oauth2.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://oauth2.googleapis.com"
    }
    "oauth2-mtls" = {
      location         = "global"
      display_name     = "oauth2.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://oauth2.mtls.googleapis.com"
    }
    "storage" = {
      location         = "global"
      display_name     = "storage.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://storage.googleapis.com"
    }
    "storage-mtls" = {
      location         = "global"
      display_name     = "storage.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://storage.mtls.googleapis.com"
    }
    "sts-api" = {
      location         = "global"
      display_name     = "sts.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://sts.googleapis.com"
    }
    "sts-mtls" = {
      location         = "global"
      display_name     = "sts.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://sts.mtls.googleapis.com"
    }
    "telemetry" = {
      location         = "global"
      display_name     = "telemetry.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://telemetry.googleapis.com"
    }
    "telemetry-mtls" = {
      location         = "global"
      display_name     = "telemetry.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://telemetry.mtls.googleapis.com"
    }
    "us-central1-agentregistry" = {
      location         = "us-central1"
      display_name     = "us-central1-agentregistry.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://us-central1-agentregistry.googleapis.com"
    }
    "us-central1-agentregistry-mtls" = {
      location         = "us-central1"
      display_name     = "us-central1-agentregistry.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://us-central1-agentregistry.mtls.googleapis.com"
    }
    "us-central1-aiplatform" = {
      location         = "us-central1"
      display_name     = "us-central1-aiplatform.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://us-central1-aiplatform.googleapis.com"
    }
    "us-central1-aiplatform-mtls" = {
      location         = "us-central1"
      display_name     = "us-central1-aiplatform.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://us-central1-aiplatform.mtls.googleapis.com"
    }
    "us-central1-cloudresourcemanager" = {
      location         = "us-central1"
      display_name     = "cloudresourcemanager.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://cloudresourcemanager.googleapis.com"
    }
    "us-central1-cloudresourcemanager-mtls" = {
      location         = "us-central1"
      display_name     = "cloudresourcemanager.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://cloudresourcemanager.mtls.googleapis.com"
    }
    "us-central1-cloudtrace" = {
      location         = "us-central1"
      display_name     = "cloudtrace.googleapis.com"
      protocol_binding = "GRPC"
      url              = "https://cloudtrace.googleapis.com"
    }
    "us-central1-cloudtrace-mtls" = {
      location         = "us-central1"
      display_name     = "cloudtrace.mtls.googleapis.com"
      protocol_binding = "GRPC"
      url              = "https://cloudtrace.mtls.googleapis.com"
    }
    "us-central1-container" = {
      location         = "us-central1"
      display_name     = "container.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://container.googleapis.com"
    }
    "us-central1-container-mtls" = {
      location         = "us-central1"
      display_name     = "container.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://container.mtls.googleapis.com"
    }
    "us-central1-iamcredentials" = {
      location         = "us-central1"
      display_name     = "iamcredentials.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://iamcredentials.googleapis.com"
    }
    "us-central1-iamcredentials-mtls" = {
      location         = "us-central1"
      display_name     = "iamcredentials.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://iamcredentials.mtls.googleapis.com"
    }
    "us-central1-iap" = {
      location         = "us-central1"
      display_name     = "iap.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://iap.googleapis.com"
    }
    "us-central1-iap-mtls" = {
      location         = "us-central1"
      display_name     = "iap.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://iap.mtls.googleapis.com"
    }
    "us-central1-logging" = {
      location         = "us-central1"
      display_name     = "logging.googleapis.com"
      protocol_binding = "HTTP_JSON"
      url              = "https://logging.googleapis.com"
    }
    "us-central1-logging-mtls" = {
      location         = "us-central1"
      display_name     = "logging.mtls.googleapis.com"
      protocol_binding = "GRPC"
      url              = "https://logging.mtls.googleapis.com"
    }
    "us-central1-modelarmor-us-central1" = {
      location         = "us-central1"
      display_name     = "modelarmor.us-central1.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://modelarmor.us-central1.rep.googleapis.com"
    }
    "us-central1-modelarmor-us-central1-mtls" = {
      location         = "us-central1"
      display_name     = "modelarmor.us-central1.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://modelarmor.us-central1.mtls.googleapis.com"
    }
    "us-central1-monitoring" = {
      location         = "us-central1"
      display_name     = "monitoring.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://monitoring.googleapis.com"
    }
    "us-central1-monitoring-mtls" = {
      location         = "us-central1"
      display_name     = "monitoring.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://monitoring.mtls.googleapis.com"
    }
    "us-central1-oauth2" = {
      location         = "us-central1"
      display_name     = "oauth2.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://oauth2.googleapis.com"
    }
    "us-central1-oauth2-mtls" = {
      location         = "us-central1"
      display_name     = "oauth2.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://oauth2.mtls.googleapis.com"
    }
    "us-central1-storage" = {
      location         = "us-central1"
      display_name     = "storage.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://storage.googleapis.com"
    }
    "us-central1-storage-mtls" = {
      location         = "us-central1"
      display_name     = "storage.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://storage.mtls.googleapis.com"
    }
    "us-central1-sts" = {
      location         = "us-central1"
      display_name     = "sts.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://sts.googleapis.com"
    }
    "us-central1-sts-mtls" = {
      location         = "us-central1"
      display_name     = "sts.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://sts.mtls.googleapis.com"
    }
    "us-central1-telemetry" = {
      location         = "us-central1"
      display_name     = "telemetry.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://telemetry.googleapis.com"
    }
    "us-central1-telemetry-mtls" = {
      location         = "us-central1"
      display_name     = "telemetry.mtls.googleapis.com"
      protocol_binding = "JSONRPC"
      url              = "https://telemetry.mtls.googleapis.com"
    }
  }
}

resource "google_agent_registry_service" "static" {
  provider = google-beta
  for_each = local.agent_registry_static_services

  project      = var.project_a_id
  location     = each.value.location
  service_id   = each.key
  display_name = each.value.display_name

  endpoint_spec {
    type = "NO_SPEC"
  }

  interfaces {
    protocol_binding = each.value.protocol_binding
    url              = each.value.url
  }
}
