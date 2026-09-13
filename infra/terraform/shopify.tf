variable "shopify_shop_domain" {
  type    = string
  default = "habibi-parfums.myshopify.com"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*\\.myshopify\\.com$", var.shopify_shop_domain))
    error_message = "Use the exact myshopify.com domain of the authorized store."
  }
}

# Secret values remain user-managed and are never loaded into Terraform state.
# Client credentials (Dev Dashboard app) are exchanged for short-lived Admin API
# access tokens via the client credentials grant; no static token is stored.
resource "google_secret_manager_secret_iam_member" "shopify_worker_client_id" {
  project   = var.project_id
  secret_id = "hbny-shopify-client-id"
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime["dagster-worker"].email}"
}

resource "google_secret_manager_secret_iam_member" "shopify_worker_client_secret" {
  project   = var.project_id
  secret_id = "hbny-shopify-client-secret"
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime["dagster-worker"].email}"
}
