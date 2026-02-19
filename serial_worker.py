import re
import time
import serial
import numpy as np
from collections import deque
from PyQt6.QtCore import QThread, pyqtSignal

import config
from config import BASE_SAMPLES, N_MULT_DEFAULT


# 데이터 파싱 함수 (config.N_CH개 값 반환)
def parse_line_4ch(line: str):
    if not line: return None
    nums = re.findall(r'[-+]?\d*\.\d+|\d+', line)
    if len(nums) < config.N_CH: return None
    return [float(v) for v in nums[-config.N_CH:]]


# 진폭 계산 함수
def compute_amp_from_samples(sample_buf: deque):
    if not sample_buf or len(sample_buf) == 0:
        return np.zeros(config.N_CH)
    pkt = np.array(sample_buf, dtype=float)
    return np.max(pkt, axis=0) - np.min(pkt, axis=0)


# 시리얼 통신 전용 스레드 클래스
class SerialWorker(QThread):
    # (원본 raw, 진폭 amp)
    sig_sample = pyqtSignal(list, object) 
    sig_status = pyqtSignal(str)
    sig_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._running = False
        self._port = None
        self._baud = 115200
        self._ser = None
        self._buf = bytearray()

        # 내부 계산용 버퍼 및 파라미터
        self.n_samples = int(BASE_SAMPLES * config.N_MULT_DEFAULT)
        self.sample_buf = deque(maxlen=self.n_samples)
        
        # 최적화: 진폭 계산 주기 관리
        self.calc_counter = 0
        self.calc_interval = 5          # 5개 샘플마다 한 번씩 Amplitude 계산 (CPU 부하 감소)
        self.last_amp = np.zeros(config.N_CH)

    # 포트 설정 정보를 주입
    def configure(self, port: str, baud: int, n_mult: int):
        self._port = port
        self._baud = baud
        self.update_params(n_mult)

    # 샘플링 윈도우 크기를 동적으로 변경
    def update_params(self, n_mult):
        self.n_samples = int(BASE_SAMPLES * n_mult)
        self.sample_buf = deque(maxlen=self.n_samples)
        self.calc_counter = 0

    def stop(self):
        self._running = False

    def run(self):
        if not self._port:
            self.sig_error.emit("No port selected.")
            return

        try:

            self._ser = serial.Serial(self._port, self._baud, timeout=0.1)
            self._ser.flushInput() # 시작 전 버퍼 비우기
        except Exception as e:
            self.sig_error.emit(f"Failed to open serial: {e}")
            return

        self._running = True
        self.sig_status.emit(f"CONNECTED: {self._ser.name}")

        try:
            while self._running:
                if self._ser is None or not self._ser.is_open: break

                # 1. 뭉텅이로 데이터 읽기 (read_all 효과)
                if self._ser.in_waiting > 0:
                    data = self._ser.read(self._ser.in_waiting)
                    self._buf.extend(data)
                    
                    # 2. 줄바꿈 기호 기준 처리
                    while b"\n" in self._buf:
                        line, rest = self._buf.split(b"\n", 1)
                        self._buf = bytearray(rest)
                        
                        try:
                            s = line.decode(errors="ignore").strip()
                            if not s: continue

                            raw_vals = parse_line_4ch(s)
                            if raw_vals is not None:
                                
                                # 2-3. Amplitude 계산 (주기적 최적화)
                                self.sample_buf.append(raw_vals)
                                self.calc_counter += 1
                                
                                if self.calc_counter >= self.calc_interval:
                                    if len(self.sample_buf) >= self.n_samples:
                                        self.last_amp = compute_amp_from_samples(self.sample_buf)
                                    self.calc_counter = 0

                                # 2-4. UI로 데이터 전송
                                self.sig_sample.emit(raw_vals, self.last_amp)

                        except Exception:
                            # 개별 라인 파싱 실패는 무시하고 다음 데이터 진행
                            continue

                # 지나치게 잦은 루프 방지 (약 1000Hz 주기로 체크)
                time.sleep(0.001) 

        except Exception as e:
            if self._running: self.sig_error.emit(f"Loop error: {e}")
        finally:
            self.cleanup()

    def cleanup(self):
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        except: pass
        self._ser = None
        self.sig_status.emit("DISCONNECTED")