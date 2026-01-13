# 🔐 KidsLock Manager v1.7.0.2

**KidsLock Manager** is een Home Assistant add-on voor het beheren van schermtijd op Android TV's. Het combineert een interactief dashboard met een krachtig weekschema en directe MQTT-integratie.

---

## 📱 De Android TV Client (APK)

De add-on kan de TV alleen vergrendelen als de bijbehorende client-app op de TV is geïnstalleerd.

1. **Downloaden**: Download het nieuwste **`kidslock-client.apk`** bestand onder de sectie "Assets" bij de laatste [KidsLock Releases](https://www.google.com/search?q=https://github.com/svdveer/kidslock-repository/releases).
2. **Installeren**: Gebruik een USB-stick of een app als *"Send Files to TV"* om de APK op je Android TV te installeren.
3. **Permissies**:
* Verleen de permissie **"Weergeven over andere apps"** (Display over other apps). Dit is nodig om het slot-scherm bovenop apps als YouTube of Netflix te tonen.
* Zorg dat de TV een **statisch IP-adres** heeft in je netwerk.



---

## 🚀 Functies

* **📅 Weekschema**: Stel per dag van de week (Ma-Zo) een unieke tijdslimiet en bedtijd in.
* **🌙 Harde Bedtijd**: De TV gaat op slot zodra de bedtijd is bereikt, ongeacht de resterende tijd.
* **🛡️ Offline Locking**: Lock-commando's worden onthouden en direct uitgevoerd zodra een TV online komt.
* **🔄 Auto-Reset**: Kijktijden worden elke nacht om 00:00 automatisch gereset.
* **📡 MQTT Discovery**: Automatische sensoren en schakelaars in Home Assistant.

---

## 🛠️ Installatie Add-on

1. Voeg deze repository URL toe aan je Home Assistant Add-on Store.
2. Installeer **KidsLock Manager**.
3. Configureer je MQTT-gegevens in de **Options** tab van de add-on.
4. Start de add-on en open de **Web UI**.
5. Voeg je TV('s) toe en sla het schema op.

---

## 📡 MQTT Entiteiten

De add-on maakt automatisch de volgende entiteiten aan:

* **Switch**: `switch.kidslock_[naam]_vergrendeling` - Om handmatig te slot erop te zetten (ook als de TV uit staat).
* **Sensor**: `sensor.kidslock_[naam]_tijd_resterend` - Toont de resterende tijd of status.

---

## 📝 Changelog

Zie [CHANGELOG.md](CHANGELOG.md) voor de volledige geschiedenis.

*Huidige versie: v1.7.0.2 (Inclusief weekschema en MQTT-feedback fix).*

