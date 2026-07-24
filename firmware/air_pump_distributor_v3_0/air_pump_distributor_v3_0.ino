/*
 포드류 배양을 운한 공기 토출 분배기 V3.0
  - 모드 드라이버 L298N 으로 두 개의 소형 솔레노이드 밸브 제어
  - 기본 코드는 칼슘리액터 출력기를 이용.
  - EEPROM 저장 기능, Run Time 을 초 단위에서 1/1000 초로 수정
  - millis() 타이머가 계속증가하다가 최대치에서 다시 0 으로 될때, 타이머 재설정 추가
  - ms 시간 변환 #define 문법 오류 수정 및 help 추가
  - claude 버그, watch_doc, lazy_timer 주소 호출, EEPROM.write update 변경, 주석 호출 수정
  - refresh_all_timers() 에서 lazy_timer는 reset이 포함되어 중복 배제
  - claude 버그, r/l/rr/ll test 큰 값(예:100000) 입력 시 16bit int 오버플로우로 밸브 안멈추는 문제 수정(long으로 변경)
  - claude 버그, r/l/rr/ll gt/rt 도 동일한 오버플로우 패턴 있어 clamp_to_uint16()로 0~65535 클램프 추가
  - claude 버그, set time hh:mm:ss 도 동일 패턴(int hh,mm,ss) 있어 long+범위클램프로 수정
  - claude 버그, "rrtest" 명령이 "rrt" 핸들러에도 걸려 R RUN 시간이 0으로 덮어써지던 파싱 충돌 수정
  - claude 개선, 명령은 개행(\n) 완성 시에만 처리(BT 순단 시 잘린 명령 오저장 방지), 수신 delay(5) 제거(연속 수신 시 loop 독점 방지), 64자 상한
  - claude 개선, debug reset의 timer0_millis 쓰기 인터럽트 보호, startup() 참조 전달로 변경
  - claude 개선, 문자열 리터럴 F() 매크로로 플래시 이동(SRAM 절약), cmdString.reserve(64), help/주석 단위 표기 실제 코드와 일치(gt=초, rt/test=ms)
  - claude 개선, 롤오버 시 refresh_all_timers() 제거(위 6번 항목 재검토): SimpleTimer.isReady()가 (millis()-_previous)를 부호 없는 32비트 뺄셈으로 비교하므로 millis() 롤오버(≈49.7일)를 스스로 정확히 넘긴다. 롤오버마다 타이머를 리셋하면 진행 중이던 GAP 카운트다운이 버려져 ~49.7일에 1회 토출(밸브 동작)이 최대 1주기 늦춰지는 '주기 밀림'이 있었음. Diff_Time(로그 시각) 보정은 유지, setup()·"refresh all" 명령에서의 호출은 필수라 그대로 둠
 이태석, 2024.9.28, 2025.5.29, 2025.10.9, 2025.12.13, 2026.7.2, 2026.7.24
*/

#include <SoftwareSerial.h>
#include <EEPROM.h>
#include <SimpleTimer.h>
#include <avr/wdt.h>

// Create a first&sceond timer and specify its interval in milliseconds
SimpleTimer firstTimer;
SimpleTimer secondTimer;
// Run timer
SimpleTimer firstRunTimer;
SimpleTimer secondRunTimer;
// Test timer
SimpleTimer firstTestTimer;
SimpleTimer secondTestTimer;
// Create a ffirst&ssceond timer and specify its interval in milliseconds
SimpleTimer ffirstTimer;
SimpleTimer ssecondTimer;
// Run timer
SimpleTimer ffirstRunTimer;
SimpleTimer ssecondRunTimer;
// Test timer
SimpleTimer ffirstTestTimer;
SimpleTimer ssecondTestTimer;
// A flag indicates, that a run and test timer is ready
bool firstFlag = false;
bool secondFlag = false;
bool firstRunFlag = false;
bool secondRunFlag = false;
bool firstTestFlag = false;
bool secondTestFlag = false;
bool ffirstFlag = false;
bool ssecondFlag = false;
bool ffirstRunFlag = false;
bool ssecondRunFlag = false;
bool ffirstTestFlag = false;
bool ssecondTestFlag = false;

//Motor right
const int motorPin1 = 9; // IN1_ L298n=>D9
const int motorPin2 = 10; // IN2_ L298n=>D10

