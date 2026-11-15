// RP2040 Zero motor firmware for the cperos-xr/l293d 8-motor board, USB-host friendly.
// Build: Arduino IDE, "Raspberry Pi Pico/RP2040" core (earlephilhower) -> board "Waveshare RP2040 Zero".
//
// Accepts on USB serial (from the Orange Pi) AND on UART0 GPIO28 TX / GPIO29 RX @9600 (old masters):
//   Binary (compatible with stock uartSlave.c): 0xAA, m1..m8 (int8), xor(m1..m8)
//   Text lines:  PING            -> PONG rp2040-motor v0.2 uptime_ms=<n> imu=<0|1> cfg=<0|1>
//                M v1 ... v8     -> OK M ...   (each -127..127; fewer values = rest 0)
//                STOP            -> OK STOP    (also ends a wheelie run)
//                INFO            -> INFO {json}
// SAFETY: every motor is set to 0 if no valid command arrives for FAILSAFE_MS
// (stock firmware keeps motors running forever if the host crashes).
//
// Since 0.2 (2026-11-14): MPU-6050 on I2C1 (GPIO26 SDA, GPIO27 SCL), read 500 times a second, and a wheelie mode
// that runs here because the Orange Pi only sees the tilt 50 times a second, over USB, with jitter.
//                IMU hz          -> OK IMU hz; then lines "I ax ay az gx gy gz temp pitch" at that rate (USB only):
//                                   acceleration in mg, turn rate in 0.1 deg/s (sensor axes, bias NOT removed),
//                                   temperature in 0.1 C, pitch in 0.01 deg
//                AX ux uy uz fx fy fz   which way is up and which way is forward, in sensor axes (the Pi knows)
//                GB bx by bz     gyro bias, deg/s, sensor axes
//                SET name value / GET   wheelie settings (table `prm` below); 0.4: the balance ones also while balancing
//                REC n           record n samples (max REC_MAX, every `recdiv`-th 500 Hz tick), e.g. during a hand-driven wheelie
//                DUMP            -> "D n", n lines "t_us ax ay az gx gy gz pitch_cdeg left right mode" (raw counts), "END"
//                GO              kick: k1p for k1ms, then k2p until the nose is `cut` deg up (or k2ms passed), then
//                                coast (power `cp`) until it is `catch` deg up or stops rising; then balance if
//                                balms > 0, else power off. Recorded from the start.
//                                (0.3, 2026-11-14: power until 30 deg then nothing still flipped the robot - the kick
//                                has far more energy than needed, so it must end early and the rest is coasting.)
//                ARM             hold the robot near its balance angle: balancing starts when it is within 4 deg
//                BAL             balance right now (pitch must be between lo and hi)
//                                When the balance time is up it lands: `lp` power backwards for `lms` ms brings the nose
//                                down (0.2 just cut the power and the robot fell either way, once onto its back).
//                MV v w          (0.4) while balancing: drive at speed v (power units, + = forward) and turn at w deg/s
//                                (+ = left). No answer; counts as a keepalive. "MV 0 0" = stay here, hold the heading.
//                LAND            (0.4) while balancing: land now
//                K               keepalive, no answer. A run stops unless some command arrives every FAILSAFE_MS.
//                When a run ends: "E <why> <ms> <highest pitch>" (USB only)
//                (0.4) when the balancing starts: "N bal <ms> <pitch> <rate>"; "I" lines get 3 more numbers: wheel
//                power, mode, heading in 0.1 deg (turned since the run started, + = left; measured around the
//                vertical, so it stays right with the nose up); DUMP lines get the heading as a 12th number.
#include <Arduino.h>
#include <Wire.h>
#include <math.h>

#define FW_VERSION   "0.4"
#define FAILSAFE_MS  500
#define PWM_FREQ     2000   // L293D is slow; keep PWM in the low kHz
#define IMU_SDA      26
#define IMU_SCL      27
#define MPU          0x68
#define TICK_US      2000   // 500 Hz
#define REC_MAX      5000   // 10 s at 500 Hz
#define ACC_LSB      8192.0f   // +-4 g
#define GYRO_LSB     32.8f     // +-1000 deg/s (a flip passes 250 deg/s)

