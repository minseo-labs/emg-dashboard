import numpy as np
import pyqtgraph as pg

import config
from config import get_ch_color, get_diag_directions, CH_OFFSET, NO_SIGNAL_VARIATION_RAW


def render(win):
    if not win.is_running or win.sample_count == 0:
        return
    is_fill_mode = win.rb_fill.isChecked()
    unified_x_ms = win.x_axis[win.ptr % win.max_display]
    
    # 그래프 업데이트 
    update_raw_graph(win, is_fill_mode, unified_x_ms)
    update_diag_vector(win)
    update_power_info(win)

LINE_HEIGHT_PX = 1.5  # Bar 모드 신호 없을 때 막대 기본 높이


def update_raw_graph(win, is_fill_mode, unified_x_ms):

    step = 30
    gap_range = 5

    for i in range(config.N_CH):

        # 현재 Raw 스케일 범위 계산 
        data_range, allowed_half_height = win.scale_manager._data_range_and_half_height()
        max_bar_pixels = 2.0 * allowed_half_height
        half_range = max(data_range / 2.0, 1.0)

        # 구간별(step) 진폭 계산 
        for chunk_start in range(0, win.max_display, step):
            chunk = win.raw_np_buf[i, chunk_start : chunk_start + step]  # 30개씩 자름 

            if chunk.size > 0:

                # 구간 내 최대, 최소 
                ch_max = np.nanmax(chunk)
                ch_min = np.nanmin(chunk)

                # 변동폭 너무 작으면 신호 없음 처리 
                if (ch_max - ch_min) < NO_SIGNAL_VARIATION_RAW:
                    new_h = LINE_HEIGHT_PX
                else:
                    bar_height_raw = ch_max - ch_min
                    ratio = min(bar_height_raw / half_range, 1.0)   # 현재 스케일 대비 비율 
                    new_h = max(ratio * max_bar_pixels, LINE_HEIGHT_PX) 

                win.height_buf[i, chunk_start : chunk_start + step] = new_h

        # 채널별 y축 오프셋 
        base_offset = (config.N_CH - 1 - i) * CH_OFFSET + (CH_OFFSET / 2)

        # fill 모드 
        if is_fill_mode:
            win.past_lines[i].setVisible(False)
            win.raw_lines[i].setVisible(False)

            # 30 간격으로 샘플링된 막대 위치 인덱스 
            sampled_indices = np.arange(0, win.max_display, step)
            display_heights = win.height_buf[i, sampled_indices].copy()

            #  몇 번째 구간(몇 번째 막대)인지
            gap_start = (win.ptr // step)

            # 링 버퍼 안 찬 경우 
            if not win.is_buf_full:
                display_heights[gap_start:] = 0
            else:
                # 링 버퍼 경계 공백 만듦 
                for g in range(gap_range):
                    display_heights[(gap_start + g) % len(display_heights)] = 0
            
            # 실제 막대 그래프에 적용 
            win.bar_items[i].setOpts(
                x=win.x_axis[sampled_indices],
                height=display_heights,
                y0=base_offset - (display_heights / 2),   # 막대 시작점 
                width=20,
            )
            
            win.bar_items[i].setVisible(True)

            # 커서 위치 계산 
            last_x = win.x_axis[(win.ptr - 1) % win.max_display] if win.sample_count > 0 else unified_x_ms
            
            # 현재 위치 표시 
            win.cursor_rects[i].setData(pos=[(last_x, base_offset)])


        # line 모드 
        else:
            win.bar_items[i].setVisible(False)

            # 링 버퍼가 안 찼을 때 
            if not win.is_buf_full:
                win.past_lines[i].setData([], [])
                win.past_lines[i].setVisible(False)

                if win.ptr <= 0:
                    x, y = np.array([]), np.array([])

                # 0 ~ ptr 구간 좌표
                else:
                    x = win.x_axis[: win.ptr]
                    y = win.scale_manager.get_scaled_array(i, win.raw_np_buf[i, : win.ptr])

            # 링 버퍼 한 바퀴 이상: past(ptr~끝) + current(0~ptr)
            else:
                # ptr 이후 구간은 과거 데이터 
                x_past = win.x_axis[win.ptr :]
                y_past = win.scale_manager.get_scaled_array(
                    i, win.raw_np_buf[i, win.ptr :]
                )
                win.past_lines[i].setData(x_past, y_past)
                win.past_lines[i].setVisible(True)

                # 0 ~ ptr 구간은 현재 구간 
                if win.ptr <= 0:
                    x, y = np.array([]), np.array([])
                else:
                    x = win.x_axis[: win.ptr]
                    y = win.scale_manager.get_scaled_array(
                        i, win.raw_np_buf[i, : win.ptr]
                    )
            # 현재 구간 라인 표시 
            win.raw_lines[i].setData(x, y)
            win.raw_lines[i].setVisible(True)

            
            # 커서 표시 
            if win.sample_count > 0:

                prev_idx = (win.ptr - 1) % win.max_display   # 현재 샘플 인덱스 계산 

                # x, y 좌표 계산 
                last_x = win.x_axis[prev_idx]
                y_cursor = win.scale_manager.get_scaled_array(
                    i, np.array([win.raw_np_buf[i, prev_idx]])
                )[0]

                win.cursor_rects[i].setData(pos=[(last_x, y_cursor)])  # 커서 위치 설정 
            else:
                win.cursor_rects[i].setData(pos=[])


def update_diag_vector(win):

    directions = get_diag_directions()
    DATA_LEN = 100

    for i in range(config.N_CH):
        if win.ptr >= DATA_LEN:
            diag_raw = win.raw_np_buf[i, win.ptr - DATA_LEN : win.ptr]
        
        # ptr이 DATA_LEN보다 작을 때 
        else:
            diag_raw = (
                # 버퍼가 한 바퀴 돈 경우에는 '뒤+앞'을 이어 붙임 
                np.concatenate(
                    [win.raw_np_buf[i, -DATA_LEN + win.ptr :], win.raw_np_buf[i, : win.ptr]]
                )
                if win.is_buf_full
                # 버퍼가 안찼으면 현재 데이터까지만 사용 
                else win.raw_np_buf[i, : win.ptr]
            )

        if diag_raw.size < 2:
            continue


        # ratio(강도) 및 파형 정규화 
        scaler = win.scale_manager.scalers[i]

        # 채널별 진폭을 0~1로 변환 
        ratio = win.scale_manager.get_vector_intensity(i, win.last_amp[i])
        
        # 신호 크기(정규화된 파형)
        denom = max(scaler.current_max - scaler.baseline, 30)
        wave = (diag_raw - scaler.baseline) / denom


        # 벡터 길이 val 계산 
        val = ratio * 1.4 * win.diag_plot_limit

        # 너무 짧으면 안그림 
        if val < 2.0:
            win.diag_lines[i].setData([], [])
            continue

        # 방향 벡터 & 수직 벡터 계산 
        dx, dy = directions[i]
        perp_x, perp_y = -dy, dx

        # 진행 거리 t (대각선 방향 진행 거리)
        t = np.linspace(0, val, len(diag_raw))

        # 물결의 흔들림 크기 
        wave_amp = 15 * ratio * (t / (val + 1e-6))
        
        # 최종 x, y 좌표 계산 (뻗는 방향 + 수직 방향)
        res_x = (t * dx) + (perp_x * wave * wave_amp)
        res_y = (t * dy) + (perp_y * wave * wave_amp)

        # 시작점 (원점)
        res_x[0], res_y[0] = 0, 0

        # 선 데이터 적용 
        win.diag_lines[i].setData(res_x, res_y)

        # 투명도 
        color = pg.mkColor(get_ch_color(i))
        color.setAlpha(int(np.clip(80 + (ratio * 175), 80, 255)))

        # 선 굵기 
        win.diag_lines[i].setPen(pg.mkPen(color=color, width=1.5 + (ratio * 3.0)))


# PWR 그래프 막대 높이 갱신 
def update_power_info(win):

    ratios = np.array([
        win.scale_manager.get_vector_intensity(i, win.last_amp[i])
        for i in range(config.N_CH)
    ])
    height_pct = np.clip(ratios * 100.0, 0, 100)
    avg_pct = float(np.mean(ratios) * 100.0)  # AVG 막대용 평균 구하기 
    avg_pct = np.clip(avg_pct, 0, 100)
    win.bar_item.setOpts(height=list(height_pct) + [avg_pct])
