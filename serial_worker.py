import re
import time
import serial
import numpy as np
from collections import deque
from PyQt6.QtCore import QThread, pyqtSignal

import config
from config import BASE_SAMPLES, N_MULT_DEFAULT

# 한 줄 문자열에서 채널 값 파싱
def parse_line(line: str):
    if not line: return None
    nums = re.findall(r'[-+]?\d*\.\d+|\d+', line)
    if len(nums) < config.N_CH: return None
    return [float(v) for v in nums[-config.N_CH:]]


# 진폭 계산 
def compute_amp_from_samples(sample_buf: deque):
    if not sample_buf or len(sample_buf) == 0:
        return np.zeros(config.N_CH)

    # (샘플개수, 채널개수) 형태의 numpy 배열로 변환
    pkt = np.array(sample_buf, dtype=float)

    # 열방향(채널) 기준
    return np.max(pkt, axis=0) - np.min(pkt, axis=0)


# 시리얼 수신, 파싱, 진폭 계산 전용 QThread. sig_sample(raw, amp)로 UI에 전송
class SerialWorker(QThread):
    sig_sample = pyqtSignal(list, object)  # (raw_vals, amp_vals)
    sig_status = pyqtSignal(str)
    sig_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._running = False

        # 시리얼 설정 값 
        self._port = None
        self._baud = 115200
        self._ser = None

        self._buf = bytearray()  # 수신 바이트 저장

        self.n_samples = int(BASE_SAMPLES * config.N_MULT_DEFAULT)  # 윈도우 크기(진폭 계산용)
        self.sample_buf = deque(maxlen=self.n_samples)
        self.calc_counter = 0  # n_samples개 들어올 때마다 진폭 계산
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
        self.sig_status.emit(f"CONNECTED: {self._ser.name}")

        try:
            while self._running:
                if self._ser is None or not self._ser.is_open: break

                # 읽을 원천 바이트 데이터 있는 경우 
                if self._ser.in_waiting > 0:
                    data = self._ser.read(self._ser.in_waiting)
                    self._buf.extend(data)

                    # 줄단위 처리 
                    while b"\n" in self._buf:
                        line, rest = self._buf.split(b"\n", 1)
                        self._buf = bytearray(rest)
                        
                        try:
                            # 바이트 -> 문자열 
                            s = line.decode(errors="ignore").strip()
                            if not s: continue
                            
                            # 문자열 -> 채널 값 파싱 
                            raw_vals = parse_line(s)

                            if raw_vals is not None:
                                
                                self.sample_buf.append(raw_vals)
                                self.calc_counter += 1
                                if self.calc_counter >= self.n_samples:
                                    if len(self.sample_buf) >= self.n_samples:
                                        self.last_amp = compute_amp_from_samples(self.sample_buf)
                                    self.calc_counter = 0

                                # UI로 raw + amp 전달 
                                self.sig_sample.emit(raw_vals, self.last_amp)

                        except Exception:
                            # 개별 라인 파싱 오류 무시 
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