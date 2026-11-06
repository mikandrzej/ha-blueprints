# ha-blueprints

Blueprinty Home Assistant.

## Sterowanie światłem

Steruje światłem za pomocą czujników ruchu, włączników slave (podążanie / przeciwny stan / toggle) i czujników drzwi, z automatycznym wyłączaniem po czasie.

[![Importuj blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fmikandrzej%2Fha-blueprints%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmikolaj%2Fsterowanie_swiatlem.yaml)

Ręcznie: Ustawienia → Automatyzacje → Blueprinty → Importuj blueprint →
`https://github.com/mikandrzej/ha-blueprints/blob/main/blueprints/automation/mikolaj/sterowanie_swiatlem.yaml`

Wymaga Home Assistant 2024.10 lub nowszego.

## Sterowanie piecem (suma logiczna stref)

Włącza piec, gdy którakolwiek głowica w domu ma otwarty zawór (np. `sensor.*_pi_heating_demand` z Zigbee2MQTT) albo termostat raportuje `hvac_action: heating`. Ma opóźnienie włączenia/wyłączenia, minimalny czas pracy i minimalną przerwę oraz opcjonalny wyłącznik sezonu grzewczego.

[![Importuj blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fmikandrzej%2Fha-blueprints%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmikolaj%2Fkotlownia_piec.yaml)

Ręcznie: `https://github.com/mikandrzej/ha-blueprints/blob/main/blueprints/automation/mikolaj/kotlownia_piec.yaml`

## Temperatura strefy (czujnik z fallbackiem na głowice)

Blueprint sensora szablonowego (`domain: template`). Zwraca temperaturę z zewnętrznego czujnika, a gdy ten jest niedostępny lub nie raportował dłużej niż ustawiony czas, średnią z głowic (minus ich kalibracja, plus opcjonalny offset). Atrybut `zrodlo` = `czujnik` / `glowice`.

Import: Ustawienia → Automatyzacje → Blueprinty → Importuj blueprint →
`https://github.com/mikandrzej/ha-blueprints/blob/main/blueprints/template/mikolaj/temperatura_strefy.yaml`

Użycie (w `configuration.yaml`, jeden wpis na strefę):

```yaml
template:
  - use_blueprint:
      path: mikolaj/temperatura_strefy.yaml
      input:
        external_sensor:
          - sensor.salon_czujnik_temperature
        trvs:
          - climate.salon_kaloryfer_okno
          - climate.salon_kaloryfer_sciana
        calibrations:
          - number.salon_kaloryfer_okno_local_temperature_calibration
          - number.salon_kaloryfer_sciana_local_temperature_calibration
        fallback_offset: -1
    name: Salon temperatura strefy
    unique_id: salon_temperatura_strefy
```
