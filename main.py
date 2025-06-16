import Metashape
from PySide2 import QtWidgets, QtCore, QtGui
import os
from pathlib import Path


class TelemetrySettingsDialog(QtWidgets.QDialog):
    """Диалог настроек импорта телеметрии"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки импорта телеметрии")
        self.setModal(True)
        self.init_ui()

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # Система координат
        crs_group = QtWidgets.QGroupBox("Система координат")
        crs_layout = QtWidgets.QVBoxLayout()
        self.crs_combo = QtWidgets.QComboBox()
        self.crs_combo.addItems([
            "EPSG::4326 (WGS 84)",
            "EPSG::3857 (Web Mercator)",
            "EPSG::32637 (WGS 84 / UTM zone 37N)",
            "Другая..."
        ])
        crs_layout.addWidget(self.crs_combo)
        crs_group.setLayout(crs_layout)
        layout.addWidget(crs_group)

        # Разделитель
        delimiter_group = QtWidgets.QGroupBox("Разделитель")
        delimiter_layout = QtWidgets.QHBoxLayout()
        self.delimiter_combo = QtWidgets.QComboBox()
        self.delimiter_combo.addItems([", (запятая)", "; (точка с запятой)", "\t (табуляция)", "Пробел"])
        delimiter_layout.addWidget(self.delimiter_combo)
        delimiter_group.setLayout(delimiter_layout)
        layout.addWidget(delimiter_group)

        # Столбцы
        columns_group = QtWidgets.QGroupBox("Расположение столбцов")
        columns_layout = QtWidgets.QFormLayout()
        self.columns_edit = QtWidgets.QLineEdit("nxyz")
        self.columns_edit.setToolTip(
            "Формат: nxyz\nn - название камеры\nx - координата X\ny - координата Y\nz - координата Z")
        columns_layout.addRow("Формат столбцов:", self.columns_edit)
        columns_group.setLayout(columns_layout)
        layout.addWidget(columns_group)

        # Кнопки
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_settings(self):
        """Получение настроек импорта"""
        # Получаем разделитель
        delimiter_map = {
            ", (запятая)": ",",
            "; (точка с запятой)": ";",
            "\t (табуляция)": "\t",
            "Пробел": " "
        }
        delimiter = delimiter_map[self.delimiter_combo.currentText()]

        # Получаем систему координат
        crs_map = {
            "EPSG::4326 (WGS 84)": "EPSG::4326",
            "EPSG::3857 (Web Mercator)": "EPSG::3857",
            "EPSG::32637 (WGS 84 / UTM zone 37N)": "EPSG::32637",
            "Другая...": "EPSG::4326"  # По умолчанию, можно добавить выбор другой CRS
        }
        crs = crs_map[self.crs_combo.currentText()]

        return {
            'delimiter': delimiter,
            'columns': self.columns_edit.text(),
            'crs': crs
        }


class MetashapeProcessor(QtCore.QThread):
    """Класс для обработки ортофотоплана в отдельном потоке"""

    progress_updated = QtCore.Signal(int)
    status_updated = QtCore.Signal(str)
    finished_successfully = QtCore.Signal(bool, str)

    def __init__(self, images_folder, telemetry_file, output_path, settings):
        super().__init__()
        self.images_folder = images_folder
        self.telemetry_file = telemetry_file
        self.output_path = output_path
        self.settings = settings
        self.doc = Metashape.app.document

    def run(self):
        """Основная функция обработки"""
        try:
            # Создание нового chunk если документ пустой
            if not self.doc.chunks:
                self.status_updated.emit("Создание нового chunk...")
                chunk = self.doc.addChunk()
            else:
                chunk = self.doc.chunk

            self.progress_updated.emit(5)

            # Загрузка изображений
            self.status_updated.emit("Загрузка изображений...")
            self.load_images(chunk)
            self.progress_updated.emit(15)

            # Загрузка телеметрии если есть
            if self.telemetry_file and os.path.exists(self.telemetry_file):
                self.status_updated.emit("Загрузка телеметрии...")
                self.load_telemetry(chunk)
                self.progress_updated.emit(20)

            # Поиск особых точек и выравнивание
            self.status_updated.emit("Поиск особых точек...")
            self.match_photos(chunk)
            self.progress_updated.emit(35)

            # Выравнивание фотографий
            self.status_updated.emit("Выравнивание фотографий...")
            self.align_photos(chunk)
            self.progress_updated.emit(50)

            # Построение плотного облака точек
            if self.settings.get('build_dense_cloud', True):
                self.status_updated.emit("Построение плотного облака точек...")
                self.build_dense_cloud(chunk)
                self.progress_updated.emit(70)

                # Построение mesh
                self.status_updated.emit("Построение полигональной модели...")
                self.build_mesh(chunk)
                self.progress_updated.emit(85)

                # Построение текстуры
                self.status_updated.emit("Построение текстуры...")
                self.build_texture(chunk)
                self.progress_updated.emit(90)

            # Экспорт ортофотоплана
            self.status_updated.emit("Экспорт ортофотоплана...")
            output_file = self.export_orthophoto(chunk)
            self.progress_updated.emit(100)

            self.status_updated.emit("Обработка завершена успешно!")
            self.finished_successfully.emit(True, output_file)

        except Exception as e:
            error_msg = f"Ошибка обработки: {str(e)}"
            self.status_updated.emit(error_msg)
            self.finished_successfully.emit(False, error_msg)

    def load_images(self, chunk):
        """Загрузка изображений в chunk"""
        image_list = []
        supported_formats = ['.jpg', '.jpeg', '.tif', '.tiff', '.png']

        for ext in supported_formats:
            image_list.extend(Path(self.images_folder).glob(f"*{ext}"))
            image_list.extend(Path(self.images_folder).glob(f"*{ext.upper()}"))

        photo_list = [str(img) for img in image_list]

        if not photo_list:
            raise Exception(f"Изображения не найдены в папке: {self.images_folder}")

        chunk.addPhotos(photo_list)

    def load_telemetry(self, chunk):
        """Загрузка телеметрии"""
        try:
            # Получаем настройки импорта
            telemetry_settings = self.settings.get('telemetry_settings', {
                'delimiter': ',',
                'columns': 'nxyz',
                'crs': 'EPSG::4326'
            })

            chunk.importReference(self.telemetry_file,
                                  format=Metashape.ReferenceFormatCSV,
                                  columns=telemetry_settings['columns'],
                                  delimiter=telemetry_settings['delimiter'],
                                  crs=Metashape.CoordinateSystem(telemetry_settings['crs']))
        except Exception as e:
            print(f"Предупреждение: Не удалось загрузить телеметрию: {e}")

    # ... остальные методы класса MetashapeProcessor остаются без изменений ...


class OrthophotoSettingsDialog(QtWidgets.QDialog):
    """Диалог настроек обработки"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        """Инициализация интерфейса диалога"""
        self.setWindowTitle("Настройки обработки ортофотоплана")
        self.setModal(True)
        self.resize(400, 500)

        layout = QtWidgets.QVBoxLayout(self)

        # Основные настройки
        main_group = QtWidgets.QGroupBox("Основные параметры")
        main_layout = QtWidgets.QFormLayout(main_group)

        # Точность выравнивания
        self.accuracy_combo = QtWidgets.QComboBox()
        self.accuracy_combo.addItems(["HighAccuracy", "MediumAccuracy", "LowAccuracy"])
        main_layout.addRow("Точность выравнивания:", self.accuracy_combo)

        # Качество плотного облака
        self.quality_combo = QtWidgets.QComboBox()
        self.quality_combo.addItems(["HighQuality", "MediumQuality", "LowQuality"])
        self.quality_combo.setCurrentText("MediumQuality")
        main_layout.addRow("Качество плотного облака:", self.quality_combo)

        # Размер текстуры
        self.texture_size_combo = QtWidgets.QComboBox()
        self.texture_size_combo.addItems(["2048", "4096", "8192", "16384"])
        self.texture_size_combo.setCurrentText("4096")
        main_layout.addRow("Размер текстуры:", self.texture_size_combo)

        layout.addWidget(main_group)

        # Дополнительные опции
        advanced_group = QtWidgets.QGroupBox("Дополнительные опции")
        advanced_layout = QtWidgets.QFormLayout(advanced_group)

        self.generic_preselection_cb = QtWidgets.QCheckBox()
        self.generic_preselection_cb.setChecked(True)
        advanced_layout.addRow("Общая предварительная выборка:", self.generic_preselection_cb)

        self.reference_preselection_cb = QtWidgets.QCheckBox()
        self.reference_preselection_cb.setChecked(True)
        advanced_layout.addRow("Опорная предварительная выборка:", self.reference_preselection_cb)

        self.adaptive_fitting_cb = QtWidgets.QCheckBox()
        self.adaptive_fitting_cb.setChecked(True)
        advanced_layout.addRow("Адаптивная подгонка:", self.adaptive_fitting_cb)

        self.build_dense_cloud_cb = QtWidgets.QCheckBox()
        self.build_dense_cloud_cb.setChecked(True)
        advanced_layout.addRow("Строить плотное облако:", self.build_dense_cloud_cb)

        layout.addWidget(advanced_group)

        # Кнопки
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_settings(self):
        """Получение настроек"""
        return {
            'accuracy': self.accuracy_combo.currentText(),
            'dense_cloud_quality': self.quality_combo.currentText(),
            'texture_size': int(self.texture_size_combo.currentText()),
            'generic_preselection': self.generic_preselection_cb.isChecked(),
            'reference_preselection': self.reference_preselection_cb.isChecked(),
            'adaptive_fitting': self.adaptive_fitting_cb.isChecked(),
            'build_dense_cloud': self.build_dense_cloud_cb.isChecked()
        }


class OrthophotoWidget(QtWidgets.QWidget):
    """Компактный виджет для панели инструментов"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.processor_thread = None
        self.settings = self.get_default_settings()
        self.init_ui()

    def init_ui(self):
        """Инициализация компактного интерфейса"""
        layout = QtWidgets.QVBoxLayout(self)

        # Группа входных данных
        input_group = QtWidgets.QGroupBox("Входные данные")
        input_layout = QtWidgets.QVBoxLayout()

        # Папка изображений
        images_layout = QtWidgets.QHBoxLayout()
        self.images_folder_edit = QtWidgets.QLineEdit()
        self.images_folder_edit.setPlaceholderText("Папка со снимками...")
        images_layout.addWidget(self.images_folder_edit)

        images_btn = QtWidgets.QPushButton("обзор")
        images_btn.setToolTip("Выбрать папку со снимками")
        images_btn.clicked.connect(self.select_images_folder)
        images_layout.addWidget(images_btn)
        input_layout.addLayout(images_layout)

        # Файл телеметрии
        telemetry_layout = QtWidgets.QHBoxLayout()
        self.telemetry_file_edit = QtWidgets.QLineEdit()
        self.telemetry_file_edit.setPlaceholderText("Файл телеметрии (опционально)...")
        telemetry_layout.addWidget(self.telemetry_file_edit)

        telemetry_btn = QtWidgets.QPushButton("обзор")
        telemetry_btn.setToolTip("Выбрать файл телеметрии")
        telemetry_btn.clicked.connect(self.select_telemetry_file)
        telemetry_layout.addWidget(telemetry_btn)
        input_layout.addLayout(telemetry_layout)

        input_group.setLayout(input_layout)
        layout.addWidget(input_group)

        # Группа выходных данных
        output_group = QtWidgets.QGroupBox("Выходные данные")
        output_layout = QtWidgets.QHBoxLayout()

        self.output_folder_edit = QtWidgets.QLineEdit()
        self.output_folder_edit.setPlaceholderText("Папка для сохранения...")
        output_layout.addWidget(self.output_folder_edit)

        output_btn = QtWidgets.QPushButton("обзор")
        output_btn.setToolTip("Выбрать папку для сохранения")
        output_btn.clicked.connect(self.select_output_folder)
        output_layout.addWidget(output_btn)
        output_group.setLayout(output_layout)
        layout.addWidget(output_group)

        # Кнопка настроек
        settings_btn = QtWidgets.QPushButton("⚙️")
        settings_btn.setMaximumWidth(25)
        settings_btn.setToolTip("Настройки обработки")
        settings_btn.clicked.connect(self.open_settings)
        layout.addWidget(settings_btn)

        # Кнопка запуска
        self.start_btn = QtWidgets.QPushButton("🚀 Создать")
        self.start_btn.setStyleSheet("QPushButton { font-weight: bold; background-color: #3498db; color: white; }")
        self.start_btn.clicked.connect(self.start_processing)
        layout.addWidget(self.start_btn)

        # Прогресс бар (скрытый)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setMaximumWidth(80)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Статус
        self.status_label = QtWidgets.QLabel("Готов")
        self.status_label.setMaximumWidth(80)
        self.status_label.setStyleSheet("color: #27ae60;")
        layout.addWidget(self.status_label)

    def get_default_settings(self):
        """Настройки по умолчанию"""
        return {
            'accuracy': 'HighAccuracy',
            'dense_cloud_quality': 'MediumQuality',
            'texture_size': 4096,
            'generic_preselection': True,
            'reference_preselection': True,
            'adaptive_fitting': True,
            'build_dense_cloud': True,
            'telemetry_settings': {
                'delimiter': ',',
                'columns': 'nxyz',
                'crs': 'EPSG::4326'
            }
        }

    def select_images_folder(self):
        """Выбор папки с изображениями"""
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Выберите папку со снимками")
        if folder:
            self.images_folder_edit.setText(folder)

    def select_telemetry_file(self):
        """Выбор файла телеметрии"""
        file, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Выберите файл телеметрии",
            "",
            "CSV Files (*.csv);;Text Files (*.txt);;All Files (*)"
        )
        if file:
            self.telemetry_file_edit.setText(file)

    def select_output_folder(self):
        """Выбор папки для сохранения"""
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения")
        if folder:
            self.output_folder_edit.setText(folder)

    def open_settings(self):
        """Открытие диалога настроек"""
        dialog = OrthophotoSettingsDialog(self)

        # Установка текущих настроек
        dialog.accuracy_combo.setCurrentText(self.settings['accuracy'])
        dialog.quality_combo.setCurrentText(self.settings['dense_cloud_quality'])
        dialog.texture_size_combo.setCurrentText(str(self.settings['texture_size']))
        dialog.generic_preselection_cb.setChecked(self.settings['generic_preselection'])
        dialog.reference_preselection_cb.setChecked(self.settings['reference_preselection'])
        dialog.adaptive_fitting_cb.setChecked(self.settings['adaptive_fitting'])
        dialog.build_dense_cloud_cb.setChecked(self.settings['build_dense_cloud'])

        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.settings = dialog.get_settings()

    def validate_inputs(self):
        """Валидация входных данных"""
        if not self.images_folder_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Выберите папку со снимками!")
            return False

        if not os.path.exists(self.images_folder_edit.text()):
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Папка со снимками не существует!")
            return False

        if not self.output_folder_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Выберите папку для сохранения!")
            return False

        if not os.path.exists(self.output_folder_edit.text()):
            try:
                os.makedirs(self.output_folder_edit.text())
            except:
                QtWidgets.QMessageBox.warning(self, "Ошибка", "Не удалось создать папку для сохранения!")
                return False

        # Проверка наличия изображений
        images_folder = self.images_folder_edit.text()
        supported_formats = ['.jpg', '.jpeg', '.tif', '.tiff', '.png']
        image_count = 0

        for ext in supported_formats:
            image_count += len(list(Path(images_folder).glob(f"*{ext}")))
            image_count += len(list(Path(images_folder).glob(f"*{ext.upper()}")))

            if image_count == 0:
                QtWidgets.QMessageBox.warning(self, "Ошибка", "В выбранной папке нет поддерживаемых изображений!")
            return False

        return True

    def start_processing(self):
        """Запуск обработки"""
        if not self.validate_inputs():
            return

        # Если указан файл телеметрии, запрашиваем настройки импорта
        telemetry_file = self.telemetry_file_edit.text() if self.telemetry_file_edit.text() else None

        if telemetry_file:
            dialog = TelemetrySettingsDialog(self)
            if dialog.exec_() != QtWidgets.QDialog.Accepted:
                return  # Пользователь отменил

            # Добавляем настройки телеметрии в общие настройки
            self.settings['telemetry_settings'] = dialog.get_settings()

        # Изменение интерфейса
        self.start_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Обработка...")
        self.status_label.setStyleSheet("color: #f39c12;")

        # Создание и запуск потока обработки
        self.processor_thread = MetashapeProcessor(
            self.images_folder_edit.text(),
            telemetry_file,
            self.output_folder_edit.text(),
            self.settings
        )

        # Подключение сигналов
        self.processor_thread.progress_updated.connect(self.update_progress)
        self.processor_thread.status_updated.connect(self.update_status)
        self.processor_thread.finished_successfully.connect(self.processing_finished)

        # Запуск потока
        self.processor_thread.start()

    def update_progress(self, value):
        """Обновление прогресс бара"""
        self.progress_bar.setValue(value)

    def update_status(self, message):
        """Обновление статуса"""
        short_message = message[:15] + "..." if len(message) > 15 else message
        self.status_label.setText(short_message)
        self.status_label.setToolTip(message)

    def processing_finished(self, success, message):
        """Завершение обработки"""
        self.start_btn.setEnabled(True)
        self.progress_bar.setVisible(False)

        if success:
            QtWidgets.QMessageBox.information(self, "Успех",
                                              f"Ортофотоплан создан!\nФайл: {os.path.basename(message)}")
            self.status_label.setText("Готов")
            self.status_label.setStyleSheet("color: #27ae60;")
        else:
            QtWidgets.QMessageBox.critical(self, "Ошибка", f"Ошибка:\n{message}")
            self.status_label.setText("Ошибка")
            self.status_label.setStyleSheet("color: #e74c3c;")


