from monitor.cloudrun import latest_with_tags, pinned_to, short, tag_urls
from monitor.run import decision_from


def test_decision_records_enforced_level_not_rule_level():
    bundle = {"mr_iid": 6, "commit": "c", "areas": ["route:GET /x"], "provenance": {"pipeline_id": 1},
              "risk": {"final": 0.15, "rule_score": 0.15}, "autonomy": {"level": "hands_off", "thresholds": {}}}
    e = decision_from(bundle, "6", "supervised")
    assert e["level"] == "supervised" and e["rule_level"] == "hands_off" and e["areas"] == ["route:GET /x"]
    unlabelled = decision_from(bundle, "12", "none")
    assert unlabelled["level"] == "supervised" and unlabelled["unreviewed"] is True  # manual gate, not earned
    assert decision_from(bundle, "6", None)["level"] == "hands_off"  # no routing info at all


def test_traffic_shapes():
    prev, cand = "projects/p/locations/r/services/s/revisions/api-00001", "projects/p/locations/r/services/s/revisions/api-00002"
    assert short(prev) == "api-00001"
    t = latest_with_tags(prev, cand)
    assert t[0] == {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}
    assert {x.get("tag") for x in t} == {None, "previous", "candidate"}
    rb = pinned_to(prev, cand)
    assert rb[0]["revision"] == "api-00001" and rb[0]["percent"] == 100 and sum(x["percent"] for x in rb) == 100
    assert tag_urls({"trafficStatuses": [{"tag": "previous", "uri": "https://previous---s"}, {"percent": 100}]}) == {"previous": "https://previous---s"}
