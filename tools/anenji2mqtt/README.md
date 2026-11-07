# anenji2mqtt

Mostek ANENJI ANJ-6200W-48V (Modbus RTU po RS232, protokół SMG-II) → MQTT z autodiscovery Home Assistant.

- Odczyt: moce PV/sieci/obciążenia/baterii, napięcia, prądy, temperatury, tryb pracy, kody błędów.
- Zapis (`WRITE_ENABLED=1`): priorytet wyjścia i ładowania, maks. prądy ładowania, brzęczyk, LCD, tryby restartu/oszczędzania.
  Napięcia baterii są tylko do odczytu.

Połączenie RJ45 (port COM falownika) → DB9 adaptera USB-RS232: pin 1 → 2, pin 2 → 3, pin 8 → 5. Pin 4 (+12 V) zostaw wolny.

Uruchomienie: `compose.yaml` jako aplikacja TrueNAS (Install via YAML), skrypt w katalogu montowanym jako `/app`.
