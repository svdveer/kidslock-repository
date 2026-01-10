# Changelog

Alle belangrijke wijzigingen aan het **KidsLock Manager** project worden in dit bestand bijgehouden.

---

## [1.6.0] - 2024-05-21

### ✨ Nieuw
- **Kijktijd Tracking (Elapsed Time)**: De add-on houdt nu per TV bij hoeveel minuten er die dag daadwerkelijk is gekeken.
- **Visuele Progressiebalk**: De Web UI heeft een nieuwe 'look' gekregen met een dynamische voortgangsbalk die van groen naar rood kleurt naarmate de daglimiet wordt bereikt.
- **MQTT Discovery v2**: Alle entiteiten worden nu automatisch aangemaakt in Home Assistant onder één apparaat per TV.
- **Interactieve MQTT Buttons**: Directe knoppen in Home Assistant voor:
  - `+15 Minuten`
  - `+30 Minuten`
  - `Reset Daglimiet`
- **Nachtelijke Reset**: Automatische reset-logica die om 00:00 uur de resterende tijd herstelt en de kijktijd op nul zet.

### 🚀 Verbeteringen
- **Data Persistentie**: De verstreken kijktijd wordt nu opgeslagen in de SQLite database, waardoor gegevens behouden blijven na een herstart van de add-on.
- **Mobiele UI**: De interface is geoptimaliseerd voor gebruik binnen de Home Assistant Companion app.
- **API Consolidatie**: De backend API is gestroomlijnd naar een universele handler voor snellere responstijden.

### 🔧 Fixes
- Probleem opgelost waarbij MQTT-entiteiten soms verdwenen na een herstart van de broker.
- UTF-8 karakterweergave verbeterd voor speciale iconen in de Web UI.

---

## [1.5.3] - 2024-05-15
- Initiële MQTT ondersteuning toegevoegd.
- Overstap naar Alpine-based Docker image voor snellere installatie.
- Switch entiteit toegevoegd voor handmatige vergrendeling.

---

## [1.0.0] - 2024-04-01
- Eerste stabiele release met basis tijdslimieten en Android TV koppeling.