const uint8_t FWD[8] = {0, 2, 4, 6, 8, 10, 12, 14};
const uint8_t REV[8] = {1, 3, 5, 7, 9, 11, 13, 15};

int8_t cur[8] = {0};
uint32_t lastCmdMs = 0, cmdCount = 0, badCount = 0;
bool failsafeTripped = true;

void setMotorF(int i, float v) {
  v = constrain(v, -127.0f, 127.0f);
  cur[i] = (int8_t)lroundf(v);
  int d = (int)lroundf(fabsf(v) * 2);
  analogWrite(FWD[i], v > 0 ? d : 0);
  analogWrite(REV[i], v < 0 ? d : 0);
}
void setMotor(int i, int v) { setMotorF(i, (float)v); }
void stopAll() { for (int i = 0; i < 8; i++) setMotor(i, 0); }

// ---------------------------------------------------------------- settings
enum { P_WL, P_WR, P_DL, P_DR, P_K1P, P_K1MS, P_K2P, P_K2MS, P_CATCH, P_TGT, P_KP, P_KD, P_KI, P_KV, P_KX, P_TM, P_KY,
       P_DB, P_MAX, P_BALMS, P_LO, P_HI, P_TAU, P_TAUR, P_RECDIV, P_TAIL, P_ARMMS, P_CUT, P_CP, P_CMS, P_DBW, P_LP, P_LMS, P_KH, P_SMAX, P_VA, P_N };
struct Param { const char *name; float v, lo, hi; };
Param prm[P_N] = {
  {"wl", 0, 0, 7}, {"wr", 1, 0, 7},          // motor channel (0-7) of the left / right wheel
  {"dl", 1, -1, 1}, {"dr", 1, -1, 1},        // +1 / -1: which way is forward for that wheel
  {"k1p", -127, -127, 127}, {"k1ms", 0, 0, 1500},     // kick part 1 (usually backwards)
  {"k2p", 127, -127, 127}, {"k2ms", 300, 0, 1500},    // kick part 2 (forwards: lifts the nose)
  {"catch", 50, 5, 120},                     // nose this high (deg): the kick is over
  {"tgt", 70, 20, 110},                      // balance angle (deg nose up)
  {"kp", 8, 0, 60}, {"kd", 0.3f, 0, 5}, {"ki", 0, 0, 50},   // power per deg, per deg/s, per deg*s
  {"kv", 0, 0, 0.5f}, {"kx", 0, 0, 0.5f},    // lean back per unit of (estimated) speed / distance: stops it running away
  {"tm", 0.15f, 0.02f, 2},                   // motor time constant for that speed estimate (s)
  {"ky", 0, 0, 2},                           // steering: power per deg/s of unwanted turning
  {"db", 0, 0, 90},                          // power added to get through the motors' dead zone
  {"max", 127, 0, 127},                      // power cap while balancing
  {"balms", 0, 0, 120000},                   // longest balance (0 = kick only, no balancing)
  {"lo", 20, -10, 100}, {"hi", 110, 30, 170},   // nose lower / higher than this: give up, power off
  {"tau", 0.5f, 0.05f, 10}, {"taur", 2, 0.05f, 20},   // how fast gravity corrects the gyro angle: idle / in a run (s)
  {"recdiv", 1, 1, 20}, {"tail", 1500, 0, 5000},      // recording: every n-th tick; ms recorded after the run ends
  {"armms", 20000, 1000, 60000},             // ARM waits this long for the robot to be held at its balance angle
  {"cut", 120, 0, 120},                      // nose this high: kick power off, coast from here (>= catch: no coasting)
  {"cp", 0, -127, 127}, {"cms", 800, 0, 3000},   // power while coasting; longest coast
  {"dbw", 1, 1, 40},                         // `db` is blended in over this much power (1 = a step: it rattled at 4-7 Hz)
  {"lp", 70, 0, 127}, {"lms", 200, 0, 1000}, // landing: this much power backwards for this long, then off
  {"kh", 0, 0, 10}, {"smax", 40, 0, 127},    // heading hold: power per deg off the heading; cap on all steering power
  {"va", 60, 5, 500},                        // MV: the speed setpoint changes this fast (power units per second)
};
#define PV(i) (prm[i].v)

