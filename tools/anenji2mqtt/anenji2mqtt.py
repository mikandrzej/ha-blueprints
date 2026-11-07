#!/usr/bin/env python3
"""ANENJI ANJ-6200W (SMG-II Modbus RTU over RS232) -> MQTT with Home Assistant discovery.
v3: odczyt + zapis ustawień (priorytety, prądy, napięcia baterii z limitami LFP 16S, brzęczyk, LCD, tryby)."""
import json, os, time, logging, queue
import minimalmodbus
import paho.mqtt.client as mqtt

PORT = os.environ.get("SERIAL_PORT", "/dev/ttyINV")
SLAVE = int(os.environ.get("SLAVE_ID", "1"))
INTERVAL = float(os.environ.get("INTERVAL", "10"))
SETTINGS_EVERY = int(os.environ.get("SETTINGS_EVERY", "3"))  # co ile cykli czytać ustawienia
MQTT_HOST = os.environ.get("MQTT_HOST", "10.22.2.20")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER") or None
MQTT_PASS = os.environ.get("MQTT_PASS") or None
DEV_ID = os.environ.get("DEVICE_ID", "falownik_anenji")
DEV_NAME = os.environ.get("DEVICE_NAME", "Falownik")
WRITE_ENABLED = os.environ.get("WRITE_ENABLED", "1") == "1"
DEBOUNCE = float(os.environ.get("DEBOUNCE", "1.0"))  # s ciszy przed zapisem
BASE = f"anenji/{DEV_ID}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("anenji2mqtt")

MODES = ["Power On", "Standby", "Sieć", "Off-Grid", "Bypass", "Ładowanie", "Błąd"]
PRIORITY = ["UTI", "SOL", "SBU", "SUB", "SUF"]
CHG_PRIORITY = ["Sieć", "PV", "PV + sieć", "Tylko PV"]

# ---- pomiary: key: (register, scale, unit, device_class, state_class, name, icon)
SENSORS = {
    "grid_voltage":        (202, 0.1,  "V",  "voltage",     "measurement", "Napięcie sieci", None),
    "grid_frequency":      (203, 0.01, "Hz", "frequency",   "measurement", "Częstotliwość sieci", None),
    "grid_power":          (204, 1,    "W",  "power",       "measurement", "Moc z sieci", None),
    "inverter_voltage":    (205, 0.1,  "V",  "voltage",     "measurement", "Napięcie inwertera", None),
    "inverter_current":    (206, 0.1,  "A",  "current",     "measurement", "Prąd inwertera", None),
    "inverter_power":      (208, 1,    "W",  "power",       "measurement", "Moc inwertera", None),
    "inverter_charging_power": (209, 1, "W", "power",       "measurement", "Moc ładowania z sieci", None),
    "output_voltage":      (210, 0.1,  "V",  "voltage",     "measurement", "Napięcie wyjścia", None),
    "output_current":      (211, 0.1,  "A",  "current",     "measurement", "Prąd wyjścia", None),
    "output_frequency":    (212, 0.01, "Hz", "frequency",   "measurement", "Częstotliwość wyjścia", None),
    "load_power":          (213, 1,    "W",  "power",       "measurement", "Moc obciążenia", None),
    "load_apparent_power": (214, 1,    "VA", "apparent_power", "measurement", "Moc pozorna obciążenia", None),
    "battery_voltage":     (215, 0.1,  "V",  "voltage",     "measurement", "Napięcie baterii", None),
    "battery_current":     (232, 0.1,  "A",  "current",     "measurement", "Prąd baterii", None),
    "battery_power":       (217, 1,    "W",  "power",       "measurement", "Moc baterii", None),
    "battery_soc":         (229, 1,    "%",  "battery",     "measurement", "SOC baterii (falownik)", None),
    "pv_voltage":          (219, 0.1,  "V",  "voltage",     "measurement", "Napięcie PV", None),
    "pv_current":          (220, 0.1,  "A",  "current",     "measurement", "Prąd PV", None),
    "pv_power":            (223, 1,    "W",  "power",       "measurement", "Moc PV", None),
    "pv_charging_power":   (224, 1,    "W",  "power",       "measurement", "Moc ładowania z PV", None),
    "load_percent":        (225, 1,    "%",  None,          "measurement", "Obciążenie", "mdi:gauge"),
    "dcdc_temperature":    (226, 1,    "°C", "temperature", "measurement", "Temperatura DCDC", None),
    "inverter_temperature":(227, 1,    "°C", "temperature", "measurement", "Temperatura inwertera", None),
    "pv_charging_current": (234, 0.1,  "A",  "current",     "measurement", "Prąd ładowania z PV", None),
}
TEXTS = {
    "mode": ("Tryb pracy", "mdi:state-machine"),
    "output_priority": ("Priorytet wyjścia", "mdi:priority-high"),
    "charging_priority": ("Priorytet ładowania", "mdi:battery-charging"),
    "fault_code": ("Kod błędu", "mdi:alert"),
    "warning_code": ("Kod ostrzeżenia", "mdi:alert-outline"),
}

