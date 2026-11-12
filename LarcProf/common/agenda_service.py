"""Agenda partagé Google Workspace : événements des prochaines semaines, lus via l'adresse iCal secrète.

Configuration (LarcCommon/config.ini, non versionné) :
    [Agenda]
    IcsUrl = https://calendar.google.com/calendar/ical/.../basic.ics
    Days = 14            ; optionnel

Lecture seule, sans compte Google. Pas de copie locale : sans Internet, l'agenda est indisponible.
Bloquant : à appeler depuis un thread de travail.
"""
from __future__ import annotations

import configparser
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

DEFAULT_DAYS = 14
FETCH_TIMEOUT_S = 10
MAX_ICS_BYTES = 5 * 1024 * 1024
LOCAL_TZ = ZoneInfo("Africa/Lome")     # fuseau d'affichage de l'école (UTC+0, sans changement d'heure)


class AgendaUnavailable(Exception):
    """Agenda non configuré, hors ligne, ou calendrier illisible."""


@dataclass(frozen=True)
class AgendaEvent:
    day: date                  # jour de début (fuseau de l'école)
    start: Optional[time]      # None = événement sur la journée
    end: Optional[time]
    title: str
    location: str = ''
    all_day: bool = False


def load_settings(cfg_path: str | None = None) -> tuple[str, int]:
    """(url, jours) depuis config.ini ; url vide si non configuré."""
    if cfg_path is None:
        from larccommon.config_loader import find_cfg
        cfg_path = find_cfg()
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(cfg_path, encoding='utf-8')
    url = cfg.get('Agenda', 'IcsUrl', fallback='').strip()
    days = cfg.getint('Agenda', 'Days', fallback=DEFAULT_DAYS)
    return url, max(1, min(days, 60))


def fetch_ics(url: str) -> bytes:
    if not url.lower().startswith('https://'):
        raise AgendaUnavailable("L'adresse de l'agenda doit commencer par https://")
    req = urllib.request.Request(url, headers={'User-Agent': 'LarcProf'})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
            data = resp.read(MAX_ICS_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise AgendaUnavailable(f"Agenda refusé par le serveur (HTTP {exc.code}). "
                                "Vérifiez l'adresse iCal secrète.") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise AgendaUnavailable("Agenda injoignable (pas d'Internet ?)") from exc
    if len(data) > MAX_ICS_BYTES:
        raise AgendaUnavailable("Calendrier trop volumineux")
    return data


def parse_events(ics: bytes, today: date, days: int = DEFAULT_DAYS) -> list[AgendaEvent]:
    """Événements dont le début tombe entre aujourd'hui (inclus) et today+days (exclu),
    récurrences déroulées, triés par jour puis heure."""
    import icalendar
    import recurring_ical_events
    try:
        cal = icalendar.Calendar.from_ical(ics)
    except Exception as exc:
        raise AgendaUnavailable("Calendrier illisible") from exc

    horizon = today + timedelta(days=days)
    out: list[AgendaEvent] = []
    for ev in recurring_ical_events.of(cal).between(today, horizon):
        if str(ev.get('STATUS', '')).upper() == 'CANCELLED':
            continue
        start = ev.get('DTSTART').dt
        end_prop = ev.get('DTEND')
        end = end_prop.dt if end_prop is not None else None
        title = str(ev.get('SUMMARY', '') or '').strip() or '(sans titre)'
        location = str(ev.get('LOCATION', '') or '').strip()

        if isinstance(start, datetime):
            if start.tzinfo is not None:
                start = start.astimezone(LOCAL_TZ)
                if isinstance(end, datetime) and end.tzinfo is not None:
                    end = end.astimezone(LOCAL_TZ)
            ev_day, s_t = start.date(), start.time().replace(second=0, microsecond=0)
            e_t = end.time().replace(second=0, microsecond=0) if isinstance(end, datetime) else None
            if not (today <= ev_day < horizon):
                continue
            out.append(AgendaEvent(ev_day, s_t, e_t, title, location, False))
        else:
            # Journée entière (DTSTART est une date). Un événement multi-jours a déjà commencé
            # avant aujourd'hui : on le montre dès aujourd'hui et jusqu'à sa fin.
            last = (end - timedelta(days=1)) if isinstance(end, date) and end > start else start
            d = max(start, today)
            while d <= last and d < horizon:
                out.append(AgendaEvent(d, None, None, title, location, True))
                d += timedelta(days=1)

    out.sort(key=lambda e: (e.day, e.start is not None, e.start or time.min, e.title.lower()))
    return out


def load_agenda(today: date | None = None, cfg_path: str | None = None) -> list[AgendaEvent]:
    url, days = load_settings(cfg_path)
    if not url:
        raise AgendaUnavailable("Agenda non configuré (section [Agenda] IcsUrl de config.ini).")
    return parse_events(fetch_ics(url), today or datetime.now(LOCAL_TZ).date(), days)