//Motor left
const int motorPin3 = 5; // IN3_ L298n=>D5
const int motorPin4 = 6; // IN4_ L298n=>D6

// Motor Control : motorSet1, motorSet2
// 오른쪽 Motor : motorPin1,motorpin2 
// 왼쪽 Motor : motorpin3,motorpin4

// 모터의 힘을 조절하는 값 - PWM 아날로그 출력
// USB 5V전원을 USB 단자로 공급, 양쪽 6V 솔레노이드 제어를 위해 100%로 출력한다.
const int R_POWER = 255; // 50+알파, 구동 전압의  1/4 출력 24V 일때, 5~6V
const int L_POWER = 255; // 250이면, 구동 전압의 100% 출력 24V 일때, 24V

const int blueTx = 2;   //Tx (보내는핀 설정)
const int blueRx = 3;   //Rx (받는핀 설정)

String cmdString = ""; //받는 문자열
SoftwareSerial BTSerial(blueTx, blueRx);      // 블루투스 시리얼 통신을 위한 객체선언
//#define BTSerial Serial

/** the current address in the EEPROM (i.e. which byte we're going to write to next) **/
const int R_GT_addr = 0;
const int R_RT_addr = 16;  // 2 번 주소 값 저장이 안되는 오류 발생, 주소 변경
const int L_GT_addr = 4;
const int L_RT_addr = 6;
const int RR_GT_addr = 8;
const int RR_RT_addr = 10;
const int LL_GT_addr = 12;
const int LL_RT_addr = 14;

#define M2msR(x) ((unsigned long)(x) * 60UL * 1000UL)  // 오른쪽 모터용 변환, 1 분
#define M2msL(x) ((unsigned long)(x) * 60UL * 1000UL)  // 왼쪽 모터용 변환, 1 분
#define S2msR(x) ((unsigned long)(x) * 1000UL)  // 1 초
#define S2msL(x) ((unsigned long)(x) * 1000UL)  // 1 초
#define S2ms(x) ((unsigned long)(x) * 1UL)  // x가 ms 로 들어오게 코딩함, 변환 안함.
#define D2ms(x) ((unsigned long)(x) * 24UL * 60UL * 60UL * 1000UL)  // 1 일

unsigned long R_Gap_Time = S2msR(10);                // 동작 주기(GAP), 단위: 초 (S2ms* = *1000)
unsigned long R_Run_Time = S2ms(12);                 // 동작 시간(RUN), 단위: ms (S2ms = 변환 없음)
unsigned long L_Gap_Time = S2msL(10);                // 동작 주기(GAP), 단위: 초 (S2ms* = *1000)
unsigned long L_Run_Time = S2ms(12);                 // 동작 시간(RUN), 단위: ms (S2ms = 변환 없음)
unsigned long RR_Gap_Time = S2msR(10);                // 동작 주기(GAP), 단위: 초 (S2ms* = *1000)
unsigned long RR_Run_Time = S2ms(12);                 // 동작 시간(RUN), 단위: ms (S2ms = 변환 없음)
unsigned long LL_Gap_Time = S2msL(10);                // 동작 주기(GAP), 단위: 초 (S2ms* = *1000)
unsigned long LL_Run_Time = S2ms(12);                 // 동작 시간(RUN), 단위: ms (S2ms = 변환 없음)

unsigned long Diff_Time = 0;  // 현재시간 유지 보정값
unsigned long prev_time = 0;  // loop에서 이전 시간 저장
unsigned int tHH, tMM, tSS, tMI;

unsigned int EEPROM_readInt(unsigned int addr)
{
  union
  {
    byte b[2];
    unsigned int f;
  } data;
  for (int i = 0; i < 2; i++)
  {
    data.b[i] = EEPROM.read(addr + i);
  }
  return data.f;
}
void EEPROM_writeInt(unsigned int addr, unsigned int x)
{
  union
  {
    byte b[2];
    unsigned int f;
  } data;
  data.f = x;
  for (int i = 0; i < 2; i++)
  {
    EEPROM.update(addr + i, data.b[i]); // 값이 다를 때만 저장, 쓰기 횟수 보호
  }
}

// EEPROM에는 unsigned int(16bit, 0~65535)로만 저장되므로, 입력값을 그 범위로 클램프한다.
// (클램프 없이 unsigned int에 직접 대입하면 예: 100000 → 34464 로 조용히 랩어라운드됨)
unsigned int clamp_to_uint16(long v) {
  if (v < 0) return 0;
  if (v > 65535L) return 65535;
  return (unsigned int)v;
}