# ---- ustawienia (blok 300..337) ----
SET_START, SET_COUNT = 300, 38

# select: key -> (register, {value: label}, name, icon)
SELECTS = {
    "set_output_priority": (301, {0: "UTI – sieć, PV, bateria", 1: "SOL – PV, sieć, bateria",
                                  2: "SBU – PV, bateria, sieć", 3: "SUB – PV, sieć, bateria"},
                            "Ustaw priorytet wyjścia", "mdi:priority-high"),
    "set_charging_priority": (331, {0: "Sieć przed PV", 1: "PV przed siecią",
                                    2: "PV i sieć razem", 3: "Tylko PV"},
                              "Ustaw priorytet ładowania", "mdi:battery-charging"),
    "set_buzzer": (303, {0: "Wyciszony", 1: "Zmiana źródła i ostrzeżenia",
                         2: "Ostrzeżenia i błędy", 3: "Tylko błędy"},
                   "Brzęczyk", "mdi:volume-high"),
}
# switch: key -> (register, name, icon)   0=OFF, 1=ON
SWITCHES = {
    "set_lcd_backlight":   (305, "Podświetlenie LCD zawsze włączone", "mdi:brightness-6"),
    "set_lcd_return":      (306, "LCD wraca na ekran główny", "mdi:monitor-dashboard"),
    "set_energy_saving":   (307, "Tryb oszczędzania energii", "mdi:leaf"),
    "set_overload_restart":(308, "Restart po przeciążeniu", "mdi:restart-alert"),
    "set_overtemp_restart":(309, "Restart po przegrzaniu", "mdi:thermometer-alert"),
    "set_overload_bypass": (310, "Bypass przy przeciążeniu", "mdi:transit-detour"),
}
# number: key -> (register, scale, min, max, step, unit, name, icon)
NUMBERS = {
    "set_max_charging_current":       (332, 0.1, 10, 120, 1, "A", "Maks. prąd ładowania (PV+sieć)", "mdi:current-dc"),
    "set_max_mains_charging_current": (333, 0.1, 2, 80, 1, "A", "Maks. prąd ładowania z sieci", "mdi:transmission-tower-import"),
    # napięcia baterii – twarde limity dla LiFePO4 16S
    "set_bulk_voltage":          (324, 0.1, 54.0, 57.6, 0.1, "V", "Bateria: napięcie ładowania (bulk)", "mdi:battery-arrow-up"),
    "set_float_voltage":         (325, 0.1, 53.0, 56.0, 0.1, "V", "Bateria: napięcie podtrzymania (float)", "mdi:battery-sync"),
    "set_overvoltage":           (323, 0.1, 57.0, 60.0, 0.1, "V", "Bateria: zabezpieczenie przepięciowe", "mdi:battery-alert"),
    "set_mains_return_voltage":  (326, 0.1, 44.0, 53.0, 0.1, "V", "Bateria: powrót z sieci na baterię", "mdi:battery-arrow-up-outline"),
    "set_mains_low_voltage":     (327, 0.1, 44.0, 53.0, 0.1, "V", "Bateria: przejście na sieć", "mdi:transmission-tower-import"),
    "set_offgrid_low_voltage":   (329, 0.1, 44.0, 53.0, 0.1, "V", "Bateria: odcięcie (off-grid)", "mdi:battery-off"),
}
VOLTAGE_REGS = {323, 324, 325, 326, 327, 329}

