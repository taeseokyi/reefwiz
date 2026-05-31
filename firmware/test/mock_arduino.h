#pragma once
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <cstdint>
#include <string>
#include <queue>

// Arduino types
typedef uint8_t byte;
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define INPUT 0
#define F(x) x
#define PROGMEM

// Simulated time
static unsigned long _millis = 0;
unsigned long millis() { return _millis; }
void delay(unsigned long ms) { _millis += ms; }
void advanceMillis(unsigned long ms) { _millis += ms; }

// Pin state tracking
static int pinModes[20] = {};
static int pinStates[20] = {};
void pinMode(int pin, int mode) { pinModes[pin] = mode; }
void digitalWrite(int pin, int val) { if(pin<20) pinStates[pin] = val; }
int digitalRead(int pin) { return pinStates[pin]; }

// Serial mock
static std::queue<char> serialInput;
static std::string serialOutput;

struct MockSerial {
    void begin(int) {}
    int available() { return !serialInput.empty(); }
    char read() {
        if (serialInput.empty()) return -1;
        char c = serialInput.front(); serialInput.pop(); return c;
    }
    void print(const char* s) { serialOutput += s; }
    void print(int v) { serialOutput += std::to_string(v); }
    void print(long v) { serialOutput += std::to_string(v); }
    void print(float v, int d=2) { char b[16]; snprintf(b,sizeof(b),"%.*f",d,(double)v); serialOutput+=b; }
    void print(double v, int d=2) { char b[16]; snprintf(b,sizeof(b),"%.*f",d,v); serialOutput+=b; }
    void println(const char* s) { serialOutput += s; serialOutput += "\n"; }
    void println(int v) { print(v); serialOutput += "\n"; }
    void println(long v) { print(v); serialOutput += "\n"; }
    void println(float v, int d=2) { print(v,d); serialOutput += "\n"; }
    void println(double v, int d=2) { print(v,d); serialOutput += "\n"; }
} Serial;

void injectSerial(const char* cmd) {
    for (int i = 0; cmd[i]; i++) serialInput.push(cmd[i]);
    serialInput.push('\n');
}

std::string flushOutput() {
    std::string out = serialOutput;
    serialOutput.clear();
    return out;
}

// EEPROM mock
static uint8_t eeprom[512] = {};
struct MockEEPROM {
    template<typename T> void get(int addr, T& val) { memcpy(&val, &eeprom[addr], sizeof(T)); }
    template<typename T> void put(int addr, const T& val) { memcpy(&eeprom[addr], &val, sizeof(T)); }
} EEPROM;

// ADS1115 mock
#define GAIN_ONE 0
#define RATE_ADS1115_8SPS 0
static float mockVoltage = 1.500; // default ~pH 7

struct Adafruit_ADS1115 {
    bool begin() { return true; }
    void setGain(int) {}
    void setDataRate(int) {}
    int16_t readADC_SingleEnded(int ch) { return (int16_t)(mockVoltage / 4.096 * 32767); }
    float computeVolts(int16_t raw) { return raw * 4.096f / 32767.0f; }
};

// DFRobot_PH mock
struct DFRobot_PH {
    void begin() {}
    float readPH(float voltage, float temp) { return 7.0 - (voltage - 1500.0) / 59.16; }
    void calibration(float v, float t) {}
    void calibration(float v, float t, char* cmd) {}
};

// DS18B20 mock
#define DEVICE_DISCONNECTED_C -127.0f
static float mockTemp = 25.0;

struct OneWire { OneWire(int) {} };
struct DallasTemperature {
    DallasTemperature(OneWire*) {}
    void begin() {}
    int getDeviceCount() { return 1; }
    void requestTemperatures() {}
    float getTempCByIndex(int) { return mockTemp; }
};

// snprintf, dtostrf
using std::isnan;

// Forward declarations (Arduino IDE auto-generates these)
void setup();
void loop();
void handleCommand();
// Forward declarations - enum will be defined in .ino
// Use a wrapper to avoid type mismatch
void onSamplingComplete();
void printKH();
void calcAndSaveKH();
void motorRunTimed(int idx, int pinA, int pinB, bool fwd, long sec);
void motorStopNow(int idx, int pinA, int pinB);
void motorAllStop();
void startAir(long totalSec, long periodSec);
void applyAir();
void stopAir();
void startWait(long sec);
bool parseSeq(const char* cmdLine);
void runSeq();
void executeSeqStep();
void advanceSeq();
void stopSeq();
void executeOneCmd(const char* rawCmd);
void printStatus();
void printHelp();
void printKHHist();
void saveKHRecord(float dkh);
void getTimeStr(char* buf);

char* dtostrf(double val, signed char width, unsigned char prec, char* s) {
    snprintf(s, 16, "%*.*f", width, prec, val);
    return s;
}
