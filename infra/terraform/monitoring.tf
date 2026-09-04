resource "google_logging_metric" "heartbeat" {
  name   = "commerce_platform_heartbeat"
  filter = "logName=\"projects/${var.project_id}/logs/commerce-platform-health\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "health" {
  display_name = "Commerce Dagster - unhealthy containers or resource pressure"
  combiner     = "OR"
  enabled      = var.runtime_image != ""
  conditions {
    display_name = "Health probe reports a failure"
    condition_matched_log {
      filter = "logName=\"projects/${var.project_id}/logs/commerce-platform-health\" AND severity>=ERROR"
    }
  }
  alert_strategy {
    notification_rate_limit { period = "1800s" }
    auto_close = "1800s"
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
}

resource "google_monitoring_alert_policy" "heartbeat" {
  display_name = "Commerce Dagster - missing health heartbeat"
  combiner     = "OR"
  enabled      = var.runtime_image != ""
  conditions {
    display_name = "No heartbeat for 10 minutes"
    condition_absent {
      filter   = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.heartbeat.name}\" AND resource.type=\"global\""
      duration = "600s"
      aggregations {
        alignment_period   = "120s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
}
