# Orange Pi Zero AI Robot — Project Handoff

**Version:** 0.2 (assembled / pre-first-boot)  
**Updated:** 2026-11-05  
**Status:** Connections assembled as reported; the electronics, USB enumeration, I²C communication, audio and software have **not yet been verified by a successful boot**.

## 1. Mission

Build a compact, modular, battery-powered robot on a 3D-printed LEGO-like chassis. Its long-term direction is to be a self-monitoring, increasingly capable AI companion that can inventory its hardware, diagnose faults, propose improvements, assist with programming, and eventually sense and act through wheels, motors, and servos. The first milestone is a **stationary robot** with camera, touchscreen expression/UI, basic audio, a persistent internal status log, and a hardware-discovery interface.

The robot should *propose* code or firmware changes, test in a controlled environment where practical, and seek owner approval before flashing firmware, changing power/motion control, or executing potentially destructive actions. The device cannot independently infer every circuit connection without instrumentation or firmware support.

## 2. Hardware inventory and present connections

| Component | Current state / connection | Notes |
|---|---|---|
| **Orange Pi Zero** | Main computer; Allwinner **H3**, reportedly **512 MB RAM** | Original Orange Pi Zero H3 family is strongly indicated; **board revision not yet confirmed**. Runs Linux once configured. |
| Single-cell LiPo battery | Feeds charger/5 V boost power module | Battery capacity, boost continuous/peak output and exact distribution wiring **not yet recorded**. |
| Charger + 5 V boost module | Feeds robot's 5 V system | Must confirm 5 V under realistic load. Charging and load-sharing behavior unknown. |
| USB expansion / hub | Plugged into the Orange Pi's USB connection | Four downstream connectors: 2 × USB-A and 2 × USB-C per owner description; actual hub topology and power limits unverified. |
| Generic **onn.** USB webcam | Plugged into the USB hub | Resolution, USB Video Class support, and power draw not yet measured. |
| **Cheap Yellow Display (CYD)** board | Plugged into the USB hub | ESP32 touchscreen board, exact CYD revision unknown. USB usually provides power/serial programming; **not automatically a Linux monitor**. Needs CYD firmware and a host/device communications protocol. |
| Single headphone / earpiece | Connected to the CYD's audio output | Actual audio driver, DAC/amplifier support, signal levels, and sound output need testing. Do not assume it works until tested. |
| **RP2040** board | Connected to native USB breakout on the Orange Pi's **13-pin expansion header** | USB data/wiring orientation must be verified before powering; USB device firmware behavior unknown. |
| Motor controller board | RP2040 is plugged into / linked with it | Exact motor controller part number and connections are unknown; **motors not installed**. |
| **PCF8574** 8-bit GPIO expander | Connected to the Orange Pi's **26-pin GPIO I²C bus** | Digital I/O expander. Module voltage/pull-ups and actual I²C address need checking. |
| **PCA9685** servo PWM controller | **Not installed yet** | Planned near-term. Servos require a separate suitably rated power rail. |
| **GY-521 / MPU-6050** 6-axis IMU | **Not installed yet** | Planned on same I²C bus; provides 3-axis accelerometer + 3-axis gyroscope, not an absolute heading sensor without additional reference. |
| Wheels, motors and servos | **Not installed yet** | Future mobility/actuation phase. |

## 3. Current connection diagram

```text
Single-cell LiPo
   └── charger + 5 V boost converter
          └── 5 V distribution (ratings and exact wiring TBD)
               └── Orange Pi Zero (Allwinner H3, ~512 MB)
                    ├── USB expansion hub (one upstream USB connection)
                    │    ├── USB webcam (onn.)
                    │    └── Cheap Yellow Display (CYD / ESP32)
                    │          └── single headphone/earpiece on CYD audio output
                    ├── 13-pin expansion header: native USB data connection
                    │    └── RP2040
                    │          └── motor controller board (motors not installed)
                    └── 26-pin header: I²C
                         └── PCF8574 8-bit digital I/O expander

Future I²C additions: GY-521 / MPU-6050; PCA9685 servo controller. -UPDATE ADDED GY-521 to I2C of Orange Pi after the pcf8574 (INLINE I2C)
Future mechanical additions: wheels, motors, servos.
```

## 4. Recorded wiring — verify physically before first power

### 4.1 26-pin Orange Pi Zero header — I²C

These are **physical header pin numbers**, not Linux GPIO identifiers:

| Physical pin | Signal | PCF8574 signal |
|---|---|---|
| **1** | 3.3 V | VCC, **only if your module supports 3.3 V** |
| **3** | I²C SDA | SDA |
| **5** | I²C SCL | SCL |
| **6** | GND | GND |

Odd pins run along one row and even pins run along the other. Confirm the printed pin-1 location, board revision and header orientation, **not just the visual row direction**. The PCF8574's I²C pull-ups must be to a compatible **3.3 V** rail; never expose the H3 GPIO/I²C pins to 5 V logic.

The owner has purple, gray, white and black jumper wires; a suggested convention from an earlier discussion was purple=3.3V, gray=SDA, white=SCL, black=GND. **Color is only a chosen convention; trace the actual ends.**