def show_orthophoto_dialog():
    """Показать диалог создания ортофотоплана"""
    try:
        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle("Создание ортофотоплана")
        dialog.setModal(True)
        dialog.resize(300, 450)

        layout = QtWidgets.QVBoxLayout(dialog)

        # Добавляем наш виджет в диалог
        orthophoto_widget = OrthophotoWidget(dialog)
        layout.addWidget(orthophoto_widget)

        # Кнопка закрытия
        close_btn = QtWidgets.QPushButton("Закрыть")
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn)

        dialog.exec_()

    except Exception as e:
        Metashape.app.messageBox(f"Ошибка создания диалога: {str(e)}")


def add_orthophoto_menu():
    """Добавление пункта меню для ортофотоплана"""
    try:
        # Получаем приложение
        app = QtWidgets.QApplication.instance()
        main_window = None

        # Ищем главное окно среди виджетов
        for widget in app.topLevelWidgets():
            if isinstance(widget, QtWidgets.QMainWindow):
                main_window = widget
                break

        if not main_window:
            print("Main window not found!")
            return

        # Получаем менюбар
        menubar = main_window.menuBar()

        # Создаем меню "Автоматизация" или добавляем в существующее
        automation_menu = None
        for action in menubar.actions():
            if action.text() == "Автоматизация":
                automation_menu = action.menu()
                break

        if not automation_menu:
            automation_menu = menubar.addMenu("Автоматизация")

        # Добавляем действие для ортофотоплана
        orthophoto_action = QtWidgets.QAction("Создать ортофотоплан", main_window)
        orthophoto_action.triggered.connect(show_orthophoto_dialog)
        automation_menu.addAction(orthophoto_action)

        print("Меню ортофотоплана добавлено успешно!")

    except Exception as e:
        print(f"Ошибка добавления меню: {e}")
        Metashape.app.messageBox(f"Ошибка добавления меню: {str(e)}")


# Основная функция для добавления функциональности
def add_orthophoto_tools():
    """Добавление инструментов ортофотоплана в Metashape"""
    print("Добавление инструментов ортофотоплана...")

    add_orthophoto_menu()

    Metashape.app.messageBox(
        "Инструменты ортофотоплана добавлены!\n\n- Панель инструментов вверху\n- Меню 'Автоматизация' -> 'Создать ортофотоплан'")


# Вызываем функцию для добавления инструментов
add_orthophoto_tools()