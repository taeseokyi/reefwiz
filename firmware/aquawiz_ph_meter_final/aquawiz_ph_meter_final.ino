/*
 * AquaWiz - pH/dKH 측정 시스템 (탄산염 화학법)
 * KH_tank = KH_ref x 10^(-DeltapH), DeltapH = pH_ref - pH_tank
 * EEPROM: 0x00-0x07 DFRobot_PH, 0x10 refDKH, 0x14 tempOffset, 0x18 calTemp
 */

#include "DFRobot_PH.h"
#include <EEPROM.h>
#include <Adafruit_ADS1X15.h>
#include <OneWire.h>
#include <DallasTemperature.h>

// ============================================================
// 핀 정의
// HC-06: 하드웨어 Serial (D0=RX, D1=TX) 직접 연결
// 업로드 시 HC-06 분리 필요
// ============================================================
#define ONE_WIRE_BUS  14
#define M1_IN1        4
#define M1_IN2        5
#define M2_IN3        6
#define M2_IN4        7
#define M3_IN1        8
#define M3_IN2        9
#define M4_IN3        10
#define M4_IN4        11
#define SOL_REF       12
#define SOL_TANK      13

// ============================================================
// EEPROM 주소
// ============================================================
#define REF_DKH_ADDR      0x10
#define TEMP_OFFSET_ADDR  0x14
#define CAL_TEMP_ADDR     0x18

// ============================================================
// 시퀀스/이력 설정
// ============================================================
#define SEQ_MAX_STEPS  20
#define SEQ_CMD_LEN    24
#define CMD_BUF_SIZE   128
#define KH_HIST_MAX    5
#define KH_TIME_LEN    4

// ============================================================
// BTPRINT 매크로 (Serial = 하드웨어 Serial = HC-06)
// ============================================================
#define BTPRINT(x)       Serial.print(x)
#define BTPRINTLN(x)     Serial.println(x)
#define BTPRINTF(x)      Serial.print(F(x))
#define BTPRINTLNF(x)    Serial.println(F(x))
#define BTPRINTFD(v,d)   Serial.print(v,d)
#define BTPRINTLNFD(v,d) Serial.println(v,d)

// ============================================================
// 구조체
// ============================================================
struct KHRecord {
    char  timestamp[KH_TIME_LEN];
    float dkh;
    bool  valid;
};

struct DateTime {
    int           hour;
    bool          valid;
    unsigned long setMillis;
};

struct MotorTimer {
    bool          active;
    int           pinA, pinB;
    unsigned long endTime;
};

struct AirState {
    bool          active;
    bool          refTurn;
    unsigned long totalEnd;
    unsigned long switchTime;
    unsigned long period;
};

struct WaitState {
    bool          active;
    unsigned long endTime;
};

struct SeqState {
    bool  active;
    char  steps[SEQ_MAX_STEPS][SEQ_CMD_LEN];
    int   total;
    int   current;
    bool  stepRunning;
};

// ============================================================
// 객체 선언
// ============================================================
OneWire           oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);
Adafruit_ADS1115  ads;
DFRobot_PH        ph;

// ============================================================
// 오버샘플링
// ============================================================
const int           SAMPLE_N        = 64;
const unsigned long SAMPLE_INTERVAL = 125;

// ============================================================
// 전역 변수
// ============================================================
int           sampleCount    = 0;
float         sampleSum      = 0.0;
unsigned long lastSampleTime = 0;
bool          voltageReady   = false;

float voltage     = 0.0;
float phValue     = 0.0;
float temperature = 25.0;
float tempOffset  = 0.0;
float calTemp     = 25.0;
float refVoltage  = 0.0;
float refDKH      = 0.0;
float refPH       = 0.0;
float tankPH      = 0.0;
float deltaPH     = 0.0;
float tankDKH     = 0.0;

bool refMeasDone  = false;
bool tankMeasDone = false;

enum MeasureMode { MODE_IDLE, MODE_TANK, MODE_REF, MODE_CALIBRATION };
MeasureMode currentMode = MODE_IDLE;

KHRecord  khHist[KH_HIST_MAX];
int       khHistHead  = 0;
int       khHistCount = 0;

DateTime   currentTime;
MotorTimer motorTimers[4];
AirState   air;
WaitState  waitState;
SeqState   seq;
bool       seqAdvancePending = false;
bool       phCalMode = false;
bool       calphPending = false;

// ============================================================
// Nernst 온도 보상
// ============================================================
float nernstPH(float phRaw, float tempC) {
    return 7.0 + (phRaw - 7.0) * (273.15 + calTemp) / (273.15 + tempC);
}