### 4.2 Orange Pi Zero 13-pin expansion header — RP2040 USB

For the **original** Orange Pi Zero H3 13-pin expansion connector, the first four header pins are commonly documented as:

| 13-pin physical pin | USB signal | Owner's current wire color |
|---|---|---|
| **1** | USB +5 V | **Red** |
| **2** | GND | **Black** |
| **3** | USB D− | **White** (assumed) |
| **4** | USB D+ | **Blue** (assumed) |

**Critical:** Those are the owner's reported wire positions; the wire *colors do not validate the signals*. Standard USB often uses green for D+, but this cable uses blue. Before applying power, continuity-test each wire against the actual USB connector contact and confirm header pin 1 orientation, 5 V/GND polarity, and that D−/D+ are not crossed. The RP2040 USB connector expects 5 V on VBUS, **not** on a 3.3 V GPIO pin. Do not apply 5 V to bare RP2040 GPIO. Use short, suitable USB wiring to reduce signal issues.

### 4.3 USB hub considerations

The hub adds **ports, not power budget**. Orange Pi + hub + webcam + CYD + RP2040 may overload a small boost module or USB supply path. Boot initially with the fewest devices possible; enumerate and add them incrementally. Avoid simultaneously feeding two conflicting 5 V sources or backfeeding the Orange Pi through a hub.

## 5. Safe first power-on and bring-up sequence

**Before switching on:**

- [ ] Power is **off** while checking all wires.
- [ ] Meter-check regulated output voltage and polarity (target near 5 V, within board/supply limits), battery polarity, and absence of accidental shorts.
- [ ] Determine continuous current capability of the boost converter and supply wiring; unknown until measured.
- [ ] Verify the 13-pin header orientation and continuity of USB VBUS, GND, D−, D+; **do not rely on red/black/white/blue color alone**.
- [ ] Verify 26-pin I²C 3.3 V and GND pin positions and PCF8574 board pull-up voltage.
- [ ] Ensure motor outputs are disconnected/unpowered (motors not fitted yet).
- [ ] Ensure microSD contains a suitable, board-specific Linux image; have a method to view serial/network boot logs.

**Staged boot:**

1. Ideally power **only the Orange Pi** from a verified 5 V supply. If the LiPo/boost module is the only supply, monitor voltage during startup.
2. Confirm Linux boots and identify the board/OS:
   ```bash
   tr -d '\0' </proc/device-tree/model; echo
   uname -a
   cat /etc/os-release
   free -h
   ```
3. Attach the hub and check enumerated USB devices:
   ```bash
   lsusb
   dmesg --follow
   ```
4. Attach the webcam and check for a V4L2 device:
   ```bash
   ls /dev/video* 2>/dev/null
   ```
5. Attach CYD and check whether its USB serial device appears (`/dev/ttyUSB*` or `/dev/ttyACM*`); determine display revision and build a simple host ↔ CYD command protocol.
6. Once 13-pin wiring has been continuity-verified, attach RP2040 and check its USB device enumeration; flash known test firmware if necessary.
7. Enable the correct I²C controller in the Orange Pi's Linux device-tree/overlays (details depend on OS image), then list buses and scan **only the intended bus**:
   ```bash
   sudo apt-get install -y i2c-tools
   i2cdetect -l
   sudo i2cdetect -y <BUS_NUMBER>
   ```
   Replace `<BUS_NUMBER>` with the appropriate bus from `i2cdetect -l`; do not assume it is bus 0 or 1.
8. Look for the PCF8574 at a board-dependent address (usually **0x20–0x27**; PCF8574A variants **0x38–0x3F**). Confirm before driving any output.
9. Verify stable supply voltage with all presently fitted peripherals attached. Capture boot logs and device inventory for the project record.

**Stop and disconnect power** if the supply sags/reset-loops, a wire or board heats unexpectedly, there is smoke/smell, or the 13-pin/26-pin connections cannot be verified.

## 6. Proposed v0.2 software architecture

The **Orange Pi** is the Linux supervisor for orchestration, logging, hardware inventory, camera capture, simple local rules, and optionally remote AI inference. **512 MB RAM makes large on-device language models impractical**; use small local utilities and an optional external AI service if internet access is available. Start with a resilient device-control layer rather than trying to run a large model locally.

The **RP2040** handles deterministic low-level timing, firmware-defined I/O and eventually motor/safety logic, with USB serial as the main command/telemetry link. The **CYD ESP32** hosts a touchscreen face/settings panel and audio features its actual board revision supports, connected through a simple USB serial message protocol. The **PCF8574** supplies extra slow digital inputs/outputs via Linux I²C (for switches, bumpers, status LEDs, etc.).

Suggested processes:

```text
robot-supervisor (Python on Orange Pi)
   ├── device_inventory.py       USB, serial ports, camera, I²C scan
   ├── event_bus.py              simple JSON event protocol
   ├── camera_service.py         capture frames at modest resolution
   ├── display_bridge.py         commands/status to CYD over USB serial
   ├── rp2040_bridge.py          commands/status to RP2040 USB
   ├── i2c_service.py            PCF8574 and later sensors
   ├── state_store.py            append-only logs + config (SD card aware)
   ├── diagnostics.py            health/power/network/device checks
   └── planner.py                suggest next test or upgrade; approval required
```

