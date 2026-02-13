import config
import numpy as np

class ChannelScaler:
    def __init__(self):
        self.current_min = config.RAW_Y_MIN_INIT
        self.current_max = config.RAW_Y_MAX_INIT
        self.baseline = (self.current_min + self.current_max) / 2

        self.baseline_alpha = 0.05
        self.decay_alpha = 0.00001

    def update(self, raw_value):
        if raw_value > self.current_max:
            self.current_max = raw_value + 5
        elif raw_value < self.current_min:
            self.current_min = raw_value - 5
        else:
            self.current_max -= (self.current_max - raw_value) * self.decay_alpha
            self.current_min += (raw_value - self.current_min) * self.decay_alpha

        target_baseline = (self.current_max + self.current_min) / 2
        self.baseline += (target_baseline - self.baseline) * self.baseline_alpha

class EMGScaleManager:
    def __init__(self, n_channels=config.N_CH):
        self.scalers = [ChannelScaler() for _ in range(n_channels)]
        # 채널별 가중치 (1.0 기본)
        self.gains = [1.0, 1.0, 1.0, 1.0]

    # 라인/바 공통: 전 채널 공통 data_range 사용 → 같은 raw 폭이 모든 채널에서 같은 크기로 보임
    def _data_range_and_half_height(self, ch_idx):
        global_min = min(s.current_min for s in self.scalers)
        global_max = max(s.current_max for s in self.scalers)
        data_range = max(global_max - global_min, 20)
        safe_margin_factor = 0.85
        allowed_half_height = (config.CH_OFFSET / 2) * safe_margin_factor
        return data_range, allowed_half_height

    def get_scaled_array(self, ch_idx, raw_array):

        scaler = self.scalers[ch_idx]
        data_range, allowed_half_height = self._data_range_and_half_height(ch_idx)
        
        # 중앙점 좌표 계산 
        base_offset = (config.N_CH - 1 - ch_idx) * config.CH_OFFSET + (config.CH_OFFSET / 2)

        # 신호 없음(0 근처)일 때는 RAW_ZERO_REF(100) 위치의 Y에 표시
        effective_raw = np.where(
            np.abs(raw_array) <= config.RAW_ZERO_THRESHOLD,
            config.RAW_ZERO_REF,
            raw_array,
        )
        # (현재값 - 기준점) / (전체 범위의 절반)
        ratios = (effective_raw - scaler.baseline) / (data_range / 2)
        ratios = np.clip(ratios, -1.0, 1.0)
        return base_offset + (ratios * allowed_half_height)

    def get_vector_intensity(self, ch_idx, amp_value):
        scaler = self.scalers[ch_idx]
        
        # 민감도 극대화: 기준선(Baseline)부터 최댓값까지만을 100% 범위로 설정
        dynamic_half_range = (scaler.current_max - scaler.baseline)
        
        # 최소 20 확보 
        if dynamic_half_range < 20: 
            dynamic_half_range = 20
            
        # 끝점 도달을 더 쉽게 하기 위해 부스트 게인 1.3배 적용
        boost_gain = 1.3 
        intensity = (amp_value / dynamic_half_range) * self.gains[ch_idx] * boost_gain
        
        # 최종 강도 
        return np.clip(intensity, 0.0, 1.0)