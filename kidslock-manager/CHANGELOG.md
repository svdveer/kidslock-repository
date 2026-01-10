# Changelog - KidsLock Manager

## [1.3.0] - 2026-01-10
### Added
- **Settings Interface**: Volledige instellingenpagina (`/settings`) toegevoegd. Beheer je TV's nu direct in de browser.
- **SQLite Database**: Configuratie wordt nu persistent opgeslagen in `kidslock.db` in plaats van YAML.
- **Dynamische Monitor**: Nieuwe TV's worden direct opgepikt door de monitor-loop zonder herstart.

### Fixed
- **Ingress 404 Errors**: Redirect-logica geoptimaliseerd voor Home Assistant Ingress (geen "Not Found" meer na acties).
- **Stability**: Harde timeouts op netwerkverzoeken voorkomen dat de add-on crasht als TV's uit staan (PID 1 fix).

## [1.1.9.015] - 2026-01-10
### Fixed
- Reparatie van de 303 Redirect-loop binnen de proxy-omgeving.

## [1.1.9.014] - 2026-01-10
### Added
- Netwerk timeouts (1.5s) toegevoegd voor verhoogde stabiliteit bij offline apparaten.

## [1.1.9] - 2026-01-09
### Initial
- Eerste stabiele release met handmatige YAML-configuratie.