// 타이머를 사용하지 않을 때, 끄는 기능이 없으므로, 20일에 한번씩 천천히 작동하게 한다.
void lazy_timer(SimpleTimer &x) {   // & 추가
  unsigned long a_month = D2ms(20);
  x.setInterval(a_month);
  x.reset();
}

void set_HHMMSSMI(unsigned long mili_time) {
  unsigned long readTime;

  readTime = mili_time/1000;

  tMI = mili_time%1000;
  tSS = readTime%60;
  tMM = (readTime/60)%60;
  tHH = (readTime/(60*60))%24;
}

unsigned long trunc_less_24h(unsigned long mili_time) {
  unsigned int hh, mm, ss, mi;
  unsigned long readTime;

  readTime = mili_time/1000;

  mi = mili_time%1000;
  ss = readTime%60;
  mm = (readTime/60)%60;
  hh = (readTime/(60*60))%24;

  return ((long)hh*3600+mm*60+ss)*1000+mi;
}

// 로그 기록 분석을 위한 타임스템프를 화면에 출력한다.
void print_TS(bool use_adjust=true) {
  unsigned int hh, mm, ss, mi;
  char buf[20];
  unsigned long timeVal, readTime;
  
  if(use_adjust) {
    timeVal = trunc_less_24h(millis()) + Diff_Time;
  }
  else{
    timeVal = trunc_less_24h(millis());
  }
  readTime = timeVal/1000;

  mi = timeVal%1000;
  ss = readTime%60;
  mm = (readTime/60)%60;
  hh = (readTime/(60*60))%24;

  sprintf(buf, " %02d:%02d:%02d.%03d ", hh,mm,ss,mi); 
  BTSerial.print(buf);
}

// ===

void R_Motor_Task_On(unsigned long duration) {
  // 오른쪽 모터 제어, 6V 모터
  analogWrite(motorPin1, R_POWER); //9 right 
  BTSerial.print(F("오른쪽 모터 펌핑 시작:"));
  print_TS();
  BTSerial.print(duration);
  BTSerial.println();
}
void R_Motor_Task_Off() {
  BTSerial.print(F("오른쪽 모터 펌핑 중지:"));
  print_TS(); // 타임 스템프 찍기
  BTSerial.println();
  analogWrite(motorPin1, 0); //9 right 
  //digitalWrite(IN1, HIGH); digitalWrite(IN2, HIGH); //급 정지
}
void L_Motor_Task_On(unsigned long duration) {
  // 왼쪽 모터 제어, 24v 모터
  analogWrite(motorPin3, L_POWER); //5 left
  BTSerial.print(F("왼쪽 모터 펌핑 시작:"));
  print_TS();
  BTSerial.print(duration);
  BTSerial.println();
}
void L_Motor_Task_Off() {
  BTSerial.print(F("왼쪽 모터 펌핑 중지:"));
  print_TS(); // 타임 스템프 찍기
  BTSerial.println();
  analogWrite(motorPin3, 0); //5 left
  //digitalWrite(IN1, HIGH); digitalWrite(IN2, HIGH); //급 정지
}

// ===

void RR_Motor_Task_On(unsigned long duration) {
  // RR 모터 제어, 6V 모터
  analogWrite(motorPin2, R_POWER); //10 right
  BTSerial.print(F("RR 모터 펌핑 시작:"));
  print_TS();
  BTSerial.print(duration);
  BTSerial.println();
}
void RR_Motor_Task_Off() {
  BTSerial.print(F("RR 모터 펌핑 중지:"));
  print_TS(); // 타임 스템프 찍기
  BTSerial.println();
  analogWrite(motorPin2, 0); //10 right
  //digitalWrite(IN1, HIGH); digitalWrite(IN2, HIGH); //급 정지
}
void LL_Motor_Task_On(unsigned long duration) {
  // 왼쪽 모터 제어, 24v 모터
  analogWrite(motorPin4, L_POWER); //6 left
  BTSerial.print(F("LL 모터 펌핑 시작:"));
  print_TS();
  BTSerial.print(duration);
  BTSerial.println();
}
void LL_Motor_Task_Off() {
  BTSerial.print(F("LL 모터 펌핑 중지:"));
  print_TS(); // 타임 스템프 찍기
  BTSerial.println();
  analogWrite(motorPin4, 0); //6 left
  //digitalWrite(IN1, HIGH); digitalWrite(IN2, HIGH); //급 정지
}

