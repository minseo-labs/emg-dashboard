import re
import time
import serial
import numpy as np
from collections import deque
from PyQt6.QtCore import QThread, pyqtSignal

import config
from config import BASE_SAMPLES, N_MULT_DEFAULT

# 한 줄 문자열에서 숫자 추출. 줄당 4개면 4ch, 6개면 6ch로 자동 감지
def parse_line(line: str):
    if not line:
        return None
    nums = re.findall(r'[-+]?\d*\.\d+|\d+', line)
    if len(nums) not in (4, 6):
        return None
    n = len(nums)
    return ([float(v) for v in nums], n)


# 진폭 계산 (채널 수는 sample_buf 행 길이에서 유추)
def compute_amp_from_samples(sample_buf: deque):
    if not sample_buf or len(sample_buf) == 0:
        return None  # 호출측에서 n_ch 알 때 np.zeros(n_ch) 사용
    pkt = np.array(sample_buf, dtype=float)
    return np.max(pkt, axis=0) - np.min(pkt, axis=0)


# 시리얼 수신, 파싱, 진폭 계산 전용 QThread. sig_sample(raw, amp)로 UI에 전송
class SerialWorker(QThread):
    sig_sample = pyqtSignal(list, object)  # (raw_vals, amp_vals)
    sig_status = pyqtSignal(str)
    sig_error = pyqtSignal(str)
    sig_channel_detected = pyqtSignal(int)  # 줄 단위로 감지한 채널 수 (4 또는 6)

    def __init__(self):
        super().__init__()
        self._running = False
        self._session_n_ch = None  # START 시점에 None, 첫 유효 줄에서 4 또는 6으로 설정

        self._port = None
        self._baud = 115200
        self._ser = None
        self._buf = bytearray()

        self.n_samples = int(BASE_SAMPLES * config.N_MULT_DEFAULT)
        self.sample_buf = deque(maxlen=self.n_samples)
        self.calc_counter = 0
        self.last_amp = np.zeros(config.N_CH)

    # 설정 
    def configure(self, port: str, baud: int, n_mult: int):
        self._port = port
        self._baud = baud
        self.update_params(n_mult)

    # 진폭 계산 윈도우 크기 변경 
    def update_params(self, n_mult):
        self.n_samples = int(BASE_SAMPLES * n_mult)
        self.sample_buf = deque(maxlen=self.n_samples)
        self.calc_counter = 0

    # 스레드 종료 요청 
    def stop(self):
        self._running = False


    # 실제 시리얼 수신 루프 
    def run(self):
        if not self._port:
            self.sig_error.emit("No port selected.")
            return

        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=0.1)
            self._ser.flushInput()
        except Exception as e:
            self.sig_error.emit(f"Failed to open serial: {e}")
            return

        self._running = True
        self._session_n_ch = None  # START 시점 리셋 → 첫 줄에서 4/6 자동 감지
        self.sig_status.emit(f"CONNECTED: {self._ser.name}")

        try:
            while self._running:
                if self._ser is None or not self._ser.is_open: break

                if self._ser.in_waiting > 0:
                    data = self._ser.read(self._ser.in_waiting)
                    self._buf.extend(data)

                    while b"\n" in self._buf:
                        line, rest = self._buf.split(b"\n", 1)
                        self._buf = bytearray(rest)

                        try:
                            s = line.decode(errors="ignore").strip()
                            if not s: continue

                            parsed = parse_line(s)
                            if parsed is None: continue

                            raw_vals, n = parsed

                            if self._session_n_ch is None:
                                # 첫 유효 줄: 채널 수 감지 후 시그널만 보내고 이 줄은 버림
                                self._session_n_ch = n
                                self.sample_buf = deque(maxlen=self.n_samples)
                                self.last_amp = np.zeros(n)
                                self.calc_counter = 0
                                self.sig_channel_detected.emit(n)
                                continue

                            if n != self._session_n_ch:
                                continue

                            self.sample_buf.append(raw_vals)
                            self.calc_counter += 1
                            if self.calc_counter >= self.n_samples:
                                if len(self.sample_buf) >= self.n_samples:
                                    amp = compute_amp_from_samples(self.sample_buf)
                                    if amp is not None:
                                        self.last_amp = amp
                                self.calc_counter = 0

                            self.sig_sample.emit(raw_vals, self.last_amp)

                        except Exception:
                            continue

                time.sleep(0.001) 

        except Exception as e:
            if self._running: self.sig_error.emit(f"Loop error: {e}")
        finally:
            self.cleanup()

    def cleanup(self):
        """포트 닫기, DISCONNECTED 시그널."""
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        except: pass
        self._ser = None
        self.sig_status.emit("DISCONNECTED")