def check_voltage_order(v):
    """v: dict register->wolty po planowanej zmianie. Rzuca ValueError przy złej kolejności."""
    rules = [
        (325, "<=", 324, "float musi być ≤ bulk"),
        (324, "<", 323, "bulk musi być < przepięcie"),
        (329, "<", 327, "odcięcie musi być < przejście na sieć"),
        (327, "<", 326, "przejście na sieć musi być < powrót na baterię"),
        (326, "<", 325, "powrót na baterię musi być < float"),
    ]
    for a, op, b, msg in rules:
        if a in v and b in v:
            ok = v[a] <= v[b] if op == "<=" else v[a] < v[b]
            if not ok:
                raise ValueError(f"{msg} ({v[a]:.1f} V vs {v[b]:.1f} V)")

# diagnostyka (tylko odczyt): key -> (register, scale, name)
SETTING_SENSORS = {
    "cfg_battery_overvoltage": (323, 0.1, "Bateria: zabezpieczenie przepięciowe"),
    "cfg_max_charging_voltage": (324, 0.1, "Bateria: napięcie ładowania (bulk)"),
    "cfg_float_voltage": (325, 0.1, "Bateria: napięcie podtrzymania (float)"),
    "cfg_mains_return_voltage": (326, 0.1, "Bateria: powrót z sieci na baterię"),
    "cfg_mains_low_voltage": (327, 0.1, "Bateria: przejście na sieć (tryb sieciowy)"),
    "cfg_offgrid_low_voltage": (329, 0.1, "Bateria: odcięcie niskiego napięcia (off-grid)"),
}

def s16(v):
    return v - 65536 if v > 32767 else v

def make_instrument():
    i = minimalmodbus.Instrument(PORT, SLAVE)
    i.serial.baudrate = 9600
    i.serial.timeout = 1.5
    i.clear_buffers_before_each_transaction = True
    return i

def read_measurements(inst):
    r = inst.read_registers(201, 34, 3)
    g = lambda a: s16(r[a - 201])
    data = {k: round(g(reg) * sc, 2) for k, (reg, sc, *_rest) in SENSORS.items()}
    m = g(201)
    data["mode"] = MODES[m] if 0 <= m < len(MODES) else str(m)
    try:
        f = inst.read_registers(100, 10, 3)
        data["fault_code"] = str((f[0] << 16) | f[1])
        data["warning_code"] = str((f[8] << 16) | f[9])
    except Exception as e:
        log.debug("fault regs: %s", e)
    return data

def read_settings(inst):
    """Zwraca dict register->value dla bloku ustawień; przy błędzie bloku czyta pojedynczo."""
    regs = {}
    try:
        vals = inst.read_registers(SET_START, SET_COUNT, 3)
        regs = {SET_START + i: v for i, v in enumerate(vals)}
    except Exception as e:
        log.debug("settings block failed (%s), reading one by one", e)
        wanted = {301, 331} | {v[0] for v in SELECTS.values()} | {v[0] for v in SWITCHES.values()} \
                 | {v[0] for v in NUMBERS.values()} | {v[0] for v in SETTING_SENSORS.values()}
        for reg in sorted(wanted):
            try:
                regs[reg] = inst.read_register(reg, 0, 3)
            except Exception:
                pass
    return regs

def settings_to_data(regs):
    d = {}
    if 301 in regs:
        p = regs[301]; d["output_priority"] = PRIORITY[p] if p < len(PRIORITY) else str(p)
    if 331 in regs:
        c = regs[331]; d["charging_priority"] = CHG_PRIORITY[c] if c < len(CHG_PRIORITY) else str(c)
    for k, (reg, opts, *_r) in SELECTS.items():
        if reg in regs:
            d[k] = opts.get(regs[reg], f"Inna wartość ({regs[reg]})")
    for k, (reg, *_r) in SWITCHES.items():
        if reg in regs:
            d[k] = "ON" if regs[reg] == 1 else "OFF"
    for k, (reg, sc, *_r) in NUMBERS.items():
        if reg in regs:
            d[k] = round(regs[reg] * sc, 1)
    for k, (reg, sc, _n) in SETTING_SENSORS.items():
        if reg in regs:
            d[k] = round(regs[reg] * sc, 1)
    return d

