# 🔐 KidsLock Manager v1.7.0

KidsLock Manager is een geavanceerde Home Assistant add-on waarmee je de schermtijd van Android TV's (of andere apparaten die reageren op HTTP-slotcommando's) beheert. Met een volledig weekschema, bedtijd-handhaving en MQTT-integratie heb je volledige controle over het mediagebruik in huis.

## 🚀 Nieuwe Functies in v1.7.0
- **📅 Volledig Weekschema**: Stel per dag van de week (Ma-Zo) een unieke tijdslimiet en bedtijd in.
- **🌙 Harde Bedtijd**: De TV gaat direct op slot zodra de bedtijd is bereikt, ongeacht de resterende tijd.
- **🔄 Auto-Reset**: Kijktijden worden elke nacht om 00:00 automatisch teruggezet naar nul.
- **🛡️ Offline Protection**: Lock/Unlock commando's worden direct uitgevoerd zodra een TV online komt.
- **📊 Live Dashboard**: Real-time overzicht van verbruik, status en resterende tijd.

## 🛠️ Installatie

1. Voeg deze repository toe aan je Home Assistant Add-on Store.
2. Installeer **KidsLock Manager**.
3. Configureer je MQTT-gegevens in de **Options** tab van de add-on.
4. Start de add-on en open de **Web UI**.
5. Voeg je TV's toe en stel het gewenste schema in per dag.

## 📡 MQTT Integratie

De add-on maakt automatisch entiteiten aan in Home Assistant via MQTT Discovery:
- **Switch**: Handmatig vergrendelen/ontgrendelen van de TV.
- **Sensor**: Geeft de resterende tijd weer (in minuten of de status "Onbeperkt"/"Bedtijd").

### MQTT Topics
- Status: `kidslock/[slug]/state` (`ON` of `OFF`)
- Resterende tijd: `kidslock/[slug]/remaining`
- Commando's: `kidslock/[slug]/set`

## 🖥️ API Interface

De add-on draait een FastAPI server op poort `8000` (bereikbaar via Ingress):
- `GET /`: Dashboard
- `GET /settings`: Weekschema beheer
- `POST /api/add_time/[name]`: Voeg extra tijd toe (+15m)
- `POST /api/reset/[name]`: Reset de kijktijd voor vandaag handmatig

## 📝 Belangrijke opmerkingen
- **Android TV App**: Deze add-on werkt het beste in combinatie met een kleine luister-app op de TV die reageert op `/lock` en `/unlock` HTTP-verzoeken op poort `8080`.
- **Netwerk**: Zorg dat je TV's een statisch IP-adres hebben in je router.

## 📄 Changelog
Zie [CHANGELOG.md](CHANGELOG.md) voor de volledige geschiedenis van updates en bugfixes.

---
*Ontwikkeld voor en door Home Assistant enthousiastelingen.*