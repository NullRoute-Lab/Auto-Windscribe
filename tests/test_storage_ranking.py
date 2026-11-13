import json
import time
from dataclasses import asdict, replace

import pytest

from auto_windscribe.errors import AppError
from auto_windscribe.models import Metrics, Outcome, Profile, Result
from auto_windscribe.ranking import fuzzy_score, make_queue, matches, rank_results
from auto_windscribe.service import refresh_catalog
from auto_windscribe.storage import Catalog, Config, Profiles, Store
from tests.fakes import FakeVPN


def success(candidate, profile, **kwargs):
    return Result(
        candidate,
        Outcome.SUCCESS,
        "verified",
        "Simulated",
        connect_s=2,
        metrics=Metrics(30, 0, 2, True),
        context=profile.context,
        **kwargs,
    )


def test_append_only_history_and_reopen(candidate, profile, store, paths):
    store.record(success(candidate, profile))
    store.record(Result(candidate, Outcome.FAILED, "failed", "Simulated", context=profile.context))
    other = Store(paths.data / "history.sqlite3")
    try:
        assert len(other.history(profile.context)) == 2
        assert other.history("unrelated") == []
    finally:
        other.close()
    assert (paths.data / "history.sqlite3").stat().st_mode & 0o777 == 0o600


def test_transactional_resume(candidate, profile, store):
    second = replace(candidate, port=80)
    scan_id = store.new_scan([candidate, second], profile)
    store.record(success(candidate, profile), scan_id, 0)
    assert store.pending(scan_id) == [(1, second)]
    assert store.scan(scan_id)["attempts"] == 1
    assert json.loads(store.scan(scan_id)["profile"])["name"] == profile.name


def test_unknown_metrics_never_win_latency_ranking(candidate, profile):
    measured = success(candidate, profile)
    unknown = success(replace(candidate, port=80), profile)
    unknown.metrics = Metrics(https_ok=True)
    rows = rank_results([unknown, measured], "rtt")
    assert rows[0].candidate == candidate
    assert rows[1].verified.metrics.rtt_ms is None


def test_reliability_uses_all_observations(candidate, profile):
    good = success(candidate, profile)
    failures = [Result(candidate, Outcome.FAILED, "failed", "Simulated") for _ in range(4)]
    rows = rank_results([good, *failures])
    assert rows[0].success_rate == 0.2
    assert rows[0].score < rank_results([good])[0].score
    assert rows[0].score > 0


def test_cancelled_and_unknown_are_not_failed(candidate, profile):
    results = [success(candidate, profile)] + [
        Result(candidate, outcome, "code", "Simulated")
        for outcome in (Outcome.CANCELLED, Outcome.UNKNOWN)
    ]
    assert rank_results(results)[0].attempts == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_tests", 0),
        ("max_tests", 501),
        ("max_tests", True),
        ("timeout", -1),
        ("timeout", float("nan")),
        ("retries", 3),
        ("interval", 0),
        ("protocols", ["ikev2"]),
        ("protocols", []),
        ("ports", {"udp": [0]}),
        ("ports", {"tcp": [True]}),
        ("name", "../escape"),
        ("download", "false"),
        ("countries", "Netherlands"),
        ("network_label", "secret@example.com"),
    ],
)
def test_profile_validation(field, value):
    with pytest.raises(ValueError):
        Profile.from_dict({field: value})


def test_profiles_roundtrip_and_reject_unknown(paths, profile):
    profiles = Profiles(paths)
    profile = replace(profile, name="custom")
    profiles.save(profile)
    assert profiles.get("custom") == profile
    profiles.delete("custom")
    assert "custom" not in profiles.all()
    with pytest.raises(ValueError):
        Profile.from_dict({"password": "not-allowed"})


def test_invalid_consent_is_fail_closed(paths):
    (paths.config / "config.json").write_text('{"live_enabled": "true"}')
    with pytest.raises(AppError):
        Config.load(paths)


def test_fuzzy_search_and_filters(candidate):
    assert fuzzy_score("tst cty", candidate.location.search_text) > 0
    assert fuzzy_score("unrelated", candidate.location.search_text) == 0
    assert matches(candidate.location, Profile(countries=["Test Country"], cities=["Test City"]))
    assert not matches(candidate.location, Profile(countries=["Other"]))
    assert not matches(candidate.location, Profile(tier="free"))


def test_queue_dynamic_ports_diversity_limits_and_cooldown(candidate, profile):
    second = replace(candidate.location, nickname="Example Two")
    cat = Catalog([candidate.location, second], {"stealth": [443, 80], "udp": [53]}, time.time())
    p = replace(profile, protocols=["stealth", "udp"], max_tests=3)
    queue = make_queue(cat, p, [])
    assert len(queue) == 3
    assert queue[0].protocol == "stealth" and queue[1].protocol == "udp"
    assert queue[1].port == 53
    failure = Result(queue[0], Outcome.FAILED, "failed", "Simulated", context=p.context)
    assert queue[0] not in make_queue(cat, p, [failure])
    failure.context = "different-network"
    assert queue[0] in make_queue(cat, p, [failure])
    p.ports = {"udp": [443]}
    with pytest.raises(AppError):
        make_queue(cat, p, [])


def test_targeted_queue_selection_before_limits(candidate, profile):
    locations = [replace(candidate.location, nickname=f"Example {i}") for i in range(600)]
    cat = Catalog(locations, {"stealth": [443]}, time.time())
    target = replace(candidate, location=locations[-1])
    assert make_queue(cat, profile, [], selected={target.key}) == [target]


def test_catalog_roundtrip_stale_and_corrupt(paths, candidate):
    path = paths.cache / "catalog.json"
    Catalog([candidate.location], {"stealth": [443]}, 1).save(path)
    assert Catalog.load(path).stale
    path.write_text('{"invalid":true}')
    with pytest.raises(AppError):
        Catalog.load(path)


async def test_failed_refresh_preserves_valid_cache(paths, candidate):
    cached = Catalog([candidate.location], {"stealth": [443]}, time.time())
    cached.save(paths.cache / "catalog.json")
    result = await refresh_catalog(FakeVPN(), paths, 86400)
    assert result.stale and result.warning
    assert result.locations == cached.locations
    assert (
        asdict(Catalog.load(paths.cache / "catalog.json"))["locations"]
        == asdict(cached)["locations"]
    )


def test_context_does_not_depend_on_selection(profile):
    assert profile.context == replace(profile, protocols=["udp"], countries=["Elsewhere"]).context
    assert profile.context != replace(profile, settings_label="new-gui-settings").context