def parse_command(key, payload):
    """Zamienia komendę z HA na (register, raw_value). Rzuca ValueError przy złych danych."""
    if key in SELECTS:
        reg, opts, *_r = SELECTS[key]
        for v, label in opts.items():
            if label == payload:
                return reg, v
        raise ValueError(f"nieznana opcja {payload!r}")
    if key in SWITCHES:
        reg = SWITCHES[key][0]
        if payload.upper() in ("ON", "1"):
            return reg, 1
        if payload.upper() in ("OFF", "0"):
            return reg, 0
        raise ValueError(f"zły stan {payload!r}")
    if key in NUMBERS:
        reg, sc, mn, mx, *_r = NUMBERS[key]
        val = float(payload)
        if not (mn <= val <= mx):
            raise ValueError(f"{val} poza zakresem {mn}..{mx}")
        return reg, int(round(val / sc))
    raise ValueError(f"nieznany klucz {key!r}")

WRITE_FC = [16]  # ten falownik nie odpowiada na FC06; FC16 działa

def write_register(inst, reg, raw):
    try:
        if WRITE_FC[0] == 16:
            inst.write_registers(reg, [raw])
        else:
            inst.write_register(reg, raw, 0, functioncode=6)
    except Exception as e:
        other = 6 if WRITE_FC[0] == 16 else 16
        log.info("FC%02d failed (%s), trying FC%02d", WRITE_FC[0], e, other)
        if other == 16:
            inst.write_registers(reg, [raw])
        else:
            inst.write_register(reg, raw, 0, functioncode=6)
        WRITE_FC[0] = other
    time.sleep(0.1)
    back = inst.read_register(reg, 0, 3)
    if back != raw:
        raise IOError(f"rejestr {reg}: zapisano {raw}, odczytano {back} (falownik odrzucił wartość?)")
    return back

def publish_discovery(cli):
    dev = {"identifiers": [DEV_ID], "name": DEV_NAME, "manufacturer": "ANENJI", "model": "ANJ-6200W-48V"}
    avail = {"availability_topic": f"{BASE}/status"}
    st = f"{BASE}/state"
    def pub(comp, k, cfg):
        cfg.update({"unique_id": f"{DEV_ID}_{k}", "default_entity_id": f"{comp}.{DEV_ID}_{k}",
                    "device": dev, **avail})
        cli.publish(f"homeassistant/{comp}/{DEV_ID}/{k}/config", json.dumps(cfg), retain=True)
    for k, (_reg, _sc, unit, dc, sc, name, icon) in SENSORS.items():
        cfg = {"name": name, "state_topic": st, "value_template": f"{{{{ value_json.{k} }}}}",
               "unit_of_measurement": unit, "state_class": sc}
        if dc: cfg["device_class"] = dc
        if icon: cfg["icon"] = icon
        pub("sensor", k, cfg)
    for k, (name, icon) in TEXTS.items():
        pub("sensor", k, {"name": name, "state_topic": st, "icon": icon,
                          "value_template": f"{{{{ value_json.{k} | default('') }}}}"})
    for k, (_reg, _sc, name) in SETTING_SENSORS.items():
        pub("sensor", k, {"name": name, "state_topic": st, "unit_of_measurement": "V",
                          "device_class": "voltage", "entity_category": "diagnostic",
                          "value_template": f"{{{{ value_json.{k} | default(none) }}}}"})
    for comp in ("select", "switch", "number"):
        src = {"select": SELECTS, "switch": SWITCHES, "number": NUMBERS}[comp]
        for k in src:
            if not WRITE_ENABLED:
                cli.publish(f"homeassistant/{comp}/{DEV_ID}/{k}/config", "", retain=True)
                continue
            cfg = {"state_topic": st, "command_topic": f"{BASE}/set/{k}", "entity_category": "config",
                   "value_template": f"{{{{ value_json.{k} | default(none) }}}}"}
            if comp == "select":
                _reg, opts, name, icon = SELECTS[k]
                cfg.update({"name": name, "icon": icon, "options": list(opts.values())})
            elif comp == "switch":
                _reg, name, icon = SWITCHES[k]
                cfg.update({"name": name, "icon": icon, "payload_on": "ON", "payload_off": "OFF",
                            "state_on": "ON", "state_off": "OFF"})
            else:
                _reg, _sc, mn, mx, step, unit, name, icon = NUMBERS[k]
                cfg.update({"name": name, "icon": icon, "min": mn, "max": mx, "step": step,
                            "unit_of_measurement": unit, "mode": "box",
                            "device_class": "voltage" if unit == "V" else "current"})
            pub(comp, k, cfg)