// ============================================================
// 현재 시각 문자열
// ============================================================
void getTimeStr(char* buf) {
    if (!currentTime.valid) {
        strcpy(buf, "??");
        return;
    }
    unsigned long elapsed = (millis() - currentTime.setMillis) / 3600000UL; // 경과 시간(시)
    int h = (currentTime.hour + (int)(elapsed % 24)) % 24;
    snprintf(buf, KH_TIME_LEN, "%02d", h);
}

// ============================================================
// dKH 이력
// ============================================================
void saveKHRecord(float dkh) {
    KHRecord &r = khHist[khHistHead];
    getTimeStr(r.timestamp);
    r.dkh   = dkh;
    r.valid = true;
    khHistHead = (khHistHead + 1) % KH_HIST_MAX;
    if (khHistCount < KH_HIST_MAX) khHistCount++;
}

void printKHHist() {
    if (khHistCount == 0) { BTPRINTLNF("[KH이력] 없음"); return; }
    BTPRINTF("[KH이력] "); BTPRINT(khHistCount); BTPRINTLNF("개");
    for (int i = 0; i < khHistCount; i++) {
        int idx = ((khHistHead - 1 - i) % KH_HIST_MAX + KH_HIST_MAX) % KH_HIST_MAX;
        KHRecord &r = khHist[idx];
        if (!r.valid) continue;
        char dkhStr[8];
        dtostrf(r.dkh, 5, 2, dkhStr);
        char buf[20];
        snprintf(buf, sizeof(buf), "%2d  %s  %s", i+1, r.timestamp, dkhStr);
        BTPRINTLN(buf);
    }
}

// ============================================================
// 초기화
// ============================================================
void setup() {
    Serial.begin(9600);  // HC-06 기본 보드레이트
    BTPRINTLNF("=== AquaWiz v3.0 ===");

    sensors.begin();
    if (sensors.getDeviceCount() == 0) BTPRINTLNF("[WARN] DS18B20 없음!");
    else                               BTPRINTLNF("[OK] DS18B20");

    if (!ads.begin()) { BTPRINTLNF("[ERR] ADS1115 실패!"); while(1){delay(1000);} }
    ads.setGain(GAIN_ONE);
    ads.setDataRate(RATE_ADS1115_8SPS);
    BTPRINTLNF("[OK] ADS1115");

    ph.begin();
    BTPRINTLNF("[OK] pH lib");

    EEPROM.get(REF_DKH_ADDR, refDKH);
    if (isnan(refDKH) || refDKH < 0.5 || refDKH > 30.0) {
        refDKH = 0.0; BTPRINTLNF("[WARN] refDKH 없음 → setref:xx.x");
    } else { BTPRINTF("[OK] refDKH: "); BTPRINTFD(refDKH,3); BTPRINTLNF(" dKH"); }

    EEPROM.get(TEMP_OFFSET_ADDR, tempOffset);
    if (isnan(tempOffset) || tempOffset < -10.0 || tempOffset > 10.0) {
        tempOffset = 0.0; BTPRINTLNF("[OK] 온도오프셋: 0.0");
    } else { BTPRINTF("[OK] 온도오프셋: "); BTPRINTFD(tempOffset,2); BTPRINTLNF(" C"); }

    EEPROM.get(CAL_TEMP_ADDR, calTemp);
    if (isnan(calTemp) || calTemp < 0.0 || calTemp > 50.0) {
        calTemp = 25.0; BTPRINTLNF("[OK] 보정온도: 25.0C (기본)");
    } else { BTPRINTF("[OK] 보정온도: "); BTPRINTFD(calTemp,1); BTPRINTLNF("C"); }

    BTPRINTLNF("[INFO] ref 매번 측정 필요");

    int pins[] = {M1_IN1, M1_IN2, M2_IN3, M2_IN4,
                  M3_IN1, M3_IN2, M4_IN3, M4_IN4, SOL_REF, SOL_TANK};
    for (int i = 0; i < 10; i++) { pinMode(pins[i], OUTPUT); digitalWrite(pins[i], LOW); }
    BTPRINTLNF("[OK] 핀 초기화");

    // 구조체 초기화
    currentTime.hour = 0;
    currentTime.valid = false; currentTime.setMillis = 0;

    for (int i = 0; i < 4; i++) {
        motorTimers[i].active = false; motorTimers[i].pinA = 0;
        motorTimers[i].pinB = 0;      motorTimers[i].endTime = 0;
    }
    for (int i = 0; i < KH_HIST_MAX; i++) {
        khHist[i].timestamp[0] = '\0'; khHist[i].dkh = 0.0; khHist[i].valid = false;
    }
    air.active = false; air.refTurn = true;
    air.totalEnd = 0;   air.switchTime = 0; air.period = 0;
    waitState.active = false; waitState.endTime = 0;
    seq.active = false; seq.total = 0; seq.current = 0;
    seq.stepRunning = false; seqAdvancePending = false;
    memset(seq.steps, 0, sizeof(seq.steps));

    BTPRINTLNF("[READY] 명령대기 (help 입력)");
}