// ---------------------------------------------------------------- IMU
bool imuOk = false, axSet = false, pitchInit = false;
uint8_t imuWho = 0;
uint32_t imuGood = 0, imuFail = 0, imuRunFail = 0, imuRetryTick = 0;
int16_t raw[7];
float up[3] = {0, 0, 1}, fwd[3] = {1, 0, 0}, lft[3] = {0, 1, 0}, gbias[3] = {0, 0, 0};
float pitch = 0, rate = 0, yawRate = 0, accMag = 1;

static float dot(const float *a, const float *b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
static bool norm(float *v) {
  float n = sqrtf(dot(v, v));
  if (n < 1e-6f) return false;
  for (int i = 0; i < 3; i++) v[i] /= n;
  return true;
}
static bool mpuWrite(uint8_t reg, uint8_t v) {
  Wire1.beginTransmission(MPU); Wire1.write(reg); Wire1.write(v);
  return Wire1.endTransmission() == 0;
}
static int mpuRead8(uint8_t reg) {
  Wire1.beginTransmission(MPU); Wire1.write(reg);
  if (Wire1.endTransmission(false) != 0 || Wire1.requestFrom((uint8_t)MPU, (size_t)1) != 1) return -1;
  return Wire1.read();
}
static bool mpuInit() {
  bool ok = mpuWrite(0x6B, 0x01)      // wake, PLL on the X gyro
         && mpuWrite(0x19, 0x00)      // 1 kHz samples
         && mpuWrite(0x1A, 0x03)      // low-pass 44 Hz (4.9 ms delay): motor shake stays out
         && mpuWrite(0x1B, 0x10)      // +-1000 deg/s
         && mpuWrite(0x1C, 0x08);     // +-4 g
  int who = mpuRead8(0x75);
  imuWho = who < 0 ? 0 : who;
  imuOk = ok && who >= 0;
  pitchInit = false;
  imuRunFail = 0;
  return imuOk;
}
static bool mpuRead() {
  Wire1.beginTransmission(MPU); Wire1.write(0x3B);
  if (Wire1.endTransmission(false) != 0 || Wire1.requestFrom((uint8_t)MPU, (size_t)14) != 14) return false;
  bool any = false;
  for (int i = 0; i < 7; i++) {
    uint8_t h = Wire1.read(), l = Wire1.read();
    raw[i] = (int16_t)((h << 8) | l);
    if (i < 3 && raw[i]) any = true;
  }
  return any;   // all-zero acceleration = the sensor lost power and is asleep again
}
static void fuse(float dt, float tau) {
  float a[3], g[3];
  for (int i = 0; i < 3; i++) { a[i] = raw[i] / ACC_LSB; g[i] = raw[4 + i] / GYRO_LSB - gbias[i]; }
  accMag = sqrtf(dot(a, a));
  rate = -dot(g, lft);          // deg/s, + = nose going up
  float pr = pitchInit ? pitch * 0.01745329f : 0;
  yawRate = dot(g, up) * cosf(pr) + dot(g, fwd) * sinf(pr);   // deg/s around the vertical, + = turning left
  float pa = atan2f(dot(a, fwd), dot(a, up)) * 57.29578f;
  if (!pitchInit) { pitch = pa; pitchInit = true; return; }
  pitch += rate * dt;
  if (accMag > 0.85f && accMag < 1.15f) {       // only believe gravity when nothing else is pushing
    float e = pa - pitch;
    while (e > 180) e -= 360;
    while (e < -180) e += 360;
    pitch += e * min(1.0f, dt / tau);
  }
  if (pitch > 180) pitch -= 360;
  if (pitch < -180) pitch += 360;
}

// ---------------------------------------------------------------- recording
struct Rec { uint32_t t; int16_t a[3], g[3], pitch, yaw; int8_t l, r; uint8_t mode; };
Rec rec[REC_MAX];
int recN = 0, recWant = 0, recSkip = 0, dumpAt = -1;
uint32_t recT0 = 0;
Stream *dumpTo = nullptr;

void recStart(int n) { recN = 0; recWant = constrain(n, 0, REC_MAX); recSkip = 0; recT0 = micros(); dumpAt = -1; }

// ---------------------------------------------------------------- wheelie run
enum { IDLE, KICK1, KICK2, BALANCE, ARMED, COAST, LAND };
uint8_t mode = IDLE;
const char *endWhy = "-";
uint32_t modeT0 = 0, runT0 = 0, tailUntil = 0, streamEvery = 0, streamN = 0, armOkTicks = 0;
float integ = 0, vEst = 0, xEst = 0, maxPitch = 0;
float heading = 0, headSet = 0, vCmd = 0, vTgt = 0, wCmd = 0, xCmd = 0;   // heading: deg turned since the run started

void wheels(float l, float r) {
  setMotorF((int)PV(P_WL), l * PV(P_DL));
  setMotorF((int)PV(P_WR), r * PV(P_DR));
}
void endRun(const char *why) {
  if (mode == IDLE) return;
  stopAll();
  failsafeTripped = true;
  mode = IDLE;
  endWhy = why;
  uint32_t ms = millis() - runT0;
  tailUntil = millis() + (uint32_t)PV(P_TAIL);
  if (Serial.availableForWrite() > 40) Serial.printf("E %s %lu %.1f\n", why, ms, maxPitch);
}
void setMode(uint8_t m) { mode = m; modeT0 = millis(); }
void startBalance() {
  integ = vEst = xEst = 0;
  vCmd = vTgt = wCmd = xCmd = 0;
  setMode(BALANCE);
  if (Serial.availableForWrite() > 40) Serial.printf("N bal %lu %.1f %.0f\n", millis() - runT0, pitch, rate);
}
const char *startRun(uint8_t m) {
  if (mode != IDLE) return "busy";
  if (!imuOk) return "no IMU";
  if (!axSet) return "no AX (mount unknown)";
  if (m == KICK1 && fabsf(pitch) > 25) return "not level";
  if ((m == BALANCE || m == ARMED) && PV(P_BALMS) < 1) return "balms is 0";
  if (m == BALANCE && (pitch < PV(P_LO) || pitch > PV(P_HI))) return "pitch outside lo..hi";
  lastCmdMs = runT0 = millis();
  failsafeTripped = false;
  maxPitch = pitch;
  armOkTicks = 0;
  heading = headSet = 0;
  endWhy = "running";
  recStart(REC_MAX);
  if (m == KICK1 && PV(P_K1MS) < 1) m = KICK2;
  if (m == BALANCE) startBalance(); else setMode(m);
  return nullptr;
}
void control(float dt) {
  uint32_t el = millis() - modeT0;
  if (pitch > maxPitch) maxPitch = pitch;
  heading += yawRate * dt;
  switch (mode) {
    case KICK1:
      wheels(PV(P_K1P), PV(P_K1P));
      if (el >= (uint32_t)PV(P_K1MS)) setMode(KICK2);
      break;
    case KICK2:
      wheels(PV(P_K2P), PV(P_K2P));
      if (pitch >= PV(P_CATCH)) { if (PV(P_BALMS) >= 1) startBalance(); else endRun("catch"); }
      else if (pitch >= PV(P_CUT)) { wheels(PV(P_CP), PV(P_CP)); setMode(COAST); }
      else if (el >= (uint32_t)PV(P_K2MS)) endRun("k2 time");
      break;
    case COAST:
      wheels(PV(P_CP), PV(P_CP));
      if (pitch >= PV(P_CATCH)) { if (PV(P_BALMS) >= 1) startBalance(); else endRun("catch"); }
      else if (rate < 0 && el > 30) {            // it stopped rising below the catch angle
        if (PV(P_BALMS) >= 1 && pitch >= PV(P_LO)) startBalance(); else endRun("short");
      }
      else if (el >= (uint32_t)PV(P_CMS)) endRun("coast time");
      break;
    case LAND:
      wheels(-PV(P_LP), -PV(P_LP));
      if (el >= (uint32_t)PV(P_LMS) || pitch < PV(P_LO)) endRun("landed");
      break;
    case ARMED:
      if (fabsf(pitch - PV(P_TGT)) < 4 && fabsf(rate) < 40) { if (++armOkTicks > 100) startBalance(); }
      else armOkTicks = 0;
      if (mode == ARMED && el >= (uint32_t)PV(P_ARMMS)) endRun("arm time");
      break;
    case BALANCE: {
      if (pitch < PV(P_LO)) { endRun("fell forward"); break; }
      if (pitch > PV(P_HI)) { endRun("fell back"); break; }
      if (el >= (uint32_t)PV(P_BALMS)) { if (PV(P_LMS) >= 1) setMode(LAND); else endRun("time"); break; }
      float dv = PV(P_VA) * dt;                    // MV setpoints, changed gently
      vCmd += constrain(vTgt - vCmd, -dv, dv);
      xCmd += vCmd * dt;
      headSet += wCmd * dt;
      float lean = constrain(PV(P_KV) * (vEst - vCmd) + PV(P_KX) * (xEst - xCmd), -15.0f, 15.0f);   // ahead of where it should be: lean back
      float e = PV(P_TGT) + lean - pitch;          // + = nose too low = falling forward = drive forward
      integ = constrain(integ + PV(P_KI) * e * dt, -PV(P_MAX), PV(P_MAX));
      float u = PV(P_KP) * e - PV(P_KD) * rate + integ;
      u += PV(P_DB) * constrain(u / PV(P_DBW), -1.0f, 1.0f);
      u = constrain(u, -PV(P_MAX), PV(P_MAX));
      vEst += (u - vEst) * min(1.0f, dt / PV(P_TM));
      xEst += vEst * dt;
      // turning left more than asked, or left of the heading: more power left, less right
      float s = constrain(PV(P_KY) * (yawRate - wCmd) + PV(P_KH) * (heading - headSet), -PV(P_SMAX), PV(P_SMAX));
      wheels(u + s, u - s);
      break;
    }
  }
}

void tick(float dt) {
  if (!imuOk) {
    if (mode != IDLE) endRun("imu");
    if (++imuRetryTick >= 250) { imuRetryTick = 0; mpuInit(); }
    return;
  }
  static uint16_t n = 0;
  bool ok = mpuRead();
  if (ok && ++n >= 250) { n = 0; int p = mpuRead8(0x6B); if (p < 0 || (p & 0x40)) ok = false; }
  if (!ok) {
    imuFail++;
    if (++imuRunFail > 10) { imuOk = false; imuRetryTick = 0; }
    return;
  }
  imuRunFail = 0;
  imuGood++;
  fuse(dt, mode == IDLE ? PV(P_TAU) : PV(P_TAUR));
  if (mode != IDLE) control(dt);
  if (recN < recWant && ++recSkip >= (int)PV(P_RECDIV)) {
    recSkip = 0;
    Rec &r = rec[recN++];
    r.t = micros() - recT0;
    for (int i = 0; i < 3; i++) { r.a[i] = raw[i]; r.g[i] = raw[4 + i]; }
    r.pitch = (int16_t)constrain(lroundf(pitch * 100), -32000L, 32000L);
    r.yaw = (int16_t)constrain(lroundf(heading * 10), -32000L, 32000L);
    r.l = (int8_t)(cur[(int)PV(P_WL)] * (int)PV(P_DL));
    r.r = (int8_t)(cur[(int)PV(P_WR)] * (int)PV(P_DR));
    r.mode = mode;
  }
  if (mode == IDLE && tailUntil && (int32_t)(millis() - tailUntil) >= 0) { tailUntil = 0; recWant = recN; }   // run over: stop recording
  if (streamEvery && ++streamN >= streamEvery) {
    streamN = 0;
    if (dumpAt < 0 && Serial.availableForWrite() > 110)
      Serial.printf("I %ld %ld %ld %ld %ld %ld %ld %ld %d %d %ld\n", lroundf(raw[0] * 1000 / ACC_LSB), lroundf(raw[1] * 1000 / ACC_LSB),
                    lroundf(raw[2] * 1000 / ACC_LSB), lroundf(raw[4] * 10 / GYRO_LSB), lroundf(raw[5] * 10 / GYRO_LSB),
                    lroundf(raw[6] * 10 / GYRO_LSB), lroundf((raw[3] / 340.0f + 36.53f) * 10), lroundf(pitch * 100),
                    (cur[(int)PV(P_WL)] * (int)PV(P_DL) + cur[(int)PV(P_WR)] * (int)PV(P_DR)) / 2, mode, lroundf(heading * 10));
  }
}

void dumpStep() {
  if (dumpAt < 0 || !dumpTo) return;
  for (int k = 0; k < 8 && dumpTo->availableForWrite() > 90; k++) {
    if (dumpAt >= recN) { dumpTo->println("END"); dumpAt = -1; return; }
    Rec &r = rec[dumpAt++];
    dumpTo->printf("%lu %d %d %d %d %d %d %d %d %d %d %d\n", r.t, r.a[0], r.a[1], r.a[2], r.g[0], r.g[1], r.g[2], r.pitch, r.l, r.r, r.mode, r.yaw);
  }
}

void info(Stream &s) {
  s.printf("INFO {\"fw\":\"rp2040-motor\",\"ver\":\"" FW_VERSION "\",\"uptime_ms\":%lu,\"cmds\":%lu,\"bad\":%lu,"
           "\"failsafe\":%s,\"failsafe_ms\":%d,\"m\":[%d,%d,%d,%d,%d,%d,%d,%d],"
           "\"imu\":{\"ok\":%s,\"who\":%d,\"good\":%lu,\"fail\":%lu,\"pitch\":%.2f,\"rate\":%.1f,\"g\":%.3f,\"ax\":%s},"
           "\"mode\":%d,\"end\":\"%s\",\"max_pitch\":%.1f,\"rec\":%d}\n",
           millis(), cmdCount, badCount, failsafeTripped ? "true" : "false", FAILSAFE_MS,
           cur[0], cur[1], cur[2], cur[3], cur[4], cur[5], cur[6], cur[7],
           imuOk ? "true" : "false", imuWho, imuGood, imuFail, pitch, rate, accMag, axSet ? "true" : "false",
           mode, endWhy, maxPitch, recN);
}

static int floats(char *t, float *out, int n) {
  int got = 0;
  for (; got < n; got++) { char *e; float x = strtof(t, &e); if (e == t) break; out[got] = x; t = e; }
  return got;
}

struct Port {
  Stream *s; uint8_t bin[10]; int binLen = 0; uint32_t binStart = 0; char line[96]; int lineLen = 0;
  void alive() { lastCmdMs = millis(); cmdCount++; }
  void setAll(const int8_t *v) {
    if (mode != IDLE) { s->println("ERR busy (wheelie run)"); return; }
    for (int i = 0; i < 8; i++) setMotor(i, v[i]);
    alive(); failsafeTripped = false;
  }
  void handleLine() {
    line[lineLen] = 0; lineLen = 0;
    char *p = line; while (*p == ' ') p++;
    if (!*p) return;
    if (!strcasecmp(p, "K")) { alive(); return; }
    if (!strncasecmp(p, "MV ", 3)) {
      float v[2];
      if (floats(p + 3, v, 2) == 2) {
        alive();
        if (mode == BALANCE) { vTgt = constrain(v[0], -90.0f, 90.0f); wCmd = constrain(v[1], -250.0f, 250.0f); }
      } else badCount++;
      return;
    }
    if (!strcasecmp(p, "LAND")) {
      if (mode != BALANCE) { s->println("ERR not balancing"); return; }
      alive();
      if (PV(P_LMS) >= 1) setMode(LAND); else endRun("land");
      s->println("OK LAND"); return;
    }
    if (!strcasecmp(p, "PING")) { s->printf("PONG rp2040-motor v" FW_VERSION " uptime_ms=%lu imu=%d cfg=%d\n", millis(), imuOk, axSet); return; }
    if (!strcasecmp(p, "STOP")) { if (mode != IDLE) endRun("stop"); stopAll(); failsafeTripped = true; s->println("OK STOP"); return; }
    if (!strcasecmp(p, "INFO")) { info(*s); return; }
    if ((p[0] == 'M' || p[0] == 'm') && (p[1] == ' ' || !p[1])) {
      int8_t v[8] = {0}; char *t = p + 1;
      for (int i = 0; i < 8; i++) { char *e; long x = strtol(t, &e, 10); if (e == t) break; if (x < -127 || x > 127) { badCount++; s->println("ERR range"); return; } v[i] = x; t = e; }
      if (mode != IDLE) { s->println("ERR busy (wheelie run)"); return; }
      setAll(v); s->printf("OK M %d %d %d %d %d %d %d %d\n", v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7]); return;
    }
    if (!strncasecmp(p, "IMU", 3) && (p[3] == ' ' || !p[3])) {
      long hz = constrain(strtol(p + 3, nullptr, 10), 0L, 250L);
      streamEvery = hz ? max(1L, 500 / hz) : 0; alive();
      s->printf("OK IMU %ld\n", hz); return;
    }
    if (!strncasecmp(p, "AX ", 3)) {
      float v[6];
      if (floats(p + 3, v, 6) != 6 || !norm(v)) { badCount++; s->println("ERR AX needs 6 numbers"); return; }
      float d = dot(v, v + 3);
      for (int i = 0; i < 3; i++) v[3 + i] -= d * v[i];          // forward exactly square to up
      if (!norm(v + 3)) { badCount++; s->println("ERR AX forward is along up"); return; }
      for (int i = 0; i < 3; i++) { up[i] = v[i]; fwd[i] = v[3 + i]; }
      lft[0] = up[1] * fwd[2] - up[2] * fwd[1]; lft[1] = up[2] * fwd[0] - up[0] * fwd[2]; lft[2] = up[0] * fwd[1] - up[1] * fwd[0];
      axSet = true; pitchInit = false; alive();
      s->println("OK AX"); return;
    }
    if (!strncasecmp(p, "GB ", 3)) {
      float v[3];
      if (floats(p + 3, v, 3) != 3) { badCount++; s->println("ERR GB needs 3 numbers"); return; }
      for (int i = 0; i < 3; i++) gbias[i] = constrain(v[i], -20.0f, 20.0f);
      alive(); s->println("OK GB"); return;
    }
    if (!strncasecmp(p, "SET ", 4)) {
      char *name = p + 4; while (*name == ' ') name++;
      char *val = strchr(name, ' ');
      if (val) {
        *val++ = 0;
        char *e; float x = strtof(val, &e);
        for (int i = 0; i < P_N && e != val; i++) if (!strcasecmp(name, prm[i].name)) {
          // 0.4: the balance settings may change while it balances (practice compares two settings in one run)
          bool live = mode == BALANCE && (i == P_TGT || i == P_KP || i == P_KD || i == P_KI || i == P_KV || i == P_KX || i == P_TM
                                          || i == P_KY || i == P_KH || i == P_SMAX || i == P_DB || i == P_DBW || i == P_MAX
                                          || i == P_VA || i == P_LP || i == P_LMS || i == P_BALMS);
          if (mode != IDLE && !live) { s->println("ERR busy (wheelie run)"); return; }
          if ((i == P_DL || i == P_DR) && x == 0) break;
          prm[i].v = constrain(x, prm[i].lo, prm[i].hi); alive();
          s->printf("OK SET %s %g\n", prm[i].name, prm[i].v); return;
        }
      }
      badCount++; s->println("ERR SET name value"); return;
    }
    if (!strcasecmp(p, "GET")) {
      s->print("PARAMS");
      for (int i = 0; i < P_N; i++) s->printf(" %s=%g", prm[i].name, prm[i].v);
      s->println(); return;
    }
    if (!strncasecmp(p, "REC", 3) && (p[3] == ' ' || !p[3])) {
      if (mode != IDLE) { s->println("ERR busy (wheelie run)"); return; }
      long n = strtol(p + 3, nullptr, 10);
      tailUntil = 0; recStart(n > 0 ? n : REC_MAX); alive();
      s->printf("OK REC %d\n", recWant); return;
    }
    if (!strcasecmp(p, "DUMP")) {
      if (mode != IDLE) { s->println("ERR busy (wheelie run)"); return; }
      recWant = recN; tailUntil = 0;             // stop recording
      dumpTo = s; dumpAt = 0; alive();
      s->printf("D %d\n", recN); return;
    }
    if (!strcasecmp(p, "GO") || !strcasecmp(p, "BAL") || !strcasecmp(p, "ARM")) {
      char c = toupper(p[0]);
      const char *err = startRun(c == 'G' ? KICK1 : c == 'B' ? BALANCE : ARMED);
      if (err) { s->printf("ERR %s\n", err); return; }
      cmdCount++;
      s->printf("OK %s\n", c == 'G' ? "GO" : c == 'B' ? "BAL" : "ARM"); return;
    }
    badCount++; s->println("ERR unknown");
  }
  void poll() {
    if (binLen && millis() - binStart > 100) { binLen = 0; badCount++; s->println("ERR packet timeout"); }
    while (s->available()) {
      uint8_t c = s->read();
      if (binLen) {
        bin[binLen++] = c;
        if (binLen == 10) {
          uint8_t x = 0; for (int i = 1; i <= 8; i++) x ^= bin[i];
          if (x == bin[9]) { int8_t v[8]; for (int i = 0; i < 8; i++) v[i] = (int8_t)bin[i + 1]; if (mode == IDLE) { setAll(v); s->println("OK BIN"); } else s->println("ERR busy (wheelie run)"); }
          else { badCount++; s->println("ERR checksum"); }
          binLen = 0;
        }
      } else if (c == 0xAA) { bin[0] = c; binLen = 1; binStart = millis(); }
      else if (c == '\n' || c == '\r') handleLine();
      else if (lineLen < (int)sizeof(line) - 1) line[lineLen++] = c;
    }
  }
};

