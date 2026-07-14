import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import keyring
import requests
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Быстрые заметки"
BASE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "QuickNotes"
else:
    DATA_DIR = BASE_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_NOTES_DIR = Path(os.environ.get("QUICK_NOTES_DIR", DATA_DIR / "notes_data"))
CONFIG_FILE = DATA_DIR / "settings.json"
KEYRING_SERVICE = "QuickNotesWebDAV"


def tray_icon() -> QIcon:
    """Draw a small icon so the app does not need an external image file."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#0082c9"))
    painter.drawRoundedRect(8, 5, 48, 54, 10, 10)
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(17, 18, 30, 4, 2, 2)
    painter.drawRoundedRect(17, 29, 24, 4, 2, 2)
    painter.drawRoundedRect(17, 40, 28, 4, 2, 2)
    painter.end()
    return QIcon(pixmap)


class ClickableList(QListWidget):
    empty_clicked = Signal()

    def mousePressEvent(self, event):
        if not self.indexAt(event.position().toPoint()).isValid():
            self.clearSelection()
            self.empty_clicked.emit()
        super().mousePressEvent(event)


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class NotesLoader(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.operation()
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        else:
            self.signals.finished.emit(result)


class NotesWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.config = self._load_config()
        self.storage_mode = self.config.get("storage_mode", "local")
        self.notes_dir = Path(self.config.get("notes_dir", DEFAULT_NOTES_DIR)).expanduser()
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.webdav_url = self.config.get("webdav_url", "")
        self.webdav_user = self.config.get("webdav_user", "")
        self.current_name: str | None = None
        self.current_dir = ""
        self.available_names: set[str] = set()
        self.note_names: list[tuple[str, str]] = []
        self.loading_notes = False
        self.load_generation = 0
        self.thread_pool = QThreadPool.globalInstance()
        self.active_workers: set[NotesLoader] = set()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(tray_icon())
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(400, 600)
        self._build_ui()
        self.refresh_notes()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        panel = QFrame()
        panel.setObjectName("panel")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(18, 70, 105, 55))
        panel.setGraphicsEffect(shadow)
        outer.addWidget(panel)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(22, 22, 22, 22)

        self.pages = QStackedWidget()
        panel_layout.addWidget(self.pages)
        self.pages.addWidget(self._build_list_page())
        self.pages.addWidget(self._build_editor_page())
        self.pages.addWidget(self._build_settings_page())

        self.setStyleSheet("""
            QWidget { font-family: "Segoe UI"; font-size: 14px; color: #193247; }
            #panel { background: #f8fbff; border: 1px solid #d6e9f7; border-radius: 20px; }
            QLineEdit, QTextEdit { background: #ffffff; border: 1px solid #d5e5f0;
                border-radius: 11px; padding: 10px 12px; selection-background-color: #1686d9; }
            QLineEdit:hover, QTextEdit:hover { border-color: #a9d1ec; }
            QLineEdit:focus, QTextEdit:focus { border: 2px solid #1686d9; padding: 9px 11px; }
            QComboBox { background: #ffffff; color: #155a8a; border: 1px solid #d5e5f0;
                border-radius: 11px; padding: 10px 36px 10px 12px; }
            QComboBox:hover, QComboBox:focus { border-color: #1686d9; }
            QComboBox::drop-down { width: 34px; border: none; background: #e9f5fd;
                border-top-right-radius: 10px; border-bottom-right-radius: 10px; }
            QComboBox QAbstractItemView { background: #ffffff; color: #155a8a;
                border: 1px solid #c9e2f3; selection-background-color: #1686d9;
                selection-color: #ffffff; outline: none; padding: 5px; }
            QListWidget { border: none; background: transparent; outline: none; padding-top: 5px; }
            QListWidget::item { margin: 2px 0; padding: 12px 11px; border: none;
                border-radius: 10px; color: #29485e; }
            QListWidget::item:hover { background: #edf7fe; color: #0c6dac; }
            QListWidget::item:selected { background: #dceffd; color: #075d97; }
            QPushButton { border: 1px solid transparent; background: #e8f4fc; color: #12679f;
                border-radius: 10px; padding: 9px 14px; font-weight: 600; }
            QPushButton:hover { background: #d7edfb; border-color: #b9ddf4; }
            QPushButton:pressed { background: #c8e5f8; }
            QPushButton#addButton, QPushButton#saveButton, QPushButton#settingsSaveButton {
                background: #1686d9; color: white; border: none; font-weight: 700; }
            QPushButton#addButton:hover, QPushButton#saveButton:hover,
            QPushButton#settingsSaveButton:hover { background: #0874c2; }
            QPushButton#addButton:pressed, QPushButton#saveButton:pressed,
            QPushButton#settingsSaveButton:pressed { background: #0667ac; }
            QPushButton#settingsButton { font-size: 17px; background: #edf6fc; }
            QLabel#title { font-size: 23px; font-weight: 700; color: #123b57; }
            QLabel#subtitle { font-size: 12px; color: #6c8799; padding-bottom: 3px; }
            QLabel#empty { color: #7890a0; }
            QMessageBox { background: #f8fbff; }
            QMessageBox QLabel { background: transparent; color: #193247; min-width: 260px; }
            QMessageBox QPushButton { background: #1686d9; color: #ffffff;
                min-width: 82px; font-weight: 600; }
            QMessageBox QPushButton:hover { background: #0874c2; }
        """)

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.list_title = QLabel("Заметки")
        self.list_title.setObjectName("title")
        add = QPushButton("+")
        add.setObjectName("addButton")
        add.setFixedSize(38, 38)
        add.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        add.setToolTip("Новая заметка")
        add.clicked.connect(self.new_note)
        settings = QPushButton("⚙")
        settings.setObjectName("settingsButton")
        settings.setFixedSize(50, 38)
        settings.setToolTip("Настройки")
        settings.clicked.connect(self.open_settings)
        header.addWidget(self.list_title)
        header.addStretch()
        header.addWidget(settings)
        header.addWidget(add)
        layout.addLayout(header)

        self.list_subtitle = QLabel("Корневая папка")
        self.list_subtitle.setObjectName("subtitle")
        layout.addWidget(self.list_subtitle)
        layout.addSpacing(5)

        self.search = QLineEdit()
        self.search.setPlaceholderText("⌕  Поиск в этой папке…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_notes)
        layout.addWidget(self.search)

        self.notes_list = ClickableList()
        self.notes_list.setSpacing(2)
        self.notes_list.itemClicked.connect(self.open_note)
        layout.addWidget(self.notes_list)

        self.empty_label = QLabel("Заметок пока нет. Нажмите +, чтобы создать первую.")
        self.empty_label.setObjectName("empty")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)
        return page

    def _build_editor_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        back = QPushButton("← Назад")
        back.clicked.connect(self.go_back)
        save = QPushButton("Сохранить")
        save.setObjectName("saveButton")
        save.clicked.connect(self.save_note)
        header.addWidget(back)
        header.addStretch()
        header.addWidget(save)
        layout.addLayout(header)

        self.note_title = QLineEdit()
        self.note_title.setPlaceholderText("Название заметки")
        self.note_title.returnPressed.connect(self.save_note)
        layout.addWidget(self.note_title)

        self.note_text = QTextEdit()
        self.note_text.setPlaceholderText("Текст заметки…")
        self.note_text.setAcceptRichText(False)
        layout.addWidget(self.note_text)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        back = QPushButton("← Назад")
        back.clicked.connect(self.go_back)
        title = QLabel("Настройки")
        title.setObjectName("title")
        header.addWidget(back)
        header.addStretch()
        header.addWidget(title)
        layout.addLayout(header)
        layout.addSpacing(18)

        layout.addWidget(QLabel("Режим хранения"))
        self.storage_mode_box = QComboBox()
        self.storage_mode_box.addItem("Локальная папка", "local")
        self.storage_mode_box.addItem("WebDAV", "webdav")
        self.storage_mode_box.currentIndexChanged.connect(self.update_settings_fields)
        layout.addWidget(self.storage_mode_box)
        layout.addSpacing(10)

        self.local_settings = QWidget()
        local_layout = QVBoxLayout(self.local_settings)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.addWidget(QLabel("Папка с заметками"))
        path_row = QHBoxLayout()
        self.folder_path = QLineEdit()
        self.folder_path.setPlaceholderText("Путь к папке")
        browse = QPushButton("…")
        browse.setFixedWidth(42)
        browse.setToolTip("Выбрать папку")
        browse.clicked.connect(self.choose_folder)
        path_row.addWidget(self.folder_path)
        path_row.addWidget(browse)
        local_layout.addLayout(path_row)

        hint = QLabel("В списке будут видны подпапки и Markdown-файлы (.md).")
        hint.setObjectName("empty")
        hint.setWordWrap(True)
        local_layout.addWidget(hint)
        layout.addWidget(self.local_settings)

        self.webdav_settings = QWidget()
        dav_layout = QVBoxLayout(self.webdav_settings)
        dav_layout.setContentsMargins(0, 0, 0, 0)
        dav_layout.addWidget(QLabel("WebDAV URL папки"))
        self.webdav_url_input = QLineEdit()
        self.webdav_url_input.setPlaceholderText(
            "https://cloud.example.com/remote.php/dav/files/user/Notes/"
        )
        dav_layout.addWidget(self.webdav_url_input)
        dav_layout.addWidget(QLabel("Имя пользователя"))
        self.webdav_user_input = QLineEdit()
        dav_layout.addWidget(self.webdav_user_input)
        dav_layout.addWidget(QLabel("Пароль приложения"))
        self.webdav_password_input = QLineEdit()
        self.webdav_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.webdav_password_input.setPlaceholderText("Оставьте пустым, чтобы не менять")
        dav_layout.addWidget(self.webdav_password_input)
        test = QPushButton("Проверить подключение")
        test.clicked.connect(self.test_webdav)
        dav_layout.addWidget(test)
        dav_hint = QLabel("Используйте пароль приложения Nextcloud, а не основной пароль.")
        dav_hint.setObjectName("empty")
        dav_hint.setWordWrap(True)
        dav_layout.addWidget(dav_hint)
        layout.addWidget(self.webdav_settings)

        save = QPushButton("Сохранить настройки")
        save.setObjectName("settingsSaveButton")
        save.clicked.connect(self.save_settings)
        layout.addWidget(save)
        layout.addStretch()
        return page

    @staticmethod
    def _load_config() -> dict:
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        return {"storage_mode": "local", "notes_dir": str(DEFAULT_NOTES_DIR)}

    def open_settings(self):
        self.folder_path.setText(str(self.notes_dir))
        self.webdav_url_input.setText(self.webdav_url)
        self.webdav_user_input.setText(self.webdav_user)
        self.webdav_password_input.clear()
        index = self.storage_mode_box.findData(self.storage_mode)
        self.storage_mode_box.setCurrentIndex(max(0, index))
        self.update_settings_fields()
        self.pages.setCurrentIndex(2)

    def choose_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self, "Выберите папку с заметками", self.folder_path.text() or str(self.notes_dir)
        )
        if selected:
            self.folder_path.setText(selected)

    def update_settings_fields(self, *_):
        is_local = self.storage_mode_box.currentData() == "local"
        self.local_settings.setVisible(is_local)
        self.webdav_settings.setVisible(not is_local)

    @staticmethod
    def normalize_webdav_url(url: str) -> str:
        return url.strip().rstrip("/") + "/"

    @staticmethod
    def credential_key(url: str, user: str) -> str:
        return f"{url}|{user}"

    def webdav_password(self, url: str | None = None, user: str | None = None) -> str:
        try:
            return keyring.get_password(
                KEYRING_SERVICE,
                self.credential_key(url or self.webdav_url, user or self.webdav_user),
            ) or ""
        except keyring.errors.KeyringError:
            return ""

    def webdav_request(self, method: str, name: str = "", **kwargs):
        url = self.webdav_url + quote(name) if name else self.webdav_url
        response = requests.request(
            method,
            url,
            auth=(self.webdav_user, self.webdav_password()),
            timeout=15,
            **kwargs,
        )
        response.raise_for_status()
        return response

    def entry_path(self, name: str) -> str:
        return f"{self.current_dir}/{name}" if self.current_dir else name

    def update_location_header(self):
        self.list_title.setText(Path(self.current_dir).name if self.current_dir else "Заметки")
        location = self.current_dir.replace("/", "  ›  ") if self.current_dir else "Корневая папка"
        storage = "WebDAV" if self.storage_mode == "webdav" else "Локально"
        self.list_subtitle.setText(f"{storage}  •  {location}")

    def test_webdav(self):
        url = self.normalize_webdav_url(self.webdav_url_input.text())
        user = self.webdav_user_input.text().strip()
        password = self.webdav_password_input.text() or self.webdav_password(url, user)
        if not urlparse(url).scheme or not user or not password:
            QMessageBox.information(self, APP_NAME, "Заполните URL, имя пользователя и пароль.")
            return
        try:
            response = requests.request(
                "PROPFIND", url, auth=(user, password), headers={"Depth": "0"}, timeout=15
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            QMessageBox.warning(self, APP_NAME, f"Подключиться не удалось:\n{exc}")
            return
        QMessageBox.information(self, APP_NAME, "Подключение к WebDAV установлено.")

    def save_settings(self):
        mode = self.storage_mode_box.currentData()
        if mode == "webdav":
            url = self.normalize_webdav_url(self.webdav_url_input.text())
            user = self.webdav_user_input.text().strip()
            password = self.webdav_password_input.text()
            if not urlparse(url).scheme or not user:
                QMessageBox.information(self, APP_NAME, "Укажите WebDAV URL и имя пользователя.")
                return
            if not password and not self.webdav_password(url, user):
                QMessageBox.information(self, APP_NAME, "Укажите пароль приложения.")
                return
            try:
                if password:
                    keyring.set_password(KEYRING_SERVICE, self.credential_key(url, user), password)
                config = {
                    "storage_mode": "webdav", "notes_dir": str(self.notes_dir),
                    "webdav_url": url, "webdav_user": user,
                }
                CONFIG_FILE.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except (OSError, keyring.errors.KeyringError) as exc:
                QMessageBox.warning(self, APP_NAME, f"Не удалось сохранить настройки:\n{exc}")
                return
            self.storage_mode, self.webdav_url, self.webdav_user = mode, url, user
            self.current_dir = ""
            self.go_back()
            return

        raw_path = self.folder_path.text().strip().strip('"')
        if not raw_path:
            QMessageBox.information(self, APP_NAME, "Укажите путь к папке с заметками.")
            return
        new_dir = Path(raw_path).expanduser()
        try:
            new_dir.mkdir(parents=True, exist_ok=True)
            if not new_dir.is_dir():
                raise OSError("указанный путь не является папкой")
            config = {
                "storage_mode": "local", "notes_dir": str(new_dir),
                "webdav_url": self.webdav_url, "webdav_user": self.webdav_user,
            }
            CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"Не удалось использовать эту папку:\n{exc}")
            return
        self.notes_dir = new_dir
        self.storage_mode = "local"
        self.current_dir = ""
        self.go_back()

    def refresh_notes(self, *_):
        self.load_generation += 1
        generation = self.load_generation
        self.notes_list.clear()
        self.update_location_header()
        if self.storage_mode == "webdav":
            self.loading_notes = True
            self.empty_label.setText("Загрузка заметок…")
            self.empty_label.setVisible(True)
            self.notes_list.setVisible(False)
            # Windows Credential Manager is accessed on the GUI thread. Only the
            # HTTP request and XML parsing run in the worker thread.
            url = self.webdav_url
            user = self.webdav_user
            password = self.webdav_password()
            if not password:
                self.notes_load_failed(
                    "Пароль WebDAV не найден. Сохраните его заново в настройках.", generation
                )
                return
            current_dir = self.current_dir
            worker = NotesLoader(
                lambda: self.fetch_webdav_names(url, user, password, current_dir)
            )
            self.active_workers.add(worker)
            worker.signals.finished.connect(
                lambda names, g=generation, w=worker: self.finish_notes_worker(w, names, g)
            )
            worker.signals.failed.connect(
                lambda message, g=generation, w=worker: self.fail_notes_worker(w, message, g)
            )
            self.thread_pool.start(worker)
            return

        try:
            directory = self.notes_dir / self.current_dir
            folders = sorted(
                (("folder", path.name) for path in directory.iterdir() if path.is_dir()),
                key=lambda entry: entry[1].casefold(),
            )
            notes = sorted(
                (("note", path.name) for path in directory.iterdir()
                 if path.is_file() and path.suffix.lower() == ".md"),
                key=lambda entry: entry[1].casefold(),
            )
            names = folders + notes
        except OSError as exc:
            self.notes_load_failed(str(exc), generation)
            return

        self.notes_loaded(names, generation)

    @staticmethod
    def fetch_webdav_names(
        url: str, user: str, password: str, current_dir: str
    ) -> list[tuple[str, str]]:
        request_url = url + quote(current_dir) + ("/" if current_dir else "")
        response = requests.request(
            "PROPFIND",
            request_url,
            auth=(user, password),
            timeout=15,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            data="""<?xml version="1.0"?><d:propfind xmlns:d="DAV:">
            <d:prop><d:resourcetype/></d:prop></d:propfind>""".encode(),
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        entries = set()
        request_path = unquote(urlparse(request_url).path).rstrip("/")
        for item in root.findall(".//{DAV:}response"):
            href = item.find("{DAV:}href")
            if href is None:
                continue
            item_path = unquote(urlparse(href.text or "").path).rstrip("/")
            if item_path == request_path:
                continue
            parent_path, _, name = item_path.rpartition("/")
            if parent_path != request_path or not name or name in {".", ".."}:
                continue
            is_folder = item.find(".//{DAV:}resourcetype/{DAV:}collection") is not None
            if is_folder:
                entries.add(("folder", name))
            elif name.lower().endswith(".md"):
                entries.add(("note", name))
        return sorted(entries, key=lambda entry: (entry[0] != "folder", entry[1].casefold()))

    def finish_notes_worker(self, worker: NotesLoader, names, generation: int):
        self.active_workers.discard(worker)
        self.notes_loaded(names, generation)

    def fail_notes_worker(self, worker: NotesLoader, message: str, generation: int):
        self.active_workers.discard(worker)
        self.notes_load_failed(message, generation)

    def notes_loaded(self, names, generation: int):
        if generation != self.load_generation:
            return
        self.loading_notes = False
        self.note_names = list(names)
        self.available_names = {name for kind, name in names if kind == "note"}
        self.filter_notes()

    def notes_load_failed(self, message: str, generation: int):
        if generation != self.load_generation:
            return
        self.loading_notes = False
        self.note_names = []
        self.available_names = set()
        self.empty_label.setText(f"Не удалось загрузить заметки:\n{message}")
        self.empty_label.setVisible(True)
        self.notes_list.setVisible(False)

    def filter_notes(self, *_):
        if self.loading_notes:
            return
        query = self.search.text().strip().casefold()
        self.notes_list.clear()

        self.update_location_header()
        if self.current_dir and (not query or "..".startswith(query)):
            item = QListWidgetItem("← ..")
            item.setData(Qt.ItemDataRole.UserRole, "folder-up")
            self.notes_list.addItem(item)

        for kind, name in self.note_names:
            title = name if kind == "folder" else Path(name).stem
            if query and query not in title.casefold():
                continue
            item = QListWidgetItem(f"📁  {title}" if kind == "folder" else f"▤  {title}")
            item.setData(Qt.ItemDataRole.UserRole, kind)
            item.setData(Qt.ItemDataRole.UserRole + 1, name)
            relative_path = self.entry_path(name)
            item.setToolTip(
                self.webdav_url + quote(relative_path) if self.storage_mode == "webdav"
                else str(self.notes_dir / relative_path)
            )
            self.notes_list.addItem(item)
        self.empty_label.setText("Заметок пока нет. Нажмите +, чтобы создать первую.")
        self.empty_label.setVisible(self.notes_list.count() == 0)
        self.notes_list.setVisible(self.notes_list.count() > 0)

    def new_note(self):
        self.current_name = None
        self.note_title.clear()
        self.note_text.clear()
        self.pages.setCurrentIndex(1)
        self.note_title.setFocus()

    def open_note(self, item: QListWidgetItem):
        kind = item.data(Qt.ItemDataRole.UserRole)
        if kind == "folder-up":
            self.current_dir = self.current_dir.rsplit("/", 1)[0] if "/" in self.current_dir else ""
            self.search.clear()
            self.refresh_notes()
            return
        name = item.data(Qt.ItemDataRole.UserRole + 1)
        if kind == "folder":
            self.current_dir = self.entry_path(name)
            self.search.clear()
            self.refresh_notes()
            return
        relative_path = self.entry_path(name)
        try:
            if self.storage_mode == "webdav":
                text = self.webdav_request("GET", relative_path).content.decode("utf-8")
            else:
                text = (self.notes_dir / relative_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, requests.RequestException) as exc:
            QMessageBox.warning(self, APP_NAME, f"Не удалось открыть заметку:\n{exc}")
            return
        self.current_name = relative_path
        self.note_title.setText(Path(name).stem)
        self.note_text.setPlainText(text)
        self.pages.setCurrentIndex(1)
        self.note_text.setFocus()

    @staticmethod
    def safe_filename(title: str) -> str:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")
        return name[:120]

    def save_note(self):
        title = self.note_title.text().strip()
        filename = self.safe_filename(title)
        if not filename:
            QMessageBox.information(self, APP_NAME, "Введите название заметки.")
            self.note_title.setFocus()
            return

        new_name = f"{filename}.md"
        new_relative_path = self.entry_path(new_name)
        if new_name in self.available_names and new_relative_path != self.current_name:
            answer = QMessageBox.question(
                self, APP_NAME, "Заметка с таким названием уже существует. Заменить её?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            if self.storage_mode == "webdav":
                self.webdav_request(
                    "PUT", new_relative_path,
                    data=self.note_text.toPlainText().encode("utf-8"),
                    headers={"Content-Type": "text/markdown; charset=utf-8"},
                )
                if self.current_name and self.current_name != new_relative_path:
                    self.webdav_request("DELETE", self.current_name)
            else:
                new_path = self.notes_dir / new_relative_path
                new_path.write_text(self.note_text.toPlainText(), encoding="utf-8")
                old_path = self.notes_dir / self.current_name if self.current_name else None
                if old_path and old_path != new_path and old_path.exists():
                    old_path.unlink()
        except (OSError, requests.RequestException) as exc:
            QMessageBox.warning(self, APP_NAME, f"Не удалось сохранить заметку:\n{exc}")
            return
        self.current_name = new_relative_path
        self.go_back()

    def go_back(self):
        self.pages.setCurrentIndex(0)
        self.refresh_notes()
        self.search.setFocus()

    def show_near_tray(self):
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - self.width() - 12
        y = screen.bottom() - self.height() - 12
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.pages.currentIndex() != 0:
                self.go_back()
            else:
                self.hide()
            return
        super().keyPressEvent(event)


class QuickNotesApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setApplicationName(APP_NAME)
        self.app.setQuitOnLastWindowClosed(False)
        self.window = NotesWindow()

        self.tray = QSystemTrayIcon(tray_icon(), self.app)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        show_action = QAction("Открыть заметки", menu)
        show_action.triggered.connect(self.toggle_window)
        new_action = QAction("Новая заметка", menu)
        new_action.triggered.connect(self.show_new_note)
        quit_action = QAction("Выход", menu)
        quit_action.triggered.connect(self.app.quit)
        menu.addAction(show_action)
        menu.addAction(new_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_window()

    def toggle_window(self):
        if self.window.isVisible():
            self.window.hide()
        else:
            self.window.show_near_tray()
            self.window.refresh_notes()

    def show_new_note(self):
        self.window.new_note()
        self.window.show_near_tray()

    def run(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.critical(None, APP_NAME, "Системный трей недоступен.")
            return 1
        self.tray.showMessage(APP_NAME, "Приложение запущено и находится в трее.",
                              QSystemTrayIcon.MessageIcon.Information, 2500)
        return self.app.exec()
if __name__ == "__main__":
    raise SystemExit(QuickNotesApp().run())