// ============================================================
// 메인 루프
// ============================================================
void loop() {
    unsigned long now = millis();

    // ① pH 오버샘플링
    if (currentMode != MODE_IDLE && now - lastSampleTime >= SAMPLE_INTERVAL) {
        lastSampleTime = now;
        int16_t raw = ads.readADC_SingleEnded(0);
        if (raw < 0) raw = 0;
        sampleSum += ads.computeVolts(raw) * 1000.0;
        sampleCount++;

        if (sampleCount % 16 == 0) {
            BTPRINTF("  샘플링: "); BTPRINT(sampleCount);
            BTPRINTF("/"); BTPRINTLN(SAMPLE_N);
        }

        if (sampleCount >= SAMPLE_N) {
            voltage = sampleSum / SAMPLE_N;
            sampleSum = 0.0; sampleCount = 0; voltageReady = true;

            sensors.requestTemperatures();
            float t = sensors.getTempCByIndex(0);
            if (t == DEVICE_DISCONNECTED_C || t < -10.0 || t > 85.0) {
                BTPRINTLNF("[WARN] DS18B20 오류→25C"); temperature = 25.0;
            } else {
                temperature = t + tempOffset;
            }
            phValue = nernstPH(ph.readPH(voltage, temperature), temperature);
            if (phValue < 0.0 || phValue > 14.0) {
                BTPRINTF("[WARN] pH 이상: "); BTPRINTLN(phValue);
            }
            onSamplingComplete();
        }
    }

    // ② 보정 모드 모니터링 (2초마다 전압/pH 표시)
    static unsigned long calMonTime = 0;
    if (phCalMode && !voltageReady && currentMode == MODE_IDLE && (long)(now - calMonTime) >= 0) {
        calMonTime = now + 2000UL;
        sensors.requestTemperatures();
        float t = sensors.getTempCByIndex(0);
        if (t != DEVICE_DISCONNECTED_C && t > -10.0 && t < 85.0) temperature = t + tempOffset;
        int16_t raw = ads.readADC_SingleEnded(0);
        if (raw < 0) raw = 0;
        float v = ads.computeVolts(raw) * 1000.0;
        float p = nernstPH(ph.readPH(v, temperature), temperature);
        BTPRINTF("  [모니터] V:"); BTPRINTFD(v,3);
        BTPRINTF(" pH:"); BTPRINTFD(p,3);
        BTPRINTF(" T:"); BTPRINTLNFD(temperature,1);
    }

    // ③ 모터 타이머 (millis 오버플로우 안전)
    for (int i = 0; i < 4; i++) {
        if (motorTimers[i].active && (long)(now - motorTimers[i].endTime) >= 0) {
            digitalWrite(motorTimers[i].pinA, LOW);
            digitalWrite(motorTimers[i].pinB, LOW);
            motorTimers[i].active = false;
            BTPRINTF("[모터"); BTPRINT(i+1); BTPRINTLNF("] 완료");
            if (seq.active && seq.stepRunning) advanceSeq();
        }
    }

    // ④ 에어 교대 (millis 오버플로우 안전)
    if (air.active) {
        if ((long)(now - air.totalEnd) >= 0) {
            stopAir(); BTPRINTLNF("[에어] 완료");
            if (seq.active && seq.stepRunning) advanceSeq();
        } else if ((long)(now - air.switchTime) >= 0) {
            air.refTurn = !air.refTurn;
            air.switchTime = now + air.period;
            applyAir();
        }
    }

    // ⑤ 대기 타이머 (millis 오버플로우 안전)
    if (waitState.active && (long)(now - waitState.endTime) >= 0) {
        waitState.active = false; BTPRINTLNF("[대기] 완료");
        if (seq.active && seq.stepRunning) advanceSeq();
    }

    // ⑥ 시퀀스 다음 단계 (재귀 방지, loop에서 처리)
    if (seqAdvancePending) {
        seqAdvancePending = false;
        executeSeqStep();
    }

    // ⑦ 명령 처리
    handleCommand();
}

// ============================================================
// pH 측정 시작
// ============================================================
void startMeasure(MeasureMode mode) {
    currentMode = mode; voltageReady = false;
    sampleSum = 0.0;    sampleCount = 0;
    lastSampleTime = millis();
    if (mode == MODE_TANK)        BTPRINTLNF("\n[START] 수조수 측정(8초)...");
    if (mode == MODE_REF)         BTPRINTLNF("\n[START] 참조수 측정(8초)...");
    if (mode == MODE_CALIBRATION) BTPRINTLNF("\n[CAL] 보정 측정중...");
}

