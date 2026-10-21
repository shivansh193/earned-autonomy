# Earned Autonomy: Cloud Run staging + production for the FastAPI backend.
# Long-lived pieces (registry, state bucket, service accounts, secrets, Workload
# Identity) come from infra/bootstrap.sh; this file only manages the services.

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  backend "gcs" {
    bucket = "mercurial-lamp-512205-q5-tfstate"
    prefix = "earned-autonomy"
  }
}

provider "google" {
  project = var.project
  region  = var.region
}

locals {
  runtime_sa = "ea-runtime@${var.project}.iam.gserviceaccount.com"
  envs = {
    staging = { image = var.staging_image, db_secret = "ea-database-url-staging" }
    prod    = { image = var.prod_image, db_secret = "ea-database-url-prod" }
  }
}

resource "google_cloud_run_v2_service" "api" {
  for_each = local.envs

  name                = "api-${each.key}"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = local.runtime_sa
    max_instance_request_concurrency = 40
    timeout                          = "60s"

    scaling {
      min_instance_count = 0 # scale to zero: pay only while serving
      max_instance_count = 2
    }

    containers {
      image = each.value.image
      resources {
        limits            = { cpu = "1", memory = "512Mi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      startup_probe {
        http_get { path = "/api/v1/utils/health-check/" }
        initial_delay_seconds = 2
        period_seconds        = 3
        failure_threshold     = 20
      }

      env {
        name  = "PROJECT_NAME"
        value = "Earned Autonomy (${each.key})"
      }
      env {
        name  = "FIRST_SUPERUSER"
        value = var.superuser_email
      }
      env {
        name  = "FRONTEND_HOST"
        value = "https://example.invalid"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = each.value.db_secret
            version = "latest"
          }
        }
      }
      env {
        name = "SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = "ea-secret-key"
            version = "latest"
          }
        }
      }
      env {
        name = "FIRST_SUPERUSER_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = "ea-superuser-password"
            version = "latest"
          }
        }
      }
    }
  }

  # Rollback shifts traffic with `gcloud run services update-traffic`; the next
  # deploy returns to "latest revision gets 100%".
  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}

# Public API (this is the hackathon's live demo URL).
resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = google_cloud_run_v2_service.api

  name     = each.value.name
  location = each.value.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "urls" {
  value = { for k, s in google_cloud_run_v2_service.api : k => s.uri }
}
