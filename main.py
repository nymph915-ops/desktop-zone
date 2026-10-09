import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, QPoint, QFileInfo, QSize
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QSizeGrip,
    QFileIconProvider,
    QMessageBox,
    QInputDialog,
)

if getattr(sys, "frozen", False):
    app_data = Path(os.environ.get("APPDATA", str(Path.home())))
    APP_DIR = app_data / "DesktopZones"
else:
    APP_DIR = Path(__file__).resolve().parent

APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP_DIR / "zones_config.json"


def display_name(path):
    name = Path(path).name
    if name.lower().endswith(".lnk"):
        return name[:-4]
    return name


def unique_backup_path(path):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = path.with_name(f"{path.stem}.broken_{timestamp}{path.suffix}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(
            f"{path.stem}.broken_{timestamp}_{counter}{path.suffix}"
        )
        counter += 1
    return candidate


class ZoneHeader(QWidget):
    def __init__(self, zone):
        super().__init__(zone)
        self.zone = zone
        self._dragging = False
        self._drag_offset = QPoint()
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = (
                event.globalPosition().toPoint()
                - self.zone.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.zone.move(
                event.globalPosition().toPoint() - self._drag_offset
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        was_dragging = self._dragging
        self._dragging = False
        super().mouseReleaseEvent(event)
        if was_dragging and not self.zone._initializing:
            self.zone.manager.save_config()


class DropListWidget(QListWidget):
    def __init__(self, zone):
        super().__init__(zone)
        self.zone = zone

        # IconMode 설정 및 아이콘 격자 배치
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(48, 48))
        self.setGridSize(QSize(76, 80))
        self.setMovement(QListWidget.Movement.Static)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setWordWrap(True)

        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDropIndicatorShown(False)  # IconMode에서 드롭 안정성을 위해 Indicator 비활성화
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setSpacing(4)
        self.itemDoubleClicked.connect(self.open_item)

        # 스타일시트 설정 (투명도 90/255)
        self.setStyleSheet("""
            QListWidget {
                background: rgba(255, 255, 255, 90);
                border: 1px solid rgba(80, 80, 80, 80);
                border-radius: 5px;
                padding: 2px;
                color: #202020;
            }
            QListWidget::item {
                padding: 4px;
                border-radius: 4px;
            }
            QListWidget::item:selected {
                background: rgba(201, 226, 255, 180);
                color: #111111;
            }
        """)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Delete:
            selected_items = self.selectedItems()
            if selected_items:
                for item in selected_items:
                    row = self.row(item)
                    self.takeItem(row)
                self.zone.manager.save_config()
                event.accept()
                return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        if not event.mimeData().hasUrls():
            event.ignore()
            return

        added_any = False
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.exists(path):
                    self.zone.add_path(path)
                    added_any = True

        if added_any:
            event.acceptProposedAction()
        else:
            event.ignore()

    def open_item(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return

        if not os.path.exists(path):
            QMessageBox.warning(
                self,
                "경로를 찾을 수 없음",
                f"파일 또는 폴더가 존재하지 않습니다.\n\n{path}",
            )
            return

        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        if not opened:
            QMessageBox.warning(
                self,
                "열기 실패",
                f"파일 또는 폴더를 열지 못했습니다.\n\n{path}",
            )


class ZoneWidget(QWidget):
    def __init__(
        self,
        manager,
        zone_id,
        title="구역",
        x=100,
        y=100,
        width=260,
        height=320,
    ):
        super().__init__(None)

        self.manager = manager
        self.zone_id = zone_id
        self.title_text = title
        self.icon_provider = QFileIconProvider()
        self._initializing = True

        self.resize_save_timer = QTimer(self)
        self.resize_save_timer.setSingleShot(True)
        self.resize_save_timer.setInterval(300)
        self.resize_save_timer.timeout.connect(self.save_after_resize)

        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(190, 160)
        self.setAcceptDrops(True)
        self.setGeometry(x, y, width, height)

        self.init_ui()
        self._initializing = False

    def init_ui(self):
        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(2, 2, 2, 2)
        self.outer_layout.setSpacing(2)

        self.panel = QFrame(self)
        self.panel.setObjectName("zonePanel")
        self.panel.setStyleSheet("""
            QFrame#zonePanel {
                background: rgba(225, 235, 245, 140);
                border: 1px solid rgba(70, 90, 110, 140);
                border-radius: 6px;
            }
        """)

        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.setContentsMargins(3, 3, 3, 2)
        self.panel_layout.setSpacing(3)

        self.header = ZoneHeader(self)
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(2, 0, 0, 0)
        self.header_layout.setSpacing(2)

        self.title_label = QLabel(self.title_text)
        self.title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.title_label.setStyleSheet("""
            QLabel {
                background: transparent;
                color: #172536;
                font-weight: bold;
                font-size: 13px;
            }
        """)

        self.rename_button = QPushButton("✎")
        self.rename_button.setFixedSize(22, 22)
        self.rename_button.setToolTip("구역 이름 변경")
        self.rename_button.clicked.connect(self.rename_zone)

        self.delete_button = QPushButton("×")
        self.delete_button.setFixedSize(22, 22)
        self.delete_button.setToolTip("구역 삭제")
        self.delete_button.clicked.connect(
            lambda: self.manager.remove_zone(self.zone_id)
        )

        for button in (self.rename_button, self.delete_button):
            button.setStyleSheet("""
                QPushButton {
                    background: rgba(255, 255, 255, 160);
                    border: 1px solid rgba(80, 80, 80, 100);
                    border-radius: 3px;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background: rgba(255, 255, 255, 240);
                }
            """)

        self.header_layout.addWidget(self.title_label)
        self.header_layout.addStretch()
        self.header_layout.addWidget(self.rename_button)
        self.header_layout.addWidget(self.delete_button)

        self.list_widget = DropListWidget(self)

        self.bottom_layout = QHBoxLayout()
        self.bottom_layout.setContentsMargins(0, 0, 0, 0)
        self.bottom_layout.addStretch()

        self.size_grip = QSizeGrip(self.panel)
        self.size_grip.setFixedSize(14, 14)
        self.bottom_layout.addWidget(self.size_grip)

        self.panel_layout.addWidget(self.header)
        self.panel_layout.addWidget(self.list_widget, 1)
        self.panel_layout.addLayout(self.bottom_layout)
        self.outer_layout.addWidget(self.panel)

    def add_path(self, path):
        normalized = os.path.normcase(os.path.abspath(path))

        for index in range(self.list_widget.count()):
            existing = self.list_widget.item(index).data(
                Qt.ItemDataRole.UserRole
            )
            if existing and os.path.normcase(os.path.abspath(existing)) == normalized:
                return

        item = QListWidgetItem(display_name(path))
        item.setData(Qt.ItemDataRole.UserRole, path)

        try:
            item.setIcon(self.icon_provider.icon(QFileInfo(path)))
        except Exception:
            pass

        item.setToolTip(path)
        self.list_widget.addItem(item)
        self.manager.save_config()

    def get_paths(self):
        return [
            self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list_widget.count())
        ]

    def load_paths(self, paths):
        self.list_widget.clear()

        for path in paths:
            if not isinstance(path, str) or not path:
                continue

            exists = os.path.exists(path)
            label = display_name(path)
            if not exists:
                label += "\n(경로 없음)"

            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path if exists else f"경로를 찾을 수 없습니다:\n{path}")

            try:
                item.setIcon(self.icon_provider.icon(QFileInfo(path)))
            except Exception:
                pass

            self.list_widget.addItem(item)

    def rename_zone(self):
        new_name, accepted = QInputDialog.getText(
            self,
            "구역 이름 변경",
            "새 구역 이름:",
            text=self.title_text,
        )

        if accepted and new_name.strip():
            self.title_text = new_name.strip()
            self.title_label.setText(self.title_text)
            self.manager.save_config()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._initializing:
            self.resize_save_timer.start()

    def save_after_resize(self):
        if not self._initializing:
            self.manager.save_config()

    def closeEvent(self, event):
        self.resize_save_timer.stop()
        event.accept()


