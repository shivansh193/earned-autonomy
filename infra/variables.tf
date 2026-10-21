variable "project" {
  type    = string
  default = "mercurial-lamp-512205-q5"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "staging_image" {
  type        = string
  description = "Image digest or tag for api-staging"
}

variable "prod_image" {
  type        = string
  description = "Image digest or tag for api-prod"
}

variable "superuser_email" {
  type    = string
  default = "admin@example.com"
}
