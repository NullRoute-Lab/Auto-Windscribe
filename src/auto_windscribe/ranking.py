"""Explainable ranking, context-specific history and bounded diverse candidate queues."""

import difflib
import time
from collections import defaultdict
from dataclasses import dataclass

from .errors import AppError
from .models import Candidate, Location, Outcome, Profile, Result
from .storage import Catalog


def fuzzy_score(query: str, text: str) -> float:
    query, text = query.casefold().strip(), text.casefold()
    if not query:
        return 1
    scores = []
    for word in query.split():
        if word in text:
            scores.append(1.0)
        else:
            best = max(
                (difflib.SequenceMatcher(None, word, token).ratio() for token in text.split()),
                default=0,
            )
            it = iter(text)
            subsequence = all(char in it for char in word)
            scores.append(max(best if best >= 0.72 else 0, 0.72 if subsequence else 0))
    return min(scores)


def matches(location: Location, profile: Profile, query: str = "") -> bool:
    def chosen(values: list[str], *names: str) -> bool:
        return not values or bool({v.casefold() for v in values} & {n.casefold() for n in names})

    return (
        chosen(profile.countries, location.country, location.region)
        and chosen(profile.cities, location.city)
        and chosen(profile.locations, location.nickname, location.key)
        and (profile.tier == "all" or profile.tier == location.tier)
        and fuzzy_score(query, location.search_text) > 0
    )


@dataclass
class Ranked:
    candidate: Candidate
    score: float
    attempts: int
    successes: int
    latest: Result
    verified: Result | None
    explanation: str

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0


def rank_results(results: list[Result], sort: str = "score") -> list[Ranked]:
    grouped: dict[str, list[Result]] = defaultdict(list)
    for result in results:
        grouped[result.candidate.key].append(result)
    ranked = []
    now = time.time()
    for group in grouped.values():
        group.sort(key=lambda r: r.timestamp, reverse=True)
        valid = [r for r in group if r.outcome in (Outcome.SUCCESS, Outcome.FAILED)]
        successes = [r for r in valid if r.outcome == Outcome.SUCCESS]
        good = successes[0] if successes else None
        # Bayesian smoothing prevents a single lucky observation dominating repeated reliability.
        reliability = (len(successes) + 1) / (len(valid) + 2)
        score = 0.0
        if good:
            score = 60 * reliability
            m = good.metrics
            score += 15 * max(0, 1 - m.loss_pct / 100) if m.loss_pct is not None else 0
            score += 10 / (1 + m.rtt_ms / 100) if m.rtt_ms is not None else 0
            score += 5 / (1 + m.jitter_ms / 20) if m.jitter_ms is not None else 0
            score += 5 / (1 + good.connect_s / 10) if good.connect_s is not None else 0
            score += 5 * max(0, 1 - (now - good.timestamp) / 604800)
        explanation = (
            "60×smoothed reliability + 15×delivery + 10×RTT + 5×jitter + "
            "5×connection time + 5×7-day freshness; unknown quality earns 0"
        )
        ranked.append(
            Ranked(
                group[0].candidate,
                round(score, 2),
                len(valid),
                len(successes),
                group[0],
                good,
                explanation,
            )
        )

    def key(row: Ranked) -> tuple[bool, float]:
        good = row.verified
        value: float | None = None
        if sort == "score":
            value = -row.score
        elif sort == "success":
            value = -row.success_rate
        elif good:
            value = {
                "rtt": good.metrics.rtt_ms,
                "loss": good.metrics.loss_pct,
                "connect": good.connect_s,
                "recent": -good.timestamp,
            }.get(sort)
        return (value is None, value if value is not None else 0)

    if sort not in ("score", "success", "rtt", "loss", "connect", "recent"):
        raise ValueError("Unknown ranking order")
    return sorted(ranked, key=key)


def make_queue(
    catalog: Catalog,
    profile: Profile,
    history: list[Result],
    query: str = "",
    selected: set[str] | None = None,
) -> list[Candidate]:
    profile.validate()
    locations = [loc for loc in catalog.locations if loc.available and matches(loc, profile, query)]
    # Nickname arguments must be unique, otherwise CLI random selection could mislabel a result.
    counts: dict[str, int] = defaultdict(int)
    for loc in catalog.locations:
        counts[loc.nickname.casefold()] += 1
    locations = [loc for loc in locations if counts[loc.nickname.casefold()] == 1]
    selected_ports = {}
    for proto in profile.protocols:
        supported = catalog.capabilities.get(proto, [])
        if not supported:
            raise AppError(
                "protocol_unavailable", f"{proto} is not in the discovered capabilities."
            )
        ports = profile.ports.get(proto, supported[:1])
        if any(port not in supported for port in ports):
            raise AppError("unsupported_port", f"Unsupported {proto} port; CLI fallback refused.")
        selected_ports[proto] = list(dict.fromkeys(ports))
    # Round-robin locations for every protocol/port sweep; max_tests counts actual attempts.
    queue = [
        Candidate(loc, proto, ports[index])
        for index in range(max(map(len, selected_ports.values()), default=0))
        for loc in locations
        for proto, ports in selected_ports.items()
        if index < len(ports)
    ]
    if selected is not None:
        queue = [c for c in queue if c.key in selected]
    contextual = [r for r in history if r.context == profile.context]
    ranking = {row.candidate.key: row for row in rank_results(contextual)}
    now = time.time()
    queue = [
        c
        for c in queue
        if not (
            c.key in ranking
            and ranking[c.key].latest.outcome == Outcome.FAILED
            and now - ranking[c.key].latest.timestamp < profile.cooldown
        )
    ]
    # Reserve exploration as well as prioritizing known successes; retain protocol preference order.
    successes = sorted(
        (c for c in queue if c.key in ranking and ranking[c.key].verified),
        key=lambda c: -ranking[c.key].score,
    )
    fresh = [c for c in queue if c not in successes]
    limit = min(profile.max_tests, 5) if profile.mode == "fast" else profile.max_tests
    n = min(len(successes), max(1, limit // 2)) if fresh else limit
    return (successes[:n] + fresh + successes[n:])[:limit]