// ============================================================
// 샘플링 완료
// ============================================================
void onSamplingComplete() {
    BTPRINTLNF("---");
    if (currentMode == MODE_TANK) {
        tankPH = phValue;
        BTPRINTF("[수조수] V:"); BTPRINTFD(voltage,3);
        BTPRINTF(" pH:"); BTPRINTFD(tankPH,3);
        BTPRINTF(" T:"); BTPRINTFD(temperature,1); BTPRINTLNF("C");
        tankMeasDone = true;
        if (seq.active && seq.stepRunning) advanceSeq();

    } else if (currentMode == MODE_REF) {
        refVoltage = voltage; refPH = phValue;
        BTPRINTF("[참조수] V:"); BTPRINTFD(refVoltage,3);
        BTPRINTF(" pH:"); BTPRINTFD(refPH,3);
        BTPRINTF(" T:"); BTPRINTFD(temperature,1); BTPRINTLNF("C");
        BTPRINTLNF("[OK] 참조 RAM 저장");
        refMeasDone = true;
        currentMode = MODE_IDLE;
        if (seq.active && seq.stepRunning) advanceSeq();
        return;
    } else if (currentMode == MODE_CALIBRATION && calphPending) {
        BTPRINTF("[CAL] V:"); BTPRINTFD(voltage,3);
        BTPRINTF(" pH:"); BTPRINTFD(phValue,3);
        BTPRINTF(" T:"); BTPRINTFD(temperature,1); BTPRINTLNF("C");
        calphPending = false;
        char calCmd[] = "CALPH";
        ph.calibration(voltage, temperature, calCmd);
    }
    BTPRINTLNF("---");
    currentMode = MODE_IDLE;
}

// ============================================================
// dKH 계산 + 이력 저장
// ============================================================
void calcAndSaveKH() {
    if (refVoltage <= 0.0) { BTPRINTLNF("[ERR] ref 없음"); if(seq.active&&seq.stepRunning)advanceSeq(); return; }
    if (refDKH    <= 0.0) { BTPRINTLNF("[ERR] refDKH 없음"); if(seq.active&&seq.stepRunning)advanceSeq(); return; }
    if (!tankMeasDone)    { BTPRINTLNF("[ERR] tank 미측정"); if(seq.active&&seq.stepRunning)advanceSeq(); return; }

    deltaPH = refPH - tankPH;
    tankDKH = refDKH * pow(10.0, -deltaPH);

    if (tankDKH < 0.0 || tankDKH > 50.0) {
        BTPRINTF("[WARN] dKH 이상:"); BTPRINTLN(tankDKH);
        if(seq.active&&seq.stepRunning)advanceSeq(); return;
    }

    char ts[KH_TIME_LEN]; getTimeStr(ts);
    BTPRINTLNF("===[dKH]===");
    BTPRINTF("  시각:"); BTPRINTLN(ts);
    BTPRINTF("  참조pH:"); BTPRINTLNFD(refPH,3);
    BTPRINTF("  수조pH:"); BTPRINTLNFD(tankPH,3);
    BTPRINTF("  dPH:"); BTPRINTLNFD(deltaPH,4);
    BTPRINTF("  refKH:"); BTPRINTFD(refDKH,3); BTPRINTLNF(" dKH");
    BTPRINTF("  수조KH:"); BTPRINTFD(tankDKH,3); BTPRINTLNF(" dKH");
    BTPRINTF("  온도:"); BTPRINTFD(temperature,1); BTPRINTLNF("C");
    BTPRINTLNF("===========");

    saveKHRecord(tankDKH);
    BTPRINTF("[OK] 이력저장 총"); BTPRINT(khHistCount); BTPRINTLNF("개");

    tankMeasDone = false; refMeasDone = false;
    if (seq.active && seq.stepRunning) advanceSeq();
}

// ============================================================
// 참조 dKH 역산 (수조 dKH 기준)
// setref에 저장된 값을 수조 dKH로 간주하여 참조 dKH를 계산
// ============================================================
void calcRefDKH() {
    if (refVoltage <= 0.0) { BTPRINTLNF("[ERR] ref 없음"); return; }
    if (refDKH    <= 0.0) { BTPRINTLNF("[ERR] setref 없음 (수조dKH 입력)"); return; }
    if (!tankMeasDone)    { BTPRINTLNF("[ERR] tank 미측정"); return; }

    float knownTankDKH = refDKH;
    deltaPH = tankPH - refPH;
    float newRefDKH = knownTankDKH * pow(10.0, -deltaPH);

    if (newRefDKH < 0.5 || newRefDKH > 30.0) {
        BTPRINTF("[WARN] refDKH 이상:"); BTPRINTLN(newRefDKH);
        return;
    }

    char ts[KH_TIME_LEN]; getTimeStr(ts);
    BTPRINTLNF("===[calcref]===");
    BTPRINTF("  시각:"); BTPRINTLN(ts);
    BTPRINTF("  참조pH:"); BTPRINTLNFD(refPH,3);
    BTPRINTF("  수조pH:"); BTPRINTLNFD(tankPH,3);
    BTPRINTF("  dPH:"); BTPRINTLNFD(deltaPH,4);
    BTPRINTF("  새refDKH:"); BTPRINTFD(newRefDKH,3); BTPRINTLNF(" dKH");
    BTPRINTF("  수조dKH:"); BTPRINTFD(knownTankDKH,3); BTPRINTLNF(" dKH");
    BTPRINTF("  온도:"); BTPRINTFD(temperature,1); BTPRINTLNF("C");
    BTPRINTLNF("===============");

    refDKH = newRefDKH;
    EEPROM.put(REF_DKH_ADDR, refDKH);
    BTPRINTF("[OK] refDKH 저장:"); BTPRINTFD(refDKH,3); BTPRINTLNF(" dKH");

    tankMeasDone = false; refMeasDone = false;
    BTPRINTLNF("[INFO] ref/tank 재측정 필요");
}

