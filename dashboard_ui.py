"""
EMG Dashboard 메인 윈도우 및 패널 빌더.

- EMGDashboard: QMainWindow 기반. SETTINGS, RAW, Diagonal Vector, PWR 패널.
- 시리얼 연결·해제, 4ch/6ch 전환, QTimer 렌더 루프, on_sample 데이터 처리.
"""
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
)
from serial_worker import SerialWorker
from graph_render import render as render_impl


class EMGDashboard(QMainWindow):
    """메인 윈도우. SETTINGS(포트·시작·중지·채널), RAW/Diagonal/PWR 패널, SerialWorker 연동."""

    def __init__(self):
        super().__init__()
        pg.setConfigOptions(antialias=True, useOpenGL=True)
        self.setWindowTitle("EMG Dashboard (Real-time Monitoring)")
        self.resize(1600, 800)

        self.scale_manager = EMGScaleManager(n_channels=config.N_CH)
        self.n_mult = int(N_MULT_DEFAULT)

        self.max_display = 5000  # RAW 시계열 링 버퍼 길이
        self.raw_np_buf = np.zeros((config.N_CH, self.max_display))
        self.x_axis = np.linspace(0, PLOT_SEC * 1000, self.max_display)

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

        self.set_running_ui(False)

        self.timer = QTimer(self)
        self.timer.setInterval(int(1000 / FPS))
        self.timer.timeout.connect(self.render)
        self.timer.start()
        self.refresh_ports()

        self.height_buf = np.zeros((config.N_CH, self.max_display))
        self.height_buf.fill(1.5)

    def set_running_ui(self, running: bool):
        """연결 중이면 START·포트·Refresh·Window Size·Channels 비활성화, STOP만 활성화."""
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.cb_port.setEnabled(not running)
        self.btn_refresh.setEnabled(not running)
        self.sp_nmult.setEnabled(not running)
        self.cb_channels.setEnabled(not running)
        self.is_running = running

    def on_channels_changed(self, index):
        """Channels 콤보 변경. 연결 중이면 무시하고 원래값 복원. 해제 후 4ch/6ch 전환 가능."""
        if self.is_running:
            QMessageBox.warning(
                self, "모드 변경",
                "연결을 끊은 후 채널 모드를 변경하세요.",
            )
            self.cb_channels.blockSignals(True)
            self.cb_channels.setCurrentIndex(0 if config.N_CH == 4 else 1)
            self.cb_channels.blockSignals(False)
            return
        n = 4 if index == 0 else 6
        config.CH_MODE = n
        config.N_CH = n
        self.reinit_channel_mode()

    def reinit_channel_mode(self):
        """채널 수 변경 시 버퍼·플롯·스케일 등 N_CH 기준으로 재생성."""
        n = config.N_CH
        self.scale_manager = EMGScaleManager(n_channels=n)
        self.raw_np_buf = np.zeros((n, self.max_display))
        self.height_buf = np.zeros((n, self.max_display))
        self.height_buf.fill(1.5)
        self.last_amp = np.zeros(n, dtype=float)
        self.cursor_colors = ["#ffffff"] * n
        self.ptr = 0
        self.is_buf_full = False
        self.sample_count = 0

        # RAW 플롯: 기존 아이템 제거 후 n개 재생성
        for r in self.cursor_rects:
            self.raw_plot.removeItem(r)
        self.cursor_rects.clear()
        for i in range(n):
            rect = pg.ScatterPlotItem(size=8, symbol="s", brush=self.cursor_colors[i])
            self.raw_plot.addItem(rect)
            self.cursor_rects.append(rect)

        for i in range(len(self.past_lines)):
            self.raw_plot.removeItem(self.past_lines[i])
            self.raw_plot.removeItem(self.raw_lines[i])
            self.raw_plot.removeItem(self.bar_items[i])
        self.past_lines.clear()
        self.raw_lines.clear()
        self.bar_items.clear()
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

        y_max = n * CH_OFFSET
        self.raw_plot.setYRange(0, y_max)
        vb = self.raw_plot.getViewBox()
        vb.setLimits(yMin=0, yMax=y_max, minYRange=50, maxYRange=y_max)

        # Diagonal: 기존 라인 제거 후 n개 재생성
        for line in self.diag_lines:
            self.diag_plot.removeItem(line)
        self.diag_lines.clear()
        for i in range(n):
            line = pg.PlotCurveItem()
            self.diag_lines.append(line)
            self.diag_plot.addItem(line)

        # PWR: bar_item 제거 후 n+1개로 재생성
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

        # SerialWorker 재생성 (last_amp 길이 등 N_CH 반영)
        if self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(500)
        self.worker = SerialWorker()
        self.worker.sig_sample.connect(self.on_sample)
        self.worker.sig_status.connect(self.set_status)
        self.worker.sig_error.connect(self.on_error)

    def card(self, title: str):
        """스타일 적용된 카드 프레임 반환. QFrame + QVBoxLayout + 제목 라벨."""
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

    def init_ui(self):
        """중앙 위젯에 SETTINGS·RAW·Diagonal·PWR 패널 배치. 좌측 SETTINGS+Diagonal, 우측 RAW+PWR."""
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
        """포트·Refresh·START·STOP·Window Size·Channels 콤보·상태 라벨 패널 빌드."""
        frame, lay = self.card("SETTINGS & MODE")
        lay.setSpacing(15)
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
        row_btn = QHBoxLayout()
        self.btn_start = QPushButton("START")
        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self.start_serial)
        self.btn_stop.clicked.connect(self.stop_serial)
        row_btn.addWidget(self.btn_start)
        row_btn.addWidget(self.btn_stop)
        lay.addLayout(row_btn)
        row_form = QHBoxLayout()
        # 왼쪽 절반: Window Size
        left_form = QWidget()
        left_form_lay = QHBoxLayout(left_form)
        left_form_lay.setContentsMargins(0, 0, 0, 0)
        left_form_lay.addWidget(QLabel("Window Size:"))
        self.sp_nmult = QSpinBox()
        self.sp_nmult.setRange(1, 100)
        self.sp_nmult.setValue(self.n_mult)
        left_form_lay.addWidget(self.sp_nmult)
        row_form.addWidget(left_form, 1)
        # 오른쪽 절반: Channels
        right_form = QWidget()
        right_form_lay = QHBoxLayout(right_form)
        right_form_lay.setContentsMargins(0, 0, 0, 0)
        right_form_lay.addWidget(QLabel("Channels:"))
        self.cb_channels = QComboBox()
        self.cb_channels.addItems(["4ch", "6ch"])
        self.cb_channels.setCurrentIndex(0 if config.N_CH == 4 else 1)
        self.cb_channels.setMinimumWidth(80)
        self.cb_channels.currentIndexChanged.connect(self.on_channels_changed)
        right_form_lay.addWidget(self.cb_channels)
        row_form.addWidget(right_form, 1)
        lay.addLayout(row_form)

        self.lbl_status = QLabel("● DISCONNECTED")
        self.lbl_status.setStyleSheet(f"color:{COLOR_STATUS_DISCONNECTED}; font-weight:800;")
        lay.addWidget(self.lbl_status)
        return frame

    def build_raw_plot_panel(self):
        """RAW 시계열 PlotWidget. Line/Fill 라디오, past_lines·raw_lines·bar_items 생성."""
        frame, lay = self.card("RAW GRAPH (Dynamic Auto-Scaling)")
        self.raw_lines = []
        self.bar_items = []

        header_layout = QHBoxLayout()
        if lay.count() > 0:
            header_label = lay.itemAt(0).widget()
            if header_label:
                header_layout.addWidget(header_label)
        header_layout.addStretch()
        self.rb_line = QRadioButton("Line")
        self.rb_fill = QRadioButton("Fill (Bar)")
        self.rb_line.setChecked(True)
        self.rb_line.setStyleSheet("color: white; font-weight: bold;")
        self.rb_fill.setStyleSheet("color: white; font-weight: bold;")
        header_layout.addWidget(self.rb_line)
        header_layout.addWidget(self.rb_fill)
        lay.addLayout(header_layout)

        self.raw_plot = pg.PlotWidget()
        self.raw_plot.setBackground(COLOR_BG)
        self.raw_plot.hideButtons()
        self.raw_plot.getAxis("left").setStyle(showValues=False)
        self.raw_plot.getAxis("bottom").enableAutoSIPrefix(False)
        x_max_ms = PLOT_SEC * 1000
        y_max = config.N_CH * CH_OFFSET
        self.raw_plot.setXRange(0, x_max_ms, padding=0)
        self.raw_plot.setLabel("bottom", "Time", units="ms")
        self.raw_plot.setYRange(0, y_max)
        vb = self.raw_plot.getViewBox()
        vb.setLimits(
            xMin=0, xMax=x_max_ms,
            minXRange=200, maxXRange=x_max_ms,
            yMin=0, yMax=y_max,
            minYRange=50, maxYRange=y_max,
        )
        self.raw_plot.setMouseEnabled(x=True, y=True)

        self.past_lines = []
        for i in range(config.N_CH):
            past_line = self.raw_plot.plot(
                [], [], pen=pg.mkPen(color=get_ch_color(i), width=RAW_LINE_WIDTH, alpha=0.6)
            )
            self.past_lines.append(past_line)
            line = self.raw_plot.plot([], [], pen=pg.mkPen(color=get_ch_color(i), width=RAW_LINE_WIDTH))
            self.raw_lines.append(line)
            bar = pg.BarGraphItem(
                x=[], height=[],
                width=20.0,
                brush=pg.mkBrush(get_ch_color(i)),
                pen=None,
            )
            self.raw_plot.addItem(bar)
            self.bar_items.append(bar)
            bar.setVisible(False)

        lay.addWidget(self.raw_plot, 1)
        return frame

    def build_diag_panel(self):
        """Diagonal Vector PlotWidget. aspect locked, 가이드선, diag_lines N개."""
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
        pen_guide = pg.mkPen(color=COLOR_CARD_BORDER, width=1, style=Qt.PenStyle.DashLine)
        self.diag_plot.addLine(x=0, pen=pen_guide)
        self.diag_plot.addLine(y=0, pen=pen_guide)
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
        """SerialWorker sig_sample 수신. raw 링버퍼·scale_manager·CSV 기록 처리."""
        if not self.is_running:
            return
            
        self.last_amp = amp_vals
        self.sample_count += 1
        curr_ts_ms = (time.time() - self.start_time_ref) * 1000
        self.raw_np_buf[:, self.ptr] = raw_vals
        for i in range(config.N_CH):
            self.scale_manager.scalers[i].update(raw_vals[i])
        self.ptr += 1
        if self.ptr >= self.max_display:  # 링 버퍼 순환
            self.ptr = 0
            self.is_buf_full = True
        if self.csv_logger:
            self.csv_logger.write_row(raw_vals, amp_vals, timestamp=curr_ts_ms)

    def refresh_ports(self):
        """시리얼 포트 목록 갱신. pyserial list_ports 사용."""
        self.cb_port.clear()
        self.cb_port.addItems([p.device for p in serial.tools.list_ports.comports()])

    def start_serial(self):
        """포트 연결·버퍼 초기화·CSV 로거 생성·SerialWorker 시작·set_running_ui(True)."""
        port = self.cb_port.currentText().strip()
        if not port:
            return
        self.raw_np_buf.fill(0)
        self.height_buf.fill(1.5)
        self.ptr = 0
        self.is_buf_full = False
        self.sample_count = 0
        self.start_time_ref = time.time()
        
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
        """SerialWorker 중지·CSV 로거 close·set_running_ui(False)."""
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(500)
        if self.csv_logger:
            self.csv_logger.close()
            self.csv_logger = None
        self.set_running_ui(False)

    def set_status(self, txt):
        """상태 라벨 업데이트. CONNECTED/DISCONNECTED에 따라 색상 변경."""
        self.lbl_status.setText(f"● {txt}")
        self.lbl_status.setStyleSheet(
            f"color:{COLOR_STATUS_CONNECTED if 'CONNECTED' in txt else COLOR_STATUS_DISCONNECTED}; font-weight:800;"
        )

    def on_error(self, msg):
        """SerialWorker sig_error 수신. 메시지 박스 표시 후 stop_serial 호출."""
        QMessageBox.critical(self, "Error", msg)
        self.stop_serial()

    def closeEvent(self, event):
        """윈도우 종료 시 stop_serial로 연결 정리 후 이벤트 수락."""
        self.stop_serial()
        event.accept()
