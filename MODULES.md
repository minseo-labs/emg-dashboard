# EMG Dashboard — 구조와 흐름

모듈별 역할과 코드 흐름을 한 문서에 정리했습니다.  
**레포 소개·설치·실행 방법**은 [README.md](README.md)를 참고하세요.

## 목차

- [사용 환경·라이브러리](#사용-환경라이브러리)
- [성능 최적화 요약](#성능-최적화-요약)
- [동적 스케일링](#동적-스케일링-그래프별-적용)
- [Part 1. 모듈별 역할](#part-1-모듈별-역할)
- [Part 2. 코드 흐름](#part-2-코드-흐름)

---

## 사용 환경·라이브러리

- **언어:** Python 3
- **GUI:** PyQt6 — 메인 윈도우, 위젯(버튼·콤보박스·스핀박스·라디오 등), 레이아웃·스타일
- **그래프:** pyqtgraph — RAW 시계열 플롯, 대각선 벡터(Diagonal Vector), PWR 막대 (PlotWidget, PlotCurveItem, BarGraphItem, ScatterPlotItem 등)
- **수치 연산:** NumPy — 버퍼·배열, min/max/clip, 스케일 계산
- **시리얼 통신:** pyserial — 포트 열기/읽기, 줄 단위 파싱
- **표준 라이브러리:** `sys`, `re`, `time`, `csv`, `os`, `datetime`, `collections.deque`

**실행:** `python main.py` (프로젝트 루트에서 실행). 시리얼 포트 선택 후 START로 수신 시작.

### 시리얼 프로토콜 (프레임 23 bytes)

| 구간 | 길이 | 설명 |
|------|------|------|
| 헤더 | 2 bytes | 프레임 식별 |
| 데이터 | 20 bytes | 5×4 |
| 체크섬 | 1 byte | XOR (헤더+데이터에 대한 XOR) |

---

## 성능 최적화 요약

### 자료구조

| 용도 | 구조 | 이유 |
|------|------|------|
| RAW 시계열 버퍼 | NumPy 배열 `(N_CH, max_display)` + `ptr` 링 버퍼 | 고정 크기, 슬라이싱·벡터 연산으로 한 번에 Y 좌표 계산 |
| X축 | `np.linspace(0, PLOT_SEC*1000, max_display)` | 한 번 생성 후 인덱스로 참조만 |
| 진폭 계산용 샘플 | `collections.deque(maxlen=n_samples)` | 최근 N개만 유지, append 시 O(1), 크기 초과 시 자동 삭제 |
| 시리얼 수신 | `bytearray` + `split(b"\n", 1)` | 바이트 누적 후 줄 단위 분리, 재할당 최소화 |
| Bar 높이 캐시 | NumPy 배열 `height_buf (N_CH, max_display)` | 구간별 높이 한 번 계산 후 setOpts에 전달 |
| CSV 로그 | 메모리 `list` 버퍼 (buffer_size=500) | 행을 모았다가 `writerows()`로 일괄 기록 |

### 계산·렌더

- **진폭 계산 주기화:** 매 샘플마다 하지 않고 `calc_interval`(5)마다, 그리고 `sample_buf`가 `n_samples` 이상일 때만 `compute_amp_from_samples()` 호출 → CPU 부하 감소.
- **Bar 모드:** RAW를 `step=30` 구간 단위로만 min/max 계산해 `height_buf`에 채운 뒤, `sampled_indices`로 줄여서 `setOpts` 한 번에 전달.
- **그래프:** pyqtgraph `useOpenGL=True` 로 GPU 가속, `antialias=True` 로 품질 유지.
- **렌더 타이머:** `FPS`(30)로 주기 제한. `is_running`/`sample_count == 0`이면 바로 return 해 불필요한 그리기 생략.

### I/O

- **CSV:** 한 행씩 쓰지 않고 버퍼가 `buffer_size`만큼 차면 `writerows()` + `file.flush()` 로 일괄 기록 → 디스크 I/O 횟수 감소.
- **시리얼:** `in_waiting`만큼 한 번에 `read()` 해서 호출 횟수 줄임. 루프 끝 `time.sleep(0.001)` 로 폴링 부하 완화.

### 스레드

- 시리얼 수신·파싱·진폭 계산은 `QThread`(SerialWorker)에서 수행하고, UI는 시그널로만 수신 → 메인 스레드 블로킹 방지.

---

## 동적 스케일링 (그래프별 적용)

데이터 범위가 다른 센서가 섞여 있어도, 각 그래프에서 어떻게 스케일이 적용되는지 정리했습니다.

### RAW 그래프 (Line 모드)

- **적용:** `get_scaled_array()` → 비율 = `(raw - 채널 baseline) / (공통 data_range / 2)`, clip −1~1 후 `allowed_half_height`로 Y 좌표 계산.
- **공통 data_range:** 전 채널의 `global_min` ~ `global_max`로 하나의 범위를 구함 (`_data_range_and_half_height`). 같은 raw 변동폭이면 모든 채널에서 **같은 세로 폭**으로 그려짐.
- **채널별:** `baseline`만 채널마다 따로 두어, "중앙"은 채널마다 다르고 **스케일(폭)은 통일**.
- **의미:** 데이터 범위가 큰 센서·작은 센서가 섞여 있어도, **같은 절대 raw 변화 = 같은 화면 크기**라서 채널 간 진폭 비교가 쉬움.

### RAW 그래프 (Bar 모드)

- **적용:** 구간(step=30)마다 `ch_max - ch_min`으로 "변동폭"을 구한 뒤, `BAR_HEIGHT_REF_RAW`(100)에 대한 비율로 막대 높이 계산. `max_bar_pixels`(채널 밴드 높이)에 비율을 곱해 픽셀 높이로 변환.
- **스케일 기준:** Bar 높이는 **고정 참조 100** 기준 비율이라, RAW 그래프 Line과는 별개. 채널별로 "최근 구간에서의 변동폭"이 크면 막대가 높게, 작으면 낮게 보임.
- **Y 위치:** 막대는 `base_offset ± (height/2)`에 세움. 데이터 범위는 **Bar 높이**에만 반영되고, 채널 밴드의 세로 위치는 고정.

### Diagonal Vector (대각선 벡터)

- **적용:** **채널별** 동적 스케일. `get_vector_intensity()`는 `dynamic_half_range = (current_max - baseline)`을 **채널마다** 사용해 `amp / dynamic_half_range`로 0~1 강도 계산. 파형 `wave`도 `(diag_raw - baseline) / denom`에서 `denom`이 채널별.
- **의미:** 데이터 범위가 큰 센서는 분모가 커져 상대적으로 벡터 길이·파형이 작게, 범위가 작은 센서는 크게 나옴. **각 채널이 자기 observed 범위 대비**로 표시됨.
- **공통 스케일 아님:** RAW Line과 달리 여기서는 전 채널 공통 data_range를 쓰지 않음.

### PWR BARS

- **적용:** 채널별 `last_amp`를 **관측 범위 대비 비율**로 변환(`get_vector_intensity`) 후 0~100%로 막대 높이 표시. AVG는 네 채널 비율의 평균.
- **의미:** 각 채널이 자기 observed 범위 대비 "몇 % 수준인지"로 막대가 그려져, 채널 간 상대적 세기를 비교하기 쉬움.

---

## Part 1. 모듈별 역할

각 파일이 담당하는 일입니다.

---

### main.py

**역할:** 프로그램 진입점

- PyQt6 `QApplication` 생성
- `EMGDashboard` 윈도우 생성 후 표시
- 이벤트 루프 실행 (`app.exec()`)

---

### config.py

**역할:** 전역 설정·상수

- **채널/레이아웃:** `N_CH`, `CH_OFFSET`, `RAW_Y_MIN_INIT`, `RAW_Y_MAX_INIT`
- **시리얼/신호:** `ENABLE_CSV_LOGGING`, `BASE_SAMPLES`, `N_MULT_DEFAULT`
- **타이밍:** `FPS`, `PLOT_SEC`
- **디자인:** `RAW_LINE_WIDTH`, `COLOR_*`, `CH_COLORS`, `SUM_BAR_COLOR`

다른 모듈이 여기서 값을 읽어 씀. 채널 수·색·스케일 등을 바꿀 때 이 파일을 수정.

---

### dashboard_ui.py

**역할:** 메인 윈도우 + UI 구성 + 시리얼·렌더 연결

- **윈도우:** `EMGDashboard` (QMainWindow), 레이아웃·카드 스타일
- **패널 구성:**
  - **SETTINGS & MODE:** 포트 선택, START/STOP, Window Size(SpinBox), 연결 상태 표시
  - **RAW GRAPH:** 라인/바 모드 선택, pyqtgraph PlotWidget, 채널별 라인·막대·커서
  - **Diagonal Vector:** 대각선 방향 벡터 플롯 (4채널)
  - **PWR BARS:** 채널별 AMP 막대(비율) + AVG
- **데이터 흐름:**
  - `SerialWorker.sig_sample` → `on_sample()`: raw/amp 수신, 버퍼·스케일러 갱신, CSV 기록
  - `SerialWorker.sig_status` / `sig_error` → 상태 표시·에러 처리
  - 타이머로 주기적 `render()` 호출 → `graph_render.render(win)` 호출
- **시리얼:** `start_serial()` / `stop_serial()` 에서 포트·n_mult 설정, 워커 시작/중지, CSV 로거 생성/종료

---

### serial_worker.py

**역할:** 시리얼 수신 + 프로토콜 파싱 + 진폭 계산 + UI로 전달

- **프로토콜:** 23 bytes/프레임 (헤더 2 + 데이터 20 + 체크섬 1 XOR). 상세는 [시리얼 프로토콜](#시리얼-프로토콜-프레임-23-bytes) 참고.
- **시리얼:** 지정 포트로 열고, `in_waiting` 기준으로 데이터 읽기 (줄 단위 또는 23바이트 프레임 단위 파싱)
- **파싱:** `parse_line_4ch(line)` — 한 줄에서 숫자 추출, 마지막 4개를 float 리스트로 반환 (4ch 가정). 이진 23바이트 프레임 파싱은 프로토콜에 맞게 구현 가능.
- **진폭:** `compute_amp_from_samples(sample_buf)` — 최근 N개 샘플의 채널별 (max−min)을 AMP로 계산
- **윈도우:** `n_mult`로 `n_samples = BASE_SAMPLES * n_mult` 결정, `sample_buf`(deque)에 샘플 누적 후 `n_samples`개 모이면 AMP 계산
- **시그널:** `sig_sample.emit(raw_vals, last_amp)` — UI에 raw·amp 전달, `sig_status` / `sig_error` — 연결 상태·에러

통신 프로토콜·채널 수가 바뀌면 파싱·채널 수 관련 부분을 여기서 수정.

---

### graph_render.py

**역할:** 화면에 그리기 (RAW 그래프, 대각선 벡터, PWR 막대)

- **render(win):** `win.is_running`·샘플 유무 확인 후, RAW / 대각선 벡터 / PWR 업데이트 함수 호출
- **update_raw_graph(win, is_fill_mode, unified_x_ms):**
  - 채널별 `height_buf` 계산 (구간별 max−min → BAR 높이 비율)
  - **Bar 모드:** 막대 위치·높이·갭, 커서 위치
  - **Line 모드:** 과거/현재 구간 분리, `scale_manager.get_scaled_array()`로 Y 좌표 계산, 라인·커서 설정
- **update_diag_vector(win):** 채널별 최근 100샘플로 파형·강도 계산, 대각선 방향 벡터 라인 그리기
- **update_power_info(win):** 채널별 `get_vector_intensity`로 비율(0~100%) 계산 후 PWR 막대 높이·RAW/AMP 라벨 갱신

실제 좌표·픽셀 계산은 `emg_scale`에 맡기고, 여기서는 "무엇을 그릴지"와 "pyqtgraph 아이템에 넣을 값"만 담당.

---

### emg_scale.py

**역할:** 채널별 스케일링 (Y축·진폭·대각선 벡터 강도)

- **ChannelScaler:** 채널당 하나
  - `current_min` / `current_max` / `baseline` 유지 (들어오는 raw에 따라 갱신)
  - `update(raw_value)`: min/max 확장·감쇠, baseline을 (min+max)/2 쪽으로 스무딩
- **EMGScaleManager:**
  - `_data_range_and_half_height(ch_idx)`: **전 채널 공통** `data_range` 사용 (모든 채널의 global_min ~ global_max로 한 개의 범위 계산), `allowed_half_height` 반환. 같은 raw 변동폭이 모든 채널에서 같은 세로 크기로 보이도록 비율 기준 통일.
  - `get_scaled_array(ch_idx, raw_array)`: raw 배열 → (채널 baseline + **공통 data_range** 기준) 비율 → 화면 Y 좌표 배열
  - `get_vector_intensity(ch_idx, amp_value)`: AMP 값 → 0~1 강도 (대각선 벡터·PWR 바 비율용)

RAW 그래프의 "세로 위치", Bar 모드의 채널 밴드, 대각선 벡터·PWR "세기"가 모두 여기서 나온 값으로 결정됨.

---

### logger.py

**역할:** CSV 로깅 (raw·amp·타임스탬프)

- **CSVLogger:** 세션당 하나의 CSV 파일 (파일명: 날짜_시간_emg.csv)
- **헤더:** Time(ms), Raw_CH0~3, Amp_CH0~3 (고정 4ch 가정)
- **write_row(raw_vals, amp_vals, timestamp):** 한 행 버퍼에 추가, 버퍼가 차면 디스크에 flush
- **flush / close:** 버퍼 비우기, 파일 닫기

`ENABLE_CSV_LOGGING`이 True일 때만 `dashboard_ui`에서 인스턴스 생성·사용.

---

### 모듈 요약 표

| 파일            | 한 줄 요약                                   |
|-----------------|----------------------------------------------|
| main.py         | 앱 실행 진입점                               |
| config.py       | 채널 수·색·스케일·FPS 등 전역 상수           |
| dashboard_ui.py | 메인 창, 패널 UI, 시리얼/타이머/렌더 연결     |
| serial_worker.py| 시리얼 수신, 파싱, 진폭 계산, 시그널로 전달   |
| graph_render.py | RAW·대각선 벡터·PWR 그래프 그리기            |
| emg_scale.py    | 채널별 min/max/baseline, **공통 data_range**로 Y비율 통일·대각선 벡터/PWR 강도 |
| logger.py       | Raw/AMP/시간 CSV 저장                        |

---

### 전체 데이터 흐름 요약

```
[시리얼 장치]
      │  한 줄 (예: "123 456 789 012\n")
      ▼
serial_worker.run()
      │  parse_line_4ch  →  raw_vals
      │  sample_buf.append(raw_vals)
      │  (n_samples 모이면) compute_amp_from_samples  →  last_amp
      │  sig_sample.emit(raw_vals, last_amp)
      ▼
dashboard_ui.on_sample(raw_vals, amp_vals)
      │  raw_np_buf[:, ptr] = raw_vals
      │  scale_manager.scalers[i].update(raw_vals[i])
      │  csv_logger.write_row(...)
      ▼
[다음 타이머 틱]
      │  render()  →  graph_render.render(win)
      │  get_scaled_array(raw_np_buf)  →  Y 좌표
      │  past_lines / raw_lines / bar_items / cursor_rects / diag_lines / bar_item 갱신
      ▼
[화면에 표시]
```

---

### 주요 참조 관계

| 흐름 | 주체 | 사용하는 것 |
|------|------|--------------|
| 기동 | main | dashboard_ui |
| UI·버퍼 | dashboard_ui | config, emg_scale, logger, serial_worker, graph_render |
| 시리얼·파싱·진폭 | serial_worker | config, parse_line_4ch, compute_amp_from_samples |
| 그리기 | graph_render | config, win.scale_manager, win.raw_np_buf, win.last_amp 등 |
| Y·강도 계산 | emg_scale | config, ChannelScaler 상태 |
| 로그 | logger | (dashboard_ui에서만 생성·호출) |