Port usb, uart;
uint32_t nextTick = 0, lastTick = 0;

void setup() {
  analogWriteFreq(PWM_FREQ);
  analogWriteRange(255);
  for (int i = 0; i < 8; i++) { pinMode(FWD[i], OUTPUT); pinMode(REV[i], OUTPUT); }
  stopAll();
  Serial.begin(115200);                       // USB CDC; baud is ignored
  Serial1.setTX(28); Serial1.setRX(29); Serial1.begin(9600);
  usb.s = &Serial; uart.s = &Serial1;
  Wire1.setSDA(IMU_SDA); Wire1.setSCL(IMU_SCL);
  Wire1.begin();
  Wire1.setClock(400000);
  Wire1.setTimeout(4, true);                  // a stuck bus must never hold up the motor failsafe
  delay(50);
  mpuInit();
  lastTick = micros(); nextTick = lastTick + TICK_US;
}

void loop() {
  usb.poll(); uart.poll();
  uint32_t now = micros();
  if ((int32_t)(now - nextTick) >= 0) {
    float dt = constrain((now - lastTick) * 1e-6f, 0.0005f, 0.05f);
    lastTick = now;
    nextTick += TICK_US;
    if ((int32_t)(now - nextTick) > 20000) nextTick = now + TICK_US;
    tick(dt);
  }
  if (!failsafeTripped && millis() - lastCmdMs > FAILSAFE_MS) {
    if (mode != IDLE) endRun("failsafe");
    stopAll(); failsafeTripped = true;
  }
  dumpStep();
}
