terraform {
  backend "gcs" {
    bucket = "commerce-agents-dev-tfstate"
    prefix = "platform"
  }
  required_version = ">= 1.13, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}

variable "project_id" { default = "commerce-agents-dev" }
variable "region" { default = "us-central1" }
variable "billing_account" { default = "015D02-62F1CD-5D6D2A" }
variable "alert_email" { default = "lauti@clicar.studio" }
variable "runtime_image" {
  type        = string
  default     = ""
  description = "Immutable runtime image digest. Empty deploys foundation only."
}

locals {
  labels   = { application = "commerce-agents", environment = "dev", managed_by = "terraform" }
  datasets = toset(["raw_shopify", "cfg", "analytics", "platform_smoke", "billing_export"])
  buckets  = toset(["landing", "artifacts", "backups", "builds"])
}

data "google_project" "current" {}

resource "google_monitoring_notification_channel" "email" {
  display_name = "Commerce dev - Lautaro"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "commerce-agents-dev - USD 100 before promotions"
  budget_filter {
    projects               = ["projects/${data.google_project.current.number}"]
    calendar_period        = "MONTH"
    credit_types_treatment = "INCLUDE_SPECIFIED_CREDITS"
    # Preserve free-tier and ordinary discounts; exclude PROMOTION credits.
    credit_types = ["FREE_TIER", "DISCOUNT", "SUSTAINED_USAGE_DISCOUNT", "COMMITTED_USAGE_DISCOUNT", "COMMITTED_USAGE_DISCOUNT_DOLLAR_BASE", "SUBSCRIPTION_BENEFIT"]
  }
  amount {
    specified_amount {
      currency_code = "USD"
      units         = "100"
    }
  }
  dynamic "threshold_rules" {
    for_each = [0.5, 0.8, 1.0]
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }
  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.email.id]
    disable_default_iam_recipients   = true
  }
}

resource "google_storage_bucket" "data" {
  for_each                    = local.buckets
  name                        = "${var.project_id}-${each.key}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = local.labels
  versioning { enabled = each.key == "backups" }
  lifecycle_rule {
    condition { age = each.key == "landing" ? 90 : 30 }
    action { type = "Delete" }
  }
}

resource "google_bigquery_dataset" "data" {
  for_each                    = local.datasets
  dataset_id                  = each.key
  location                    = var.region
  delete_contents_on_destroy  = false
  labels                      = local.labels
  default_table_expiration_ms = each.key == "platform_smoke" ? 604800000 : null
}

resource "google_artifact_registry_repository" "runtime" {
  location      = var.region
  repository_id = "commerce"
  format        = "DOCKER"
  labels        = local.labels
}

resource "google_service_account" "runtime" {
  for_each     = toset(["dagster-control", "dagster-worker", "commerce-build", "cost-reporter"])
  account_id   = each.key
  display_name = each.key
}

resource "google_project_iam_member" "logging" {
  project  = var.project_id
  for_each = google_service_account.runtime
  role     = "roles/logging.logWriter"
  member   = "serviceAccount:${each.value.email}"
}
resource "google_project_iam_member" "metrics" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.runtime["dagster-control"].email}"
}
resource "google_project_iam_member" "bq_jobs" {
  project  = var.project_id
  for_each = toset(["dagster-worker", "cost-reporter"])
  role     = "roles/bigquery.jobUser"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}
resource "google_bigquery_dataset_iam_member" "worker_data" {
  for_each   = setsubtract(local.datasets, toset(["billing_export"]))
  dataset_id = google_bigquery_dataset.data[each.key].dataset_id
  role       = each.key == "cfg" ? "roles/bigquery.dataViewer" : "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runtime["dagster-worker"].email}"
}
resource "google_bigquery_dataset_iam_member" "billing_reader" {
  dataset_id = google_bigquery_dataset.data["billing_export"].dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.runtime["cost-reporter"].email}"
}
resource "google_storage_bucket_iam_member" "worker" {
  for_each = toset(["landing", "artifacts"])
  bucket   = google_storage_bucket.data[each.key].name
  role     = "roles/storage.objectUser"
  member   = "serviceAccount:${google_service_account.runtime["dagster-worker"].email}"
}
resource "google_storage_bucket_iam_member" "control_artifacts" {
  bucket = google_storage_bucket.data["artifacts"].name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.runtime["dagster-control"].email}"
}
resource "google_storage_bucket_iam_member" "artifact_metadata" {
  for_each = toset(["dagster-control", "dagster-worker"])
  bucket   = google_storage_bucket.data["artifacts"].name
  role     = "roles/storage.legacyBucketReader"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}
resource "google_storage_bucket_iam_member" "control_backups" {
  bucket = google_storage_bucket.data["backups"].name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime["dagster-control"].email}"
}
resource "google_storage_bucket_iam_member" "builds" {
  bucket = google_storage_bucket.data["builds"].name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime["commerce-build"].email}"
}
resource "google_storage_bucket_iam_member" "build_metadata" {
  bucket = google_storage_bucket.data["builds"].name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.runtime["commerce-build"].email}"
}
resource "google_artifact_registry_repository_iam_member" "pull" {
  for_each   = toset(["dagster-control", "dagster-worker"])
  repository = google_artifact_registry_repository.runtime.name
  location   = var.region
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.runtime[each.key].email}"
}
resource "google_artifact_registry_repository_iam_member" "push" {
  repository = google_artifact_registry_repository.runtime.name
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.runtime["commerce-build"].email}"
}

resource "google_secret_manager_secret" "postgres" {
  secret_id = "dagster-postgres-password"
  replication {
    auto {}
  }
  labels = local.labels
}
# Secret values are generated outside Terraform, never stored in state or Git.
resource "google_secret_manager_secret_iam_member" "postgres" {
  for_each  = toset(["dagster-control", "dagster-worker"])
  secret_id = google_secret_manager_secret.postgres.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_compute_network" "platform" {
  name                    = "commerce-platform"
  auto_create_subnetworks = false
}
resource "google_compute_subnetwork" "platform" {
  name                     = "commerce-platform"
  ip_cidr_range            = "10.42.0.0/24"
  region                   = var.region
  network                  = google_compute_network.platform.id
  private_ip_google_access = true
}
resource "google_compute_address" "postgres" {
  name         = "dagster-private"
  address_type = "INTERNAL"
  address      = "10.42.0.10"
  subnetwork   = google_compute_subnetwork.platform.id
  region       = var.region
}
resource "google_compute_firewall" "iap" {
  name          = "dagster-ssh-iap-only"
  network       = google_compute_network.platform.name
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["dagster-control"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
resource "google_compute_firewall" "postgres" {
  name        = "dagster-postgres-workers-only"
  network     = google_compute_network.platform.name
  source_tags = ["dagster-worker"]
  target_tags = ["dagster-control"]
  allow {
    protocol = "tcp"
    ports    = ["5432"]
  }
}

output "project_id" { value = var.project_id }
output "budget_name" { value = google_billing_budget.monthly.name }
output "notification_channel" { value = google_monitoring_notification_channel.email.name }
output "registry" { value = "${var.region}-docker.pkg.dev/${var.project_id}/commerce" }
