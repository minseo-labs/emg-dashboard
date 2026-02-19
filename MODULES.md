# EMG Dashboard — 구조와 흐름

모듈별 역할과 코드 흐름을 한 문서에 정리했습니다.  
**레포 소개·설치·실행 방법**은 [README.md](README.md)를 참고하세요.

## 목차

- [사용 환경·라이브러리](#사용-환경라이브러리)
- [성능 최적화 요약](#성능-최적화-요약)
- [동적 스케일링](#동적-스케일링-그래프별-적용)
- [Part 1. 모듈별 역할](#part-1-모듈별-역할)
- [Part 2. 코드 흐름](#part-2-전체-데이터-흐름-요약)

---

## 사용 환경·라이브러리

- **언어:** Python 3
- **GUI:** PyQt6 — 메인 윈도우, 위젯(버튼·콤보박스·스핀박스·라디오 등), 레이아웃·스타일
- **그래프:** pyqtgraph — RAW 시계열 플롯, 대각선 벡터(Diagonal Vector), PWR 막대 (PlotWidget, PlotCurveItem, BarGraphItem, ScatterPlotItem 등)
- **수치 연산:** NumPy — 버퍼·배열, min/max/clip, 스케일 계산
- **시리얼 통신:** pyserial — 포트 열기/읽기, 줄 단위 파싱
- **표준 라이브러리:** `sys`, `re`, `time`, `csv`, `os`, `datetime`, `collections.deque`

**실행:** `python main.py` (프로젝트 루트에서 실행). 시리얼 포트 선택 후 START로 수신 시작.

### 시리얼 프로토콜

**텍스트 (구현됨)**  
- 한 줄에 N_CH개 실수(공백 구분), `\n` 종료. 4ch면 4개, 6ch면 6개.
- Channels 설정에 따라 `parse_line`이 마지막 N_CH개만 파싱.

**프레임 23 bytes (문서화만, 미구현)**  

| 구간 | 길이 | 설명 |
|------|------|------|
| 헤더 | 2 bytes | 프레임 식별 |
| 데이터 | 20 bytes | 채널별 RAW (형식 프로토콜별 정의) |
| 체크섬 | 1 byte | XOR (헤더+데이터) |

---

## 성능 최적화 요약

### 자료구조

| 자료구조 | 타입 | 용도 |
|----------|------|------|
| **raw_np_buf** | NumPy `(N_CH, max_display)` | RAW 시계열 링 버퍼. ptr로 쓰기 위치 관리 |
| **x_axis** | NumPy 1D `linspace` | 시간축 (0 ~ PLOT_SEC×1000 ms). 한 번 생성 후 인덱스로만 참조 |
| **height_buf** | NumPy `(N_CH, max_display)` | Bar 모드 구간별 막대 높이 캐시 |
| **sample_buf** | `collections.deque(maxlen=n_samples)` | 진폭 계산용 최근 N샘플. append O(1), 초과 시 자동 삭제 |
| **_buf** | `bytearray` | 시리얼 수신 바이트 누적 → 줄 단위 split |
| **CSVLogger.buffer** | Python `list` | 로그 행 버퍼. 차면 writerows 일괄 기록 |
| **last_amp** | NumPy 1D | 채널별 최근 진폭 (max−min) |

**정리:** 시계열·버퍼는 NumPy 배열 + ptr 링 버퍼(슬라이싱·벡터 연산), 진폭 윈도우는 deque(최근 N개만 유지), 시리얼은 bytearray(바이트 누적 후 줄 단위 파싱), CSV는 list 버퍼(배치 기록으로 I/O 감소).

### 계산·렌더

- **진폭 계산 주기:** `n_samples`개 샘플이 들어올 때마다 한 번 `compute_amp_from_samples(sample_buf)` 호출. (예: 윈도우 50이면 50줄마다 계산 → CPU 부하 감소.)
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

- **적용:** `get_scaled_array()` — raw ≤ `RAW_ZERO_THRESHOLD`(1.0)이면 `RAW_ZERO_REF`(100)로 치환(`effective_raw`). 비율 = `(effective_raw - baseline) / (data_range / 2)`, clip −1~1 후 `base_offset + ratios * allowed_half_height` 로 Y 좌표 계산.
- **공통 data_range:** `_data_range_and_half_height()` — `has_data`인 채널 중 `current_min > 0`, `current_max > 0`인 것만 모아 `global_min`·`global_max` 계산. 없으면 `RAW_Y_MIN_INIT`·`RAW_Y_MAX_INIT` 사용. `data_range = max(global_max - global_min, 20)`.
- **채널별:** `baseline`만 채널마다 따로. **스케일(폭)은 공통 data_range로 통일** → 같은 raw 변동폭이면 모든 채널에서 같은 세로 폭.

### RAW 그래프 (Bar 모드)

- **적용:** 구간(step=30)마다 `ch_max - ch_min`으로 변동폭(`bar_height_raw`) 계산. 변동폭 < `NO_SIGNAL_VARIATION_RAW`(1.0)이면 `LINE_HEIGHT_PX`(최소 높이). 그 외 `half_range = max(data_range/2, 1.0)`, `ratio = min(bar_height_raw / half_range, 1.0)` 로 막대 높이 결정.
- **스케일 기준:** Line과 **동일한 공통 data_range** 사용 → 채널 간 비교 일치.
- **Y 위치:** 막대는 `base_offset ± (height/2)`에 세움.

### Diagonal Vector (대각선 벡터)

- **적용:** **채널별** 동적 스케일. `get_vector_intensity()`는 `dynamic_half_range = max(current_max - baseline, 20)` 로 `intensity = (amp / dynamic_half_range) * gains * 1.3` (clip 0~1). 파형 `wave = (diag_raw - baseline) / denom`, `denom = max(current_max - baseline, 30)`.
- **의미:** 각 채널이 **자기 관측 범위 대비**로 표시됨. 공통 data_range 미사용.

### PWR BARS

- **적용:** 채널별 `last_amp`를 `get_vector_intensity`로 0~1 비율 변환 후 0~100% 막대 높이. AVG는 N_CH개 채널 비율의 평균.
- **의미:** 각 채널이 자기 관측 범위 대비 "몇 % 수준인지"로 표시 → 채널 간 상대적 세기 비교 용이.

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
- **RAW/신호:** `RAW_ZERO_REF`, `RAW_ZERO_THRESHOLD`, `NO_SIGNAL_VARIATION_RAW` (Bar 모드 변동폭 기준)
- **시리얼/신호:** `ENABLE_CSV_LOGGING`, `BASE_SAMPLES`, `N_MULT_DEFAULT`
- **타이밍:** `FPS`, `PLOT_SEC`
- **디자인:** `RAW_LINE_WIDTH`, `COLOR_*`, `CH_COLORS`, `SUM_BAR_COLOR`

다른 모듈이 여기서 값을 읽어 씀. 채널 수·색·스케일 등을 바꿀 때 이 파일을 수정.

---

### dashboard_ui.py

**역할:** 메인 윈도우 + UI 구성 + 시리얼·렌더 연결

- **윈도우:** `EMGDashboard` (QMainWindow), 레이아웃·카드 스타일
- **패널 구성:**
  - **SETTINGS & MODE:** 포트 선택, START/STOP, Window Size(SpinBox), Channels(4ch/6ch), 연결 상태 표시
  - **RAW GRAPH:** 라인/바 모드 선택, pyqtgraph PlotWidget, 채널별 라인·막대·커서
  - **Diagonal Vector:** 대각선 방향 벡터 플롯 (N_CH)
  - **PWR BARS:** 채널별 AMP 막대(비율) + AVG
- **데이터 흐름:**
  - `SerialWorker.sig_sample` → `on_sample()`: raw/amp 수신, 버퍼·스케일러 갱신, CSV 기록
  - `SerialWorker.sig_status` / `sig_error` → 상태 표시·에러 처리
  - 타이머로 주기적 `render()` 호출 → `graph_render.render(win)` 호출
- **시리얼:** `start_serial()` / `stop_serial()` 에서 포트·n_mult 설정, 워커 시작/중지, CSV 로거 생성/종료

---

### serial_worker.py

**역할:** 시리얼 수신 + 프로토콜 파싱 + 진폭 계산 + UI로 전달

- **프로토콜:** 텍스트(한 줄 N_CH개 실수 + `\n`) 파싱 구현. 23바이트 프레임은 문서화만 되어 있고 미구현.
- **시리얼:** 지정 포트로 열고, `in_waiting`만큼 읽은 뒤 줄(`\n`) 단위로 분리·`parse_line` 파싱.
- **파싱:** `parse_line(line)` — 한 줄에서 숫자 추출, 마지막 N_CH개를 float 리스트로 반환 (config.N_CH에 따라 4ch/6ch 등 여러 센서 대응).
- **진폭:** `compute_amp_from_samples(sample_buf)` — 최근 N개 샘플의 채널별 (max−min)을 AMP 배열로 반환
- **윈도우:** `n_mult`로 `n_samples = BASE_SAMPLES * n_mult` 결정, `sample_buf`(deque)에 샘플 누적, `n_samples`개 들어올 때마다 AMP 계산
- **시그널:** `sig_sample.emit(raw_vals, last_amp)` — UI에 raw·amp 전달, `sig_status` / `sig_error` — 연결 상태·에러

통신 프로토콜·채널 수가 바뀌면 파싱·채널 수 관련 부분을 여기서 수정.

---

### graph_render.py

**역할:** 화면에 그리기 (RAW 그래프, 대각선 벡터, PWR 막대)

- **render(win):** `win.is_running`·샘플 유무 확인 후, `unified_x_ms = win.x_axis[win.ptr % win.max_display]`, RAW / 대각선 벡터 / PWR 업데이트
- **update_raw_graph(win, is_fill_mode, unified_x_ms):**
  - 채널별 `height_buf` 계산 (구간별 max−min → data_range/2 기준 비율, Line과 동일 스케일)
  - **Bar 모드:** 막대 위치·높이·갭, 커서 위치
  - **Line 모드:** 과거/현재 구간 분리, `scale_manager.get_scaled_array()`로 Y 좌표 계산, 라인·커서 설정
- **update_diag_vector(win):** 채널별 최근 100샘플로 파형·강도 계산, 대각선 방향 벡터 라인 그리기
- **update_power_info(win):** 채널별 `get_vector_intensity`로 비율(0~100%) 계산 후 PWR 막대 높이 갱신

실제 좌표·픽셀 계산은 `emg_scale`에 맡기고, 여기서는 "무엇을 그릴지"와 "pyqtgraph 아이템에 넣을 값"만 담당.

---

### emg_scale.py

**역할:** 채널별 스케일링 (Y축·진폭·대각선 벡터 강도)

- **ChannelScaler:** 채널당 하나
  - `current_min` / `current_max` / `baseline` 유지 (들어오는 raw에 따라 갱신)
  - `update(raw_value)`: min/max 확장·감쇠, baseline을 (min+max)/2 쪽으로 스무딩
- **EMGScaleManager:**
  - `_data_range_and_half_height()`: **전 채널 공통** `data_range` 사용 (has_data인 채널의 global_min ~ global_max로 범위 계산), `allowed_half_height` 반환.
  - `get_scaled_array(ch_idx, raw_array)`: raw 배열 → (채널 baseline + **공통 data_range** 기준) 비율 → 화면 Y 좌표 배열
  - `get_vector_intensity(ch_idx, amp_value)`: AMP 값 → 0~1 강도 (대각선 벡터·PWR 바 비율용)

RAW 그래프의 "세로 위치", Bar 모드의 채널 밴드, 대각선 벡터·PWR "세기"가 모두 여기서 나온 값으로 결정됨.

---

### logger.py

**역할:** CSV 로깅 (raw·amp·타임스탬프)

- **CSVLogger:** 세션당 하나의 CSV 파일 (파일명: 날짜_시간_emg.csv)
- **헤더:** Time(ms), Raw_CH0~N, Amp_CH0~N (config.N_CH 기준)
- **write_row(raw_vals, amp_vals, timestamp):** 한 행 버퍼에 추가, 버퍼가 차면 디스크에 flush
- **flush / close:** 버퍼 비우기, 파일 닫기

`ENABLE_CSV_LOGGING`이 True일 때만 `dashboard_ui`에서 인스턴스 생성·사용.

---

### 모듈 요약 표

| 파일            | 한 줄 요약                                   |
|-----------------|----------------------------------------------|
| main.py         | 대시보드 실행 진입점                         |
| config.py       | 채널 수·색·스케일·FPS 등 전역 상수           |
| dashboard_ui.py | 메인 창, 패널 UI, 시리얼/타이머/렌더 연결     |
| serial_worker.py| 시리얼 수신, 파싱, 진폭 계산, 시그널로 전달   |
| graph_render.py | RAW·대각선 벡터·PWR 그래프 그리기            |
| emg_scale.py    | 채널별 min/max/baseline, **공통 data_range**로 Y비율 통일·대각선 벡터/PWR 강도 |
| logger.py       | Raw/AMP/시간 CSV 저장                        |

---

## Part 2. 전체 데이터 흐름 요약

대시보드 기동부터 데이터 수신·화면 갱신·종료까지의 흐름입니다.

---

```
[시리얼 장치]
      │  한 줄 (예: "123 456 789 012\n")
      ▼
serial_worker.run()
      │  parse_line  →  raw_vals
      │  sample_buf.append(raw_vals)
      │  n_samples개마다 sample_buf로 compute_amp_from_samples  →  last_amp
      │  sig_sample.emit(raw_vals, last_amp)
      ▼
dashboard_ui.on_sample(raw_vals, amp_vals)
      │  last_amp = amp_vals
      │  raw_np_buf[:, ptr] = raw_vals
      │  scale_manager.scalers[i].update(raw_vals[i])
      │  ptr += 1, ptr ≥ max_display이면 ptr=0, is_buf_full=True
      │  (ENABLE_CSV_LOGGING이면) csv_logger.write_row(raw_vals, amp_vals, timestamp)
      ▼
[다음 타이머 틱]
      │  render()  →  graph_render.render(win)
      │  is_running·sample_count 확인, 없으면 return
      │  update_raw_graph / update_diag_vector / update_power_info
      │  get_scaled_array(ch_idx, raw_slice)  →  Y 좌표, past_lines/raw_lines/bar_items/cursor_rects/diag_lines/bar_item 갱신
      ▼
[화면에 표시]
```

---

### 주요 참조 관계

| 흐름 | 주체 | 사용하는 것 |
|------|------|--------------|
| 기동 | main | dashboard_ui |
| UI·버퍼 | dashboard_ui | config, emg_scale, logger, serial_worker, graph_render |
| 시리얼·파싱·진폭 | serial_worker | config, parse_line, compute_amp_from_samples |
| 그리기 | graph_render | config, win.scale_manager, win.raw_np_buf, win.last_amp 등 |
| Y·강도 계산 | emg_scale | config, ChannelScaler 상태 |
| 로그 | logger | (dashboard_ui에서만 생성·호출) |
