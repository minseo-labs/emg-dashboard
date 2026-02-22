import sys
from PyQt6.QtWidgets import QApplication

from dashboard_ui import EMGDashboard

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = EMGDashboard()
    window.show()
    sys.exit(app.exec())
