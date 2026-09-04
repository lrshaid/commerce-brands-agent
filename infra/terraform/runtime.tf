resource "google_compute_disk" "dagster" {
  count  = var.runtime_image != "" ? 1 : 0
  name   = "dagster-data"
  type   = "pd-balanced"
  zone   = "${var.region}-a"
  size   = 30
  labels = local.labels
  lifecycle { prevent_destroy = true }
}

resource "google_compute_instance" "dagster" {
  count                     = var.runtime_image != "" ? 1 : 0
  name                      = "dagster-control"
  zone                      = "${var.region}-a"
  machine_type              = "e2-medium"
  tags                      = ["dagster-control"]
  labels                    = local.labels
  allow_stopping_for_update = true
  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 20
      type  = "pd-standard"
    }
  }
  attached_disk {
    source      = google_compute_disk.dagster[0].id
    device_name = "dagster-data"
  }
  network_interface {
    subnetwork = google_compute_subnetwork.platform.id
    network_ip = google_compute_address.postgres.address
    # Outbound image/package downloads without the fixed cost of Cloud NAT.
    # Ingress is denied except SSH from IAP; UI is bound to loopback only.
    access_config {}
  }
  service_account {
    email  = google_service_account.runtime["dagster-control"].email
    scopes = ["cloud-platform"]
  }
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  lifecycle {
    prevent_destroy = true
  }
  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
    startup-script = templatefile("${path.module}/../runtime/bootstrap.sh.tftpl", {
      health_base64         = filebase64("${path.module}/../runtime/health.py")
      health_service_base64 = filebase64("${path.module}/../runtime/commerce-health.service")
      health_timer_base64   = filebase64("${path.module}/../runtime/commerce-health.timer")
      backup_base64         = filebase64("${path.module}/../runtime/backup.sh")
      backup_service_base64 = filebase64("${path.module}/../runtime/commerce-backup.service")
      backup_timer_base64   = filebase64("${path.module}/../runtime/commerce-backup.timer")
      compose_base64        = filebase64("${path.module}/../runtime/compose.yaml")
      project               = var.project_id
      region                = var.region
      artifact_bucket       = google_storage_bucket.data["artifacts"].name
      image                 = var.runtime_image
    })
  }
  depends_on = [google_secret_manager_secret_iam_member.postgres, google_artifact_registry_repository_iam_member.pull]
}

resource "google_cloud_run_v2_job" "worker" {
  count               = var.runtime_image != "" ? 1 : 0
  name                = "dagster-worker"
  location            = var.region
  deletion_protection = false
  labels              = local.labels
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.runtime["dagster-worker"].email
      timeout         = "1800s"
      max_retries     = 0
      containers {
        image = var.runtime_image
        resources { limits = { cpu = "2", memory = "4Gi" } }
        dynamic "env" {
          for_each = {
            GOOGLE_CLOUD_PROJECT  = var.project_id
            GOOGLE_CLOUD_REGION   = var.region
            DAGSTER_POSTGRES_HOST = google_compute_address.postgres.address
            ARTIFACT_BUCKET       = google_storage_bucket.data["artifacts"].name
            SHOPIFY_SHOP_DOMAIN   = var.shopify_shop_domain
            SHOPIFY_API_VERSION   = "2026-04"
          }
          content {
            name  = env.key
            value = env.value
          }
        }
        env {
          name = "SHOPIFY_ADMIN_ACCESS_TOKEN"
          value_source {
            secret_key_ref {
              secret  = "shopify-admin-access-token"
              version = "1"
            }
          }
        }
        env {
          name = "DAGSTER_POSTGRES_PASSWORD"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.postgres.secret_id
              version = "latest"
            }
          }
        }
      }
      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.platform.name
          subnetwork = google_compute_subnetwork.platform.name
          tags       = ["dagster-worker"]
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.postgres, google_secret_manager_secret_iam_member.shopify_worker]
}

resource "google_cloud_run_v2_job_iam_member" "control" {
  count    = var.runtime_image != "" ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.worker[0].name
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.runtime["dagster-control"].email}"
}

resource "google_cloud_run_v2_job_iam_member" "control_read" {
  count    = var.runtime_image != "" ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.worker[0].name
  role     = "roles/run.viewer"
  member   = "serviceAccount:${google_service_account.runtime["dagster-control"].email}"
}

output "vm" {
  value = var.runtime_image != "" ? google_compute_instance.dagster[0].name : null
}
