"""PySide6 GUI: drag & drop media files → queue → sequential transcription/translation."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QSettings, QTimer, QUrl
from PySide6.QtGui import QIcon, QDesktopServices, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                               QGroupBox, QComboBox, QCheckBox, QDoubleSpinBox, QSpinBox, QLineEdit,
                               QPlainTextEdit, QProgressBar, QFileDialog, QMessageBox, QSplitter)

from . import APP_NAME, __version__
from .runtime import bundle_dir, app_data_dir
from . import pipeline as pl

log = logging.getLogger("whispersub.gui")

ST_WAIT, ST_RUN, ST_DONE, ST_ERR, ST_CANCEL = "대기", "진행 중", "완료", "오류", "취소됨"
STAGE_LABEL = {"decode": "오디오 디코딩", "transcribe": "음성 인식", "translate": "번역", "done": "완료"}


# --------------------------------------------------------------------------- worker thread
class Worker(QThread):
    item_status = Signal(int, str, str)        # row, status, detail
    item_progress = Signal(int, str, float)    # row, stage, 0..1
    message = Signal(str)
    download_state = Signal(bool)              # model download running?
    finished_all = Signal()

    def __init__(self, jobs: list[tuple[int, str]], settings: pl.Settings, transcriber: pl.Transcriber):
        super().__init__()
        self.jobs, self.st, self.tr = jobs, settings, transcriber
        self._cancel = False
        self._skip_current = False

    def cancel(self):
        self._cancel = True

    def skip_current(self):
        self._skip_current = True

    def _cancelled(self):
        return self._cancel or self._skip_current

    def run(self):
        try:
            if not pl.model_is_cached(self.st.model):
                self.message.emit(f"모델 '{self.st.model}'을(를) 처음 사용합니다 — Hugging Face에서 다운로드합니다 "
                                  f"(large-v3 기준 약 3 GB, 1회).")
                self.download_state.emit(True)
                try:
                    self.tr.load(self.st.model, self.st.device, self.message.emit)
                finally:
                    self.download_state.emit(False)
            for row, path in self.jobs:
                if self._cancel:
                    self.item_status.emit(row, ST_CANCEL, "")
                    continue
                self._skip_current = False
                self.item_status.emit(row, ST_RUN, "")
                try:
                    res = pl.process_file(path, self.st, self.tr, logger=self.message.emit,
                                          progress=lambda stage, v, r=row: self.item_progress.emit(r, stage, v),
                                          cancel=self._cancelled)
                    names = ", ".join(Path(p).name for p in res.outputs if p.endswith(".srt"))
                    self.item_status.emit(row, ST_DONE, f"{res.kept}줄 · {res.seconds:.0f}s · {names}")
                except pl.Cancelled:
                    self.item_status.emit(row, ST_CANCEL, "")
                    self.message.emit("취소됨")
                except Exception as e:  # noqa: BLE001
                    log.exception("process failed: %s", path)
                    self.item_status.emit(row, ST_ERR, f"{type(e).__name__}: {e}")
                    self.message.emit(f"오류: {type(e).__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            log.exception("worker crashed")
            self.message.emit(f"치명적 오류: {e}\n{traceback.format_exc()}")
        finally:
            self.finished_all.emit()


# --------------------------------------------------------------------------- main window
class DropTable(QTableWidget):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__(0, 3)
        self.setAcceptDrops(True)
        self.setHorizontalHeaderLabels(["파일", "상태", "결과 / 진행"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        paths = []
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if not p:
                continue
            if os.path.isdir(p):
                for root, _, files in os.walk(p):
                    paths += [os.path.join(root, f) for f in sorted(files)]
            else:
                paths.append(p)
        self.files_dropped.emit(paths)
        e.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__} — 영상 → 자막 (Whisper)")
        self.resize(1000, 720)
        ico = bundle_dir() / "assets" / "icon.ico"
        if ico.exists():
            self.setWindowIcon(QIcon(str(ico)))
        self.qs = QSettings(APP_NAME, APP_NAME)
        self.transcriber = pl.Transcriber()
        self.worker: Worker | None = None
        self.dl_timer = QTimer(self)
        self.dl_timer.setInterval(2000)
        self.dl_timer.timeout.connect(self._poll_download)
        self._build_ui()
        self._load_settings()
        self._restore_queue()

    # ---------------- UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        hint = QLabel("여기에 동영상/오디오 파일 또는 폴더를 드래그&드롭하세요. 목록 순서대로 처리되며, 결과(SRT)는 각 영상과 같은 폴더에 저장됩니다.")
        hint.setStyleSheet("color:#555; padding:4px;")
        root.addWidget(hint)

        splitter = QSplitter(Qt.Vertical)
        root.addWidget(splitter, 1)

        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.table = DropTable()
        self.table.files_dropped.connect(self.add_files)
        self.table.doubleClicked.connect(lambda idx: self._open_folder(idx.row()))
        tl.addWidget(self.table, 1)

        btns = QHBoxLayout()
        self.b_add = QPushButton("파일 추가…")
        self.b_add.clicked.connect(self._pick_files)
        self.b_rm = QPushButton("선택 제거")
        self.b_rm.clicked.connect(self._remove_selected)
        self.b_clear = QPushButton("완료 항목 지우기")
        self.b_clear.clicked.connect(self._clear_done)
        self.b_open = QPushButton("폴더 열기")
        self.b_open.clicked.connect(lambda: self._open_folder(self.table.currentRow()))
        self.b_apply = QPushButton("번역 TSV 적용…")
        self.b_apply.setToolTip("선택한 영상의 *.translate.tsv(번역 열을 채운 것) 또는 id<TAB>번역 형식 파일로 번역 자막을 만듭니다")
        self.b_apply.clicked.connect(self._apply_tsv)
        for b in (self.b_add, self.b_rm, self.b_clear, self.b_open, self.b_apply):
            btns.addWidget(b)
        btns.addStretch(1)
        self.b_start = QPushButton("▶ 시작")
        self.b_start.setMinimumWidth(110)
        self.b_start.setStyleSheet("font-weight:bold;")
        self.b_start.clicked.connect(self.start)
        self.b_skip = QPushButton("현재 파일 건너뛰기")
        self.b_skip.clicked.connect(self._skip)
        self.b_stop = QPushButton("■ 중지")
        self.b_stop.clicked.connect(self.stop)
        self.b_skip.setEnabled(False)
        self.b_stop.setEnabled(False)
        btns.addWidget(self.b_start)
        btns.addWidget(self.b_skip)
        btns.addWidget(self.b_stop)
        tl.addLayout(btns)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFormat("대기")
        tl.addWidget(self.progress)
        splitter.addWidget(top)

        bottom = QWidget()
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self._settings_box(), 0)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        self.log_view.setFont(f)
        bl.addWidget(self.log_view, 1)
        splitter.addWidget(bottom)
        splitter.setSizes([380, 300])

        self.statusBar().showMessage(f"로그 파일: {app_data_dir() / 'whispersub.log'}")

    def _settings_box(self) -> QWidget:
        box = QGroupBox("설정")
        g = QGridLayout(box)
        r = 0
        g.addWidget(QLabel("모델"), r, 0)
        self.c_model = QComboBox()
        self.c_model.addItems(pl.MODEL_CHOICES)
        self.c_model.setToolTip("large-v3: 최고 정확도 · large-v3-turbo: 빠름 · medium/small: 저사양")
        g.addWidget(self.c_model, r, 1)
        r += 1
        g.addWidget(QLabel("장치"), r, 0)
        self.c_device = QComboBox()
        self.c_device.addItems(["auto", "cuda", "cpu"])
        self.c_device.setToolTip("auto: NVIDIA GPU(CUDA) 우선, 실패 시 CPU")
        g.addWidget(self.c_device, r, 1)
        r += 1
        g.addWidget(QLabel("원본 언어"), r, 0)
        self.c_lang = QComboBox()
        for code, label in pl.LANG_CHOICES:
            self.c_lang.addItem(label, code)
        g.addWidget(self.c_lang, r, 1)
        r += 1
        self.k_fast = QCheckBox("빠른 모드 (beam 2, 폴백 없음)")
        self.k_fast.setToolTip("대사가 적고 잡음/신음이 긴 영상에서 2~3배 빠름. 정확도는 약간 낮아짐")
        g.addWidget(self.k_fast, r, 0, 1, 2)
        r += 1
        g.addWidget(QLabel("자막 최대 표시(초)"), r, 0)
        self.n_maxdur = QDoubleSpinBox()
        self.n_maxdur.setRange(2.0, 30.0)
        self.n_maxdur.setSingleStep(1.0)
        g.addWidget(self.n_maxdur, r, 1)
        r += 1
        self.k_overwrite = QCheckBox("기존 .srt 덮어쓰기")
        self.k_overwrite.setToolTip("끄면 기존 파일을 보존하고 <이름>.<언어>.whisper.srt 로 저장")
        g.addWidget(self.k_overwrite, r, 0, 1, 2)
        r += 1
        self.k_tsv = QCheckBox("번역용 TSV 저장")
        g.addWidget(self.k_tsv, r, 0, 1, 2)
        r += 1

        tbox = QGroupBox("LLM 번역 (OpenAI 호환 API: LM Studio / Ollama / OpenAI …)")
        tbox.setCheckable(True)
        self.k_translate = tbox
        tg = QGridLayout(tbox)
        tg.addWidget(QLabel("서버 URL"), 0, 0)
        self.e_url = QLineEdit()
        self.e_url.setPlaceholderText("http://localhost:1234/v1")
        tg.addWidget(self.e_url, 0, 1)
        tg.addWidget(QLabel("모델"), 1, 0)
        self.e_model = QLineEdit()
        self.e_model.setPlaceholderText("비우면 서버의 첫 모델")
        tg.addWidget(self.e_model, 1, 1)
        tg.addWidget(QLabel("API 키"), 2, 0)
        self.e_key = QLineEdit()
        self.e_key.setEchoMode(QLineEdit.Password)
        self.e_key.setPlaceholderText("로컬 서버는 비워 둠")
        tg.addWidget(self.e_key, 2, 1)
        tg.addWidget(QLabel("대상 언어"), 3, 0)
        self.c_target = QComboBox()
        self.c_target.setEditable(True)
        self.c_target.addItems(["ko", "en", "ja", "zh"])
        tg.addWidget(self.c_target, 3, 1)
        tg.addWidget(QLabel("배치(줄)"), 4, 0)
        self.n_batch = QSpinBox()
        self.n_batch.setRange(5, 200)
        tg.addWidget(self.n_batch, 4, 1)
        self.b_test = QPushButton("연결 테스트")
        self.b_test.clicked.connect(self._test_llm)
        tg.addWidget(self.b_test, 5, 0, 1, 2)
        g.addWidget(tbox, r, 0, 1, 2)
        r += 1
        g.setRowStretch(r, 1)
        box.setMaximumWidth(420)
        return box

    # ---------------- settings persistence
    def _settings(self) -> pl.Settings:
        return pl.Settings(
            model=self.c_model.currentText(), device=self.c_device.currentText(),
            language=self.c_lang.currentData(), fast=self.k_fast.isChecked(),
            max_dur=self.n_maxdur.value(), overwrite=self.k_overwrite.isChecked(), write_tsv=self.k_tsv.isChecked(),
            translate=self.k_translate.isChecked(), llm_url=self.e_url.text().strip() or "http://localhost:1234/v1",
            llm_model=self.e_model.text().strip(), llm_api_key=self.e_key.text(),
            target_lang=self.c_target.currentText().strip() or "ko", llm_batch=self.n_batch.value())

    def _load_settings(self):
        d = {k: self.qs.value(f"settings/{k}") for k in pl.Settings().to_dict()}
        st = pl.Settings.from_dict({k: v for k, v in d.items() if v is not None})
        self.c_model.setCurrentText(st.model)
        self.c_device.setCurrentText(st.device)
        self.c_lang.setCurrentIndex(max(0, self.c_lang.findData(st.language)))
        self.k_fast.setChecked(st.fast)
        self.n_maxdur.setValue(st.max_dur)
        self.k_overwrite.setChecked(st.overwrite)
        self.k_tsv.setChecked(st.write_tsv)
        self.k_translate.setChecked(st.translate)
        self.e_url.setText(st.llm_url)
        self.e_model.setText(st.llm_model)
        self.e_key.setText(st.llm_api_key)
        self.c_target.setCurrentText(st.target_lang)
        self.n_batch.setValue(st.llm_batch)

    def _save_settings(self):
        for k, v in self._settings().to_dict().items():
            self.qs.setValue(f"settings/{k}", v)

    def _restore_queue(self):
        paths = self.qs.value("queue/pending", [])
        if isinstance(paths, str):
            paths = [paths]
        self.add_files([p for p in paths if os.path.exists(p)], silent=True)

    def _save_queue(self):
        pending = [self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())
                   if self.table.item(r, 1).text() in (ST_WAIT, ST_RUN, ST_CANCEL)]
        self.qs.setValue("queue/pending", pending)

    # ---------------- queue ops
    def add_files(self, paths: list[str], silent: bool = False):
        existing = {self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())}
        added = skipped = 0
        for p in paths:
            p = os.path.abspath(p)
            if Path(p).suffix.lower() not in pl.MEDIA_EXTS or p in existing:
                skipped += 1
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            it = QTableWidgetItem(os.path.basename(p))
            it.setToolTip(p)
            it.setData(Qt.UserRole, p)
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, QTableWidgetItem(ST_WAIT))
            self.table.setItem(r, 2, QTableWidgetItem(""))
            existing.add(p)
            added += 1
        if not silent and (added or skipped):
            self._log(f"{added}개 추가" + (f", {skipped}개 건너뜀(지원하지 않는 형식/중복)" if skipped else ""))
        self._save_queue()

    def _pick_files(self):
        exts = " ".join(f"*{e}" for e in sorted(pl.MEDIA_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "미디어 파일 선택", self.qs.value("ui/last_dir", ""),
                                                f"미디어 파일 ({exts});;모든 파일 (*)")
        if files:
            self.qs.setValue("ui/last_dir", os.path.dirname(files[0]))
            self.add_files(files)

    def _remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            if self.table.item(r, 1).text() == ST_RUN:
                continue
            self.table.removeRow(r)
        self._save_queue()

    def _clear_done(self):
        for r in range(self.table.rowCount() - 1, -1, -1):
            if self.table.item(r, 1).text() in (ST_DONE, ST_ERR, ST_CANCEL):
                self.table.removeRow(r)
        self._save_queue()

    def _open_folder(self, row: int):
        if row < 0:
            return
        p = self.table.item(row, 0).data(Qt.UserRole)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p)))

    # ---------------- run control
    def start(self):
        if self.worker and self.worker.isRunning():
            return
        jobs = [(r, self.table.item(r, 0).data(Qt.UserRole)) for r in range(self.table.rowCount())
                if self.table.item(r, 1).text() in (ST_WAIT, ST_CANCEL, ST_ERR)]
        if not jobs:
            QMessageBox.information(self, APP_NAME, "처리할 파일이 없습니다. 파일을 드래그&드롭하거나 추가하세요.")
            return
        self._save_settings()
        st = self._settings()
        for r, _ in jobs:
            self.table.item(r, 1).setText(ST_WAIT)
            self.table.item(r, 2).setText("")
        self.worker = Worker(jobs, st, self.transcriber)
        self.worker.item_status.connect(self._on_status)
        self.worker.item_progress.connect(self._on_progress)
        self.worker.message.connect(self._log)
        self.worker.download_state.connect(self._on_download_state)
        self.worker.finished_all.connect(self._on_finished)
        self._set_running(True)
        self._log(f"시작: {len(jobs)}개 파일 · 모델 {st.model} · 장치 {st.device} · 언어 {st.language}"
                  + (" · 빠른 모드" if st.fast else "") + (f" · 번역→{st.target_lang}" if st.translate else ""))
        self.worker.start()

    def stop(self):
        if self.worker:
            self.worker.cancel()
            self._log("중지 요청 — 현재 세그먼트 처리 후 멈춥니다…")

    def _skip(self):
        if self.worker:
            self.worker.skip_current()

    def _set_running(self, running: bool):
        for w in (self.b_start, self.b_add, self.b_apply, self.c_model, self.c_device, self.c_lang, self.k_fast,
                  self.k_translate):
            w.setEnabled(not running)
        self.b_stop.setEnabled(running)
        self.b_skip.setEnabled(running)
        if not running:
            self.progress.setValue(0)
            self.progress.setFormat("대기")

    def _on_status(self, row: int, status: str, detail: str):
        if row < self.table.rowCount():
            self.table.item(row, 1).setText(status)
            if detail or status != ST_RUN:
                self.table.item(row, 2).setText(detail)
            if status == ST_RUN:
                self.table.scrollToItem(self.table.item(row, 0))
        self._save_queue()

    def _on_progress(self, row: int, stage: str, v: float):
        label = STAGE_LABEL.get(stage, stage)
        self.progress.setValue(int(v * 1000))
        self.progress.setFormat(f"{label} {v*100:.0f}%")
        if row < self.table.rowCount():
            self.table.item(row, 2).setText(f"{label} {v*100:.0f}%")

    def _on_download_state(self, running: bool):
        if running:
            self.progress.setRange(0, 0)
            self.progress.setFormat("모델 다운로드 중…")
            self.dl_timer.start()
        else:
            self.dl_timer.stop()
            self.progress.setRange(0, 1000)

    def _poll_download(self):
        mb = pl.model_cache_bytes(self.c_model.currentText()) / 1e6
        self.statusBar().showMessage(f"모델 다운로드 중… {mb:,.0f} MB")

    def _on_finished(self):
        self._set_running(False)
        self.statusBar().showMessage("모든 작업 완료")
        self._log("── 모든 작업 완료")
        QApplication.alert(self)

    # ---------------- misc
    def _apply_tsv(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, APP_NAME, "목록에서 영상을 먼저 선택하세요.")
            return
        video = self.table.item(row, 0).data(Qt.UserRole)
        guess = str(Path(video).with_name(Path(video).stem + ".translate.tsv"))
        tsv, _ = QFileDialog.getOpenFileName(self, "번역 TSV 선택", guess if os.path.exists(guess) else os.path.dirname(video),
                                             "TSV (*.tsv *.txt);;모든 파일 (*)")
        if not tsv:
            return
        try:
            out = pl.apply_translation(video, tsv, self._settings(), self._log)
            self.table.item(row, 2).setText(f"번역 적용 → {out.name}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, str(e))

    def _test_llm(self):
        st = self._settings()
        models = pl.check_llm(st.llm_url, st.llm_api_key)
        if models is None:
            QMessageBox.warning(self, APP_NAME, f"연결 실패: {st.llm_url}\n서버가 켜져 있는지, URL이 /v1 로 끝나는지 확인하세요.")
        else:
            QMessageBox.information(self, APP_NAME, "연결 성공. 모델:\n" + "\n".join(models[:20]))

    def _log(self, msg: str):
        self.log_view.appendPlainText(msg)
        log.info(msg)

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self, APP_NAME, "작업이 진행 중입니다. 종료할까요?") != QMessageBox.Yes:
                e.ignore()
                return
            self.worker.cancel()
            self.worker.wait(5000)
        self._save_settings()
        self._save_queue()
        super().closeEvent(e)


def run_gui(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    ico = bundle_dir() / "assets" / "icon.ico"
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    w = MainWindow()
    w.show()
    shot = None
    if "--screenshot" in argv:                      # dev/docs helper: save a PNG of the window and quit
        i = argv.index("--screenshot")
        shot = argv[i + 1] if i + 1 < len(argv) else "screenshot.png"
        argv = argv[:i] + argv[i + 2:]
    files = [a for a in argv[1:] if os.path.exists(a)]
    if files:
        w.add_files(files)
    if shot:
        def _grab():
            w.grab().save(shot)
            app.quit()
        QTimer.singleShot(1500, _grab)
    return app.exec()
