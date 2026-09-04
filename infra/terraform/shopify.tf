variable "shopify_shop_domain" {
  type    = string
  default = "sobrecodigo.myshopify.com"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*\\.myshopify\\.com$", var.shopify_shop_domain))
    error_message = "Use the exact myshopify.com domain of the authorized store."
  }
}

# Secret value remains user-managed and is never loaded into Terraform state.
resource "google_secret_manager_secret_iam_member" "shopify_worker" {
  project   = var.project_id
  secret_id = "shopify-admin-access-token"
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime["dagster-worker"].email}"
}