// ============================================================
// 모터 제어
// ============================================================
void motorRunTimed(int idx, int pinA, int pinB, bool fwd, long sec) {
    if (sec <= 0 || sec > 3600) {
        BTPRINTLNF("[ERR] 모터시간 1~3600초");
        if(seq.active&&seq.stepRunning)advanceSeq(); return;
    }
    if (motorTimers[idx].active) {
        digitalWrite(motorTimers[idx].pinA, LOW);
        digitalWrite(motorTimers[idx].pinB, LOW);
    }
    digitalWrite(pinA, fwd ? HIGH : LOW);
    digitalWrite(pinB, fwd ? LOW  : HIGH);
    motorTimers[idx].active  = true;
    motorTimers[idx].pinA    = pinA;
    motorTimers[idx].pinB    = pinB;
    motorTimers[idx].endTime = millis() + (unsigned long)sec * 1000UL;
    BTPRINTF("[M"); BTPRINT(idx+1);
    if (fwd) BTPRINTF("] 정방향 "); else BTPRINTF("] 역방향 ");
    BTPRINT(sec); BTPRINTLNF("초");
}

void motorStopNow(int idx, int pinA, int pinB) {
    digitalWrite(pinA, LOW); digitalWrite(pinB, LOW);
    motorTimers[idx].active = false;
    BTPRINTF("[M"); BTPRINT(idx+1); BTPRINTLNF("] 정지");
}

void motorAllStop() {
    motorStopNow(0,M1_IN1,M1_IN2); motorStopNow(1,M2_IN3,M2_IN4);
    motorStopNow(2,M3_IN1,M3_IN2); motorStopNow(3,M4_IN3,M4_IN4);
}

// ============================================================
// 에어 공급
// ============================================================
void startAir(long totalSec, long periodSec) {
    if (totalSec<=0||totalSec>7200)  { BTPRINTLNF("[ERR] 에어시간 1~7200"); if(seq.active&&seq.stepRunning)advanceSeq(); return; }
    if (periodSec<=0||periodSec>totalSec) { BTPRINTLNF("[ERR] 주기 1~총시간"); if(seq.active&&seq.stepRunning)advanceSeq(); return; }
    stopAir();
    air.active     = true; air.refTurn = true;
    air.totalEnd   = millis() + (unsigned long)totalSec  * 1000UL;
    air.switchTime = millis() + (unsigned long)periodSec * 1000UL;
    air.period     = (unsigned long)periodSec * 1000UL;
    applyAir();
    BTPRINTF("[에어] "); BTPRINT(totalSec); BTPRINTF("초/"); BTPRINT(periodSec); BTPRINTLNF("초주기");
}

void applyAir() {
    digitalWrite(SOL_REF, LOW); digitalWrite(SOL_TANK, LOW);
    if (air.refTurn) { digitalWrite(SOL_REF,  HIGH); BTPRINTLNF("[에어] 참조ON"); }
    else             { digitalWrite(SOL_TANK, HIGH); BTPRINTLNF("[에어] 수조ON"); }
}

void stopAir() {
    air.active = false;
    digitalWrite(SOL_REF, LOW); digitalWrite(SOL_TANK, LOW);
}

// ============================================================
// 대기
// ============================================================
void startWait(long sec) {
    if (sec<=0||sec>3600) { BTPRINTLNF("[ERR] 대기 1~3600초"); if(seq.active&&seq.stepRunning)advanceSeq(); return; }
    waitState.active  = true;
    waitState.endTime = millis() + (unsigned long)sec * 1000UL;
    BTPRINTF("[대기] "); BTPRINT(sec); BTPRINTLNF("초");
}

