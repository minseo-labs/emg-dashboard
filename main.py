"""
EMG Dashboard 진입점.

실행: python main.py (프로젝트 루트에서)
- PyQt6 앱 초기화 후 EMGDashboard 메인 윈도우 표시
- 시리얼 포트 선택 → START로 수신 시작
"""
import sys
from PyQt6.QtWidgets import QApplication

from dashboard_ui import EMGDashboard

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = EMGDashboard()
    window.show()
    sys.exit(app.exec())
