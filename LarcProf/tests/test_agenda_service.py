"""Tests de l'agenda : lecture d'un calendrier iCal (format Google Agenda), sans réseau."""
from __future__ import annotations

from datetime import date, time

import pytest

pytest.importorskip("icalendar")
pytest.importorskip("recurring_ical_events")

from common import agenda_service as ag  # noqa: E402

TODAY = date(2026, 9, 28)      # lundi

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//FR
BEGIN:VEVENT
UID:a1
DTSTART:20260929T080000Z
DTEND:20260929T090000Z
SUMMARY:Reunion pedagogique
LOCATION:Salle des profs
END:VEVENT
BEGIN:VEVENT
UID:a2
DTSTART;TZID=Europe/Paris:20260930T100000
DTEND;TZID=Europe/Paris:20260930T113000
SUMMARY:Conseil de classe
END:VEVENT
BEGIN:VEVENT
UID:a3
DTSTART;VALUE=DATE:20261002
DTEND;VALUE=DATE:20261003
SUMMARY:Ferie
END:VEVENT
BEGIN:VEVENT
UID:a4
DTSTART;VALUE=DATE:20260926
DTEND;VALUE=DATE:20260930
SUMMARY:Stage
END:VEVENT
BEGIN:VEVENT
UID:a5
DTSTART:20260928T140000Z
DTEND:20260928T150000Z
RRULE:FREQ=WEEKLY;COUNT=4
SUMMARY:Coordination hebdo
END:VEVENT
BEGIN:VEVENT
UID:a6
DTSTART:20260930T150000Z
DTEND:20260930T160000Z
STATUS:CANCELLED
SUMMARY:Annule
END:VEVENT
BEGIN:VEVENT
UID:a7
DTSTART:20261020T080000Z
DTEND:20261020T090000Z
SUMMARY:Trop loin
END:VEVENT
BEGIN:VEVENT
UID:a8
DTSTART:20260920T080000Z
DTEND:20260920T090000Z
SUMMARY:Deja passe
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture()
def events():
    return ag.parse_events(ICS, TODAY, 14)


def titles(evs):
    return [e.title for e in evs]


def test_window_keeps_only_the_next_two_weeks(events):
    assert "Trop loin" not in titles(events) and "Deja passe" not in titles(events)
    assert "Annule" not in titles(events)


def test_sorted_by_day_then_time_all_day_first(events):
    keys = [(e.day, e.start is not None, e.start or time.min) for e in events]
    assert keys == sorted(keys)


def test_utc_event_is_shown_in_school_time(events):
    e = next(x for x in events if x.title == "Reunion pedagogique")
    assert (e.day, e.start, e.end, e.location) == (date(2026, 9, 29), time(8, 0), time(9, 0), "Salle des profs")


def test_timezone_event_is_converted(events):
    e = next(x for x in events if x.title == "Conseil de classe")
    # 10:00 Paris (UTC+2 fin septembre) = 08:00 à Lomé (UTC+0)
    assert (e.day, e.start, e.end) == (date(2026, 9, 30), time(8, 0), time(9, 30))


def test_all_day_event(events):
    e = next(x for x in events if x.title == "Ferie")
    assert e.all_day and e.start is None and e.day == date(2026, 10, 2)


def test_multi_day_event_started_before_today_shows_until_its_end(events):
    days = [e.day for e in events if e.title == "Stage"]
    assert days == [date(2026, 9, 28), date(2026, 9, 29)]      # 26-29 sept. : reste lundi et mardi


def test_weekly_recurrence_is_expanded(events):
    days = [e.day for e in events if e.title == "Coordination hebdo"]
    # fenêtre = 28/09 → 11/10 inclus : la 3e occurrence (12/10) est hors des 14 jours
    assert days == [date(2026, 9, 28), date(2026, 10, 5)]


def test_unreadable_calendar_is_reported():
    with pytest.raises(ag.AgendaUnavailable):
        ag.parse_events(b"pas un calendrier", TODAY)


def test_non_https_address_is_refused():
    with pytest.raises(ag.AgendaUnavailable):
        ag.fetch_ics("http://exemple.org/x.ics")
    with pytest.raises(ag.AgendaUnavailable):
        ag.fetch_ics("file:///C:/secret.ics")


def test_missing_configuration(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text("[Agenda]\nIcsUrl =\n", encoding="utf-8")
    with pytest.raises(ag.AgendaUnavailable, match="non configuré"):
        ag.load_agenda(TODAY, str(cfg))


def test_settings_are_read_and_bounded(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text("[Agenda]\nIcsUrl = https://x/y.ics\nDays = 999\n", encoding="utf-8")
    assert ag.load_settings(str(cfg)) == ("https://x/y.ics", 60)
