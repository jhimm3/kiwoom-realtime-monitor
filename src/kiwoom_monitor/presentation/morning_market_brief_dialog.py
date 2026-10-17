"""Non-modal NAS morning brief reader and prompt editor."""
from __future__ import annotations

from html import escape
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from PySide6.QtCore import QTime, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QTextBrowser,
    QTimeEdit, QVBoxLayout, QWidget,
)
from kiwoom_monitor.application.morning_market_brief import DEFAULT_PROMPT, DEFAULT_SETTINGS
from kiwoom_monitor.infrastructure.chatgpt_plan import reasoning_choices
from kiwoom_monitor.infrastructure.news_ai import MODEL_OPTIONS
from kiwoom_monitor.presentation.settings_request_worker import SettingsRequestWorker
from kiwoom_monitor.infrastructure.time_display import format_kst_time


class MorningMarketBriefDialog(QDialog):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.setWindowTitle("아침 시황 · AI 설정")
        self.resize(950, 740)
        self._client = client
        self._worker = None
        self._revision = None
        self._models = ()
        self._loading = False
        self._settings = dict(DEFAULT_SETTINGS)
        self._status = QLabel("NAS 설정을 불러옵니다.")
        self._tabs = QTabWidget()
        self._report = QTextBrowser()
        self._rendered_report = None
        self._report.setOpenExternalLinks(True)
        report_panel = QWidget()
        report_layout = QVBoxLayout(report_panel)
        report_layout.addWidget(self._report)
        actions = QHBoxLayout()
        self._refresh = QPushButton("결과 새로고침")
        self._refresh.clicked.connect(self.refresh_report)
        self._run = QPushButton("지금 시황 생성")
        self._run.clicked.connect(self.run_now)
        actions.addWidget(self._refresh)
        actions.addWidget(self._run)
        report_layout.addLayout(actions)
        self._tabs.addTab(report_panel, "아침 시황")

        panel = QWidget()
        form = QFormLayout(panel)
        guide = QLabel("매일 정한 한국 시각에 AI가 직접 웹 검색·분석합니다. NAS에 결과를 한 번 저장하고 앱이 공유합니다.\n프롬프트를 바꿔도 결과의 기준 시각·출처·유효기간은 함께 저장합니다.")
        guide.setWordWrap(True)
        form.addRow(guide)
        self._enabled = QCheckBox("매일 자동 생성")
        self._time = QTimeEdit(QTime(7, 55))
        self._time.setDisplayFormat("HH:mm")
        self._provider = QComboBox()
        self._provider.addItem("ChatGPT 구독", "chatgpt_plan")
        self._provider.addItem("OpenAI API", "openai")
        self._model = QComboBox()
        self._effort = QComboBox()
        self._ttl = QSpinBox()
        self._ttl.setRange(15, 720)
        self._ttl.setSuffix("분")
        self._watchlist = QPlainTextEdit()
        self._watchlist.setMaximumHeight(70)
        self._watchlist.setPlaceholderText("관심 업종·종목과 중요 조건. 계좌 번호나 API 키는 입력하지 마세요.")
        self._prompt = QPlainTextEdit()
        self._prompt.setMinimumHeight(220)
        self._prompt.setPlainText(DEFAULT_PROMPT)
        form.addRow(self._enabled)
        form.addRow("실행 시각 (한국)", self._time)
        form.addRow("공급자", self._provider)
        form.addRow("추론 모델", self._model)
        form.addRow("추론 수준", self._effort)
        form.addRow("시황 유효기간", self._ttl)
        form.addRow("관심 목록", self._watchlist)
        form.addRow("시황 프롬프트", self._prompt)
        reset = QPushButton("기본 프롬프트 불러오기")
        reset.clicked.connect(lambda: self._prompt.setPlainText(DEFAULT_PROMPT))
        self._save = QPushButton("설정 저장")
        self._save.clicked.connect(self.save)
        form.addRow(reset, self._save)
        self._tabs.addTab(panel, "프롬프트·모델·예약")
        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)
        layout.addWidget(self._status)
        self._provider.currentIndexChanged.connect(self._populate_models)
        self._model.currentIndexChanged.connect(self._populate_effort)
        self._timer = QTimer(self)
        self._timer.setInterval(10_000)
        self._timer.timeout.connect(self.refresh_report)

    def showEvent(self, event):
        super().showEvent(event)
        if self._revision is None:
            self.load()
        self._timer.start()

    def _request(self, operation, success):
        if self._worker is not None:
            return
        self._run.setEnabled(False)
        self._save.setEnabled(False)
        self._status.setText("NAS 처리 중…")
        worker = SettingsRequestWorker(operation)
        self._worker = worker
        worker.succeeded.connect(success)
        worker.failed.connect(lambda message: self._status.setText(message))
        worker.finished.connect(lambda: self._finished(worker))
        worker.start()

    def _finished(self, worker):
        self._worker = None
        self._run.setEnabled(self._revision is not None)
        self._save.setEnabled(self._revision is not None)
        worker.deleteLater()

    def load(self):
        def get():
            settings = self._client.morning_brief_settings()
            try:
                models = self._client.chatgpt_models()
                model_error = ""
            except Exception:
                models, model_error = (), "ChatGPT 모델 조회 전 · 뉴스 AI 설정에서 NAS 연결을 확인하세요."
            return settings, models, model_error, self._client.morning_brief()
        self._request(get, self._loaded)

    def _loaded(self, result):
        settings, self._models, model_error, report = result
        self._loading = True
        self._settings = settings
        self._revision = int(settings["revision"])
        self._enabled.setChecked(settings["enabled"])
        self._time.setTime(QTime.fromString(settings["time"], "HH:mm"))
        self._provider.setCurrentIndex(max(0, self._provider.findData(settings["provider"])))
        self._populate_models()
        self._model.setCurrentIndex(self._model.findData(settings["model"]))
        self._populate_effort()
        self._effort.setCurrentIndex(max(0, self._effort.findData(settings["reasoning_effort"])))
        self._ttl.setValue(settings["ttl_minutes"])
        self._watchlist.setPlainText(settings["watchlist"])
        self._prompt.setPlainText(settings["prompt"])
        self._loading = False
        self._show_report(report)
        self._status.setText(model_error or "NAS 설정을 불러왔습니다.")

    def _populate_models(self, *_):
        self._model.clear()
        provider = self._provider.currentData()
        models = self._models if provider == "chatgpt_plan" else (
            ("GPT-6.1 Sol", "gpt-6.1-sol"), ("GPT-6 Astra", "gpt-6-astra"), *MODEL_OPTIONS["openai"])
        for label, slug in models:
            self._model.addItem(label, slug)
        if self._loading and self._settings["provider"] == provider:
            slug = self._settings["model"]
            if self._model.findData(slug) < 0:
                self._model.addItem(f"저장 모델 · {slug} (연결 후 확인)", slug)
        self._populate_effort()

    def _populate_effort(self, *_):
        previous = self._effort.currentData()
        self._effort.clear()
        self._effort.addItem("서비스 기본값", "")
        for effort in reasoning_choices(str(self._model.currentData() or "")):
            self._effort.addItem(effort, effort)
        self._effort.setCurrentIndex(max(0, self._effort.findData(previous)))

    def save(self):
        if self._revision is None:
            return
        values = {"expected_revision": self._revision, "enabled": self._enabled.isChecked(),
            "time": self._time.time().toString("HH:mm"), "provider": self._provider.currentData(),
            "model": self._model.currentData(), "reasoning_effort": self._effort.currentData(),
            "ttl_minutes": self._ttl.value(), "prompt": self._prompt.toPlainText(),
            "watchlist": self._watchlist.toPlainText()}
        self._request(lambda: self._client.save_morning_brief_settings(values), self._saved)

    def _saved(self, settings):
        self._settings = settings
        self._revision = int(settings["revision"])
        self._status.setText(f"저장 완료 · 매일 {settings['time']} 한국 시각")

    def run_now(self):
        self._request(self._client.run_morning_brief,
                      lambda result: self._status.setText("시황 생성 중 · 완료 후 이 화면에서 조회할 수 있습니다."))

    def refresh_report(self):
        if self._revision is not None:
            self._request(self._client.morning_brief, self._show_report)

    def _show_report(self, report):
        run = report.get("run")
        if not run:
            self._rendered_report = None
            self._report.setPlainText("아직 생성된 시황이 없습니다. 예약 시각과 AI 연결을 확인하거나 ‘지금 시황 생성’을 누르세요.")
            self._status.setText("시황 없음")
            return
        status = run["status"]
        lines = [f"<h2>아침 시황 · {escape(format_kst_time(run['cutoff_at']))}</h2>",
            f"<p>{escape(str(run['provider']))} / {escape(str(run['model']))} / {escape(str(run['reasoning_effort'] or '서비스 기본값'))}</p>"]
        if status != "completed":
            error = str(run.get('error', ''))
            error = {"brief_evidence_timestamp_missing": "원문 게시 시각과 시간 정밀도가 맞지 않아 검증을 통과하지 못했습니다.",
                     "brief_evidence_date_missing": "원문 게시 날짜가 확인되지 않아 검증을 통과하지 못했습니다.",
                     "brief_evidence_source_unverified": "검색 도구에서 확인되지 않은 출처가 있어 검증을 통과하지 못했습니다."}.get(error, error)
            label = "원래 실행 기록" if run.get("result_recovered_from_raw_response") else "상태"
            lines.append(f"<p>{label}: {escape(status)} · {escape(error)}</p>")
            if status == "running":
                lines.append("<p>재시작 뒤에도 이 상태라면 이전 요청의 완료 여부가 미확인입니다. 자동 재요청하지 않습니다.</p>")
        if run.get("result"):
            result = run["result"]
            if run.get("result_recovered_from_raw_response"):
                lines.append("<p>보존된 응답을 연구 자료로 조회했습니다. 원래 실패 기록은 유지하며 새 AI 호출은 없습니다.</p>")
            current = result.get("schema") in {"morning-market-brief-v3", "morning-market-brief-v4"}
            lines.append("<p>아래 수치·사실·열람 표기는 모델이 반환한 연구 자료입니다. 확인 부족·상충 사항은 누락·미확인 정보에 함께 표시합니다.</p>")
            lines.append("<p>장전 초기 가설 · 장중 검증 전 · 판단엔진 연결 전</p>" if result.get("schema") == "morning-market-brief-v4" else "<p>이전 계약 보고서 · 현재 엔진 입력에서 제외</p>")
            lines.append("<p>" + escape(result["evidence_summary"] if current else result.get("summary", "")).replace("\n", "<br>") + "</p>")
            evidence = {item["evidence_id"]: item for item in result.get("evidence", [])}
            def links_for(refs):
                links = []
                for ref in refs:
                    if ref not in evidence:
                        continue
                    url = evidence[ref]["url"]
                    label = escape(ref + " (모델 표기: " + evidence[ref]["verification"] + ")")
                    # Research retention must not turn an arbitrary model URI into
                    # an executable link in the desktop UI.
                    parsed = urlsplit(url)
                    links.append(f'<a href="{escape(url, quote=True)}">{label}</a>'
                        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password
                        else label + " · " + escape(url))
                return " · ".join(links)
            if current:
                for key, title in (("market_assessment", "시장 방향"), ("participation", "참여 폭"), ("concentration", "집중도")):
                    axis = result[key]
                    lines.append(f"<p><b>{title}</b>: {escape(axis['state'])} · {escape(axis['reason'])}<br>{links_for(axis['evidence_ids'])}</p>")
                calendar = result["calendar"]
                lines.append(f"<p>한국 시장: {escape(calendar['kr_session_status'])} · 최근 거래일 {escape(str(calendar['kr_last_trading_date']))} · 다음 거래일 {escape(str(calendar['kr_next_trading_date']))}<br>미국 시장: {escape(calendar['us_session_status'])} · 최근 거래일 {escape(str(calendar['us_last_trading_date']))}</p>")
            lines.append("<h3>시장 자료</h3><table border='1' cellspacing='0' cellpadding='5'><tr><th>지표</th><th>값 / 단위</th><th>변화율</th><th>기준 시각·거래일 / 상태</th><th>근거</th></tr>")
            for metric in result["market_data"]:
                links = links_for(metric["evidence_ids"]) if current else " · ".join(f'<a href="{escape(url, quote=True)}">원문</a>' for url in metric["source_urls"])
                observation = format_kst_time(metric['observed_at'], missing="미확인")
                if metric.get('timestamp_precision') == 'trading_date' and metric.get('trading_date'):
                    observation = metric['trading_date'] + " · 모델 표기: 확정 종가(거래일 기준)"
                lines.append(f"<tr><td>{escape(metric['id'])}</td><td>{escape(str(metric['value']))} {escape(metric['unit'])}</td><td>{escape(str(metric['change_pct']))}</td><td>{escape(observation)} / {escape(metric['status'])}</td><td>{links}</td></tr>")
            lines.append("</table>")
            facts = {item["fact_id"]: item for item in result.get("facts", [])}
            if current:
                lines.append("<h3>출처 · 모델 열람 표기</h3>")
                for ref, item in evidence.items():
                    lines.append("<p>" + links_for([ref]) + " · " + escape(item['url']) + "</p>")
                lines.append("<h3>사실 자료 · 모델 분류</h3>")
                for fact in facts.values():
                    lines.append(f"<p>{escape(fact['statement'])} · {escape(fact['fact_status'])}<br>{links_for(fact['evidence_ids'])}</p>")
            lines.append("<h3>핵심 재료</h3>")
            for event in result["events"]:
                links = links_for(event["evidence_ids"]) if current else " · ".join(f'<a href="{escape(url, quote=True)}">원문</a>' for url in event["source_urls"])
                fact_text = " / ".join(facts[ref]["statement"] for ref in event["fact_ids"] if ref in facts) if current else event["fact"]
                lines.append(f"<p><b>{escape(event['title'])}</b> ({escape(event['scope'])} / {escape(event['novelty'])})<br>사실: {escape(fact_text)}<br>해석: {escape(event['interpretation'])}<br>{links}</p>")
                if current:
                    persistence = event['persistence']
                    lines.append(f"<p>방향: {escape(event['direction'])} · 중요도: {escape(event['importance'])} / {escape(event['importance_reason'])}<br>신규성: {escape(event['novelty_basis'])}<br>지속성: {escape(persistence['horizon'])} · {escape(persistence['mechanism'])}<br>유지 전제: {escape(' / '.join(persistence['assumptions']))} · 재검토: {escape(format_kst_time(persistence['review_due_at']))}</p>")
            if current:
                for key, title in (("themes_to_observe", "테마 관계 후보"), ("stock_contexts", "종목 관계 후보")):
                    lines.append(f"<h3>{title}</h3>")
                    for item in result[key]:
                        label = item.get("name", item.get("security_name", ""))
                        lines.append(f"<p>{escape(label)} · proposed / {escape(item['relation_source'])}<br>{escape(item['reason'])}<br>{links_for(item['evidence_ids'])}</p>")
                lines.append("<h3>검증 가설과 관찰 조건</h3>")
                for hypothesis in result["hypotheses"]:
                    lines.append(f"<p><b>{escape(hypothesis['statement'])}</b> · {escape(hypothesis['status'])}</p>")
                    for key, title in (("confirming_signals", "확인"), ("contradicting_signals", "반대"), ("invalidation_conditions", "무효화")):
                        for signal in hypothesis[key]:
                            lines.append(f"<p>{title} · {escape(signal['data_source'])} / {escape(signal['target_ref'])}: {escape(signal['observation'])} ({escape(signal['expected_change'])})</p>")
                lines.append("<h3>누락·미확인 정보</h3><ul>" + "".join(f"<li>{escape(item['field'])}: {escape(item['reason'])}</li>" for item in result["unknowns"]) + "</ul>")
            else:
                for key in ("drivers", "risks", "sectors", "checklist", "data_gaps"):
                    for item in result.get(key, []):
                        lines.append("<p>" + escape(item["text"] if isinstance(item, dict) else item) + "</p>")
            expiry = datetime.fromtimestamp(run["expires_at"], ZoneInfo("Asia/Seoul")).strftime("%m-%d %H:%M")
            lines.append(f"<p>유효기간: {expiry} 한국 시각 · {escape(report['context']['reason'])}</p>")
        run_key = run.get("run_id") or run["cutoff_at"]
        rendered = (run_key, "".join(lines))
        if rendered != self._rendered_report:
            same_run = self._rendered_report is not None and self._rendered_report[0] == run_key
            bars = (self._report.verticalScrollBar(), self._report.horizontalScrollBar())
            positions = tuple(bar.value() for bar in bars)
            self._report.setHtml(rendered[1])
            if same_run:
                for bar, position in zip(bars, positions):
                    bar.setValue(position)
            self._rendered_report = rendered
        self._status.setText("보존 응답 연구 자료 조회" if run.get("result_recovered_from_raw_response") else
            {"completed": "시황 조회 완료", "running": "시황 생성 중", "failed": "시황 생성 실패"}.get(status, status))

    def closeEvent(self, event):
        if self._worker is not None:
            self._status.setText("진행 중인 NAS 요청이 끝나면 닫을 수 있습니다.")
            event.ignore()
            return
        self._timer.stop()
        super().closeEvent(event)
