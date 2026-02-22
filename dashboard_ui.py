import time
import numpy as np
import serial.tools.list_ports
import pyqtgraph as pg

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QFrame, QSpinBox, QMessageBox,
    QRadioButton,
)

import config
from emg_scale import EMGScaleManager
from logger import CSVLogger
from config import (
    FPS, PLOT_SEC,
    N_MULT_DEFAULT,
    ENABLE_CSV_LOGGING,
    get_ch_color, SUM_BAR_COLOR,
    CH_OFFSET,
    RAW_LINE_WIDTH,
    COLOR_BG, COLOR_CARD_BORDER,
    COLOR_STATUS_CONNECTED, COLOR_STATUS_DISCONNECTED,
    MIN_BUF, MAX_BUF, RATE_UPDATE_INTERVAL, BUF_RESIZE_THRESHOLD,
)
from serial_worker import SerialWorker
from graph_render import render as render_impl


class EMGDashboard(QMainWindow):

    def __init__(self):
        super().__init__()
        pg.setConfigOptions(antialias=True, useOpenGL=True)
        self.setWindowTitle("EMG Dashboard (Real-time Monitoring)")
        self.resize(1600, 800)

        self.scale_manager = EMGScaleManager(n_channels=config.N_CH)
        self.n_mult = int(N_MULT_DEFAULT)

        # RAW 버퍼: 초기값은 2000, START 후 수신 속도에 따라 "최근 PLOT_SEC초" 분량으로 동적 조정
        self.max_display = 2000
        self.raw_np_buf = np.zeros((config.N_CH, self.max_display))
        self.x_axis = np.linspace(0, PLOT_SEC * 1000, self.max_display)
        self._last_rate_update_time = 0.0

        self.cursor_colors = ["#ffffff"] * config.N_CH
        self.cursor_rects = []

        self.init_ui()

        for i in range(config.N_CH):
            rect = pg.ScatterPlotItem(size=8, symbol="s", brush=self.cursor_colors[i])
            self.raw_plot.addItem(rect)
            self.cursor_rects.append(rect)

        self.ptr = 0           # 링 버퍼 쓰기 인덱스
        self.is_buf_full = False
        self.sample_count = 0
        self.last_amp = np.zeros(config.N_CH, dtype=float)
        self.is_running = False

        self.csv_logger = None
        self.worker = SerialWorker()
        self.worker.sig_sample.connect(self.on_sample)
        self.worker.sig_status.connect(self.set_status)
        self.worker.sig_error.connect(self.on_error)
        self.worker.sig_channel_detected.connect(self.on_channel_detected)

        self.set_running_ui(False)

        self.timer = QTimer(self)
        self.timer.setInterval(int(1000 / FPS))
        self.timer.timeout.connect(self.render)
        self.timer.start()
        self.refresh_ports()

        self.height_buf = np.zeros((config.N_CH, self.max_display))
        self.height_buf.fill(1.5)

    def set_running_ui(self, running: bool):
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.cb_port.setEnabled(not running)
        self.btn_refresh.setEnabled(not running)
        self.sp_nmult.setEnabled(not running)
        self.is_running = running

    # 센서가 줄 단위로 보낸 개수(4 또는 6)로 채널 수 자동 감지
    def on_channel_detected(self, n: int):
        config.N_CH = n
        self.reinit_channel_mode()

    # 채널 모드 재설정 (N_CH 변경 시 전체 UI / 버퍼 초기화, 워커는 유지)
    def reinit_channel_mode(self):

        n = config.N_CH

        # 내부 데이터 버퍼 초기화 
        self.scale_manager = EMGScaleManager(n_channels=n)
        self.raw_np_buf = np.zeros((n, self.max_display))
        self.height_buf = np.zeros((n, self.max_display))
        self.height_buf.fill(1.5)
        self.last_amp = np.zeros(n, dtype=float)
        self.cursor_colors = ["#ffffff"] * n
        self.ptr = 0
        self.is_buf_full = False
        self.sample_count = 0

        # RAW Plot 재생성 
        for r in self.cursor_rects:
            self.raw_plot.removeItem(r)
        self.cursor_rects.clear()

        for i in range(n):
            rect = pg.ScatterPlotItem(size=8, symbol="s", brush=self.cursor_colors[i])
            self.raw_plot.addItem(rect)
            self.cursor_rects.append(rect)

        # 기존 라인, 바 제거 
        for i in range(len(self.past_lines)):
            self.raw_plot.removeItem(self.past_lines[i])
            self.raw_plot.removeItem(self.raw_lines[i])
            self.raw_plot.removeItem(self.bar_items[i])
        self.past_lines.clear()
        self.raw_lines.clear()
        self.bar_items.clear()

        # 채널 수에 맞게 라인, 바 재생성 
        for i in range(n):
            past_line = self.raw_plot.plot(
                [], [], pen=pg.mkPen(color=get_ch_color(i), width=RAW_LINE_WIDTH, alpha=0.6)
            )
            self.past_lines.append(past_line)
            line = self.raw_plot.plot([], [], pen=pg.mkPen(color=get_ch_color(i), width=RAW_LINE_WIDTH))
            self.raw_lines.append(line)
            bar = pg.BarGraphItem(
                x=[], height=[], width=20.0,
                brush=pg.mkBrush(get_ch_color(i)), pen=None,
            )
            self.raw_plot.addItem(bar)
            self.bar_items.append(bar)
            bar.setVisible(False)

        # Raw y축 범위 재설정 
        y_max = n * CH_OFFSET
        self.raw_plot.setYRange(0, y_max, padding=0)
        vb = self.raw_plot.getViewBox()
        vb.setLimits(yMin=0, yMax=y_max, minYRange=50, maxYRange=y_max)
        vb.setRange(yRange=(0, y_max))
        QTimer.singleShot(10, lambda: (self.raw_plot.setYRange(0, y_max, padding=0),
                                       self.raw_plot.getViewBox().setRange(yRange=(0, y_max))))

        # Diagonal Plot 재생성 
        for line in self.diag_lines:
            self.diag_plot.removeItem(line)
        self.diag_lines.clear()

        for i in range(n):
            line = pg.PlotCurveItem()
            self.diag_lines.append(line)
            self.diag_plot.addItem(line)

        # PWR Plot 재생성 
        self.pwr_plot.removeItem(self.bar_item)
        
        x_axis = self.pwr_plot.getAxis("bottom")
        ticks = [(i, f"CH{i}") for i in range(n)] + [(n, "AVG")]
        x_axis.setTicks([ticks])

        self.bar_item = pg.BarGraphItem(
            x=np.arange(n + 1),
            height=[0] * (n + 1),
            width=0.6,
            brushes=[pg.mkBrush(get_ch_color(i)) for i in range(n)] + [pg.mkBrush(SUM_BAR_COLOR)],
        )
        self.pwr_plot.addItem(self.bar_item)

    # 수신 속도에 맞춰 RAW 링 버퍼를 new_len으로 조정. 최근 데이터만 복사
    def _resize_raw_buffers(self, new_len: int):
        n_ch = config.N_CH
        old_len = self.max_display
        if self.is_buf_full:
            seq = [(self.ptr + i) % old_len for i in range(old_len)]
        else:
            seq = list(range(self.ptr))
        take = min(new_len, len(seq))
        new_raw = np.zeros((n_ch, new_len))
        new_height = np.ones((n_ch, new_len)) * 1.5
        if take > 0:
            indices = seq[-take:]
            for ch in range(n_ch):
                new_raw[ch, :take] = self.raw_np_buf[ch, indices]
                new_height[ch, :take] = self.height_buf[ch, indices]
        self.raw_np_buf = new_raw
        self.height_buf = new_height
        self.max_display = new_len
        self.x_axis = np.linspace(0, PLOT_SEC * 1000, new_len)
        if take >= new_len:
            self.ptr = 0
            self.is_buf_full = True
        else:
            self.ptr = take
            self.is_buf_full = False

    def card(self, title: str):
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame { background: #121826; border: 1px solid #2a3550; border-radius: 12px; }
            QLabel { color: #e6e9f2; border: none; }
            QPushButton { background: #2a3550; color: white; padding: 6px; border-radius: 6px; font-weight: bold; }
            QPushButton:hover { background: #3d4d75; }
            QPushButton:disabled { background: #1c2538; color: #4d5d7e; border: 1px solid #2a3550; }
            QComboBox { background: #1c2538; color: white; border: 1px solid #3d4d75; border-radius: 4px; padding: 4px; }
            QSpinBox { background: #1c2538; color: white; border: 1px solid #3d4d75; border-radius: 4px; padding-right: 2px; }
        """)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(15, 15, 15, 15)
        lbl = QLabel(title)
        lbl.setStyleSheet("font-size: 14px; font-weight: 800; color: #9fb3ff; margin-bottom: 5px;")
        lay.addWidget(lbl)
        return frame, lay

    # 패널 배치 
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        self.panel_settings = self.build_settings_panel()
        self.panel_raw = self.build_raw_plot_panel()
        self.panel_diag = self.build_diag_panel()
        self.panel_pwr = self.build_pwr_panel()

        left_container = QVBoxLayout()
        left_container.addWidget(self.panel_settings, 0)
        left_container.addWidget(self.panel_diag, 1)

        right_container = QVBoxLayout()
        right_container.addWidget(self.panel_raw, 5)
        right_container.addWidget(self.panel_pwr, 3)

        main_layout.addLayout(left_container, 1)
        main_layout.addLayout(right_container, 2)

    def build_settings_panel(self):

        frame, lay = self.card("SETTINGS & MODE")
        lay.setSpacing(15)

        # Port 선택 
        row_port = QHBoxLayout()

        self.cb_port = QComboBox()
        self.cb_port.setMinimumWidth(160)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setFixedWidth(85)

        self.btn_refresh.clicked.connect(self.refresh_ports)

        row_port.addWidget(QLabel("Port:"))
        row_port.addWidget(self.cb_port, 1)
        row_port.addWidget(self.btn_refresh)

        lay.addLayout(row_port)

        # Start/Stop 버튼 영역 
        row_btn = QHBoxLayout()

        self.btn_start = QPushButton("START")

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setEnabled(False)

        self.btn_start.clicked.connect(self.start_serial)
        self.btn_stop.clicked.connect(self.stop_serial)

        row_btn.addWidget(self.btn_start)
        row_btn.addWidget(self.btn_stop)

        lay.addLayout(row_btn)

        # Window Size 설정
        row_form = QHBoxLayout()
        row_form.addWidget(QLabel("Window Size:"))
        self.sp_nmult = QSpinBox()
        self.sp_nmult.setRange(1, 100)
        self.sp_nmult.setValue(self.n_mult)
        row_form.addWidget(self.sp_nmult)
        row_form.addStretch()
        lay.addLayout(row_form)

        # 연결 상태 표시 라벨 
        self.lbl_status = QLabel("● DISCONNECTED")
        self.lbl_status.setStyleSheet(f"color:{COLOR_STATUS_DISCONNECTED}; font-weight:800;")
        lay.addWidget(self.lbl_status)
        return frame

    def build_raw_plot_panel(self):
        frame, lay = self.card("RAW GRAPH (Dynamic Auto-Scaling)")

        self.raw_lines = []
        self.bar_items = []

        # 헤더 영역 (제목 + 모드 선택)
        header_layout = QHBoxLayout()

        if lay.count() > 0:
            header_label = lay.itemAt(0).widget()
            if header_label:
                header_layout.addWidget(header_label)

        header_layout.addStretch()  # 오른쪽으로 밀기 

        self.rb_line = QRadioButton("Line")
        self.rb_fill = QRadioButton("Fill (Bar)")

        self.rb_line.setChecked(True)   # 기본 모드(line)
        
        self.rb_line.setStyleSheet("color: white; font-weight: bold;")
        self.rb_fill.setStyleSheet("color: white; font-weight: bold;")

        header_layout.addWidget(self.rb_line)
        header_layout.addWidget(self.rb_fill)

        lay.addLayout(header_layout)
        
        # PlotWidget 생성 및 기본 설정 
        self.raw_plot = pg.PlotWidget()
        self.raw_plot.setBackground(COLOR_BG)
        self.raw_plot.hideButtons()
        self.raw_plot.getAxis("left").setStyle(showValues=False)
        self.raw_plot.getAxis("bottom").enableAutoSIPrefix(False)

        # x축, y축 범위 설정 
        x_max_ms = PLOT_SEC * 1000
        y_max = config.N_CH * CH_OFFSET

        self.raw_plot.setXRange(0, x_max_ms, padding=0)
        self.raw_plot.setLabel("bottom", "Time", units="ms")

        self.raw_plot.setYRange(0, y_max, padding=0)

        vb = self.raw_plot.getViewBox()
        vb.setLimits(
            xMin=0, xMax=x_max_ms,
            minXRange=200, maxXRange=x_max_ms,   # 최소, 최대 확대 범위 
            yMin=0, yMax=y_max,
            minYRange=50, maxYRange=y_max,
        )
        self.raw_plot.setMouseEnabled(x=True, y=True)

        # 채널별 라인/바 아이템 생성 
        self.past_lines = []

        for i in range(config.N_CH):
            
            # 과거 라인 
            past_line = self.raw_plot.plot(
                [], [], pen=pg.mkPen(color=get_ch_color(i), width=RAW_LINE_WIDTH, alpha=0.6)
            )
            self.past_lines.append(past_line)

            # 현재 라인 
            line = self.raw_plot.plot([], [], pen=pg.mkPen(color=get_ch_color(i), width=RAW_LINE_WIDTH))
            self.raw_lines.append(line)

            # bar 모드용 막대그래프 
            bar = pg.BarGraphItem(
                x=[], height=[],
                width=20.0,
                brush=pg.mkBrush(get_ch_color(i)),
                pen=None,
            )
            self.raw_plot.addItem(bar)
            self.bar_items.append(bar)
            bar.setVisible(False)

        # 레이아웃에 PlotWidget 추가 
        lay.addWidget(self.raw_plot, 1)
        return frame


    def build_diag_panel(self):

        frame, lay = self.card("Diagonal Vector")

        self.diag_plot = pg.PlotWidget()
        self.diag_plot.setBackground(COLOR_BG)
        self.diag_plot_limit = 50

        self.diag_plot.setXRange(-self.diag_plot_limit, self.diag_plot_limit)
        self.diag_plot.setYRange(-self.diag_plot_limit, self.diag_plot_limit)

        self.diag_plot.getAxis("left").hide()
        self.diag_plot.getAxis("bottom").hide()

        self.diag_plot.setAspectLocked(True)
        self.diag_plot.setMouseEnabled(x=False, y=False)

        # 기준 가이드라인 
        pen_guide = pg.mkPen(color=COLOR_CARD_BORDER, width=1, style=Qt.PenStyle.DashLine)
        self.diag_plot.addLine(x=0, pen=pen_guide)
        self.diag_plot.addLine(y=0, pen=pen_guide)

        # 채널별 벡터 라인 생성 
        self.diag_lines = []

        for i in range(config.N_CH):
            line = pg.PlotCurveItem()
            self.diag_lines.append(line)
            self.diag_plot.addItem(line)

        lay.addWidget(self.diag_plot, 1)

        return frame

    def build_pwr_panel(self):

        frame, lay = self.card("PWR BARS")
        self.pwr_plot = pg.PlotWidget()
        self.pwr_plot.setBackground(COLOR_BG)

        # y축 범위 고정 
        self.pwr_plot.setYRange(0, 110, padding=0)
        self.pwr_plot.enableAutoRange(axis="y", enable=False)

        # x축 라벨 설정 
        x_axis = self.pwr_plot.getAxis("bottom")
        ticks = [(i, f"CH{i}") for i in range(config.N_CH)] + [(config.N_CH, "AVG")]
        x_axis.setTicks([ticks])

        # 막대 그래프 생성 
        self.bar_item = pg.BarGraphItem(
            x=np.arange(config.N_CH + 1),
            height=[0] * (config.N_CH + 1),
            width=0.6,
            brushes=[pg.mkBrush(get_ch_color(i)) for i in range(config.N_CH)] + [pg.mkBrush(SUM_BAR_COLOR)],
        )
        self.pwr_plot.addItem(self.bar_item)
        lay.addWidget(self.pwr_plot, 1)

        return frame

    # QTimer 콜백하면 graph_render.render(self) 호출
    def render(self):
        render_impl(self)

    def on_sample(self, raw_vals, amp_vals):
        
        if not self.is_running:
            return
        if len(raw_vals) != config.N_CH:
            return
        self.last_amp = amp_vals
        self.sample_count += 1

        # 현재 타임스탬프(ms): START 시점 기준
        curr_ts_ms = (time.time() - self.start_time_ref) * 1000

        # raw 데이터 링버퍼에 저장
        self.raw_np_buf[:, self.ptr] = raw_vals

        # 동적 오토스케일 계산 (최대, 최소값 갱신)
        for i in range(config.N_CH):
            self.scale_manager.scalers[i].update(raw_vals[i])
        
        # 링버퍼 포인터 이동 및 순환
        self.ptr += 1
        if self.ptr >= self.max_display:
            self.ptr = 0
            self.is_buf_full = True

        # 수신 속도 기반 동적 버퍼 크기 (최근 PLOT_SEC초만 표시, 1초 주기 재계산)
        elapsed = time.time() - self.start_time_ref
        if elapsed >= RATE_UPDATE_INTERVAL and elapsed > 0:
            if (time.time() - self._last_rate_update_time) >= RATE_UPDATE_INTERVAL:
                self._last_rate_update_time = time.time()
                rate = self.sample_count / elapsed
                new_len = int(round(rate * PLOT_SEC))
                new_len = max(MIN_BUF, min(MAX_BUF, new_len))
                if new_len != self.max_display and (
                    abs(new_len - self.max_display) / max(self.max_display, 1) > BUF_RESIZE_THRESHOLD
                ):
                    self._resize_raw_buffers(new_len)
        
        # csv 로깅 처리 
        if self.csv_logger:
            self.csv_logger.write_row(raw_vals, amp_vals, timestamp=curr_ts_ms)


    def refresh_ports(self):
        self.cb_port.clear()
        self.cb_port.addItems([p.device for p in serial.tools.list_ports.comports()])

    def start_serial(self):
        port = self.cb_port.currentText().strip()
        if not port:
            return
        self.raw_np_buf.fill(0)
        self.height_buf.fill(1.5)
        self.ptr = 0
        self.is_buf_full = False
        self.sample_count = 0
        self.start_time_ref = time.time()
        self._last_rate_update_time = self.start_time_ref

        # RAW 뷰를 채널 수에 맞게 0~y_max로 설정 (4ch/6ch 모두 전체 채널 보이도록)
        y_max = config.N_CH * CH_OFFSET
        self.raw_plot.setYRange(0, y_max, padding=0)
        self.raw_plot.getViewBox().setRange(yRange=(0, y_max))

        # 스케일 정보 초기화
        self.scale_manager.reset()

        self.csv_logger = None
        if ENABLE_CSV_LOGGING:
            try:
                self.csv_logger = CSVLogger(buffer_size=500)
            except Exception as e:
                QMessageBox.critical(self, "Logger Error", str(e))
                return

        self.n_mult = self.sp_nmult.value()
        self.worker.configure(port, 115200, self.n_mult)
        self.worker.start()
        self.set_running_ui(True)


    def stop_serial(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(500)
        if self.csv_logger:
            self.csv_logger.close()
            self.csv_logger = None
        self.set_running_ui(False)

    def set_status(self, txt):
        self.lbl_status.setText(f"● {txt}")
        self.lbl_status.setStyleSheet(
            f"color:{COLOR_STATUS_CONNECTED if 'CONNECTED' in txt else COLOR_STATUS_DISCONNECTED}; font-weight:800;"
        )

    def on_error(self, msg):
        QMessageBox.critical(self, "Error", msg)
        self.stop_serial()

    def closeEvent(self, event):
        self.stop_serial()
        event.accept()