// ============================================================
// 시퀀스
// ============================================================
bool parseSeq(const char* cmdLine) {
    const char* colon = strchr(cmdLine, ':');
    if (!colon) { BTPRINTLNF("[ERR] seq 형식: seq:cmd1|cmd2"); return false; }
    seq.total = 0; seq.current = 0; seq.active = false; seq.stepRunning = false;
    const char* p = colon + 1;
    while (*p && seq.total < SEQ_MAX_STEPS) {
        const char* sep = strchr(p, '|');
        int len = sep ? (int)(sep - p) : (int)strlen(p);
        if (len > 0) {
            if (len >= SEQ_CMD_LEN) len = SEQ_CMD_LEN - 1;
            strncpy(seq.steps[seq.total], p, len);
            seq.steps[seq.total][len] = '\0';
            seq.total++;
        }
        if (!sep) break;
        p = sep + 1;
    }
    if (seq.total == 0) { BTPRINTLNF("[ERR] 명령 없음"); return false; }
    if (*p && seq.total >= SEQ_MAX_STEPS) {
        BTPRINTF("[ERR] seq 최대 "); BTPRINT(SEQ_MAX_STEPS);
        BTPRINTLNF("단계 초과!");
        seq.total = 0; return false;
    }
    return true;
}

void runSeq() {
    seq.active = true; seq.current = 0; seq.stepRunning = false;
    BTPRINTF("[SEQ] 시작 "); BTPRINT(seq.total); BTPRINTLNF("단계");
    executeSeqStep();
}

void executeSeqStep() {
    if (!seq.active || seq.current >= seq.total) {
        seq.active = false; seq.stepRunning = false;
        BTPRINTLNF("[SEQ] 완료"); return;
    }
    BTPRINTF("[SEQ] "); BTPRINT(seq.current+1); BTPRINTF("/");
    BTPRINT(seq.total); BTPRINTF("→"); BTPRINTLN(seq.steps[seq.current]);
    seq.stepRunning = true;
    executeOneCmd(seq.steps[seq.current]);
}

void advanceSeq() {
    seq.stepRunning = false; seq.current++;
    seqAdvancePending = true;
}

void stopSeq() {
    seq.active = false; seq.stepRunning = false;
    seqAdvancePending = false; BTPRINTLNF("[SEQ] 중단");
}

// ============================================================
// 단일 명령 실행
// ============================================================
void executeOneCmd(const char* rawCmd) {
    char cmd[SEQ_CMD_LEN]; strncpy(cmd, rawCmd, SEQ_CMD_LEN-1); cmd[SEQ_CMD_LEN-1]='\0';
    // 소문자 변환
    for (int i = 0; cmd[i]; i++) if (cmd[i]>='A'&&cmd[i]<='Z') cmd[i]+=32;

    // settime:HH
    if (strncmp(cmd, "settime:", 8) == 0) {
        int h = atoi(cmd + 8);
        if (h >= 0 && h <= 23) {
            currentTime.hour = h;
            currentTime.valid = true; currentTime.setMillis = millis();
            char ts[KH_TIME_LEN]; getTimeStr(ts);
            BTPRINTF("[OK] 시각(시): "); BTPRINTLN(ts);
        } else { BTPRINTLNF("[ERR] settime:HH (0~23)"); }
        if (seq.active && seq.stepRunning) advanceSeq(); return;
    }

    // ref / tank / calckh / calcref
    if (strcmp(cmd,"ref")==0)     { refMeasDone=false;  startMeasure(MODE_REF);  return; }
    if (strcmp(cmd,"tank")==0)    { tankMeasDone=false; startMeasure(MODE_TANK); return; }
    if (strcmp(cmd,"calckh")==0)  { calcAndSaveKH(); return; }
    if (strcmp(cmd,"calcref")==0) { calcRefDKH(); if(seq.active&&seq.stepRunning)advanceSeq(); return; }

    // 모터: m1f:초, m1b:초, m1s
    struct { int idx; int pa; int pb; const char* pf; } mdef[4] = {
        {0,M1_IN1,M1_IN2,"m1"}, {1,M2_IN3,M2_IN4,"m2"},
        {2,M3_IN1,M3_IN2,"m3"}, {3,M4_IN3,M4_IN4,"m4"}
    };
    for (int i = 0; i < 4; i++) {
        char pff[3]; strncpy(pff, mdef[i].pf, 2); pff[2]='\0';
        char pfF[5], pfB[5], pfS[4];
        snprintf(pfF, sizeof(pfF), "%sf:", pff);
        snprintf(pfB, sizeof(pfB), "%sb:", pff);
        snprintf(pfS, sizeof(pfS), "%ss", pff);
        if (strncmp(cmd, pfF, 4)==0) { motorRunTimed(mdef[i].idx,mdef[i].pa,mdef[i].pb,true,  atol(cmd+4)); return; }
        if (strncmp(cmd, pfB, 4)==0) { motorRunTimed(mdef[i].idx,mdef[i].pa,mdef[i].pb,false, atol(cmd+4)); return; }
        if (strcmp(cmd, pfS)==0)     { motorStopNow(mdef[i].idx,mdef[i].pa,mdef[i].pb); if(seq.active&&seq.stepRunning)advanceSeq(); return; }
    }

    // 에어: air:총초:주기초
    if (strncmp(cmd,"air:",4)==0) {
        long tot=0, per=0; char* p2;
        tot = strtol(cmd+4, &p2, 10);
        if (*p2==':') per = strtol(p2+1, NULL, 10);
        startAir(tot, per); return;
    }
    if (strcmp(cmd,"airoff")==0) { stopAir(); BTPRINTLNF("[에어] OFF"); if(seq.active&&seq.stepRunning)advanceSeq(); return; }

    // 대기
    if (strncmp(cmd,"wait:",5)==0) { startWait(atol(cmd+5)); return; }

    // 솔레노이드 직접
    if (strcmp(cmd,"ron")==0)  { digitalWrite(SOL_REF,  HIGH); BTPRINTLNF("[SOL] 참조ON"); }
    else if (strcmp(cmd,"ton")==0)  { digitalWrite(SOL_TANK, HIGH); BTPRINTLNF("[SOL] 수조ON"); }
    else { BTPRINTF("[?] "); BTPRINTLN(cmd); }
    if (seq.active && seq.stepRunning) advanceSeq();
}

