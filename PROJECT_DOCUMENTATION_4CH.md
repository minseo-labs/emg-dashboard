# EMG Dashboard — 프로젝트 상세 문서

이 문서는 근전도(EMG) 실시간 시각화 대시보드의 개발 환경, 아키텍처, 핵심 로직, UI, 알고리즘을 정리한 것이다. **4채널/6채널** 모드를 지원한다. 기존 README·MODULES와 중복되는 부분은 요약하고, 상세 설명·설정·역할 분리는 이 문서를 기준으로 한다.

---

## 목차

1. [개발 환경 요구사항](#1-개발-환경-요구사항)
2. [아키텍처 개요](#2-아키텍처-개요)
3. [핵심 로직 상세](#3-핵심-로직-상세)
4. [UI 구성 요소](#4-ui-구성-요소)
5. [주요 기능 및 알고리즘](#5-주요-기능-및-알고리즘)
6. [개발 과정에서의 문제와 해결](#6-개발-과정에서의-문제와-해결)
7. [기타 참고 사항](#7-기타-참고-사항)
8. [설정값·상수 일람](#8-설정값상수-일람)
9. [용어 정의](#9-용어-정의)
10. [시그널·슬롯·이벤트](#10-시그널슬롯이벤트)
11. [RAW 링 버퍼·구간 분리](#11-raw-링-버퍼구간-분리)
12. [ChannelScaler·baseline 로직 상세](#12-channelscalerbaseline-로직-상세)
13. [Diagonal Vector 수식](#13-diagonal-vector-수식)
14. [CSV 로거 상세](#14-csv-로거-상세)
15. [에러 처리·종료](#15-에러-처리종료)
16. [데이터 범위가 다른 센서(동적 스케일 요약)](#16-데이터-범위가-다른-센서동적-스케일-요약)
17. [제한 사항·알려진 이슈](#17-제한-사항알려진-이슈)
18. [초기화·생성 순서 상세](#18-초기화생성-순서-상세)
19. [시리얼 수신·파싱 로직 상세](#19-시리얼-수신파싱-로직-상세)
20. [RAW Bar 모드 갭·표시 로직 상세](#20-raw-bar-모드-갭표시-로직-상세)
21. [기타 로직·동작 상세](#21-기타-로직동작-상세)

---

## 1. 개발 환경 요구사항

### 1.1 소프트웨어

| 구분 | 요구 사항 | 용도 |
|------|-----------|------|
| **언어** | Python 3.x | 전역 |
| **GUI** | PyQt6 | 메인 윈도우, 위젯(버튼·콤보박스·스핀박스·라디오·레이아웃), 스타일시트 |
| **그래프** | pyqtgraph | RAW 시계열, Diagonal Vector, PWR 막대 (PlotWidget, PlotCurveItem, BarGraphItem, ScatterPlotItem) |
| **수치** | NumPy | 버퍼·배열, min/max/clip, 스케일·비율 계산 |
| **시리얼** | pyserial | 포트 열기/읽기, 줄 단위 또는 바이트 단위 파싱 |
| **표준 라이브러리** | `sys`, `re`, `time`, `csv`, `os`, `datetime`, `collections.deque` | 앱 진입, 정규식 파싱, 타이밍, 로깅, deque 버퍼 |

- **실행**: 프로젝트 루트에서 `python main.py`. 시리얼 포트 선택 후 START로 수신 시작.

### 1.2 설치 및 실행 절차

```text
# 가상환경 생성 및 활성화 (Windows)
python -m venv .venv
.venv\Scripts\activate

# 의존성 설치
pip install PyQt6 pyqtgraph numpy pyserial

# 실행
python main.py
```

- macOS/Linux: `source .venv/bin/activate`. 프로젝트 루트에서 실행할 것.

### 1.3 하드웨어

- **시리얼 장치**: 4채널 EMG 센서(또는 4값을 한 줄로 전송하는 장치).
- **프로토콜**: 한 줄에 4개 실수(공백 등 구분), 줄 끝 `\n` (텍스트) 또는 문서화된 23바이트 이진 프레임(헤더 2 + 데이터 20 + 체크섬 1 XOR).

### 1.4 그래픽·렌더 설정

- **pyqtgraph 전역 옵션** (`dashboard_ui.__init__`):
  - `antialias=True`: 선·곡선 안티앨리어싱.
  - `useOpenGL=True`: GPU 가속으로 고속 렌더.
- **렌더 주기**: `FPS=30` → `QTimer` 간격 `1000/30` ms. `is_running`·`sample_count == 0`이면 그리기 생략.
- **RAW 플롯**: Y축 숫자 비표시(`showValues=False`), X축 단위 ms, ViewBox 범위·줌 제한 설정.

---

## 2. 아키텍처 개요

### 2.1 역할 분리 (MVC 유사)

| 역할 | 담당 모듈 | 설명 |
|------|-----------|------|
| **View + Controller** | `dashboard_ui.py` | 메인 윈도우, 패널·위젯 구성, 사용자 입력(START/STOP/포트/Window Size), 시그널 슬롯 연결, 타이머로 주기적 렌더 호출 |
| **Model (데이터·스케일)** | `emg_scale.py`, 버퍼(`raw_np_buf`, `height_buf`, `last_amp` 등) | 채널별 min/max/baseline, 공통 data_range, 스케일/비율 계산; RAW·AMP 데이터 보관 |
| **데이터 수집·파싱** | `serial_worker.py` | 시리얼 수신, 줄/프레임 파싱, 진폭 계산, 시그널로 UI에 전달 |
| **시각화** | `graph_render.py` | RAW 그래프(Line/Bar), Diagonal Vector, PWR 막대의 좌표·아이템 갱신 (모델은 건드리지 않고 win 참조만) |
| **설정** | `config.py` | 채널 수, FPS, 색상, 오프셋, 스케일 관련 상수 |
| **부가 I/O** | `logger.py` | CSV 로깅(버퍼 후 일괄 flush) |

- **진입점**: `main.py` — `QApplication` 생성, `EMGDashboard` 생성·표시, `app.exec()`.

### 2.2 핵심 클래스·객체

| 클래스/객체 | 파일 | 역할 |
|-------------|------|------|
| `EMGDashboard` | dashboard_ui | QMainWindow 서브클래스; 전체 UI, 버퍼, 타이머, 시리얼 워커·시그널 연결 |
| `SerialWorker` | serial_worker | QThread; 시리얼 루프, 파싱, 진폭 계산, `sig_sample`/`sig_status`/`sig_error` |
| `EMGScaleManager` | emg_scale | 채널별 `ChannelScaler` 리스트, `get_scaled_array`, `get_vector_intensity`, `_data_range_and_half_height()` |
| `ChannelScaler` | emg_scale | 채널당 min/max/baseline 유지, `update(raw_value)` |
| `CSVLogger` | logger | 세션당 CSV 파일, 버퍼·flush·close |

### 2.3 주요 위젯·아이템

| 위젯/아이템 | 소유 | 역할 |
|-------------|------|------|
| `raw_plot` | EMGDashboard | pyqtgraph PlotWidget; RAW 시계열(Line 또는 Bar) |
| `past_lines`, `raw_lines`, `bar_items`, `cursor_rects` | EMGDashboard | 채널별 라인·막대·커서(ScatterPlotItem) |
| `diag_plot`, `diag_lines` | EMGDashboard | Diagonal Vector 플롯 및 채널별 PlotCurveItem |
| `pwr_plot`, `bar_item` | EMGDashboard | PWR 막대 1개 BarGraphItem (4채널+AVG = 5개 막대) |
| `cb_port`, `btn_start`, `btn_stop`, `sp_nmult`, `lbl_status` | EMGDashboard | 설정 패널 컨트롤 |
| `rb_line`, `rb_fill` | EMGDashboard | RAW Line / Fill(Bar) 모드 선택 |

---

## 3. 핵심 로직 상세

### 3.1 파일별 주요 메서드·함수

| 파일 | 메서드/함수 | 역할 |
|------|-------------|------|
| **main.py** | `main` | `QApplication`, `EMGDashboard()`, `show()`, `exec()` |
| **dashboard_ui.py** | `__init__` | 버퍼·스케일러·UI 초기화, 워커·타이머·시그널 연결 |
| | `init_ui` | 좌/우 레이아웃, 설정·RAW·Diagonal·PWR 패널 배치 |
| | `build_settings_panel` | 포트, Refresh, START/STOP, Window Size, Channels(4ch/6ch), 상태 라벨 |
| | `build_raw_plot_panel` | RAW 플롯, Line/Fill 라디오, 채널별 라인·막대·커서 생성 |
| | `build_diag_panel` | Diagonal Vector 플롯, 가이드 라인, diag_lines 4개 |
| | `build_pwr_panel` | PWR PlotWidget, BarGraphItem(N_CH+1), CH0~CH(N-1)·AVG 틱 |
| | `render` | `graph_render.render(win)` 호출 |
| | `on_sample` | raw/amp 수신 시 버퍼·ptr·스케일러 갱신, CSV write_row |
| | `start_serial` | 버퍼 초기화, scale_manager.reset(), CSVLogger 생성(옵션), worker.configure·start, set_running_ui(True) |
| | `stop_serial` | worker.stop·wait, csv_logger.close, set_running_ui(False) |
| | `set_running_ui` | START/STOP/포트/Refresh/Window Size/Channels 활성·비활성 |
| **serial_worker.py** | `parse_line_4ch(line)` | 한 줄에서 숫자 추출, 마지막 N_CH개 float 반환 |
| | `compute_amp_from_samples(sample_buf)` | deque → 채널별 (max−min) amp 배열 반환 |
| | `run` | 시리얼 열기, `in_waiting` 읽기, 줄 단위 split, 파싱·AMP 계산·sig_sample |
| | `update_params(n_mult)` | n_samples = BASE_SAMPLES * n_mult, sample_buf 재생성 |
| **graph_render.py** | `render(win)` | is_running·sample_count 검사 후 update_raw_graph, update_diag_vector, update_power_info |
| | `update_raw_graph(win, ...)` | 채널별 height_buf 계산, Line/Bar 분기, get_scaled_array로 Y, 커서 위치 |
| | `update_diag_vector(win)` | 채널별 최근 100샘플, get_vector_intensity·방향 벡터, diag_lines setData |
| | `update_power_info(win)` | get_vector_intensity → 비율 0~100%, bar_item 높이·AVG |
| **emg_scale.py** | `_data_range_and_half_height(ch_idx)` | global_min/max → data_range, allowed_half_height 반환 |
| | `get_scaled_array(ch_idx, raw_array)` | effective_raw(0→RAW_ZERO_REF), 비율·clip, base_offset + 비율*반높이 |
| | `get_vector_intensity(ch_idx, amp_value)` | dynamic_half_range, 0~1 강도 반환 |
| **logger.py** | `write_row(raw_vals, amp_vals, timestamp)` | 버퍼에 추가, buffer_size 도달 시 flush |
| | `flush`, `close` | writerows·file.flush, 파일 닫기 |

### 3.2 동작 흐름 (요약)

1. **기동**: main → EMGDashboard 생성 → init_ui → 타이머 start, refresh_ports.
2. **START**: start_serial → 버퍼 초기화, worker.configure(port, 115200, n_mult), worker.start() → run() 진입.
3. **수신 루프**(SerialWorker): in_waiting 읽기 → 줄 단위 split → parse_line_4ch → sample_buf.append, 주기적 compute_amp_from_samples → sig_sample.emit(raw_vals, last_amp).
4. **UI 수신**: on_sample → raw_np_buf[:, ptr] = raw_vals, scale_manager.scalers[i].update(raw_vals[i]), ptr 증가, CSV write_row.
5. **렌더 루프**: QTimer.timeout → render(win) → update_raw_graph, update_diag_vector, update_power_info (is_running·sample_count 확인 후).
6. **STOP**: stop_serial → worker.stop(), wait, csv_logger.close, set_running_ui(False).

### 3.3 스레드 안정성

- **시리얼·파싱·진폭**: 모두 `SerialWorker`(QThread) 내부에서 수행. UI 스레드에서는 시그널로만 데이터 수신.
- **버퍼·스케일러 갱신**: `on_sample`이 메인 스레드에서 호출되므로, `raw_np_buf`·`ptr`·`scale_manager` 수정은 메인 스레드에서만 발생.
- **렌더**: 타이머가 메인 스레드에서 `render`를 호출하고, `win`의 버퍼는 읽기만 하므로 레이스 없음. 시리얼 스레드는 버퍼를 쓰지 않음(시그널로 값만 전달).

### 3.4 수신 패킷 구조 (문서화된 프로토콜)

- **텍스트**: 한 줄에 4개 실수, `\n` 종료. 예: `123 456 789 012\n`.
- **이진 (23 bytes)**:
  - 헤더 2 bytes (프레임 식별)
  - 데이터 20 bytes (5×4 등; 4ch 시 4×4=16 사용 등으로 확장 가능)
  - 체크섬 1 byte (XOR, 헤더+데이터)
- 현재 구현은 **텍스트** 파싱(`parse_line_4ch`)만 사용. 이진은 프로토콜에 맞게 별도 파서 구현 시 적용.
- **parse_line_4ch**: `re.findall(r'[-+]?\d*\.\d+|\d+', line)`로 숫자 추출 후 마지막 4개를 float 리스트로 반환. 4개 미만이면 None.

### 3.5 설정·실행 순서

1. config 상수 로드 (import 시).
2. EMGDashboard 생성: scale_manager(N_CH), raw_np_buf, x_axis, height_buf, cursor_rects, last_raw/last_amp, worker, 타이머.
3. init_ui: 패널 생성 순서 — settings → raw → diag → pwr; 좌측(settings, diag), 우측(raw, pwr).
4. START 시: raw_np_buf.fill(0), height_buf.fill(1.5), ptr=0, is_buf_full=False, sample_count=0, start_time_ref, CSVLogger(옵션), worker.configure·start.

---

## 4. UI 구성 요소

### 4.1 디자인 시스템

- **배경**: `#121826` (COLOR_BG). 카드 테두리: `#2a3550` (COLOR_CARD_BORDER).
- **카드**: QFrame, 배경 #121826, 테두리 1px solid #2a3550, border-radius 12px. 내부 여백 15px.
- **버튼**: 배경 #2a3550, hover #3d4d75, disabled #1c2538, border-radius 6px.
- **콤보/스핀**: 배경 #1c2538, 테두리 #3d4d75.
- **채널 색상**: CH_COLORS — 빨강·주황·초록·파랑 (`#ff4757`, `#ff9f43`, `#2ed573`, `#1e90ff`). AVG 막대: SUM_BAR_COLOR `#9fb3ff`.
- **상태**: CONNECTED `#2ed573`, DISCONNECTED `#ff6b6b`. 카드 제목·강조: `#9fb3ff`, 라벨 `#e6e9f2`.

### 4.2 레이아웃 구조

- **최상위**: QMainWindow → central QWidget → QHBoxLayout(main_layout).
- **좌측 (left_container, QVBoxLayout)**: panel_settings(0), panel_diag(1).
- **우측 (right_container, QVBoxLayout)**: panel_raw(5), panel_pwr(3).
- **main_layout**: left_container(1), right_container(2). Margins 15, spacing 15.
- **RAW 패널**: 카드 내부 — 제목+Line/Fill 라디오(header_layout), raw_plot(1).
- **PWR 패널**: 카드 내부 — pwr_plot(1), BarGraphItem 5개(CH0~CH3, AVG).

---

## 5. 주요 기능 및 알고리즘

### 5.1 데이터 구조

| 이름 | 타입 | 크기/형태 | 용도 |
|------|------|-----------|------|
| raw_np_buf | np.ndarray | (N_CH, max_display), float | RAW 시계열 링 버퍼. ptr로 기록 위치 관리 |
| x_axis | np.ndarray | (max_display,) | 0 ~ PLOT_SEC*1000 ms 균등 분할, 한 번 생성 후 재사용 |
| height_buf | np.ndarray | (N_CH, max_display) | Bar 모드 구간별 막대 높이 캐시 |
| last_raw | list | 길이 4 | 최근 수신 raw 4개 |
| last_amp | np.ndarray | (N_CH,) | 최근 진폭(채널별 max−min) |
| sample_buf | deque | maxlen=n_samples | SerialWorker 내부, 최근 N샘플 (진폭 계산용) |
| CSVLogger.buffer | list | 최대 buffer_size(600) | 로그 행 누적 후 writerows 일괄 기록 |

### 5.2 색상·채널

- CH_COLORS: 4채널 순서대로 적용 (RAW 라인·막대·Diagonal·PWR). AVG는 SUM_BAR_COLOR.
- cursor_rects: 흰색(#ffffff) 사각형 ScatterPlotItem.

### 5.3 주요 기능 요약

- **RAW Line**: 링 버퍼 past/current 구간 분리, get_scaled_array로 Y 계산, 0 근처는 RAW_ZERO_REF(100) 위치에 표시.
- **RAW Bar**: step=30 구간별 max−min → data_range/2 기준 비율(Line과 동일) → height_buf, BarGraphItem setOpts.
- **Diagonal Vector**: 4방향 (-0.707,-0.707), (-0.707,0.707), (0.707,0.707), (0.707,-0.707). 최근 100샘플, get_vector_intensity로 길이·펜 두께·알파.
- **PWR**: get_vector_intensity → 0~100% 높이, AVG는 4채널 비율 평균.

### 5.4 최적화

- **진폭 계산**: 매 샘플이 아니라 calc_interval(5)마다, sample_buf 길이 ≥ n_samples일 때만 compute_amp_from_samples 호출.
- **Bar**: 구간(step) 단위로 height_buf 채운 뒤 sampled_indices로 setOpts 한 번에 전달.
- **렌더**: is_running·sample_count == 0이면 return. FPS 30으로 주기 제한.
- **CSV**: 버퍼가 buffer_size만큼 차면 writerows+flush. 시리얼은 in_waiting만큼 한 번에 read, 루프 끝 sleep(0.001).

### 5.5 스케일링 기법 (상세)

- **공통 data_range (RAW Line)**  
  - `_data_range_and_half_height`: global_min = min(모든 scaler.current_min), global_max = max(모든 scaler.current_max). data_range = max(global_max − global_min, 20).  
  - 같은 raw 변동폭이면 모든 채널에서 같은 세로 폭. baseline만 채널별.
- **get_scaled_array**  
  - effective_raw: abs(raw_array) ≤ RAW_ZERO_THRESHOLD(1.0)이면 RAW_ZERO_REF(100)으로 치환.  
  - ratios = (effective_raw − baseline) / (data_range/2), clip −1~1.  
  - Y = base_offset + ratios * allowed_half_height. base_offset = (N_CH−1−ch_idx)*CH_OFFSET + CH_OFFSET/2.
- **get_vector_intensity (Diagonal·PWR)**  
  - dynamic_half_range = current_max − baseline (최소 20).  
  - intensity = (amp_value / dynamic_half_range) * gains[ch_idx] * 1.3, clip 0~1.  
  - 채널별 “자기 관측 범위 대비” 비율.
- **RAW Bar**: 구간별 (ch_max−ch_min) / (data_range/2) 비율로 막대 높이. Line과 동일한 공통 data_range 사용 → 채널 간 비교 일치.

---

## 6. 개발 과정에서의 문제와 해결

### 6.1 조이스틱 → 대각선 벡터(Diagonal Vector) 명칭 통일

- **문제**: 코드·문서에 “joystick”이 남아 있어, 실제 기능(대각선 방향 벡터)과 불일치.
- **해결**:  
  - emg_scale: `get_joystick_intensity` → `get_vector_intensity`.  
  - dashboard_ui: `build_joystick_panel` → `build_diag_panel`, `panel_joy` → `panel_diag`, `joy_plot` → `diag_plot`, `plot_limit` → `diag_plot_limit`, `diag_lines`.  
  - graph_render: `update_joystick_vector` → `update_diag_vector`, `joy_raw` → `diag_raw`, `win.diag_plot_limit`.  
  - MODULES/README: “조이스틱” → “대각선 벡터” 문구 통일.

### 6.2 PWR 바를 절대값이 아닌 비율로 표시

- **문제**: PWR 막대가 last_amp를 0~100 clamp한 절대값이라, 채널 간 “채워진 정도” 비교가 어려움.
- **해결**: update_power_info에서 채널별 get_vector_intensity(0~1)를 구한 뒤 ×100으로 막대 높이(0~100%) 사용. AVG는 4채널 비율의 평균. 라벨에 비율(%) 표시 후, 하단 라벨 자체를 제거(6.5 참고).

### 6.3 MODULES.md 가독성·역할 분리

- **문제**: MODULES.md가 너무 길고, README와 “프로젝트 구조 표” 등이 겹침.
- **해결**:  
  - MODULES에 목차 추가, 헤딩 계층 ##/### 통일, “조이스틱” 문구 제거.  
  - README는 “소개·설치·실행·프로토콜·문서 링크”만 두고, “프로젝트 구조 표”는 MODULES로만.  
  - MODULES 상단에 “레포 소개·설치·실행은 README 참고” 안내 추가.

### 6.4 시리얼 프로토콜 문서화

- **문제**: 실제 프로토콜(23 bytes: 헤더 2, 데이터 20, 체크섬 1 XOR)이 문서에 없음.
- **해결**: README와 MODULES에 “시리얼 프로토콜” 섹션 추가. 표로 구간·길이·설명 정리. MODULES의 serial_worker 설명에 23바이트 프레임 및 링크 추가.

### 6.5 그래프 하단 RAW/AMP 라벨 제거

- **문제**: RAW 그래프 하단 “RAW: [0,0,0,0]”, PWR 하단 “AMP: ... AVG=...” 라벨이 불필요하게 차지.
- **해결**: dashboard_ui에서 lbl_rawnums·lbl_pwr 생성 및 레이아웃 추가 제거. graph_render.update_power_info에서 해당 setText 호출 제거.

### 6.6 신호 없음(0)일 때 RAW Line 위치를 “100”으로 통일

- **문제**: raw=0일 때 채널마다 baseline이 달라 세로 위치가 제각각.
- **해결**: config에 RAW_ZERO_REF=100, RAW_ZERO_THRESHOLD=1.0 추가. get_scaled_array에서 abs(raw_array) ≤ 1.0인 구간을 100으로 치환한 effective_raw로 비율 계산 → “각 오프셋에서 100 위치”에 플롯.

### 6.7 4ch / 6ch 모드 전환 가능 여부

- **질문**: 4채널·6채널을 모드로 전환해 RAW/Vector/PWR을 4개·6개로 바꿀 수 있는지.  
- **결론**: 가능. 헤더에 4/6을 두고, 헤더 값에 따라 데이터 길이·파싱·UI 채널 수(N)를 결정하면 됨. 4채널 전용 센서와 6채널 전용 센서가 완전히 달라도, “한 프레임에 오는 값 개수”만 맞추면 동일 구조로 지원 가능.

### 6.8 .gitignore 추가

- **문제**: venv, __pycache__, data/*.csv 등이 버전 관리에 포함될 수 있음.
- **해결**: .gitignore에 .venv/, __pycache__/, *.pyc, data/*.csv, *.log, .env, .idea/, .vscode/, .DS_Store 등 추가.

---

## 7. 기타 참고 사항

### 7.1 문서 간 역할

- **README.md**: 프로젝트 소개, 요구사항, 설치·실행, 시리얼 프로토콜 요약, MODULES 링크.
- **MODULES.md**: 구조·흐름·동적 스케일링·모듈 역할·코드 흐름 상세. 실행 방법은 README 참고.
- **본 문서(PROJECT_DOCUMENTATION_4CH.md)**: 개발 환경, 아키텍처, 핵심 로직, UI, 알고리즘을 한곳에 정리. 4ch/6ch 모드 지원.

### 7.2 채널 모드 (4ch/6ch)

- SETTINGS 패널의 **Channels** 콤보박스로 4ch/6ch 선택. 연결 해제 후에만 변경 가능.
- 변경 시 `reinit_channel_mode()`로 버퍼·플롯·스케일러·SerialWorker를 N_CH 기준으로 재생성.
- RAW: N개 라인·막대·커서 생성. Y 범위 N*CH_OFFSET. (이미 구현됨)
- Diagonal Vector: 방향을 360°/N 간격으로 계산(4→90°, 6→60°). diag_lines N개.
- PWR: BarGraphItem N+1개, ticks CH0~CH(N-1), AVG. CH_COLORS 6개 확장.
- serial_worker: N에 따라 파싱 개수·프레임 길이 분기.

### 7.3 상수 변경 시 주로 수정하는 파일

- 채널 수·색·오프셋·FPS·플롯 길이: `config.py`.
- 파싱·채널 수: `serial_worker.py` (parse_line_4ch, compute_amp_from_samples, N_CH).
- 스케일 공식: `emg_scale.py`.  
- RAW/Vector/PWR 시각 요소: `graph_render.py`, `dashboard_ui.py` (build_*_panel).

---

## 8. 설정값·상수 일람

| 구분 | 이름 | 값 | 위치 | 설명 |
|------|------|-----|------|------|
| **공통** | N_CH, CH_MODE | 4 | config | 채널 수 (4ch/6ch 전환 가능) |
| | FPS | 30 | config | 렌더 주기(Hz), 타이머 간격 = 1000/FPS ms |
| | PLOT_SEC | 5.0 | config | RAW X축 표시 구간(초) |
| | max_display | 5000 | dashboard_ui | RAW 링 버퍼·x_axis 샘플 수 |
| **시리얼/신호** | BASE_SAMPLES | 5 | config | 진폭 윈도우 기본 샘플 수 |
| | N_MULT_DEFAULT | 10 | config | n_samples = BASE_SAMPLES * n_mult |
| | calc_interval | 5 | serial_worker | 몇 샘플마다 진폭 재계산 |
| **RAW 스케일** | RAW_Y_MIN_INIT, RAW_Y_MAX_INIT | 55, 100 | config | ChannelScaler 초기 min/max |
| | CH_OFFSET | 100 | config | 채널 밴드 세로 간격(px) |
| | RAW_ZERO_REF | 100 | config | 신호 없음일 때 표시할 raw 기준값 |
| | RAW_ZERO_THRESHOLD | 1.0 | config | 이 값 이하면 "신호 없음"으로 치환 |
| | safe_margin_factor | 0.85 | emg_scale | allowed_half_height 계산 |
| **ChannelScaler** | baseline_alpha | 0.05 | emg_scale | baseline 스무딩 계수 |
| | decay_alpha | 0.00001 | emg_scale | min/max 감쇠 계수 |
| **RAW Bar** | step | 30 | graph_render | 구간 크기, Bar 샘플 간격 |
| | gap_range | 5 | graph_render | Bar 갭 구간 수 |
| | NO_SIGNAL_VARIATION_RAW | 1.0 | graph_render | 구간 변동폭 < 이 값이면 최소 높이 |
| | LINE_HEIGHT_PX | 1.5 | graph_render | 신호 없음 구간 막대 최소 높이 |
| **Diagonal** | DATA_LEN | 100 | graph_render | 채널당 최근 100샘플 |
| | diag_plot_limit | 50 | dashboard_ui | diag_plot X/Y 범위 ±50 |
| | boost_gain | 1.3 | emg_scale | get_vector_intensity 부스트 |
| **CSV** | buffer_size | 500 | dashboard_ui | CSVLogger 생성 시 전달 |
| | directory | "data" | logger | CSV 기본 저장 폴더 |
| **Window Size** | sp_nmult | 1 ~ 100 (SpinBox setRange) | dashboard_ui | START 시 worker.configure(port, 115200, n_mult)에 전달 |

---

## 9. 용어 정의

| 용어 | 설명 |
|------|------|
| **RAW** | 시리얼에서 수신한 원시 값. 채널당 하나의 스칼라 per 샘플. |
| **AMP (진폭)** | 최근 N샘플 구간에서 채널별 (max − min). |
| **baseline** | ChannelScaler의 "중앙" 추정값. (min+max)/2 방향으로 스무딩. |
| **data_range** | 전 채널 공통. global_max − global_min (최소 20). |
| **allowed_half_height** | 채널 밴드 중심에서 위/아래로 쓸 수 있는 반높이(px). |
| **dynamic_half_range** | 채널별 (current_max − baseline), 최소 20. |
| **effective_raw** | \|raw\| ≤ RAW_ZERO_THRESHOLD이면 100으로 치환한 배열. |
| **ptr** | raw_np_buf에 다음으로 쓸 인덱스. 0 ~ max_display−1 순환. |
| **is_buf_full** | 링 버퍼가 한 바퀴 이상 채워졌는지. |
| **n_samples** | 진폭 계산에 쓰는 샘플 수. BASE_SAMPLES * n_mult. |

---

## 10. 시그널·슬롯·이벤트

| 시그널/이벤트 | 소스 | 슬롯/동작 |
|---------------|------|-----------|
| sig_sample | SerialWorker | on_sample → 버퍼·ptr·스케일러·CSV 갱신 |
| sig_status | SerialWorker | set_status → lbl_status 텍스트·색상 |
| sig_error | SerialWorker | on_error → QMessageBox.critical, stop_serial |
| QTimer.timeout | QTimer | render → graph_render.render(win) |
| btn_start.clicked | - | start_serial |
| btn_stop.clicked | - | stop_serial |
| btn_refresh.clicked | - | refresh_ports |
| closeEvent | QMainWindow | stop_serial 후 event.accept() |

---

## 11. RAW 링 버퍼·구간 분리

- **버퍼**: raw_np_buf (N_CH, max_display). 매 샘플마다 raw_np_buf[:, ptr] = raw_vals, ptr += 1. ptr가 max_display에 도달하면 ptr=0, is_buf_full=True.
- **과거/현재 분리 (Line 모드)**: is_buf_full == True일 때 과거 = x_axis[ptr:], raw_np_buf[i, ptr:] → past_lines. 현재 = x_axis[:ptr], raw_np_buf[i, :ptr] → raw_lines. is_buf_full == False일 때 past_lines는 빈 데이터, 현재만 raw_lines에.
- **Y 좌표**: 두 구간 모두 get_scaled_array(ch_idx, raw_slice)로 변환 후 setData. 커서는 (ptr−1) 인덱스의 x, get_scaled_array로 구한 y 한 점.
- **채널 인덱스와 Y 방향**: base_offset = (N_CH−1−ch_idx)*CH_OFFSET + CH_OFFSET/2. ch_idx=0일 때 Y가 가장 크고(화면 상단), ch_idx=3일 때 Y가 가장 작음(화면 하단). 즉 채널 0이 위, 채널 3이 아래.

---

## 12. ChannelScaler·baseline 로직 상세

- **초기값**: current_min = RAW_Y_MIN_INIT(55), current_max = RAW_Y_MAX_INIT(100), baseline = (min+max)/2. has_data = False.
- **update(raw_value)**: raw_value ≤ RAW_ZERO_THRESHOLD(1.0)이면 return(0은 min/max 갱신에서 제외). has_data = True. raw_value > current_max → current_max 갱신. raw_value < current_min → current_min 갱신. 그 외 → decay로 current_max/current_min 수축. target_baseline = (current_max + current_min)/2, baseline 스무딩(baseline_alpha).
- **의미**: 신호가 벗어나면 범위 확장, 안에 있으면 감쇠. baseline은 (min+max)/2 쪽으로 스무딩. 0은 센서 미인식 등으로 간주하고 min/max에 반영하지 않음.

---

## 13. Diagonal Vector 수식

- **방향 (4채널)**: (-0.707,-0.707), (-0.707,0.707), (0.707,0.707), (0.707,-0.707).
- **최근 100샘플**: ptr ≥ DATA_LEN(100)이면 diag_raw = raw_np_buf[i, ptr−100:ptr]. ptr < 100이면: is_buf_full이 True일 때 raw_np_buf[i, −100+ptr:] 와 raw_np_buf[i, :ptr]를 concatenate해 100개로 채움. is_buf_full이 False일 때는 diag_raw = raw_np_buf[i, :ptr]만 사용(길이 < 100). diag_raw.size < 2이면 해당 채널은 스킵(continue).
- **파형**: denom = max(current_max − baseline, 30), wave = (diag_raw − baseline) / denom.
- **벡터 길이**: val = ratio * 1.4 * diag_plot_limit. ratio = get_vector_intensity(i, last_amp[i]).
- **좌표**: t = linspace(0, val, len(diag_raw)). perp = (−dy, dx). res_x = t*dx + perp_x * wave * wave_amp, res_y = t*dy + perp_y * wave * wave_amp. wave_amp = 15*ratio*(t/(val+1e-6)). 시작점 (0,0) 고정.
- **스타일**: 펜 두께 1.5 + ratio*3, 알파 80 + ratio*175.

---

## 14. CSV 로거 상세

- **생성**: START 시 ENABLE_CSV_LOGGING이 True면 CSVLogger(buffer_size=500). directory 기본 "data".
- **파일명**: data/YYYYMMDD_HHMMSS_emg.csv.
- **헤더**: Time(ms), Raw_CH0~Raw_CH(N-1), Amp_CH0~Amp_CH(N-1). config.N_CH 기준. 한 번만 기록.
- **write_row**: timestamp는 start_serial에서 설정한 start_time_ref 기준 (time.time() - start_time_ref)*1000. 전달받은 timestamp가 있으면 그대로 사용. processed_raw = [int(float(v)) for v in raw_vals], processed_amp = [int(round(float(v))) for v in amp_vals]. 버퍼에 [relative_time_ms, raw0~3, amp0~3] 추가. len(buffer) >= buffer_size면 flush. ValueError/TypeError 발생 시 print만 하고 해당 행은 버퍼에 넣지 않음.
- **종료**: STOP 시 close() → flush 후 파일 닫기. flush 시 writerows + file.flush + buffer.clear. close 시 file.closed 확인 후 닫고 "Saved: ..." 출력.

---

## 15. 에러 처리·종료

- **시리얼 열기 실패**: run() 내 try에서 실패 시 sig_error.emit, return. on_error에서 QMessageBox.critical, stop_serial.
- **파싱 실패**: 줄 단위 try/except, 개별 라인 실패 시 무시.
- **루프 예외·종료**: run() 상위 try/except에서 예외 시 sig_error. run() 종료 시(정상/예외 무관) **finally 블록에서 항상 cleanup() 호출**. cleanup()에서 _ser 닫기, _ser = None, sig_status.emit("DISCONNECTED"). 대시보드에서 STOP 후 refresh_ports()로 포트 목록 갱신.
- **CSVLogger 생성 실패**: start_serial 내에서 예외 시 QMessageBox.critical, return.
- **closeEvent**: 창 닫을 때 stop_serial() 후 event.accept().

---

## 16. 데이터 범위가 다른 센서(동적 스케일 요약)

- **RAW Line**: 전 채널 공통 data_range. 같은 raw 변동폭이면 같은 세로 폭. baseline만 채널별.
- **RAW Bar**: 구간별 변동폭을 Line과 동일한 data_range/2 기준 비율로 막대 높이. 채널 간 비교 일치.
- **Diagonal Vector·PWR**: 채널별 dynamic_half_range. 각 채널이 자기 관측 범위 대비 비율. 공통 data_range 미사용.

---

## 17. 제한 사항·알려진 이슈

- **채널 수**: 4ch/6ch 모드 지원. Channels 콤보로 전환 가능(연결 해제 후).
- **파싱**: 텍스트(한 줄 4실수 + \n)만 지원. 23바이트 이진은 미구현.
- **diag_lines**: build_diag_panel에서 for i in range(4) 하드코딩.
- **진폭 초기**: sample_buf가 n_samples 미만일 때 last_amp는 이전 값 유지.
- **CSV**: 헤더·컬럼이 config.N_CH 기준(동적).

---

## 18. 초기화·생성 순서 상세

- **EMGDashboard __init__ 순서**: 상수·버퍼·스케일러·CSV 플래그 초기화 후 **init_ui()** 호출. init_ui() 안에서 raw_plot, diag_plot, pwr_plot 등이 생성됨. **raw_plot은 build_raw_plot_panel()에서 생성**되므로, **init_ui() 호출이 끝난 뒤**에만 raw_plot이 존재함.
- **cursor_rects**: init_ui() **이후**, for i in range(N_CH)로 채널별 ScatterPlotItem을 만들어 **raw_plot.addItem(rect)** 로 추가. 즉 cursor_rects는 raw_plot이 만들어진 다음에만 붙일 수 있음.
- **set_running_ui(False)**: __init__ 마지막에 한 번 호출해 초기 상태(미연결)에서 START만 활성화, STOP/포트/Refresh/Window Size는 비활성.

---

## 19. 시리얼 수신·파싱 로직 상세

- **_buf (bytearray)**: 수신 바이트를 계속 누적. **b"\\n" in self._buf**일 때까지 read한 데이터를 _buf에 append.
- **줄 분리**: `b"\\n" in self._buf`이면 **split(b"\\n", 1)** 로 첫 번째 줄만 꺼냄. 나머지 바이트는 _buf에 남김. 꺼낸 줄은 **decode(errors="ignore").strip()**.
- **빈 줄·파싱 실패**: strip 결과가 빈 문자열이면 continue. **parse_line_4ch(s)** 호출해 실패(반환 None 등)하면 continue. 성공 시에만 sig_sample 등 후속 처리.
- **calc_counter / last_amp**: 진폭 계산 후 last_amp 갱신. calc_counter는 내부 타이밍/갱신 주기용. cleanup() 시 _buf 초기화, 시리얼 닫기, 시그널만 DISCONNECTED.

---

## 20. RAW Bar 모드 갭·표시 로직 상세

- **unified_x_ms**: render()에서 **unified_x_ms = win.x_axis[win.ptr % win.max_display]** 로 "현재 시간축 위치"를 구함. Bar 모드에서 last_x 계산 시, win.sample_count > 0이 아니면 unified_x_ms를 사용해 막대 X 위치를 맞춤.
- **gap_start**: **gap_start = win.ptr // step** (step은 Bar 구간 크기, 예: 30). "현재 ptr이 속한 구간"의 인덱스.
- **갭 목적**: "지금 기록 중인 구간"을 막대 높이 0으로 비워서 커서처럼 보이게 함.
- **is_buf_full == False**: **display_heights[gap_start:] = 0**. 아직 한 바퀴 안 채웠으므로 gap_start 이후 구간은 비움.
- **is_buf_full == True**: **(gap_start + g) % len(display_heights)** 인덱스들(g는 0 ~ gap_range−1)을 0으로 설정. 링 구조에 맞춰 "현재 기록 구간"만 갭으로 표시.

---

## 21. 기타 로직·동작 상세

- **RAW 패널 헤더 레이아웃**: build_raw_plot_panel에서 카드의 첫 번째 위젯(제목 라벨)을 **lay.itemAt(0).widget()**으로 꺼내 **header_layout**에 넣고, Line/Fill 라디오 버튼을 같은 header_layout에 추가. 제목과 모드 선택이 **한 줄**에 오도록 함.
- **compute_amp_from_samples 반환값**: 채널별 진폭 배열 **amp** (np.ndarray). 샘플 버퍼가 비어 있으면 np.zeros(config.N_CH) 반환.
- **PWR bar_item**: x = np.arange(N_CH+1). update_power_info에서는 setOpts(height=...)만 갱신. N_CH+1개 막대(CH0~CH(N-1), AVG)의 X 위치는 고정, 높이만 갱신.
- **start_time_ref**: **start_serial()**에서 **start_time_ref = time.time()** 설정. on_sample()에서 CSV용 상대 시간(ms)은 **(time.time() - start_time_ref) * 1000** 으로 계산해 write_row에 전달.

---

*이 문서는 EMG Dashboard 기준으로 작성되었으며, 4ch/6ch 모드는 SETTINGS 패널 Channels 콤보로 전환 가능하다. 프로토콜 변경 시  “확장 시 참고”와 MODULES.md를 참고하면 된다.*
