# 🔒 KidsLock Manager

Beheer eenvoudig de schermtijd van de Android TV's in huis direct vanuit Home Assistant. KidsLock Manager monitort of een TV aanstaat, telt de minuten af en vergrendelt het scherm automatisch wanneer de limiet is bereikt of wanneer het bedtijd is.

## ✨ Functies

* **Real-time Monitoring**: Controleert via Ping of de TV actief is.
* **Slimme Tijdmeting**: De klok pauzeert automatisch wanneer de TV wordt uitgezet of handmatig wordt vergrendeld.
* **Dagelijkse Limiet**: Stel per kind/TV een maximum aantal minuten in per dag.
* **Bedtijd Controle**: Vergrendelt de TV automatisch na een instelbaar tijdstip (bijv. 21:00).
* **Onbeperkt Modus**: Omzeil met één schakelaar alle restricties voor ouderlijk gebruik of speciale gelegenheden.
* **Ingress UI**: Een overzichtelijk dashboard binnen de Home Assistant interface.
* **MQTT Integratie**: Maakt automatisch entiteiten aan voor resterende tijd en vergrendeling.

## 🚀 Installatie

1.  Voeg deze repository toe aan je Home Assistant Add-on winkel.
2.  Installeer de **KidsLock Manager**.
3.  Zorg dat de [KidsLock Android App](https://github.com/svdveer/kidslock-repository) is geïnstalleerd op je Android TV.
4.  Configureer de TV's in de 'Configuratie' tab van de add-on.

## ⚙️ Configuratie Voorbeeld

```yaml
mqtt:
  host: "core-mosquitto"
  port: 1883
  username: "kidslocktv"
  password: "kidslocktv"
tvs:
  - name: "Woonkamer"
    ip: "192.168.2.78"
    daily_limit: 120
    bedtime: "21:00"
    no_limit_mode: false