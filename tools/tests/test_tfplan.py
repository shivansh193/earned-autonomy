from risk.bundle import build_bundle
from risk.engine import decide
from risk.ledger import INITIAL
from tfplan import analyse
from tfplan.parse import cloud_run_profile, monthly_cost


def run_service(name, min_instances=1, cpu="1", memory="512Mi", ingress="INGRESS_TRAFFIC_ALL"):
    return {
        "name": name,
        "ingress": ingress,
        "template": [{
            "scaling": [{"min_instance_count": min_instances, "max_instance_count": 3}],
            "containers": [{"image": "img:1", "resources": [{"limits": {"cpu": cpu, "memory": memory}}]}],
        }],
    }


def rc(address, rtype, actions, before, after):
    return {"address": address, "mode": "managed", "type": rtype, "name": address.split(".")[-1],
            "change": {"actions": actions, "before": before, "after": after}}


def plan(*changes):
    return {"format_version": "1.2", "terraform_version": "1.9.8", "resource_changes": list(changes)}


def test_image_update_is_not_risky():
    p = plan(rc("google_cloud_run_v2_service.api_prod", "google_cloud_run_v2_service", ["update"],
                run_service("api-prod"), run_service("api-prod")))
    out = analyse(p)
    assert out["changes"] == [{"type": "update", "resource": "google_cloud_run_v2_service.api_prod",
                               "resource_type": "google_cloud_run_v2_service", "risky": False, "reason": None}]
    assert out["cost_delta_usd_month"] == 0.0


def test_replacing_database_is_risky_and_escalates():
    db = {"name": "app-db", "deletion_protection": True}
    p = plan(rc("google_sql_database_instance.db", "google_sql_database_instance", ["delete", "create"], db, dict(db)))
    infra = analyse(p)
    assert infra["changes"][0]["type"] == "replace" and infra["changes"][0]["risky"]
    assert "stateful" in infra["changes"][0]["reason"]
    b = build_bundle(mr_iid=1, commit="c", pipeline_id=1, changed_files=["infra/db.tf"], routes=[],
                     tests={"passed": 1, "failed": 0, "skipped": 0}, security=[],
                     replay={"status_mismatches": 0, "body_diffs_real": 0}, infra=infra)
    d = decide(b, dict(INITIAL), {})
    assert d["autonomy"]["level"] == "assisted"
    assert "infra:google_sql_database_instance.db" in b["areas"]


def test_public_iam_grant_and_deletion_protection_off():
    p = plan(
        rc("google_storage_bucket_iam_member.public", "google_storage_bucket_iam_member", ["create"], None,
           {"bucket": "b", "role": "roles/storage.objectViewer", "member": "allUsers"}),
        rc("google_sql_database_instance.db", "google_sql_database_instance", ["update"],
           {"name": "db", "deletion_protection": True}, {"name": "db", "deletion_protection": False}),
        rc("google_project_iam_member.ci", "google_project_iam_member", ["create"], None,
           {"role": "roles/editor", "member": "serviceAccount:ci@x"}),
    )
    reasons = {c["resource"]: c["reason"] for c in analyse(p)["changes"]}
    assert "public access" in reasons["google_storage_bucket_iam_member.public"]
    assert "deletion protection" in reasons["google_sql_database_instance.db"]
    assert "broad role roles/editor" in reasons["google_project_iam_member.ci"]


def test_prod_min_instances_to_zero_and_cost_delta():
    p = plan(rc("google_cloud_run_v2_service.api_prod", "google_cloud_run_v2_service", ["update"],
                run_service("api-prod", min_instances=1), run_service("api-prod", min_instances=0)))
    out = analyse(p)
    assert out["changes"][0]["risky"] and "min instances set to 0" in out["changes"][0]["reason"]
    assert out["cost_delta_usd_month"] < 0
    # staging scaling to zero is fine
    p2 = plan(rc("google_cloud_run_v2_service.api_staging", "google_cloud_run_v2_service", ["update"],
                 run_service("api-staging", min_instances=1), run_service("api-staging", min_instances=0)))
    assert analyse(p2)["changes"][0]["risky"] is False


def test_cost_model():
    prof = cloud_run_profile(run_service("x", min_instances=2, cpu="1000m", memory="1Gi"))
    assert prof == {"name": "x", "min_instances": 2, "cpu": 1.0, "memory_gib": 1.0, "ingress": "INGRESS_TRAFFIC_ALL"}
    assert round(monthly_cost(prof), 2) == round(2 * (0.000018 + 0.000002) * 30 * 24 * 3600, 2)
    assert cloud_run_profile(run_service("y", memory="500M"))["memory_gib"] < 0.5


def test_no_op_and_data_sources_ignored():
    p = plan(rc("google_cloud_run_v2_service.a", "google_cloud_run_v2_service", ["no-op"], run_service("a"), run_service("a")),
             {"address": "data.google_project.p", "mode": "data", "type": "google_project", "change": {"actions": ["read"]}})
    out = analyse(p)
    assert out["changes"] == [] and out["summary"] == {"no-op": 1}


def test_attribute_changes_list_what_changes_and_mask_sensitive():
    before = run_service("api-prod")
    after = run_service("api-prod")
    after["template"][0]["scaling"][0]["max_instance_count"] = 1
    after["template"][0]["containers"][0]["env"] = [{"name": "DB_PASSWORD", "value": "s3cret"}]
    ch = rc("google_cloud_run_v2_service.api_prod", "google_cloud_run_v2_service", ["update"], before, after)
    ch["change"]["after_sensitive"] = {"template": [{"containers": [{"env": [{"value": True}]}]}]}
    out = analyse(plan(ch))
    changed = out["attribute_changes"][0]["changed"]
    assert changed["template[0].scaling[0].max_instance_count"] == [3, 1]
    assert changed["template[0].containers[0].env[0].value"] == [None, "(sensitive)"]
    assert "s3cret" not in str(out)
    assert out["changes"][0]["risky"] is False  # the rules don't see a capacity cut; the Infra agent should


def test_carbon_delta_follows_always_on_capacity():
    p = plan(rc("google_cloud_run_v2_service.api_prod", "google_cloud_run_v2_service", ["update"],
                run_service("api-prod", min_instances=0), run_service("api-prod", min_instances=1)))
    out = analyse(p)
    # 1 vCPU + 0.5 GiB always on: (2.485 + 0.196) W x 1.1 PUE x 720 h = 2.12 kWh x 0.413 = 0.88 kg
    assert 0.8 < out["carbon_delta_kg_co2e_month"] < 0.95
    back = analyse(plan(rc("google_cloud_run_v2_service.api_prod", "google_cloud_run_v2_service", ["update"],
                           run_service("api-prod", min_instances=1), run_service("api-prod", min_instances=0))))
    assert back["carbon_delta_kg_co2e_month"] == -out["carbon_delta_kg_co2e_month"]
