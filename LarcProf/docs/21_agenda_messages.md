# 21 — Agenda partagé et messagerie (accueil LarcProf)

Rangée « Agenda | Messages » sous la frise de l'année, sur l'accueil LarcProf (`HomeWindow`) et
sur l'accueil intégré à LarcHub (`HomeWidget`). Code : `views/home_info_panel.py`.

## Principe
- **Avec Internet**, les deux cartes fonctionnent. **Sans Internet**, elles affichent
  « Disponible avec Internet » (détail technique en infobulle) : aucune copie locale, les anciens
  messages ne sont pas visibles hors ligne. Reconnexion automatique à l'actualisation suivante
  (toutes les 5 minutes tant que l'accueil est visible, ou bouton d'actualisation).
- Toutes les requêtes réseau tournent hors du thread d'interface (`common/async_task.py`).
- À chaque actualisation, la frise est recalculée ; elle n'est redessinée que si les dates de
  l'année, des trimestres ou des unités ont changé (`HomeWindow._refresh_timeline`).

## Agenda (`common/agenda_service.py`)
Calendrier Google Workspace partagé, lu par son **adresse iCal secrète** (lecture seule, sans compte
Google). Configuration dans `LarcCommon/config.ini` (non versionné) :

```ini
[Agenda]
IcsUrl = https://calendar.google.com/calendar/ical/.../basic.ics
Days = 14
```
Récurrences déroulées, journées entières, événements multi-jours, annulations ignorées ; heures
affichées à l'heure de Lomé. Seules les adresses `https://` sont acceptées.

## Messages (`common/messages_service.py`, `DbInit/cloud_messages.sql`)
Stockés **uniquement sur le cloud Supabase** (connexion propre, sans changer le mode serveur).
- Message individuel : `recipient_id` renseigné (professeur ↔ coordinateur, tout le personnel).
- Diffusion à tous : `recipient_id` NULL, **réservée aux coordinateurs** (contrôle applicatif +
  déclencheur SQL). Lue par le personnel actif (`type_teacher/coordonator/supervisor/secretary/director`),
  pas par les élèves ni les parents.
- Accusé de lecture par utilisateur (`larc_message_read`). Pas de suppression : un message se masque
  (`deleted_at`). Table à croissance libre : exception au principe gabarit, comme `student_event`.

## Installer la messagerie sur le cloud
Exécuter `DbInit/cloud_messages.sql` une fois sur Supabase (idempotent). Tant que les tables n'existent
pas, la carte Messages affiche « La messagerie n'est pas encore installée sur le serveur cloud ».

## Tests
`tests/test_messages_service.py` (base PostgreSQL temporaire locale, créée puis supprimée),
`tests/test_agenda_service.py` (calendrier d'exemple), `tests/test_home_info_panel.py` (panneau, service simulé).