// ============================================================
// 명령 처리
// ============================================================
void handleCommand() {
    static char cmdBuf[CMD_BUF_SIZE];
    if (!Serial.available()) return;
    int i = 0;
    unsigned long t = millis();
    while (millis() - t < 200) {
        if (Serial.available()) {
            char c = Serial.read();
            if (c == '\n' || c == '\r') break;
            if (i < (int)sizeof(cmdBuf) - 1) cmdBuf[i++] = c;
        }
    }
    bool truncated = (i >= (int)sizeof(cmdBuf) - 1);
    cmdBuf[i] = '\0';
    while (i > 0 && (cmdBuf[i-1]==' '||cmdBuf[i-1]=='\r')) cmdBuf[--i]='\0';
    if (cmdBuf[0] == '\0') return;
    if (truncated) {
        BTPRINTF("[ERR] 명령이 "); BTPRINT(CMD_BUF_SIZE-1);
        BTPRINTLNF("자 초과! seq를 나눠 실행하세요");
        return;
    }

    char cmdL[SEQ_CMD_LEN+10];
    strncpy(cmdL, cmdBuf, sizeof(cmdL)-1); cmdL[sizeof(cmdL)-1]='\0';
    for (int i=0; cmdL[i]; i++) if(cmdL[i]>='A'&&cmdL[i]<='Z') cmdL[i]+=32;

    // pH 보정
    if (strcmp(cmdL,"enterph")==0||strcmp(cmdL,"calph")==0||strcmp(cmdL,"exitph")==0) {
        if (strcmp(cmdL,"enterph")==0) {
            phCalMode = true;
            calphPending = false;
            char enterCmd[] = "ENTERPH";
            ph.calibration(voltage, temperature, enterCmd);
            BTPRINTLNF("[보정] 진입→안정화 후 calph 실행");
        } else if (!phCalMode) {
            BTPRINTLNF("[ERR] enterph 먼저 실행");
        } else if (strcmp(cmdL,"calph")==0) {
            calphPending = true;
            startMeasure(MODE_CALIBRATION);
        } else if (strcmp(cmdL,"exitph")==0) {
            if (!voltageReady) { BTPRINTLNF("[WARN] calph 먼저 실행"); return; }
            char exitCmd[] = "EXITPH";
            ph.calibration(voltage, temperature, exitCmd);
            if (calTemp > 0.1 && abs(temperature - calTemp) > 2.0) {
                BTPRINTF("[WARN] 보정T차이>2C! ");
                BTPRINTFD(calTemp,1); BTPRINTF("→"); BTPRINTLNFD(temperature,1);
            }
            calTemp = temperature;
            EEPROM.put(CAL_TEMP_ADDR, calTemp);
            BTPRINTF("[보정] 완료 보정온도:"); BTPRINTFD(calTemp,1); BTPRINTLNF("C");
            phCalMode = false;
        }
        return;
    }

    // setref
    if (strncmp(cmdL,"setref:",7)==0) {
        float v = atof(cmdBuf+7);
        if (v>=0.5&&v<=30.0) { refDKH=v; EEPROM.put(REF_DKH_ADDR,refDKH); BTPRINTF("[OK] refDKH:"); BTPRINTFD(refDKH,3); BTPRINTLNF(" dKH"); }
        else BTPRINTLNF("[ERR] 0.5~30.0");
        return;
    }

    // settemp
    if (strncmp(cmdL,"settemp:",8)==0) {
        float v = atof(cmdBuf+8);
        if (v>=-10.0&&v<=10.0) {
            tempOffset=v; EEPROM.put(TEMP_OFFSET_ADDR,tempOffset);
            BTPRINTF("[OK] 오프셋:"); BTPRINTFD(tempOffset,2); BTPRINTLNF("C");
            sensors.requestTemperatures();
            float raw=sensors.getTempCByIndex(0);
            if (raw!=DEVICE_DISCONNECTED_C&&raw>-10.0&&raw<85.0) {
                BTPRINTF("[INFO] raw:"); BTPRINTFD(raw,2);
                BTPRINTF("+"); BTPRINTFD(tempOffset,2);
                BTPRINTF("="); BTPRINTLNFD(raw+tempOffset,2);
            }
        } else BTPRINTLNF("[ERR] -10~10");
        return;
    }

    // seq
    if (strncmp(cmdL,"seq:",4)==0) {
        if (seq.active) { BTPRINTLNF("[WARN] 실행중→seqstop"); return; }
        if (parseSeq(cmdBuf)) runSeq();
        return;
    }
    if (strcmp(cmdL,"seqstop")==0) { stopSeq(); motorAllStop(); stopAir(); waitState.active=false; return; }

    // 일반
    if (strcmp(cmdL,"status")==0)   { printStatus(); return; }
    if (strcmp(cmdL,"khhist")==0)   { printKHHist(); return; }
    if (strcmp(cmdL,"help")==0) { printHelp(); return; }

    executeOneCmd(cmdBuf);
}

