# Secret value remains user-managed and is never loaded into Terraform state.
resource "google_secret_manager_secret_iam_member" "klaviyo_worker" {
  project   = var.project_id
  secret_id = "klaviyo-api-key"
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime["dagster-worker"].email}"
}