def main():
    commands = queue.Queue()
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"anenji2mqtt_{DEV_ID}")
    if MQTT_USER:
        cli.username_pw_set(MQTT_USER, MQTT_PASS)
    cli.will_set(f"{BASE}/status", "offline", retain=True)

    def on_connect(c, u, flags, rc, props=None):
        log.info("MQTT connected (%s), zapis %s", rc, "WŁĄCZONY" if WRITE_ENABLED else "wyłączony")
        publish_discovery(c)
        c.publish(f"{BASE}/status", "online", retain=True)
        if WRITE_ENABLED:
            c.subscribe(f"{BASE}/set/#")

    def on_message(c, u, msg):
        key = msg.topic.rsplit("/", 1)[-1]
        payload = msg.payload.decode("utf-8", "replace").strip()
        if msg.retain:
            log.warning("Pomijam zachowaną (retained) komendę %s=%s", key, payload)
            return
        commands.put((key, payload))

    cli.on_connect = on_connect
    cli.on_message = on_message
    cli.connect(MQTT_HOST, MQTT_PORT, 60)
    cli.loop_start()

    inst = None
    while inst is None:
        try:
            inst = make_instrument()
        except Exception as e:
            log.warning("Nie mogę otworzyć portu %s (%s) – czekam 10 s", PORT, e)
            time.sleep(10)
    fails, cycle = 0, 0
    settings = {}
    while True:
        t0 = time.time()
        force_settings = False
        # 1) komendy z HA
        pending = {}
        if not commands.empty():
            # debounce: zbieraj komendy, aż przez DEBOUNCE s nie przyjdzie nowa (szybkie klikanie +/-)
            last = time.time()
            while time.time() - last < DEBOUNCE:
                try:
                    key, payload = commands.get(timeout=0.1)
                    pending[key] = payload  # zostaje tylko ostatnia wartość danego ustawienia
                    last = time.time()
                except queue.Empty:
                    pass
        for key, payload in pending.items():
            try:
                reg, raw = parse_command(key, payload)
                if reg in VOLTAGE_REGS:
                    cur = {r: inst.read_register(r, 0, 3) / 10 for r in sorted(VOLTAGE_REGS)}
                    cur[reg] = raw / 10
                    check_voltage_order(cur)
                write_register(inst, reg, raw)
                log.info("ZAPIS OK: %s = %s (rejestr %d = %d)", key, payload, reg, raw)
                cli.publish(f"{BASE}/last_write", json.dumps({"key": key, "value": payload, "ok": True}, ensure_ascii=False))
            except Exception as e:
                log.error("ZAPIS NIEUDANY: %s = %s: %s", key, payload, e)
                cli.publish(f"{BASE}/last_write", json.dumps({"key": key, "value": payload, "ok": False, "error": str(e)}, ensure_ascii=False))
            force_settings = True
        # 2) odczyt
        try:
            data = read_measurements(inst)
            if force_settings or cycle % SETTINGS_EVERY == 0 or not settings:
                settings = settings_to_data(read_settings(inst))
            data.update(settings)
            cli.publish(f"{BASE}/state", json.dumps(data, ensure_ascii=False))
            if fails:
                cli.publish(f"{BASE}/status", "online", retain=True)
            fails = 0
        except Exception as e:
            fails += 1
            log.warning("Modbus read failed (%d): %s", fails, e)
            if fails == 3:
                cli.publish(f"{BASE}/status", "offline", retain=True)
            if fails % 5 == 0:
                try:
                    inst.serial.close()
                except Exception:
                    pass
                try:
                    inst = make_instrument()
                    log.info("Port %s otwarty ponownie", PORT)
                except Exception as e2:
                    log.warning("Nie mogę otworzyć portu %s (%s) – adapter odłączony? Ponawiam.", PORT, e2)
        cycle += 1
        # 3) czekaj, ale reaguj szybko na komendy
        while time.time() - t0 < INTERVAL and commands.empty():
            time.sleep(0.2)

if __name__ == "__main__":
    main()