// ============================================================
// 상태 출력
// ============================================================
void printStatus() {
    char ts[KH_TIME_LEN]; getTimeStr(ts);
    BTPRINTLNF("=== 상태 ===");
    BTPRINTF("시각:"); BTPRINTLN(ts);
    BTPRINTF("온도:"); BTPRINTFD(temperature,1); BTPRINTF("C 오프셋:"); BTPRINTFD(tempOffset,2); BTPRINTF(" 보정T:"); BTPRINTFD(calTemp,1); BTPRINTLNF("C");
    BTPRINTF("수조pH:"); BTPRINTLNFD(tankPH,3);
    BTPRINTF("참조pH:"); BTPRINTLNFD(refPH,3);
    BTPRINTF("dPH:"); BTPRINTLNFD(deltaPH,4);
    BTPRINTF("refKH:"); BTPRINTFD(refDKH,3); BTPRINTLNF(" dKH");
    BTPRINTF("수조KH:"); BTPRINTFD(tankDKH,3); BTPRINTLNF(" dKH");
    BTPRINTF("refV:"); BTPRINTFD(refVoltage,3); BTPRINTLNF(" mV");
    BTPRINTF("KH이력:"); BTPRINT(khHistCount); BTPRINTLNF("개");
    const char* mn[4] = {"M1","M2","M3","M4"};
    for (int i=0; i<4; i++) {
        BTPRINTF("["); BTPRINT(mn[i]); BTPRINTF("] ");
        if (motorTimers[i].active) {
            long r=((long)motorTimers[i].endTime-(long)millis())/1000L;
            BTPRINTF("동작 잔여:"); BTPRINT(r); BTPRINTLNF("초");
        } else BTPRINTLNF("정지");
    }
    BTPRINTF("[에어] ");
    if (air.active) {
        long r=((long)air.totalEnd-(long)millis())/1000L;
        if (air.refTurn) BTPRINTF("참조ON"); else BTPRINTF("수조ON");
        BTPRINTF(" 잔여:"); BTPRINT(r); BTPRINTLNF("초");
    } else BTPRINTLNF("정지");
    BTPRINTF("[대기] ");
    if (waitState.active) {
        long r=((long)waitState.endTime-(long)millis())/1000L;
        BTPRINT(r); BTPRINTLNF("초");
    } else BTPRINTLNF("-");
    BTPRINTF("[SEQ] ");
    if (seq.active) { BTPRINT(seq.current+1); BTPRINTF("/"); BTPRINTLN(seq.total); }
    else BTPRINTLNF("-");
    BTPRINTLNF("============");
}

// ============================================================
// 도움말
// ============================================================
void printHelp() {
    BTPRINTLNF("=== 명령어 ===");
    BTPRINTLNF("[pH] settime:HH | ref | tank | calckh | calcref");
    BTPRINTLNF("     setref:x | settemp:x | khhist | status");
    BTPRINTLNF("     help");
    BTPRINTLNF("[보정] enterph | calph | exitph");
    BTPRINTLNF("[모터] m1f:초 m1b:초 m1s (m2~m4동일)");
    BTPRINTLNF("[에어] air:총초:주기초 | airoff");
    BTPRINTLNF("[대기] wait:초");
    BTPRINTLNF("[SEQ] seq:cmd1|cmd2|... | seqstop");
    BTPRINTLNF("=============");
}
