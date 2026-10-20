"""Terraform plan -> infrastructure evidence: what changes, what is risky, what it costs."""

from tfplan.parse import analyse

__all__ = ["analyse"]
