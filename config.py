import math

CH_MODE = 4
N_CH = CH_MODE
FPS = 30
PLOT_SEC = 5.0

# [시리얼/신호 처리]
ENABLE_CSV_LOGGING = True
BASE_SAMPLES = 5
N_MULT_DEFAULT = 10

# [RAW 그래프 스케일]
CH_OFFSET = 100
RAW_Y_MIN_INIT = 55
RAW_Y_MAX_INIT = 100
RAW_ZERO_REF = 100
RAW_ZERO_THRESHOLD = 1.0
NO_SIGNAL_VARIATION_RAW = 1.0  # Bar 모드: 구간 변동폭 이하면 신호 없음(최소 높이)

# [디자인 설정]
RAW_LINE_WIDTH = 1.6
COLOR_BG = "#121826"
COLOR_CARD_BORDER = "#2a3550"
COLOR_STATUS_CONNECTED = "#2ed573"
COLOR_STATUS_DISCONNECTED = "#ff6b6b"

CH_COLORS = [
    "#ff4757",   # 빨강
    "#ff9f43",   # 주황
    "#ffdd59",   # 노랑
    "#2ed573",   # 초록
    "#1e90ff",   # 파랑
    "#a55eea",   # 보라
]
CH_4_INDICES = (0, 1, 3, 4)


# 채널별 인덱스 색상 반환 
def get_ch_color(ch_idx):
    idx = CH_4_INDICES[ch_idx] if N_CH == 4 else ch_idx
    return CH_COLORS[idx]



def get_diag_directions():
    if N_CH == 4:
        angles_deg = [225, 135, 45, 315] 
    else:
        angles_deg = [225, 180, 135, 45, 0, 315]
    return [(math.cos(math.radians(a)), math.sin(math.radians(a))) for a in angles_deg]


SUM_BAR_COLOR = "#9fb3ff"