// ===

void print_all() {
      BTSerial.print(F("오른쪽 휴지(GAP) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(R_GT_addr));     // 블루투스에 데이터 전송 합니다.
      BTSerial.print(F("오른쪽 동작(RUN) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(R_RT_addr));     // 블루투스에 데이터 전송 합니다.
      BTSerial.print(F("왼쪽 휴지(GAP) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(L_GT_addr));     // 블루투스에 데이터 전송 합니다.
      BTSerial.print(F("왼쪽 동작(RUN) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(L_RT_addr));     // 블루투스에 데이터 전송 합니다.

      BTSerial.print(F("RR 휴지(GAP) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(RR_GT_addr));     // 블루투스에 데이터 전송 합니다.
      BTSerial.print(F("RR 동작(RUN) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(RR_RT_addr));     // 블루투스에 데이터 전송 합니다.
      BTSerial.print(F("LL 휴지(GAP) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(LL_GT_addr));     // 블루투스에 데이터 전송 합니다.
      BTSerial.print(F("LL 동작(RUN) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(LL_RT_addr));     // 블루투스에 데이터 전송 합니다.
}

void refresh_all_timers() {
  // timed actions setup
  firstTimer.setInterval(R_Gap_Time);
  secondTimer.setInterval(L_Gap_Time);
  firstRunTimer.setInterval(R_Run_Time);
  secondRunTimer.setInterval(L_Run_Time);

  firstTimer.reset();
  secondTimer.reset();
  firstRunTimer.reset();
  secondRunTimer.reset();
  lazy_timer(firstTestTimer);   // firstTestTimer.reset();
  lazy_timer(secondTestTimer);  // secondTestTimer.reset();

  // A flag indicates, that a run and test timer is ready
  firstFlag = true;
  secondFlag = true;
  firstRunFlag = false;
  secondRunFlag = false;
  firstTestFlag = false;
  secondTestFlag = false;

// ===

  // timed actions setup
  ffirstTimer.setInterval(RR_Gap_Time);
  ssecondTimer.setInterval(LL_Gap_Time);
  ffirstRunTimer.setInterval(RR_Run_Time);
  ssecondRunTimer.setInterval(LL_Run_Time);

  ffirstTimer.reset();
  ssecondTimer.reset();
  ffirstRunTimer.reset();
  ssecondRunTimer.reset();
  lazy_timer(ffirstTestTimer);   // ffirstTestTimer.reset();
  lazy_timer(ssecondTestTimer);  // ssecondTestTimer.reset();

  // A flag indicates, that a run and test timer is ready
  ffirstFlag = true;
  ssecondFlag = true;
  ffirstRunFlag = false;
  ssecondRunFlag = false;
  ffirstTestFlag = false;
  ssecondTestFlag = false;

  BTSerial.println(F("Refreshed all timers!"));
}

// 값 전달로 받으면 복사본 소멸자가 end()를 호출하는 잠재 위험이 있어 참조로 받는다
void startup(SoftwareSerial &s_out) {
  s_out.println(F("2025.12.13_AirPumpFlow_Controller"));  // 작성날짜를 출력 합니다.
  s_out.println(F("APFC_002.v3.0"));                  // 키트번호를 출력 합니다.
  s_out.println(F("START..."));                  // 시리얼 데이터 전송 합니다.
}

// the setup function runs once when you press reset or power the board
void setup() {
  // Serial.begin(9600);                         // HardwareSerial 시리얼모니터 통신 속도 설정 및 시작 합니다.
  // while (!Serial) {
  //   ;
  // }
  BTSerial.begin(9600);                       // 블루투스 통신 데이터 속도 설정 합니다. (모듈의 설졍을 변경하면 똑같이 맞추어줘야합니다.)
  while (!BTSerial) {
    ;
  }

  // startup(Serial);
  startup(BTSerial);

  cmdString.reserve(64);  // 명령 버퍼 힙 파편화 방지 (수신 상한 64자와 동일)

  // initialize digital pin LED_BUILTIN(D13) as an output.
  pinMode(LED_BUILTIN, OUTPUT);
  
  // EEPROM 초기 설정값 읽어서 초기화
  //EEPROM_writeInt(R_GT_addr, 10); // 공장 초기값 설정, 필요시에만 실행
  //EEPROM_writeInt(R_RT_addr, 12); // 공장 초기값 설정, 필요시에만 실행
  //EEPROM_writeInt(L_GT_addr, 10); // 공장 초기값 설정, 필요시에만 실행
  //EEPROM_writeInt(L_RT_addr, 12); // 공장 초기값 설정, 필요시에만 실행
  //EEPROM_writeInt(RR_GT_addr, 10); // 공장 초기값 설정, 필요시에만 실행
  //EEPROM_writeInt(RR_RT_addr, 12); // 공장 초기값 설정, 필요시에만 실행
  //EEPROM_writeInt(LL_GT_addr, 10); // 공장 초기값 설정, 필요시에만 실행
  //EEPROM_writeInt(LL_RT_addr, 12); // 공장 초기값 설정, 필요시에만 실행
  R_Gap_Time = S2msR(EEPROM_readInt(R_GT_addr));
  R_Run_Time = S2ms(EEPROM_readInt(R_RT_addr));
  L_Gap_Time = S2msL(EEPROM_readInt(L_GT_addr));
  L_Run_Time = S2ms(EEPROM_readInt(L_RT_addr));
  RR_Gap_Time = S2msR(EEPROM_readInt(RR_GT_addr));
  RR_Run_Time = S2ms(EEPROM_readInt(RR_RT_addr));
  LL_Gap_Time = S2msL(EEPROM_readInt(LL_GT_addr));
  LL_Run_Time = S2ms(EEPROM_readInt(LL_RT_addr));
  
  // Set pins as outputs
  pinMode(motorPin1, OUTPUT); // 9
  pinMode(motorPin2, OUTPUT); // 10
  pinMode(motorPin3, OUTPUT); // 5
  pinMode(motorPin4, OUTPUT); // 6

  wdt_disable(); // ← delay 전에 워치독 먼저 끄기
  delay(10000);  // reset 명령 후, 터미널에서 끊어진 블루투스를 연결하는 지연 시간을 준다.
  wdt_enable(WDTO_4S);

  print_all();  // 타이머 설정 저장 정보를 모두 출력한다.

  // timed actions setup
  refresh_all_timers();
}

// the loop function runs over and over again forever
void loop() {
  unsigned long curr_time = millis();

  if (prev_time > curr_time) { // millis() 롤오버(≈49.7일마다 최대치에서 0 으로 되돌아가는 시점)
    Diff_Time = trunc_less_24h(trunc_less_24h(4294967295L) + Diff_Time + 1);
    prev_time = 0L;
    // 여기서 refresh_all_timers() 를 호출하지 않는다: SimpleTimer 는 (millis()-_previous)를
    // 부호 없는 32비트 뺄셈으로 재므로 롤오버를 스스로 정확히 넘긴다. 리셋하면 진행 중이던
    // GAP 카운트다운이 버려져 ~49.7일에 1회 토출이 최대 1주기 밀렸음(2026.7.24 제거).
  }
  else {
    prev_time = curr_time;
  }
  set_HHMMSSMI(trunc_less_24h(curr_time) + Diff_Time);  // 현재 시간을 시분초로 분해한다.
  // 여기서부터, tHH,tMM,tSS,tMI 값을 비교해서 주기적인 작업을 할 수 있다.
  
  if (!firstRunFlag && !secondRunFlag && !ffirstRunFlag && !ssecondRunFlag) {
    if (firstTimer.isReady() && firstFlag) {                  // 1. Check is ready a first timer
      firstFlag = false;
      R_Motor_Task_On(EEPROM_readInt(R_RT_addr));
      firstRunTimer.reset();
      firstRunFlag = true;
    }
    else if (secondTimer.isReady() && secondFlag) { // 3. Check is ready a second timer
      secondFlag = false;
      L_Motor_Task_On(EEPROM_readInt(L_RT_addr));
      secondRunTimer.reset();
      secondRunFlag = true;
    }
    else if (ffirstTimer.isReady() && ffirstFlag) {                  // 11. Check is ready a ffirst timer
      ffirstFlag = false;
      RR_Motor_Task_On(EEPROM_readInt(RR_RT_addr));
      ffirstRunTimer.reset();
      ffirstRunFlag = true;
    }
    else if (ssecondTimer.isReady() && ssecondFlag) { // 33. Check is ready a ssecond timer
      ssecondFlag = false;
      LL_Motor_Task_On(EEPROM_readInt(LL_RT_addr));
      ssecondRunTimer.reset();
      ssecondRunFlag = true;
    }    
  }
  if (firstRunTimer.isReady() && firstRunFlag) { // 2. 오른쪽 동작 시간 완료 판단
    firstRunFlag = false;
    R_Motor_Task_Off();
    firstTimer.reset();
    firstFlag = true;
  }
  if (secondRunTimer.isReady() && secondRunFlag) { // 4. 왼쪽 동작 시간 완료 판단
    secondRunFlag = false;
    L_Motor_Task_Off();
    secondTimer.reset();
    secondFlag = true;
  }
  if (ffirstRunTimer.isReady() && ffirstRunFlag) { // 22. RR 동작 시간 완료 판단
    ffirstRunFlag = false;
    RR_Motor_Task_Off();
    ffirstTimer.reset();
    ffirstFlag = true;
  }
  if (ssecondRunTimer.isReady() && ssecondRunFlag) { // 44. LL 동작 시간 완료 판단
    ssecondRunFlag = false;
    LL_Motor_Task_Off();
    ssecondTimer.reset();
    ssecondFlag = true;
  }

// === 테스트 동작 타이머 처리

  if (firstTestTimer.isReady() && firstTestFlag) { // *. 오른쪽 테스트 시간 완료 판단
    firstTestFlag = false;
    R_Motor_Task_Off();
    lazy_timer(firstTestTimer);
  }
  if (secondTestTimer.isReady() && secondTestFlag) { // *. 왼쪽 테스트 시간 완료 판단
    secondTestFlag = false;
    L_Motor_Task_Off();
    lazy_timer(secondTestTimer);
  }
  if (ffirstTestTimer.isReady() && ffirstTestFlag) { // *. RR 테스트 시간 완료 판단
    ffirstTestFlag = false;
    RR_Motor_Task_Off();
    lazy_timer(ffirstTestTimer);
  }
  if (ssecondTestTimer.isReady() && ssecondTestFlag) { // *. LL 테스트 시간 완료 판단
    ssecondTestFlag = false;
    LL_Motor_Task_Off();
    lazy_timer(ssecondTestTimer);
  }


  user_interaction();

  wdt_reset();
}


void user_interaction() {
  while(BTSerial.available())  //BTSerial 값이 있으면
  {
    char cmdChar = (char)BTSerial.read();  //BTSerial int형식의 값을 char형식으로 변환
    cmdString += cmdChar;   //수신되는 문자열을 cmdString에 모두 붙임
    if (cmdChar == '\n') break;   // 한 줄 완성 → 아래에서 즉시 처리 (남은 수신분은 다음 loop 에서)
    if (cmdString.length() > 64) {  // 개행 없이 계속 길어지면(노이즈 유입) 버린다
      cmdString = "";
      BTSerial.println(F("too long, discarded"));
    }
  }
  // 문자 사이 delay(5) 대기는 제거: 데이터가 연속 유입되면 이 함수가 loop()를 독점해
  // 밸브 Off 판정이 멈추는 문제가 있었다. 대신 개행이 올 때까지 cmdString에 누적한다.

  // 개행(\n)으로 끝나는 완전한 명령만 처리한다. BT 순단으로 앞부분만 도착한 명령이
  // 잘못된 값으로 처리/저장되는 것을 방지 (예: "rrt 500"이 "rrt 50"까지만 와서 50 저장)
  if(cmdString.length() > 0 && cmdString.charAt(cmdString.length()-1) == '\n')
  {
    BTSerial.print(F("수신 명령문 : ")); BTSerial.print(cmdString); // cmdString에 \n 포함
 
    if(cmdString=="on\n")  //myString 값이 'on' 이라면
    {
      digitalWrite(LED_BUILTIN, HIGH); //보드 내장 LED ON
    }
    if(cmdString=="off\n") {
      digitalWrite(LED_BUILTIN, LOW);  //보드 내장 LED OFF
    }
    if(cmdString.substring(0,3)=="rgt") {
      unsigned int data = clamp_to_uint16(cmdString.substring(3).toInt());
      EEPROM_writeInt(R_GT_addr, data);
      R_Gap_Time = S2msR(data);
      BTSerial.print(F("오른쪽 휴지(GAP) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(R_GT_addr));     // 블루투스에 데이터 전송 합니다.
    }
    // "rrtest"도 앞 3글자가 "rrt"라 이 핸들러에 걸리므로 제외한다 (안 하면 rrtest 때마다 R RUN 시간이 0으로 덮어써짐)
    if(cmdString.substring(0,3)=="rrt" && cmdString.substring(0,6)!="rrtest") {
      unsigned int data = clamp_to_uint16(cmdString.substring(3).toInt());
      EEPROM_writeInt(R_RT_addr, data);
      R_Run_Time = S2ms(data);
      BTSerial.print(F("오른쪽 동작(RUN) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(R_RT_addr));     // 블루투스에 데이터 전송 합니다.
    }
    if(cmdString.substring(0,3)=="lgt") {
      unsigned int data = clamp_to_uint16(cmdString.substring(3).toInt());
      EEPROM_writeInt(L_GT_addr, data);
      L_Gap_Time = S2msL(data);
      BTSerial.print(F("왼쪽 휴지(GAP) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(L_GT_addr));     // 블루투스에 데이터 전송 합니다.
    }
    if(cmdString.substring(0,3)=="lrt") {
      unsigned int data = clamp_to_uint16(cmdString.substring(3).toInt());
      EEPROM_writeInt(L_RT_addr, data);
      L_Run_Time = S2ms(data);
      BTSerial.print(F("왼쪽 동작(RUN) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(L_RT_addr));     // 블루투스에 데이터 전송 합니다.
    }

// ===

    if(cmdString.substring(0,4)=="rrgt") {
      unsigned int data = clamp_to_uint16(cmdString.substring(4).toInt());
      EEPROM_writeInt(RR_GT_addr, data);
      RR_Gap_Time = S2msR(data);
      BTSerial.print(F("RR 휴지(GAP) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(RR_GT_addr));     // 블루투스에 데이터 전송 합니다.
    }
    if(cmdString.substring(0,4)=="rrrt") {
      unsigned int data = clamp_to_uint16(cmdString.substring(4).toInt());
      EEPROM_writeInt(RR_RT_addr, data);
      RR_Run_Time = S2ms(data);
      BTSerial.print(F("RR 동작(RUN) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(RR_RT_addr));     // 블루투스에 데이터 전송 합니다.
    }
    if(cmdString.substring(0,4)=="llgt") {
      unsigned int data = clamp_to_uint16(cmdString.substring(4).toInt());
      EEPROM_writeInt(LL_GT_addr, data);
      LL_Gap_Time = S2msL(data);
      BTSerial.print(F("LL 휴지(GAP) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(LL_GT_addr));     // 블루투스에 데이터 전송 합니다.
    }
    if(cmdString.substring(0,4)=="llrt") {
      unsigned int data = clamp_to_uint16(cmdString.substring(4).toInt());
      EEPROM_writeInt(LL_RT_addr, data);
      LL_Run_Time = S2ms(data);
      BTSerial.print(F("LL 동작(RUN) 시간 설정 값: "));
      BTSerial.println(EEPROM_readInt(LL_RT_addr));     // 블루투스에 데이터 전송 합니다.
    }

    if(cmdString=="ls\n") {
      print_all();
    }
    
    if(cmdString.substring(0,5)=="rtest") {  // 오른쪽 구동 테스트
      long data = cmdString.substring(5).toInt();  // toInt()는 long 반환, int(16bit)에 담으면 100000 등에서 오버플로우됨
      if (data < 0) data = 0;
      unsigned long msdata = S2ms(data);
      firstTestTimer.setInterval(msdata);
      R_Motor_Task_On(msdata);
      firstTestTimer.reset();
      firstTestFlag = true;
    }
    if(cmdString.substring(0,5)=="ltest") {  // 왼쪽 구동 테스트
      long data = cmdString.substring(5).toInt();  // toInt()는 long 반환, int(16bit)에 담으면 100000 등에서 오버플로우됨
      if (data < 0) data = 0;
      unsigned long msdata = S2ms(data);
      secondTestTimer.setInterval(msdata);
      L_Motor_Task_On(msdata);
      secondTestTimer.reset();
      secondTestFlag = true;
    }
    if(cmdString.substring(0,6)=="rrtest") {  // RR 구동 테스트
      long data = cmdString.substring(6).toInt();  // toInt()는 long 반환, int(16bit)에 담으면 100000 등에서 오버플로우됨
      if (data < 0) data = 0;
      unsigned long msdata = S2ms(data);
      ffirstTestTimer.setInterval(msdata);
      RR_Motor_Task_On(msdata);
      ffirstTestTimer.reset();
      ffirstTestFlag = true;
    }
    if(cmdString.substring(0,6)=="lltest") {  // LL 구동 테스트
      long data = cmdString.substring(6).toInt();  // toInt()는 long 반환, int(16bit)에 담으면 100000 등에서 오버플로우됨
      if (data < 0) data = 0;
      unsigned long msdata = S2ms(data);
      ssecondTestTimer.setInterval(msdata);
      LL_Motor_Task_On(msdata);
      ssecondTestTimer.reset();
      ssecondTestFlag = true;
    }
    
    // reset 명령이 들어오면, 무한 루프로 멈춰있으면, 와치독 실행 중지로 자동 리셋 되게 함.
    // 리셋이 되면, 보드가 초기화 되면서 새로운 설정값으로 모터 동작이 시작된다.    
    if(cmdString=="reset\n") {
      // do nothing while true: infinity loop to hang MCU
      while (true) {
        BTSerial.println(F("다시 시작 중입니다."));
        delay(1000);
      }
    }

    // 기계시간과 현재시간의 차이를 저장합니다.
    if(cmdString.substring(0,8)=="set time") {  // hh:mm:ss 값을 받아 설정합니다.
      long hh, mm, ss;  // toInt()는 long 반환, int(16bit)로 받으면 잘못된 입력에서 오버플로우됨
      String inString = cmdString.substring(8);

      int index1 = inString.indexOf(':');
      int index2 = inString.indexOf(':',index1+1);
      int index3 = inString.length();

      hh = inString.substring(0, index1).toInt();
      mm = inString.substring(index1+1,index2).toInt();
      ss = inString.substring(index2+1,index3).toInt();

      // 정상 범위로 클램프 (오타/기형 입력 방어)
      if (hh < 0) hh = 0; if (hh > 23) hh = 23;
      if (mm < 0) mm = 0; if (mm > 59) mm = 59;
      if (ss < 0) ss = 0; if (ss > 59) ss = 59;

      Diff_Time = ((long)24*3600+0*60+0)*1000 + (hh*3600+mm*60+ss)*1000 - trunc_less_24h(millis());
    }
    
    // 설정된 시간을 출력 합니다.
    if(cmdString=="time\n") {  // hh:mm:ss 시간을 출력 합니다.
      BTSerial.print(F("Current time :"));
      print_TS();
      BTSerial.println();
    }
    
    // 타이머 리셋시 변화 디버깅
    if(cmdString=="debug reset\n") {  // 타이머 리셋 상황을 재현합니다.
      extern volatile unsigned long timer0_millis; //타이머변수

      noInterrupts();  // 4바이트 변수라 쓰기 도중 Timer0 ISR과 겹치면 값이 깨진다
      timer0_millis = 4294967295UL - 120000UL; // 리셋 되기전 120초로 타이머 설정
      interrupts();
      BTSerial.println(millis());
      BTSerial.println(Diff_Time);
      print_TS();
      print_TS(false);
      BTSerial.println();
    }
    
    // 타이머 재설정
    if(cmdString=="refresh all\n") {  // 모든 타이머를 초기화하가 재시작 합니다.
      refresh_all_timers();
    }

    // 타이머 변화 디버깅
    if(cmdString=="debug\n") {  // 타이머 상황을 표시합니다.
      BTSerial.println(millis());
      BTSerial.println(Diff_Time);
      BTSerial.print(tHH); BTSerial.print(F(":"));
      BTSerial.print(tMM); BTSerial.print(F(":"));
      BTSerial.print(tSS); BTSerial.print(F("."));
      BTSerial.print(tMI);
      print_TS(false);
      BTSerial.println();
    }

    // help 정보 표시
    if(cmdString=="help\n") {
      BTSerial.println(F("Command Line: help, debug, refresh all, debug reset, time, set time hh:mm:ss, reset, l'ltest <ms>, r'rtest <ms>, ls, l'lrt <ms>, l'lgt <sec>, r'rrt <ms>, r'rgt <sec>, off, on"));
    }

    cmdString="";  //cmdString 변수값 초기화
  }
}
