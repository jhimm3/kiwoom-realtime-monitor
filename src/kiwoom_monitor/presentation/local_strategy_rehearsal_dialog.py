from pathlib import Path
from sqlite3 import Error as SQLiteError

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton

from kiwoom_monitor.central_server.local_strategy_rehearsal import FAMILY_LABELS, LocalStrategyRehearsal


class LocalStrategyRehearsalDialog(QDialog):
    def __init__(self, root, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle('KRX·NXT 로컬 주문 리허설')
        self._root, self._settings = root, settings
        self._run = None
        self._source = QLineEdit(settings.value('local_rehearsal_source', '', type=str))
        self._venue = QComboBox()
        self._venue.addItems(['KRX', 'NXT'])
        self._venue.setCurrentText(settings.value('local_rehearsal_venue', 'KRX', type=str))
        self._summary = QLabel('전략 자료 파일과 주문 거래소를 선택하세요. 가짜 브로커와 별도 로컬 원장을 사용합니다.')
        self._summary.setTextFormat(Qt.TextFormat.PlainText)
        self._summary.setWordWrap(True)
        self._browse = QPushButton('파일 선택')
        self._browse.clicked.connect(self._choose)
        self._start = QPushButton('설정 저장·복구')
        self._start.clicked.connect(self._open)
        self._next = QPushButton('다음 관측')
        self._next.clicked.connect(self._advance)
        self._play = QPushButton('연속 재생')
        self._play.clicked.connect(self._toggle_play)
        self._stop_entry = QPushButton('신규 진입 중지')
        self._stop_entry.clicked.connect(self._block_entry)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

        source = QHBoxLayout()
        source.addWidget(self._source, 1)
        source.addWidget(self._browse)
        buttons = QHBoxLayout()
        for button in (self._start, self._next, self._play, self._stop_entry):
            buttons.addWidget(button)
        form = QFormLayout(self)
        form.addRow('전략 재생 파일', source)
        form.addRow('주문 거래소', self._venue)
        form.addRow(buttons)
        form.addRow('리허설 상태', self._summary)
        self._update_buttons()

    def _choose(self):
        name, _ = QFileDialog.getOpenFileName(self, '로컬 리허설 입력', self._source.text(), 'JSON (*.json)')
        if name:
            self._source.setText(name)

    def _open(self):
        self.stop()
        filename, venue = self._source.text().strip(), self._venue.currentText()
        try:
            self._run = LocalStrategyRehearsal(Path(filename), self._root, venue)
            self._settings.setValue('local_rehearsal_source', filename)
            self._settings.setValue('local_rehearsal_venue', venue)
            self._render(self._run.status())
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, SQLiteError) as error:
            self.stop()
            self._summary.setText(f'리허설을 열 수 없습니다. {error}')
        self._update_buttons()

    def _advance(self):
        if self._run is None:
            return
        try:
            self._render(self._run.step())
            if self._run.done:
                self._timer.stop()
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, SQLiteError) as error:
            self.stop()
            self._summary.setText(f'리허설이 중단됐습니다. {error}')
        self._update_buttons()

    def _render(self, status):
        stopped = '\n새 매수 중지. 기존 보유·미체결은 계속 관리합니다.' if status['entry_stopped'] else ''
        lines = [f"{status['venue']} · 관측 {status['cursor']}/{status['total']} · 가짜 요청 {status['commands']}건",
                 f"계좌 현금 {status['available_cash']:,}원 · 매수 예약 {status['reserved_cash']:,}원"]
        for symbol, plan in status['plans'].items():
            stages = ' · '.join(f"{stage['action']} {stage['state']} {stage['filled']}주"
                                for stage in plan['stages'].values())
            lines.append(f"{symbol} {FAMILY_LABELS[plan['family']]} · 매수 {plan['BUY'] or '-'} · "
                         f"매도 {plan['SELL'] or '-'} · 보유 {plan['held']}주" + (f' · {stages}' if stages else ''))
            if plan['reason']:
                lines.append(plan['reason'])
        self._summary.setText('\n'.join(lines) + stopped)

    def _toggle_play(self):
        if self._timer.isActive():
            self._timer.stop()
        else:
            self._timer.start(0)
        self._update_buttons()

    def _block_entry(self):
        try:
            self._run.stop_entry()
            self._render(self._run.status())
        except (OSError, ValueError, RuntimeError, SQLiteError) as error:
            self.stop()
            self._summary.setText(f'리허설이 중단됐습니다. {error}')

    def _update_buttons(self):
        ready = self._run is not None and not self._run.done
        self._next.setEnabled(ready and not self._timer.isActive())
        self._play.setEnabled(ready)
        self._play.setText('일시 중지' if self._timer.isActive() else '연속 재생')
        self._stop_entry.setEnabled(ready)

    def stop(self):
        self._timer.stop()
        if self._run is not None:
            run, self._run = self._run, None
            run.close()
        self._update_buttons()

    def done(self, result):
        self.stop()
        super().done(result)
