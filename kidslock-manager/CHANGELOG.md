# Changelog - KidsLock Manager

## [1.7.0] - 2024-05-24
### Toegevoegd
- **Weekschema**: Stel per dag van de week een unieke tijdslimiet en bedtijd in.
- **Harde Bedtijd**: TV gaat nu direct op slot bij het bereiken van de bedtijd, ongeacht de resterende minuten.
- **Offline Locking**: Statuswijzigingen (Lock/Unlock) worden nu opgeslagen en direct uitgevoerd zodra een TV online komt.
- **Reset-functie**: Handmatige knop op het dashboard om de kijktijd van de huidige dag te wissen.

### Opgelost
- **Anti-Spook Logica**: Fix voor timers die doorliepen terwijl de TV in stand-by stond (verbeterde ping-validatie).
- **Auto-Reset Bug**: Kijktijden worden nu betrouwbaar om 00:00 gereset op basis van de systeemdatum.
- **MQTT v2 API**: Callback waarschuwingen verholpen door overstap naar de nieuwste Paho API.

## [1.6.5]
- Overstap naar SQLite database voor permanente opslag van instellingen.
- Ingress paden gecorrigeerd voor betere weergave in de Home Assistant app.

## [1.6.0]
- Initiële versie met basis kijktijd tracking en MQTT Discovery.