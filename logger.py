"""
CSV 데이터 로깅 모듈.

- 연결 시작 시 data/ 폴더에 YYYYMMDD_HHMMSS_emg.csv 생성
- write_row: raw·amp 행 버퍼에 추가. buffer_size 도달 시 writerows 일괄 기록
- flush: 버퍼 내용 디스크 기록, I/O 횟수 감소
"""
import csv
import os
import time
from datetime import datetime

import config


class CSVLogger:
    """시리얼 raw·amp 데이터를 CSV로 저장. 버퍼링으로 I/O 횟수 감소."""

    def __init__(self, directory="data", buffer_size=600):
        """
        데이터 저장 시스템 초기화
        :param directory: CSV 파일이 저장될 폴더명
        :param buffer_size: 메모리에 유지할 데이터 행 수 (I/O 오버헤드 방지용)
        """
        # 저장 폴더 생성
        if not os.path.exists(directory):
            os.makedirs(directory)
        
        # 파일명 생성 (현재 날짜_시간_emg.csv)
        self.filename = os.path.join(directory, datetime.now().strftime("%Y%m%d_%H%M%S_emg.csv"))
        self.start_ts = time.perf_counter()

        # 파일 핸들 유지 및 작성자(Writer) 설정
        self.file = open(self.filename, 'w', newline='', encoding='utf-8')
        self.writer = csv.writer(self.file)

        # 버퍼 시스템 초기화
        self.buffer = []
        self.buffer_size = buffer_size
        self._header_written = False # 헤더 중복 작성 방지 플래그

    def _write_header(self):
        header = (
            ['Time(ms)']
            + [f'Raw_CH{i}' for i in range(config.N_CH)]
            + [f'Amp_CH{i}' for i in range(config.N_CH)]
        )
        self.writer.writerow(header)
        self._header_written = True

    def write_row(self, raw_vals, amp_vals, timestamp=None):
        """
        [수정] window.py에서 보낸 timestamp 키워드 인자를 받을 수 있도록 매개변수 추가
        """
        # 헤더가 아직 안 써졌다면 기록
        if not self._header_written:
            self._write_header()

        # [로직 개선] 밖에서 timestamp를 주면 그것을 쓰고, 없으면 여기서 직접 계산
        if timestamp is not None:
            relative_time_ms = int(round(timestamp))
        else:
            relative_time_ms = int(round((time.perf_counter() - self.start_ts) * 1000))
        
        # 데이터 가공
        try:
            processed_raw = [int(float(v)) for v in raw_vals]
            processed_amp = [int(round(float(v))) for v in amp_vals]
            
            # 버퍼에 데이터 추가 (첫 번째 열은 항상 Time(ms))
            self.buffer.append([relative_time_ms] + processed_raw + processed_amp)
            
            # 버퍼가 가득 차면 디스크에 일괄 기록 (Batch Processing)
            if len(self.buffer) >= self.buffer_size:
                self.flush()
        except (ValueError, TypeError) as e:
            print(f"Logger Error: {e}")

    def flush(self):
        """메모리 버퍼의 데이터를 실제 디스크 파일로 출력"""
        if self.buffer:
            try:
                # 여러 줄을 한 번에 쓰기 (Disk I/O 횟수 감소)
                self.writer.writerows(self.buffer) 
                self.file.flush()   # OS 수준의 버퍼 비우기 요청
                self.buffer.clear() # 메모리 초기화
            except Exception as e:
                print(f"Flush Error: {e}")

    def close(self):
        """종료 시 남은 데이터를 모두 저장하고 리소스 해제"""
        self.flush()
        if not self.file.closed:
            self.file.close()
            print(f"Saved: {self.filename}")