class ZoneManager(QWidget):
    def __init__(self):
        super().__init__()

        self.zones = {}
        self.next_zone_id = 1
        self._loading = False

        self.setWindowTitle("바탕화면 구역 관리")
        self.setMinimumWidth(270)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
        )

        self.init_ui()
        self.load_config()

        for zone in self.zones.values():
            zone.show()

    def init_ui(self):
        layout = QVBoxLayout(self)

        heading = QLabel("바탕화면 구역 관리")
        heading.setStyleSheet("font-size: 15px; font-weight: bold;")

        description = QLabel(
            "파일·폴더·바로가기를 구역 안으로 끌어 놓으세요.\n"
            "아이콘을 더블클릭하면 실행되며, Delete 키로 구역에서 제외할 수 있습니다."
        )
        description.setWordWrap(True)

        self.add_button = QPushButton("＋ 구역 추가")
        self.add_button.clicked.connect(lambda: self.add_zone())

        self.save_button = QPushButton("설정 저장")
        self.save_button.clicked.connect(self.save_config)

        layout.addWidget(heading)
        layout.addWidget(description)
        layout.addWidget(self.add_button)
        layout.addWidget(self.save_button)

    def calculate_new_position(self):
        base_x = 150
        base_y = 150
        step = 60

        count = len(self.zones)
        x = base_x + (count * step) % 500
        y = base_y + (count * step) % 350

        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            if x + 260 > geo.right():
                x = geo.left() + 50
            if y + 320 > geo.bottom():
                y = geo.top() + 50

        return (x, y, 260, 320)

    def add_zone(self, title=None, geometry=None, paths=None):
        zone_id = self.next_zone_id
        self.next_zone_id += 1

        if geometry is None:
            geometry = self.calculate_new_position()

        if title is None:
            title = f"구역 {zone_id}"

        zone = ZoneWidget(self, zone_id, title, *geometry)
        self.zones[zone_id] = zone

        if paths:
            zone.load_paths(paths)

        zone.show()
        zone.raise_()
        zone.activateWindow()

        if not self._loading:
            self.save_config()

        return zone

    def remove_zone(self, zone_id):
        zone = self.zones.pop(zone_id, None)
        if zone is None:
            return

        zone.resize_save_timer.stop()
        zone.close()
        zone.deleteLater()

        self.save_config()

    def save_config(self):
        if self._loading:
            return

        data = {
            "next_zone_id": self.next_zone_id,
            "zones": [],
        }

        for zone_id, zone in self.zones.items():
            geometry = zone.geometry()
            data["zones"].append({
                "id": zone_id,
                "title": zone.title_text,
                "geometry": [
                    geometry.x(),
                    geometry.y(),
                    geometry.width(),
                    geometry.height(),
                ],
                "paths": zone.get_paths(),
            })

        temp_file = CONFIG_FILE.with_suffix(".tmp")

        try:
            with open(temp_file, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())

            os.replace(temp_file, CONFIG_FILE)

        except Exception as error:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except Exception:
                pass

            QMessageBox.warning(
                self,
                "설정 저장 오류",
                f"설정을 저장하지 못했습니다.\n\n{error}",
            )

    def load_config(self):
        if not CONFIG_FILE.exists():
            return

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)

            if not isinstance(data, dict):
                raise ValueError("설정 파일의 최상위 형식이 올바르지 않습니다.")

            saved_zones = data.get("zones", [])
            if not isinstance(saved_zones, list):
                raise ValueError("구역 목록 형식이 올바르지 않습니다.")

        except Exception as error:
            backup = unique_backup_path(CONFIG_FILE)
            backup_message = ""

            try:
                shutil.copy2(CONFIG_FILE, backup)
                backup_message = f"\n백업 파일: {backup}"
            except Exception as backup_error:
                backup_message = f"\n백업 실패: {backup_error}"

            QMessageBox.warning(
                self,
                "설정 파일 오류",
                "설정 파일을 읽을 수 없습니다. 기본 설정으로 시작합니다.\n"
                f"오류: {error}{backup_message}",
            )
            return

        self._loading = True
        try:
            used_ids = set()

            for saved in saved_zones:
                if not isinstance(saved, dict):
                    continue

                try:
                    zone_id = int(saved.get("id", self.next_zone_id))
                    if zone_id < 1 or zone_id in used_ids:
                        zone_id = self.next_zone_id

                    title = str(saved.get("title", f"구역 {zone_id}"))

                    geometry = saved.get("geometry", [100, 100, 260, 320])
                    if not isinstance(geometry, (list, tuple)) or len(geometry) != 4:
                        geometry = [100, 100, 260, 320]

                    geometry = [int(value) for value in geometry]
                    geometry[2] = max(190, geometry[2])
                    geometry[3] = max(160, geometry[3])

                    paths = saved.get("paths", [])
                    if not isinstance(paths, list):
                        paths = []

                    used_ids.add(zone_id)
                    self.next_zone_id = max(self.next_zone_id, zone_id + 1)

                    zone = ZoneWidget(self, zone_id, title, *geometry)
                    self.zones[zone_id] = zone
                    zone.load_paths(paths)

                except Exception:
                    continue

            try:
                saved_next_id = int(data.get("next_zone_id", 1))
                self.next_zone_id = max(self.next_zone_id, saved_next_id, 1)
            except Exception:
                pass

        finally:
            self._loading = False

    def closeEvent(self, event):
        self.save_config()

        for zone in list(self.zones.values()):
            zone.close()

        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Desktop Zones")

    manager = ZoneManager()
    manager.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
