# 🔐 KidsLock Manager v1.6.0

Beheer eenvoudig de schermtijd van de Android TV's in huis direct vanuit Home Assistant. KidsLock Manager monitort of een TV actief is, houdt de kijktijd bij en vergrendelt het scherm automatisch wanneer de limiet is bereikt of wanneer de ingestelde bedtijd is aangebroken.



## ✨ Belangrijkste Functies

* **Real-time Monitoring**: Detecteert via Ping of de TV aanstaat.
* **Kijktijd Tracking**: Houdt nauwkeurig bij hoe lang er die dag daadwerkelijk is gekeken (`elapsed time`).
* **Visueel Dashboard**: Vernieuwde Web UI met progressiebalken die van kleur veranderen naarmate de limiet nadert.
* **MQTT Discovery v2**: TV's verschijnen automatisch als apparaten in Home Assistant met de volgende entiteiten:
    * **Sensoren**: Resterende tijd en Totale kijktijd vandaag.
    * **Buttons**: Directe actieknoppen voor `+15m`, `+30m` en `Reset Daglimiet`.
    * **Switch**: Handmatig de TV vergrendelen of ontgrendelen.
* **Nachtelijke Reset**: Timers worden elke nacht om 00:00 uur automatisch gereset naar de daglimiet.
* **Data Persistentie**: Instellingen en verbruikte tijd worden opgeslagen in een lokale database (SQLite), zodat ze behouden blijven na een herstart.

## 📦 Installatie

### 1. Android TV App
* Installeer de [KidsLock Android App](https://github.com/svdveer/kidslock-repository) op elke TV die je wilt beheren.
* Geef de app de benodigde rechten: **Toon boven andere apps** en **Gebruikstoegang**.
* Noteer het IP-adres van de TV.

### 2. Home Assistant Add-on
* Voeg deze repository toe aan je Home Assistant Add-on winkel.
* Installeer de **KidsLock Manager**.
* Vul je MQTT-broker gegevens in bij het tabblad **Configuratie**.
* Start de add-on.

## ⚙️ Configuratie

In versie 1.6.0 configureer je TV's **niet langer via YAML**, maar via de ingebouwde Web UI. De YAML-configuratie in Home Assistant wordt alleen gebruikt voor de MQTT-verbinding:

```yaml
mqtt:
  host: "core-mosquitto"
  port: 1883
  username: "je-gebruiker"
  password: "je-wachtwoord"