### CYD touchscreen MVP

- Boot screen with animated **face / expression** and status (booting, curious, happy, thinking, warning, idle).
- Touch controls: **Status**, **Camera**, **Devices**, **Settings**, **Logs**, and **Ask / Suggest**.
- Show CPU/RAM, uptime, I²C devices, USB devices, and last successful RP2040 ping.
- Host-to-CYD messages can be line-delimited JSON, for example:
  ```json
  {"type":"face","mood":"curious"}
  {"type":"status","camera":true,"rp2040":true,"i2c":["0x20"]}
  ```
- CYD touch events return button identifiers, e.g. `{"type":"touch","action":"scan_devices"}`.
- Audio: determine CYD hardware output and driver first; implement safe, low-volume test tone / notification only after confirming actual pinout and electrical output.

### Self-monitoring / learning MVP

1. Detect attached USB/I²C devices and show known vs unknown hardware.
2. Record every test with timestamp, result, firmware/version, and wiring assumptions.
3. Identify missing features (e.g., no wheels, no IMU, no servo board) and generate practical next-step suggestions.
4. Maintain a concise hardware knowledge file; ask for explicit approval before flashing code or reconfiguring hardware.
5. Use **hardware capability discovery**, not assertions of literal self-awareness.

## 7. Planned I²C expansion

**GY-521 / MPU-6050:** Connect SDA/SCL to the same I²C bus and GND to common ground. The MPU-6050 logic is **3.3 V**, so verify the GY-521 breakout's supply requirements and pull-up voltages; using a suitable 3.3 V supply is generally safest when the breakout supports it. Typical addresses are **0x68** or **0x69** depending on AD0. Use its accelerometer and gyroscope to detect tilt, rotation, bumps and motion; it cannot provide a stable absolute compass bearing by itself.

**PCA9685:** Shares SDA/SCL and common ground with the above devices. Typical default address **0x40**, adjustable on most modules. Logic **VCC** can be 3.3 V if supported by the particular breakout; servo supply **V+** is separate and must provide the servos' required voltage/current. Do **not** power servos from the Orange Pi 3.3 V pin or its USB power line.

**PCF8574:** Already wired; suitable for digital switches, buttons, bump sensors, indicators and other slow GPIO. It does not generate servo PWM and is not an ADC.

Multiple I²C modules may share SDA/SCL if unique addresses, compatible logic voltage, correct pull-ups and manageable bus length/capacitance are maintained.

## 8. Next development milestones

### Milestone A — Electrical and Linux bring-up
- Confirm board identity/revision and Linux OS.
- Validate stable 5 V power, USB host and hub behavior.
- Enumerate webcam, CYD, RP2040.
- Scan I²C and read PCF8574 safely.

### Milestone B — First interactive behavior
- CYD animated face and touchscreen navigation.
- Orange Pi → CYD status messages; CYD → Orange Pi touch events.
- Low-overhead camera preview/capture and USB status.
- RP2040 USB health/ping protocol.

### Milestone C — Self-inventory / guided improvement
- Persistent inventory, error logs, simple diagnostics, and next-step suggestions.
- Optional AI reasoning service for natural-language planning, explicitly separated from safety-critical device control.
- Approval gates for code flashing, shell commands, motor commands and power-sensitive changes.

### Milestone D — Motion sensors and actuation
- Add and calibrate GY-521.
- Add PCA9685 and correctly powered servos.
- Add wheels/motors, motor current measurement if possible, and emergency-stop behavior.
- Test low-speed movement and obstacle response in a controlled area.

## 9. Outstanding facts to resolve

- Exact **Orange Pi Zero PCB revision** and successful kernel device-tree model string.
- Actual Linux distribution, microSD configuration and network/serial access.
- Battery capacity, charger/boost board part number, maximum output current, and path by which 5 V reaches the Orange Pi.
- Identity/topology of the USB hub; whether it is bus-powered or self-powered.
- 13-pin USB cable continuity and actual D+/D− wiring; confirm 5 V and GND.
- Exact RP2040 board and motor controller model; installed firmware.
- Exact CYD ESP32 board revision, USB-UART type, display/touch controller, and audio output implementation.
- PCF8574 board chip marking, address straps, logic power and I²C pull-up voltage.
- Webcam UVC support and audio test results.

## 10. First prompt for the next development session

> We are building a modular AI robot. Read the attached **Orange Pi Zero AI Robot — Project Handoff v0.2**. It is physically assembled but has not yet completed its first boot. Help us bring up the electronics safely, verify Linux/USB/I²C peripherals, create a tiny Orange Pi supervisor, program a touchscreen emotional face for the Cheap Yellow Display, communicate with an RP2040 over USB, and inventory the PCF8574. Treat all unverified hardware connections as assumptions until tested, especially the 13-pin native USB wiring. Don't assume that USB makes the CYD an ordinary video monitor. Wheels, motors, servos, PCA9685, and GY-521 are future steps.
