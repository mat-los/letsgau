"""Tests for the merge stage (Phase 3): matching, AI tie-break, status, cleanup."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from letsgau import merge
from letsgau.ai_runner import AiError, AiRequest, AiResult
from letsgau.merge import Decisions, MergeReport, Pair, apply, candidate_score, title_similarity
from letsgau.models import Event, EventsFile, EventStatus, Usage

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "merge"
DAY1 = date(2026, 11, 9)  # first run
DAY2 = date(2026, 11, 12)  # second run (weekly:mon,thu)
COVERED = {"Schongau", "Peiting", "Altenstadt", "Herzogsägmühle"}
MUNICIPALITY = {"Herzogsägmühle": "Peiting", "Peiting": "Peiting", "Schongau": "Schongau"}


def ev(
    title: str,
    start: str,
    place: str = "Schongau",
    *,
    end: str | None = None,
    time: str | None = None,
    url: str | None = None,
    seen: date = DAY1,
    **extra: Any,
) -> Event:
    from letsgau.crawl import event_id

    start_date = date.fromisoformat(start)
    data: dict[str, Any] = {
        "id": event_id(start_date, title, place),
        "title": {"de": title, "en": title},
        "description": {"de": f"{title} in {place}.", "en": f"{title} in {place}."},
        "venue": {"name": None, "place": place, "address": None},
        "schedule": {"start_date": start, "end_date": end, "start_time": time},
        "primary_tag": "market-festival",
        "tags": ["market-festival"],
        "sources": [
            {
                "url": url or f"https://example.org/{title.lower().replace(' ', '-')}",
                "primary": True,
                "first_seen": seen.isoformat(),
                "last_seen": seen.isoformat(),
            }
        ],
        "first_seen": seen.isoformat(),
        "last_verified": seen.isoformat(),
    }
    data.update(extra)
    return Event.model_validate(data)


def run(
    stored: EventsFile | None,
    crawled: Sequence[Event],
    today: date,
    decide: merge.Decide = merge.no_ai,
) -> tuple[EventsFile, MergeReport]:
    report = MergeReport()
    result = apply(
        stored,
        crawled,
        today=today,
        covered_places=COVERED,
        municipality_of=MUNICIPALITY,
        deep_check_days=7,
        unconfirmed_after=2,
        decide=decide,
        report=report,
    )
    return result, report


def by_title(events: EventsFile) -> dict[str, Event]:
    return {e.title.de: e for e in events.events}


# --- similarity ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Schongauer Christkindlmarkt", "Christkindlmarkt Schongau", 1.0),
        ("Schongauer Weihnachtszauber 2026", "Weihnachtszauber", 1.0),
        ("EC Peiting – EV Füssen", "EC Peiting vs. EV Füssen", 1.0),
        ("Adventsbasar im Pfarrheim", "Adventsbasar", 0.9),
    ],
)
def test_similar_titles(a: str, b: str, expected: float) -> None:
    assert title_similarity(a, b, ["Schongau", "Peiting"]) == expected


def test_different_titles_score_low() -> None:
    assert title_similarity("Kabarett im Ballenhaus", "Wochenmarkt", ["Schongau"]) < 0.5
    assert title_similarity("Konzert Kinderchor", "Konzert Blaskapelle") < merge.CLEAR


def test_candidate_score_needs_overlap_place_and_time() -> None:
    a = ev("Leonhardiritt", "2026-11-15", "Peiting", time="10:00")
    assert candidate_score(ev("Leonhardiritt", "2026-11-15", "Peiting", time="10:30"), a) == 1.0
    assert candidate_score(ev("Leonhardiritt", "2026-11-16", "Peiting"), a) == 0.0  # date
    assert candidate_score(ev("Leonhardiritt", "2026-11-15", "Schongau"), a) == 0.0  # place
    assert candidate_score(ev("Leonhardiritt", "2026-11-15", "Peiting", time="19:00"), a) == 0.0
    # districts count as their municipality
    district = ev("Leonhardiritt", "2026-11-15", "Herzogsägmühle")
    assert candidate_score(district, a, MUNICIPALITY) == 1.0
    # multi-day overlap
    market = ev("Christkindlmarkt", "2026-11-27", end="2026-12-13")
    assert candidate_score(ev("Christkindlmarkt", "2026-12-05"), market) == 1.0


# --- acceptance #11: two runs, no duplicates, sources appended ---------------------------


def test_two_runs_create_no_duplicates_and_append_sources() -> None:
    first, report1 = run(
        None,
        [
            ev("Schongauer Christkindlmarkt", "2026-11-27", end="2026-12-13"),
            ev("EC Peiting – EV Füssen", "2026-11-13", "Peiting", time="20:00"),
        ],
        DAY1,
    )
    assert len(report1.added) == 2
    second, report2 = run(
        first,
        [
            # same events, other titles/sources, from a later run
            ev(
                "Christkindlmarkt Schongau",
                "2026-11-27",
                end="2026-12-13",
                url="https://kreisbote.example/markt",
                seen=DAY2,
            ),
            ev(
                "EC Peiting vs. EV Füssen",
                "2026-11-13",
                "Peiting",
                time="20:00",
                url="https://ecpeiting.example/spielplan",
                seen=DAY2,
            ),
        ],
        DAY2,
    )
    assert len(second.events) == 2
    assert report2.added == []
    assert len(report2.merged_fuzzy) == 2
    market = by_title(second)["Christkindlmarkt Schongau"]
    assert market.id == "2026-11-27-schongauer-christkindlmarkt"  # stored id kept
    assert [s.url for s in market.sources] == [
        "https://example.org/schongauer-christkindlmarkt",
        "https://kreisbote.example/markt",
    ]
    assert market.sources[0].primary and not market.sources[1].primary
    assert market.first_seen == DAY1 and market.last_verified == DAY2


def test_unclear_pair_is_decided_by_the_ai() -> None:
    stored, _ = run(None, [ev("Adventskonzert der Stadtkapelle", "2026-12-05", time="19:00")], DAY1)
    crawled = [ev("Konzert im Advent", "2026-12-05", time="19:30", seen=DAY2)]
    seen: list[tuple[Sequence[Pair], Sequence[Event]]] = []

    def says(answer: bool) -> merge.Decide:
        def decide(pairs: Sequence[Pair], new: Sequence[Event]) -> Decisions:
            seen.append((pairs, new))
            return Decisions(same={p.number for p in pairs} if answer else set())

        return decide

    merged, report = run(stored, crawled, DAY2, says(True))
    pairs, new = seen[0]
    assert len(pairs) == 1 and merge.UNCLEAR <= pairs[0].score < merge.CLEAR
    # the new event and the stored one both get the topic check
    assert [e.title.de for e in new] == ["Konzert im Advent", "Adventskonzert der Stadtkapelle"]
    assert len(merged.events) == 1
    assert merged.events[0].id == stored.events[0].id
    assert report.merged_fuzzy == [(crawled[0].id, stored.events[0].id)]

    separate, report = run(stored, crawled, DAY2, says(False))
    assert len(separate.events) == 2
    assert report.added == [crawled[0].id]


def test_same_slot_pair_goes_to_the_ai_even_with_other_words() -> None:
    a = ev("Fibromyalgiesymposium in Schongau", "2026-11-12", time="18:00")
    b = ev("Symposium Fibromyalgie", "2026-11-12", time="18:00", url="https://other.example/x")
    other_time = ev("Symposium Fibromyalgie", "2026-11-12", time="19:00")
    no_time = ev("Symposium Fibromyalgie", "2026-11-12")
    assert merge.candidate_score(b, a) < merge.CLEAR  # not merged without the AI
    assert merge.same_slot(b, a)
    assert not merge.same_slot(other_time, a) and not merge.same_slot(no_time, a)
    seen: list[Sequence[Pair]] = []

    def decide(pairs: Sequence[Pair], new: Sequence[Event]) -> Decisions:
        seen.append(pairs)
        return Decisions(same={p.number for p in pairs})

    merged, _ = run(None, [a, b], DAY1, decide)
    assert [(p.new.title.de, p.old.title.de) for p in seen[0]] == [
        ("Symposium Fibromyalgie", "Fibromyalgiesymposium in Schongau")
    ]
    assert len(merged.events) == 1
    assert len(merged.events[0].sources) == 2


def test_stored_duplicates_are_cleaned_up_with_the_ai() -> None:
    old = ev("Handy & Co. – Sprechstunde", "2026-11-20", time="14:00")
    dup = ev(
        "Projekt Handy und Co.", "2026-11-20", time="14:00", url="https://b.example/h", seen=DAY2
    )
    item = ev("Fragerunde", "2026-11-20", time="14:30", parent_id=dup.id)
    other = ev("Kino: Alte Liebe", "2026-11-20", time="14:00")  # same slot, different event
    stored = EventsFile(updated_at="2026-11-10T00:00:00Z", events=[old, dup, item, other])
    crawled = [ev("Projekt Handy und Co.", "2026-11-20", time="14:00", url="https://c.example")]
    asked: list[tuple[str, str]] = []

    def decide(pairs: Sequence[Pair], new: Sequence[Event]) -> Decisions:
        asked.extend((p.new.title.de, p.old.title.de) for p in pairs)
        handy = {p.number for p in pairs if "Kino" not in p.new.title.de + p.old.title.de}
        return Decisions(same=handy)

    merged, report = run(stored, crawled, DAY2, decide)
    assert ("Projekt Handy und Co.", "Handy & Co. – Sprechstunde") in asked
    assert sorted(e.id for e in merged.events) == sorted([old.id, item.id, other.id])
    kept = next(e for e in merged.events if e.id == old.id)
    assert {s.url for s in kept.sources} >= {"https://b.example/h", "https://c.example"}
    assert next(e for e in merged.events if e.title.de == "Fragerunde").parent_id == old.id
    assert (dup.id, old.id) in report.merged_fuzzy
    # without an AI answer nothing is merged
    untouched, _ = run(stored, [], DAY2)
    assert len(untouched.events) == 4


def test_duplicates_within_one_run_are_merged() -> None:
    merged, _ = run(
        None,
        [
            ev("Adventsbasar im Pfarrheim", "2026-11-21", end="2026-11-22"),
            ev("Adventsbasar", "2026-11-21", end="2026-11-22", url="https://rce.example/x"),
        ],
        DAY1,
    )
    assert len(merged.events) == 1
    assert len(merged.events[0].sources) == 2


# --- acceptance #12: cancellation, postponement, vanished event, program items -----------


def test_cancellation_from_the_crawl() -> None:
    stored, _ = run(None, [ev("Leonhardiritt", "2026-11-15", "Peiting")], DAY1)
    merged, report = run(
        stored, [ev("Leonhardiritt", "2026-11-15", "Peiting", status="cancelled", seen=DAY2)], DAY2
    )
    event = merged.events[0]
    assert event.status is EventStatus.CANCELLED
    assert report.cancelled == [event.id]


def test_postponement_links_old_to_new_date() -> None:
    old = ev("Kabarett im Ballenhaus", "2026-11-20", time="20:00")
    stored, _ = run(None, [old], DAY1)
    new_date = ev("Kabarett im Ballenhaus", "2026-12-04", time="20:00", seen=DAY2)
    postponed = ev(
        "Kabarett im Ballenhaus",
        "2026-11-20",
        time="20:00",
        seen=DAY2,
        status="postponed",
        postponed_to=new_date.id,
    )
    merged, report = run(stored, [postponed, new_date], DAY2)
    events = {e.id: e for e in merged.events}
    assert events[old.id].status is EventStatus.POSTPONED
    assert events[old.id].postponed_to == new_date.id
    assert events[new_date.id].status is EventStatus.SCHEDULED
    assert report.postponed == [old.id]


def test_vanished_event_becomes_unconfirmed_and_recovers() -> None:
    vanishing = ev("Malabend im Café", "2026-11-14", "Altenstadt", time="19:00")
    later = ev("Wintermarkt", "2026-12-01", "Altenstadt")  # outside the deep-check window
    elsewhere = ev("Konzert", "2026-11-13", "Burggen")  # municipality not crawled
    stored, _ = run(None, [vanishing, later, elsewhere], DAY1)

    after1, r1 = run(stored, [], DAY1 + timedelta(days=1))
    e = {x.id: x for x in after1.events}
    assert e[vanishing.id].misses == 1 and e[vanishing.id].status is EventStatus.SCHEDULED
    assert e[later.id].misses == 0
    assert e[elsewhere.id].misses == 0
    assert r1.missed == [vanishing.id]

    after2, r2 = run(after1, [], DAY1 + timedelta(days=2))
    e = {x.id: x for x in after2.events}
    assert e[vanishing.id].misses == 2
    assert e[vanishing.id].status is EventStatus.UNCONFIRMED
    assert r2.unconfirmed == [vanishing.id]

    found = ev("Malabend im Café", "2026-11-14", "Altenstadt", time="19:00", seen=DAY2)
    after3, _ = run(after2, [found], DAY2)
    e = {x.id: x for x in after3.events}
    assert e[vanishing.id].status is EventStatus.SCHEDULED and e[vanishing.id].misses == 0


def test_cancelled_events_are_not_counted_as_missed() -> None:
    stored, _ = run(None, [ev("Leonhardiritt", "2026-11-15", "Peiting", status="cancelled")], DAY1)
    after, report = run(stored, [], DAY2)
    assert after.events[0].misses == 0 and report.missed == []


def test_program_items_point_to_the_stored_parent() -> None:
    market = ev("Mittelaltermarkt", "2026-11-28", end="2026-11-29")
    stored, _ = run(None, [market], DAY1)
    # second run: parent under another title (fuzzy match), child points to the new id
    parent2 = ev("Mittelaltermarkt Schongau", "2026-11-28", end="2026-11-29", seen=DAY2)
    child = ev("Feuerschlucker", "2026-11-28", time="18:00", seen=DAY2, parent_id=parent2.id)
    merged, _ = run(stored, [parent2, child], DAY2)
    events = by_title(merged)
    assert len(merged.events) == 2
    assert events["Feuerschlucker"].parent_id == market.id


# --- acceptance #13: multi-day stored once, past deleted ---------------------------------


def test_multi_day_event_reported_per_day_is_stored_once() -> None:
    merged, _ = run(
        None,
        [
            ev("Christkindlmarkt", "2026-11-27", end="2026-12-13"),
            ev("Christkindlmarkt", "2026-12-05", url="https://other.example/markt"),
        ],
        DAY1,
    )
    assert len(merged.events) == 1
    assert merged.events[0].schedule.end_date == date(2026, 12, 13)


FRI_SAT = ["2026-11-13", "2026-11-14", "2026-11-20", "2026-11-21"]
SATURDAYS = ["2026-11-14", "2026-11-21"]


def test_weekday_split_series_is_stored_once() -> None:
    """A Friday/Saturday market reported twice, once per weekday, is one event."""
    whole = ev(
        "Weibsdeifi-Markt (freitags)",
        "2026-11-13",
        end="2026-11-21",
        schedule={
            "start_date": "2026-11-13",
            "end_date": "2026-11-21",
            "dates": FRI_SAT,
            "opening_hours": [
                {"days": "fri", "from": "15:00", "to": "18:00"},
                {"days": "sat", "from": "10:00", "to": "14:00"},
            ],
        },
    )
    saturdays = ev(
        "Weibsdeifi-Markt (samstags)",
        "2026-11-14",
        url="https://rce.example/sa",
        schedule={
            "start_date": "2026-11-14",
            "end_date": "2026-11-21",
            "start_time": "10:00",
            "dates": SATURDAYS,
        },
    )
    # within one crawl
    merged, _ = run(None, [whole, saturdays], DAY1)
    assert len(merged.events) == 1
    kept = merged.events[0]
    assert kept.title.de == "Weibsdeifi-Markt (freitags)"
    assert [d.isoformat() for d in kept.schedule.dates or []] == FRI_SAT
    assert len(kept.sources) == 2
    # already stored twice (older runs): cleaned up without asking the AI
    stored = EventsFile(updated_at="2026-11-10T00:00:00Z", events=[whole, saturdays])
    cleaned, report = run(stored, [], DAY2)
    assert [e.id for e in cleaned.events] == [whole.id]
    assert (saturdays.id, whole.id) in report.merged_fuzzy


def test_weekday_series_on_different_days_stay_apart() -> None:
    fridays = ev(
        "Stammtisch freitags",
        "2026-11-13",
        schedule={
            "start_date": "2026-11-13",
            "end_date": "2026-11-20",
            "dates": ["2026-11-13", "2026-11-20"],
        },
    )
    saturdays = ev(
        "Stammtisch samstags",
        "2026-11-14",
        url="https://b.example/st",
        schedule={"start_date": "2026-11-14", "end_date": "2026-11-21", "dates": SATURDAYS},
    )
    merged, _ = run(None, [fridays, saturdays], DAY1)
    assert len(merged.events) == 2


def test_past_events_are_deleted_ongoing_kept() -> None:
    stored, _ = run(
        None,
        [
            ev("Gestern", "2026-11-08"),
            ev("Vorgestern", "2026-11-07"),
            ev("Laufend", "2026-11-01", end="2026-11-30"),
        ],
        date(2026, 11, 7),
    )
    after, report = run(stored, [], DAY1)
    assert {e.title.de for e in after.events} == {"Gestern", "Laufend"}
    assert report.deleted == [ev("Vorgestern", "2026-11-07").id]


# --- topic filter ----------------------------------------------------------------------


def test_new_events_on_excluded_topics_are_dropped() -> None:
    course = ev("Erste-Hilfe-Kurs", "2026-11-20")
    market = ev("Wochenmarkt", "2026-11-20")

    def decide(pairs: Sequence[Pair], new: Sequence[Event]) -> Decisions:
        return Decisions(excluded={course.id: "First-aid courses and similar mandatory courses"})

    merged, report = run(None, [course, market], DAY1, decide)
    assert [e.title.de for e in merged.events] == ["Wochenmarkt"]
    assert report.excluded == {course.id: "First-aid courses and similar mandatory courses"}


# --- AI decider with the merge prompt ----------------------------------------------------


class FakeRunner:
    def __init__(self, answer: dict[str, Any] | None = None, error: bool = False) -> None:
        self.answer = answer
        self.error = error
        self.requests: list[AiRequest] = []

    def run(self, request: AiRequest) -> AiResult:
        self.requests.append(request)
        if self.error:
            raise AiError("claude failed")
        return AiResult(data=self.answer or {}, usage=Usage(requests=1))


def test_ai_decider_prompt_and_answer() -> None:
    answer = json.loads((FIXTURES / "haiku_answer.json").read_text(encoding="utf-8"))
    runner = FakeRunner(answer)
    topics = ["First-aid courses and similar mandatory courses"]
    decide = merge.ai_decider(runner, "claude-haiku-5-5", topics)
    old = ev("Adventskonzert der Stadtkapelle", "2026-12-05", time="19:00")
    new = ev("Konzert im Advent", "2026-12-05", time="19:30")
    course = ev("Erste-Hilfe-Kurs", "2026-11-20")
    decisions = decide([Pair(1, new, old, 0.6)], [new, course])

    assert decisions.same == {1}
    assert decisions.excluded == {course.id: topics[0]}
    request = runner.requests[0]
    assert request.model == "claude-haiku-5-5" and request.web is False
    assert "Pair 1:" in request.prompt
    assert "- A: Konzert im Advent | 2026-12-05 19:30" in request.prompt
    assert "- B: Adventskonzert der Stadtkapelle | 2026-12-05 19:00" in request.prompt
    assert f"- {course.id} | Erste-Hilfe-Kurs" in request.prompt
    assert topics[0] in request.prompt


def test_ai_decider_skips_call_without_work_and_survives_errors() -> None:
    runner = FakeRunner()
    assert merge.ai_decider(runner, "m", [])([], [ev("X", "2026-11-20")]) == Decisions()
    assert runner.requests == []  # no pairs, no topics: no call
    failing = FakeRunner(error=True)
    pair = Pair(1, ev("A", "2026-11-20"), ev("B", "2026-11-20"), 0.6)
    assert merge.ai_decider(failing, "m", ["x"])([pair], []) == Decisions()


def test_program_item_never_matches_its_main_event() -> None:
    market = ev("Christkindlmarkt", "2026-11-27", end="2026-12-13")
    choir = ev("Kinderchor am Christkindlmarkt", "2026-11-28", time="17:00", parent_id=market.id)
    assert candidate_score(choir, market) == 0.0
    merged, _ = run(None, [market, choir], DAY1)
    assert len(merged.events) == 2


def test_fresh_times_per_date_update_a_longer_stored_schedule() -> None:
    def film(dates: list[str], hours: list[dict[str, str]]) -> Event:
        schedule = {
            "start_date": dates[0],
            "end_date": dates[-1],
            "dates": dates,
            "opening_hours": hours,
        }
        return ev("Film: Test", dates[0], schedule=schedule)

    weekly = {"days": "sat", "from": "20:00", "to": "20:00"}
    old = film(["2026-11-14", "2026-11-21"], [weekly])
    new = film(["2026-11-21"], [{"days": "2026-11-21", "from": "17:30", "to": "17:30"}])
    combined = merge.combine(old, new)
    assert combined.schedule.dates == old.schedule.dates  # the longer schedule is kept
    hours = {h.days: h.from_ for h in combined.schedule.opening_hours or []}
    assert hours == {"sat": "20:00", "2026-11-21": "17:30"}
    assert merge.combine(old, film(["2026-11-21"], [])).schedule.opening_hours == [
        old.schedule.opening_hours[0]  # type: ignore[index]
    ]


def test_merged_duplicates_keep_both_categories() -> None:
    old = ev(
        "KircheKreativ: Acrylmalen",
        "2026-11-14",
        tags=["art-culture", "religion"],
        primary_tag="art-culture",
    )
    new = ev(
        "Acrylmalen (KircheKreativ)",
        "2026-11-14",
        tags=["talk-workshop", "art-culture"],
        primary_tag="talk-workshop",
    )
    combined = merge.combine(old, new)
    assert combined.tags == ["talk-workshop", "art-culture", "religion"]
    assert combined.primary_tag == new.primary_tag
    many = ev("X", "2026-11-14", tags=["concert", "party", "food-drink"], primary_tag="concert")
    assert merge.combine(old, many).tags == ["concert", "party", "food-drink"]  # at most 3


def test_merge_prompt_treats_one_date_of_a_series_as_the_same_event() -> None:
    text = " ".join((merge.PROMPTS_DIR / "merge.md").read_text(encoding="utf-8").split())
    assert "a single date of that same series" in text and "answer true" in text


def test_venue_words_and_film_prefix_do_not_count() -> None:
    a = ev(
        "Film: Heimsuchung – Eine Jahrhundertgeschichte",
        "2026-11-12",
        venue={"name": "Lagerhaus-Kino", "place": "Schongau", "address": None},
    )
    b = ev(
        "Lagerhaus-Kino: Heimsuchung – Eine Jahrhundertgeschichte",
        "2026-11-12",
        venue={"name": "Lagerhaus-Kino", "place": "Schongau", "address": None},
    )
    assert candidate_score(a, b) >= merge.CLEAR
    other = ev(
        "Film: Sinn und Sinnlichkeit",
        "2026-11-12",
        venue={"name": "Lagerhaus-Kino", "place": "Schongau", "address": None},
    )
    assert candidate_score(a, other) < merge.UNCLEAR  # two films stay apart


def test_same_slot_and_shared_source_merge_without_the_ai() -> None:
    url = "https://www.schongau-evangelisch.de/"
    a = ev('Gospelchor-Probe "Get Together"', "2026-11-28", time="10:00", url=url)
    b = ev("Gospelchor-Projekt: Probe", "2026-11-28", time="10:00", url=url)
    assert merge.clear_by_source(a, b)
    assert not merge.clear_by_source(a, ev("Gospelchor-Projekt: Probe", "2026-11-28", time="10:00"))
    assert not merge.clear_by_source(a, ev("Kinderturnen", "2026-11-28", time="10:00", url=url))

    def failing(pairs: Any, new_events: Any) -> Decisions:  # the AI is not available
        return Decisions()

    stored = EventsFile(updated_at="2026-11-12T00:00:00Z", events=[a, b])
    result = apply(stored, [], decide=failing, today=DAY1)
    assert len(result.events) == 1  # one entry left, without any AI decision


def test_a_stored_event_on_an_excluded_topic_is_removed() -> None:
    meeting = ev("Schützenverein, Jahreshauptversammlung", "2026-12-05", time="20:00")
    concert = ev("Adventskonzert", "2026-12-05", time="19:00")
    stored = EventsFile(updated_at="2026-11-12T00:00:00Z", events=[meeting, concert])

    def decide(pairs: Any, events: Any) -> Decisions:
        assert {e.id for e in events} == {meeting.id, concert.id}
        return Decisions(excluded={meeting.id: "members only"})

    report = MergeReport()
    result = apply(stored, [], decide=decide, today=DAY1, report=report)
    assert [e.title.de for e in result.events] == ["Adventskonzert"]
    assert report.excluded == {meeting.id: "members only"}


# --- wrong municipality, per-day start times, single dates of a series ---------------


def test_same_venue_on_a_shared_page_matches_across_municipalities() -> None:
    url = "https://calendar.example/altenstadt/vdk-nikolausfeier"
    venue = {"name": "Jakob-Pfeiffer-Haus", "place": "Altenstadt", "address": None}
    a = ev("VdK Pfaffenwinkel, Nikolausfeier", "2026-12-12", "Altenstadt", time="14:00", url=url)
    a = a.model_copy(update={"venue": a.venue.model_copy(update=venue)})
    b = ev("VdK Pfaffenwinkel: Nikolausfeier", "2026-12-12", time="14:00", url=url)
    b = b.model_copy(update={"venue": b.venue.model_copy(update={"name": "Jakob-Pfeiffer-Haus"})})
    assert merge.same_place(b, a) and merge.candidate_score(b, a) >= merge.CLEAR
    # the same venue name without a shared page is not enough ("Marienplatz" exists twice)
    c = b.model_copy(update={"sources": ev("x", "2026-12-12").sources})
    assert not merge.same_place(c, a) and merge.candidate_score(c, a) == 0.0
    stored = EventsFile(updated_at="2026-11-10T00:00:00Z", events=[a, b])
    merged, report = run(stored, [], DAY2)
    assert len(merged.events) == 1 and len(report.merged_fuzzy) == 1


def test_start_time_from_opening_hours_counts_for_the_slot() -> None:
    hours = [
        {"days": "2026-11-27,2027-01-06", "from": "18:00", "to": "18:00"},
        {"days": "2027-02-12", "from": "19:00", "to": "19:00"},
    ]
    dates = ["2026-11-27", "2027-01-06", "2027-02-12"]
    tour = ev("Ins Licht gerückt – Taschenlampenführung", "2026-11-27", end="2027-02-12")
    tour = tour.model_copy(
        update={
            "schedule": tour.schedule.model_validate(
                {**tour.schedule.model_dump(), "dates": dates, "opening_hours": hours}
            )
        }
    )
    other = ev("Themenführung In's Licht gerückt", "2026-11-27", end="2027-02-12", time="18:00")
    other = other.model_copy(
        update={"schedule": other.schedule.model_copy(update={"dates": tour.schedule.dates})}
    )
    assert merge.same_slot(other, tour)
    late = other.model_copy(
        update={"schedule": other.schedule.model_copy(update={"start_time": "22:00"})}
    )
    assert not merge.same_slot(late, tour) and merge.candidate_score(late, tour) == 0.0
    # an opening-hour range is no start time
    open_hours = [merge.OpeningHours(days="fri", from_="18:00", to="20:00")]
    market = tour.model_copy(
        update={"schedule": tour.schedule.model_copy(update={"opening_hours": open_hours})}
    )
    assert not merge.same_slot(other, market)


def test_single_date_of_a_series_at_the_same_time_goes_to_the_ai() -> None:
    series = ev("Fotogruppe Altenstadt: Clubabend", "2026-11-18", "Altenstadt", time="19:30")
    series = series.model_copy(
        update={
            "schedule": series.schedule.model_copy(
                update={
                    "end_date": date(2026, 11, 25),
                    "dates": [date(2026, 11, 18), date(2026, 11, 25)],
                }
            )
        }
    )
    show = ev("Bilderschau der Fotogruppe Altenstadt", "2026-11-25", "Altenstadt", time="19:30")
    unrelated = ev("Vortrag Sri Lanka", "2026-11-25", "Altenstadt", time="19:30")
    assert merge.candidate_score(show, series) < merge.UNCLEAR
    assert merge.same_slot(show, series)
    assert not merge.same_slot(unrelated, series)  # a later day needs similar titles
