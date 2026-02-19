import numpy as np
import pyqtgraph as pg

import config
from config import get_ch_color, get_diag_directions, CH_OFFSET


def render(win):
    if not win.is_running or win.sample_count == 0:
        return
    is_fill_mode = win.rb_fill.isChecked()  # ui에서 선택 모드 확인(line/bar)
    unified_x_ms = win.x_axis[win.ptr % win.max_display]

    update_raw_graph(win, is_fill_mode, unified_x_ms)
    update_diag_vector(win)
    update_power_info(win)

NO_SIGNAL_VARIATION_RAW = 1.0   # 신호 없음 판단 기준
LINE_HEIGHT_PX = 1.5  # 신호 없는 구간 선의 두께


def update_raw_graph(win, is_fill_mode, unified_x_ms):
    step = 30
    gap_range = 5

    for i in range(config.N_CH):
        data_range, allowed_half_height = win.scale_manager._data_range_and_half_height()
        max_bar_pixels = 2.0 * allowed_half_height
        half_range = max(data_range / 2.0, 1.0)  # Line 모드와 동일 기준 (division by zero 방지)

        # 구간별(Step) 데이터 처리 (막대의 높이 결정)
        for chunk_start in range(0, win.max_display, step):
            chunk = win.raw_np_buf[i, chunk_start : chunk_start + step]

            if chunk.size > 0:
                ch_max = np.nanmax(chunk)
                ch_min = np.nanmin(chunk)
                # 구간 내 변화 폭 작은 경우
                if (ch_max - ch_min) < NO_SIGNAL_VARIATION_RAW:
                    new_h = LINE_HEIGHT_PX
                else:
                    # Line과 동일: data_range/2 기준 비율 (채널 간 비교 일치)
                    bar_height_raw = ch_max - ch_min
                    ratio = min(bar_height_raw / half_range, 1.0)
                    new_h = max(ratio * max_bar_pixels, LINE_HEIGHT_PX)

                win.height_buf[i, chunk_start : chunk_start + step] = new_h # 계산된 높이들 (ui 업데이트용)

        # 기준선 고정 (막대를 어디에 세울 것인가)
        base_offset = (config.N_CH - 1 - i) * CH_OFFSET + (CH_OFFSET / 2)

        # 모드 선택 
        if is_fill_mode:

            # bar 모드 선택 시 line은 그려지지 않음 
            win.past_lines[i].setVisible(False)
            win.raw_lines[i].setVisible(False)

            # step 간격으로 인덱스 추출 (막대 사이의 공간)
            sampled_indices = np.arange(0, win.max_display, step)
            display_heights = win.height_buf[i, sampled_indices].copy()

            gap_start = (win.ptr // step)   # 현재 막대 위치 계산 

            # 실시간 갱신 
            if not win.is_buf_full:
                display_heights[gap_start:] = 0   # 버퍼가 처음 채워지는 중인 경우 0
            else:
                for g in range(gap_range):
                    display_heights[(gap_start + g) % len(display_heights)] = 0    # 버퍼가 다시 순환 중인 경우 gap_range 만큼 지움 
            
            win.bar_items[i].setOpts(
                x=win.x_axis[sampled_indices],
                height=display_heights,
                y0=base_offset - (display_heights / 2),   # 막대 시작점 
                width=20,
            )
            
            # 막대그래프 보이도록 
            win.bar_items[i].setVisible(True)  

            # 가장 최신 좌표 (데이터 찍히는 곳)
            last_x = win.x_axis[(win.ptr - 1) % win.max_display] if win.sample_count > 0 else unified_x_ms
            
            # 커서 배치 
            win.cursor_rects[i].setData(pos=[(last_x, base_offset)])

        # line 그래프 모드 
        else:
            win.bar_items[i].setVisible(False)

            # 버퍼가 다 안찼을 경우 
            if not win.is_buf_full:
                win.past_lines[i].setData([], [])
                win.past_lines[i].setVisible(False)

                # 데이터 유무 확인 
                if win.ptr <= 0:
                    x, y = np.array([]), np.array([])

                # 0 ~ ptr 까지의 좌표 가져옴 
                else:
                    x = win.x_axis[: win.ptr]
                    y = win.scale_manager.get_scaled_array(i, win.raw_np_buf[i, : win.ptr])

            # 버퍼가 한바퀴 이상 돌았을 경우 
            else:
                # ptr ~ 끝 
                x_past = win.x_axis[win.ptr :]
                y_past = win.scale_manager.get_scaled_array(
                    i, win.raw_np_buf[i, win.ptr :]
                )
                win.past_lines[i].setData(x_past, y_past)
                win.past_lines[i].setVisible(True)

                # 0 ~ ptr 
                if win.ptr <= 0:
                    x, y = np.array([]), np.array([])
                else:
                    x = win.x_axis[: win.ptr]
                    y = win.scale_manager.get_scaled_array(
                        i, win.raw_np_buf[i, : win.ptr]
                    )
            # 데이터를 선 객체에 주입 
            win.raw_lines[i].setData(x, y)
            win.raw_lines[i].setVisible(True)

            # 커서 찍기 
            if win.sample_count > 0:   # 데이터 있다면 

                # 최신 위치 찾기 
                prev_idx = (win.ptr - 1) % win.max_display
                last_x = win.x_axis[prev_idx]

                # 커서 높이 결정 
                y_cursor = win.scale_manager.get_scaled_array(
                    i, np.array([win.raw_np_buf[i, prev_idx]])
                )[0]

                win.cursor_rects[i].setData(pos=[(last_x, y_cursor)])
            else:
                win.cursor_rects[i].setData(pos=[])  # 데이터 없으면 커서 지움


def update_diag_vector(win):
    directions = get_diag_directions()
    DATA_LEN = 100

    for i in range(config.N_CH):
        if win.ptr >= DATA_LEN:
            diag_raw = win.raw_np_buf[i, win.ptr - DATA_LEN : win.ptr]
        else:
            diag_raw = (
                np.concatenate(
                    [win.raw_np_buf[i, -DATA_LEN + win.ptr :], win.raw_np_buf[i, : win.ptr]]
                )
                if win.is_buf_full
                else win.raw_np_buf[i, : win.ptr]
            )

        if diag_raw.size < 2:
            continue

        scaler = win.scale_manager.scalers[i]
        ratio = win.scale_manager.get_vector_intensity(i, win.last_amp[i])
        denom = max(scaler.current_max - scaler.baseline, 30)
        wave = (diag_raw - scaler.baseline) / denom
        val = ratio * 1.4 * win.diag_plot_limit
        if val < 2.0:
            win.diag_lines[i].setData([], [])
            continue

        dx, dy = directions[i]
        perp_x, perp_y = -dy, dx
        t = np.linspace(0, val, len(diag_raw))
        wave_amp = 15 * ratio * (t / (val + 1e-6))
        res_x = (t * dx) + (perp_x * wave * wave_amp)
        res_y = (t * dy) + (perp_y * wave * wave_amp)
        res_x[0], res_y[0] = 0, 0
        win.diag_lines[i].setData(res_x, res_y)
        color = pg.mkColor(get_ch_color(i))
        color.setAlpha(int(np.clip(80 + (ratio * 175), 80, 255)))
        win.diag_lines[i].setPen(pg.mkPen(color=color, width=1.5 + (ratio * 3.0)))


def update_power_info(win):
    # 채널별 관측 범위 대비 비율(0~1)로 계산 후 0~100% 막대 높이로 사용
    ratios = np.array([
        win.scale_manager.get_vector_intensity(i, win.last_amp[i])
        for i in range(config.N_CH)
    ])
    height_pct = np.clip(ratios * 100.0, 0, 100)
    avg_pct = float(np.mean(ratios) * 100.0)
    avg_pct = np.clip(avg_pct, 0, 100)
    win.bar_item.setOpts(height=list(height_pct) + [avg_pct])
