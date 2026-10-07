from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from pathlib import Path
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
from typing import Any, Mapping

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
    QComboBox, QSpinBox, QFormLayout, QDialogButtonBox,
)

from kiwoom_monitor.presentation.historical_news_review_dialog import HistoricalNewsReviewDialog
from kiwoom_monitor.presentation.process_control import (
    AuxiliaryProcessManager,
    build_auxiliary_command,
    process_identity_document,
    write_json_command,
)
from kiwoom_monitor.research_process import (load_research_process_request,
    load_independent_comparison_request, load_development_validation_request,
    load_final_holdout_execution_request, FinalHoldoutExposureRequest)
from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy, campaign_worker_retry_delay_ms
from kiwoom_monitor.application.research_search import ExperimentSpec
from kiwoom_monitor.application.research_evaluation import DevelopmentEvidence
from kiwoom_monitor.application.research_families import get_research_family
from kiwoom_monitor.application.research_hypotheses import (
    DevelopmentEvidenceRef,
    HypothesisGenerationRequest,
    build_development_evidence_snapshot,
    generate_research_hypotheses,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository


def _record_research_operation_owner(files: Mapping[str, Path], process: object, kind: str) -> bool:
    """Persist a child identity only when its PID and creation token are verifiable."""
    pid = getattr(process, 'pid', None)
    if type(pid) is not int or pid <= 0:
        return False
    identity = process_identity_document(pid)
    if not identity['start_token']:
        return False
    write_json_command(files['owner'], {
        'version': 'research_operation_owner/v1', 'kind': kind,
        'pid': identity['pid'], 'start_token': identity['start_token'],
        'request': str(files['request'].resolve()),
        'request_sha256': hashlib.sha256(files['request'].read_bytes()).hexdigest(),
        'result': str(files['result'].resolve()),
        'cancel': str(files['cancel'].resolve()),
    })
    return True


class ResearchDialog(QDialog):
    """고정 JSON 요청을 낮은 우선순위의 별도 프로세스에서 실행한다."""

    def __init__(self, state_dir: Path, parent=None, *, theme_repository: object | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("전략 연구")
        self.resize(880, 480)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._state_dir = Path(state_dir)
        self._theme_repository = theme_repository
        self._settings = QSettings("KiwoomMonitor", "ResearchDialog")
        self._manager = AuxiliaryProcessManager()
        self._result_path = self._state_dir / "last_research_result.json"
        self._cancel_path = self._state_dir / "research_cancel.request"
        self._campaign_path = self._state_dir / 'research_campaign_selection.json'
        self._campaign_selection: dict[str, str] | None = None
        self._campaign_suspended = False
        self._campaign_process = False
        self._campaign_resume_pending = False
        self._last_campaign_document = ''
        self._worker_claim: dict[str, Any] | None = None
        self._registration: dict[str, Any] | None = None
        self._comparison_dialog: IndependentComparisonDialog | None = None
        self._validation_dialog: DevelopmentValidationDialog | None = None
        self._final_holdout_dialog: FinalHoldoutDialog | None = None
        self._historical_news_review_dialog: HistoricalNewsReviewDialog | None = None

        self._request_path = QLineEdit(str(self._settings.value("request_path", "")))
        self._request_path.setPlaceholderText("연구 요청 JSON 파일을 선택하세요")
        browse = QPushButton("요청 파일 선택")
        browse.clicked.connect(self._browse_request)
        request_row = QHBoxLayout()
        request_row.addWidget(self._request_path, 1)
        request_row.addWidget(browse)

        self._summary = QLabel("요청 파일을 선택하면 데이터·비용·fold 설정을 검사합니다.")
        self._summary.setWordWrap(True)
        self._status = QLabel("대기")
        self._continuous = QCheckBox("남은 실험 자동 재개")
        self._continuous.setChecked(
            self._settings.value("continuous_resume", False, type=bool)
        )
        self._continuous.setToolTip(
            "한 번의 시간 예산이 끝나도 같은 동결 데이터의 남은 실험을 자동으로 이어갑니다."
        )
        self._continuous.toggled.connect(self._continuous_changed)
        self._run = QPushButton("기준선 비교 실행")
        self._run.clicked.connect(self._start)
        self._cancel = QPushButton("취소")
        self._cancel.setEnabled(False)
        self._cancel.clicked.connect(self._request_cancel)
        actions = QHBoxLayout()
        actions.addWidget(self._status, 1)
        actions.addWidget(self._continuous)
        actions.addWidget(self._run)
        actions.addWidget(self._cancel)

        campaign_actions = QHBoxLayout()
        self._campaign_status = QLabel('지속 연구 · 등록된 캠페인 없음')
        self._register_campaign = QPushButton('캠페인에 요청 등록')
        self._register_campaign.clicked.connect(self._register_campaign_request)
        self._campaign_run = QPushButton('시작 / 재개')
        self._campaign_run.clicked.connect(self._start_campaign)
        self._campaign_pause = QPushButton('일시정지')
        self._campaign_pause.clicked.connect(lambda: self._set_campaign_state('PAUSED'))
        self._campaign_stop = QPushButton('중지')
        self._campaign_stop.clicked.connect(lambda: self._set_campaign_state('STOPPED'))
        campaign_actions.addWidget(self._campaign_status, 1)
        for button in (self._register_campaign, self._campaign_run, self._campaign_pause, self._campaign_stop):
            campaign_actions.addWidget(button)
        for button in (self._campaign_run, self._campaign_pause, self._campaign_stop):
            button.setEnabled(False)
        self._campaign_budget = QPushButton('실험 예산 / 재시도')
        self._campaign_budget.setEnabled(False)
        self._campaign_budget.clicked.connect(self._edit_campaign_budget)
        self._campaign_inputs = QPushButton('새 자료 폴더')
        self._campaign_inputs.setEnabled(False)
        self._campaign_inputs.clicked.connect(self._edit_campaign_inputs)
        self._campaign_hypotheses = QPushButton('자동 가설 설정 / 현황')
        self._campaign_hypotheses.setEnabled(False)
        self._campaign_hypotheses.clicked.connect(self._edit_campaign_hypotheses)
        budget_actions = QHBoxLayout()
        self._review_historical_news = QPushButton('과거 뉴스 검토')
        self._review_historical_news.clicked.connect(self._show_historical_news_review)
        budget_actions.addWidget(self._review_historical_news)
        self._compare_partitions = QPushButton('독립 구간 결과 비교')
        self._compare_partitions.clicked.connect(self._show_independent_comparison)
        budget_actions.addWidget(self._compare_partitions)
        self._validate_partitions = QPushButton('여러 구간 순차 검증')
        self._validate_partitions.clicked.connect(self._show_development_validation)
        budget_actions.addWidget(self._validate_partitions)
        self._evaluate_final = QPushButton('최종 평가')
        self._evaluate_final.clicked.connect(self._show_final_holdout)
        budget_actions.addWidget(self._evaluate_final)
        budget_actions.addStretch()
        budget_actions.addWidget(self._campaign_hypotheses)
        budget_actions.addWidget(self._campaign_budget)
        budget_actions.addWidget(self._campaign_inputs)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels((
            "구간", "상태", "기준 손익", "필터 손익", "차이",
            "회피 손실", "놓친 이익",
        ))
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)

        layout = QVBoxLayout(self)
        layout.addLayout(request_row)
        layout.addWidget(self._summary)
        layout.addLayout(actions)
        layout.addLayout(campaign_actions)
        layout.addLayout(budget_actions)
        layout.addWidget(self._table)

        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self._poll_process)
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.setInterval(1000)
        self._resume_timer.timeout.connect(self._resume_after_slice)
        self._worker_retry_timer = QTimer(self)
        self._worker_retry_timer.setSingleShot(True)
        self._worker_retry_timer.timeout.connect(self._restore_campaign)
        self._request_path.editingFinished.connect(self._preview_request)
        if self._request_path.text().strip():
            QTimer.singleShot(0, self._preview_request)
        QTimer.singleShot(0, self._restore_campaign)
        geometry = self._settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def _show_historical_news_review(self) -> None:
        if self._historical_news_review_dialog is None:
            self._historical_news_review_dialog = HistoricalNewsReviewDialog(
                self._state_dir, self, theme_repository=self._theme_repository,
            )
        self._historical_news_review_dialog.show()
        self._historical_news_review_dialog.raise_()
        self._historical_news_review_dialog.activateWindow()

    def _show_development_validation(self) -> None:
        if self._validation_dialog is None:
            self._validation_dialog = DevelopmentValidationDialog(self._state_dir, self)
        self._validation_dialog.show()
        self._validation_dialog.raise_()
        self._validation_dialog.activateWindow()

    def _show_independent_comparison(self) -> None:
        if self._comparison_dialog is None:
            self._comparison_dialog = IndependentComparisonDialog(self._state_dir, self)
        self._comparison_dialog.show()
        self._comparison_dialog.raise_()
        self._comparison_dialog.activateWindow()

    def _show_final_holdout(self) -> None:
        if self._final_holdout_dialog is None:
            self._final_holdout_dialog = FinalHoldoutDialog(self._state_dir, self)
        self._final_holdout_dialog.show()
        self._final_holdout_dialog.raise_()
        self._final_holdout_dialog.activateWindow()

    def _campaign_repository(self) -> ResearchRepository:
        if self._campaign_selection is None:
            raise ValueError('등록된 캠페인이 없습니다')
        return ResearchRepository(Path(self._campaign_selection['database']))

    def _restore_campaign(self) -> None:
        if self._registration is not None:
            return
        if self._campaign_selection is None:
            if not self._campaign_path.is_file():
                return
            try:
                selection = json.loads(self._campaign_path.read_text(encoding='utf-8'))
                if selection.get('version') != 'research_campaign_selection/v1' or not all(
                    isinstance(selection.get(key), str) and selection[key] for key in ('database', 'runs_dir', 'campaign_id')
                ) or not Path(selection['database']).is_file():
                    raise ValueError('저장된 캠페인 경로를 확인하세요')
                self._campaign_selection = selection
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                self._campaign_status.setText(f'캠페인 복원 확인 필요 · {exc}')
                return
        try:
            campaign = self._campaign_repository().load_campaign(self._campaign_selection['campaign_id'])
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'캠페인 복원 확인 필요 · {exc}')
            return
        self._display_campaign(campaign)
        if campaign['desired_state'] == 'RUNNING' and not self._campaign_suspended:
            self._launch_campaign_worker()

    def _register_campaign_request(self) -> None:
        if self._manager.is_running or self._registration is not None:
            return
        try:
            request = load_research_process_request(Path(self._request_path.text().strip()))
            if request.mode != 'limited_search' or request.search is None:
                raise ValueError('제한 탐색 요청만 캠페인에 등록할 수 있습니다')
            if request.development_partition is not None:
                self._start_campaign_registration(request)
                return
            if self._campaign_selection is None:
                self._state_dir.mkdir(parents=True, exist_ok=True)
                selection = {'version': 'research_campaign_selection/v1', 'campaign_id': 'campaign_' + uuid.uuid4().hex,
                             'database': str(request.database), 'runs_dir': str(request.runs_dir)}
                repository = ResearchRepository(request.database)
                repository.create_campaign(selection['campaign_id'], '지속 연구', ResearchCampaignPolicy())
                repository.enqueue_campaign_experiment(selection['campaign_id'], request.search, request.dataset)
                write_json_command(self._campaign_path, selection)
                self._campaign_selection = selection
            else:
                if Path(self._campaign_selection['database']).resolve() != request.database.resolve() or Path(self._campaign_selection['runs_dir']).resolve() != request.runs_dir.resolve():
                    raise ValueError('같은 캠페인은 동일한 연구 DB와 결과 폴더를 사용해야 합니다')
                repository = self._campaign_repository()
                repository.enqueue_campaign_experiment(self._campaign_selection['campaign_id'], request.search, request.dataset)
            self._display_campaign(repository.load_campaign(self._campaign_selection['campaign_id']))
        except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error) as exc:
            self._schedule_worker_retry()
            self._campaign_status.setText(f'캠페인 등록 확인 필요 · {exc}')

    def _start_campaign_registration(self, request) -> None:
        self._resume_timer.stop()
        self._worker_retry_timer.stop()
        self._state_dir.mkdir(parents=True, exist_ok=True)
        if self._campaign_selection is None:
            selection = {'version': 'research_campaign_selection/v1', 'campaign_id': 'campaign_' + uuid.uuid4().hex,
                         'database': str(request.database), 'runs_dir': str(request.runs_dir)}
            repository = ResearchRepository(request.database)
            repository.create_campaign(selection['campaign_id'], '지속 연구', ResearchCampaignPolicy())
            # Persist the paused owner before launch so commit-before-result is recoverable.
            write_json_command(self._campaign_path, selection)
            self._campaign_selection = selection
        selection = dict(self._campaign_selection)
        if Path(selection['database']).resolve() != request.database.resolve() or Path(selection['runs_dir']).resolve() != request.runs_dir.resolve():
            raise ValueError('같은 캠페인은 동일한 연구 DB와 결과 폴더를 사용해야 합니다')
        campaign = self._campaign_repository().load_campaign(selection['campaign_id'])
        operation = uuid.uuid4().hex
        files = {name: self._state_dir / f'campaign_registration_{operation}.{suffix}'
                 for name, suffix in (('request', 'json'), ('result', 'result.json'), ('cancel', 'cancel'))}
        self._registration = {**files, 'selection': selection, 'source_spec': request.search}
        document = {
            'mode': request.mode, 'family': request.family, 'dataset': str(request.dataset),
            'database': str(request.database), 'runs_dir': str(request.runs_dir),
            'strategy': request.strategy.to_dict(), 'execution': request.execution.to_dict(),
            'evaluation': request.evaluation.to_dict(), 'session_profile': request.session_profile,
            'development_partition': request.development_partition.to_dict(), 'search': request.search.to_dict(),
        }
        try:
            write_json_command(files['request'], document)
            command = build_auxiliary_command('kiwoom_monitor.research_process', '--research-process', [
                '--request', str(files['request']), '--register-campaign', selection['campaign_id'],
                '--result', str(files['result']), '--cancel', str(files['cancel']),
            ])
            self._manager.start(command, Path.cwd(), below_normal_priority=True)
        except (OSError, ValueError, RuntimeError):
            self._clear_campaign_registration()
            raise
        self._campaign_process = False
        self._display_campaign(campaign)
        for button in (self._register_campaign, self._run, self._continuous):
            button.setEnabled(False)
        self._cancel.setEnabled(True)
        self._status.setText('별도 프로세스에서 개발 구간을 캠페인에 등록 중…')
        self._poll.start()

    def _clear_campaign_registration(self) -> None:
        registration = self._registration
        self._registration = None
        if registration is not None:
            for name in ('request', 'result', 'cancel'):
                try:
                    registration[name].unlink(missing_ok=True)
                except OSError as exc:
                    self._status.setToolTip(f'등록 임시 파일 정리 확인 · {exc}')
        self._register_campaign.setEnabled(True)
        self._run.setEnabled(True)
        self._continuous.setEnabled(True)
        self._cancel.setEnabled(False)

    def _poll_campaign_registration(self) -> None:
        process = self._manager.process
        if process is None or process.poll() is None:
            return
        registration = self._registration
        self._poll.stop()
        self._manager.clear()
        message = ''
        try:
            result = json.loads(registration['result'].read_text(encoding='utf-8'))
            if not isinstance(result, Mapping):
                raise ValueError('등록 결과가 올바르지 않습니다')
            if result.get('status') == 'cancelled' and process.returncode == 2:
                message = '캠페인 요청 등록 취소'
            elif process.returncode != 0 or result.get('status') != 'ok':
                message = '캠페인 등록 확인 필요 · ' + str(result.get('reason', '등록 프로세스 실행 실패'))
            else:
                selection = registration['selection']
                if result.get('kind') != 'campaign_registration' or result.get('campaign_id') != selection['campaign_id'] or self._campaign_selection != selection or not isinstance(result.get('registered'), bool):
                    raise ValueError('요청과 다른 캠페인 등록 결과입니다')
                repository = self._campaign_repository()
                job = repository.load_campaign_job(selection['campaign_id'], result.get('job_id'))
                if job is None or ExperimentSpec.from_dict(job['request']).experiment_id != result.get('experiment_id') or ExperimentSpec.from_dict(job['source_request']).evidence_dict() != registration['source_spec'].evidence_dict():
                    raise ValueError('저장된 요청과 등록 결과가 일치하지 않습니다')
                message = '캠페인 요청 등록 완료' if result.get('registered') else '이미 등록된 캠페인 요청입니다'
                if registration['cancel'].is_file():
                    message = '취소 요청 전에 캠페인 등록이 완료되었습니다'
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error) as exc:
            message = f'캠페인 등록 확인 필요 · {exc}'
        finally:
            self._clear_campaign_registration()
        self._status.setText(message)
        try:
            self._display_campaign(self._campaign_repository().load_campaign(self._campaign_selection['campaign_id']))
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'캠페인 상태 확인 필요 · {exc}')
        self._schedule_worker_retry()

    def _display_campaign(self, campaign: Mapping[str, Any]) -> None:
        registering = self._registration is not None
        self._campaign_inputs.setEnabled(not registering and campaign.get('desired_state') != 'RUNNING' and not self._manager.is_running)
        labels = {'RUNNING': '실행 중 / 다음 작업 대기', 'PAUSED': '일시정지', 'STOPPED': '중지',
                  'WAITING_DATA': '등록할 자료 대기', 'SEARCH_SPACE_EXHAUSTED': '등록된 조합 검증 완료',
                  'WAITING_HYPOTHESIS': '자동 가설 대기',
                  'RESOURCE_BLOCKED': '자원 한도 도달', 'NEEDS_ATTENTION': '확인 필요'}
        state = str(campaign.get('operational_state', ''))
        reason = str(campaign.get('reason', ''))
        if reason in ('trial_budget_exhausted', 'budget_expansion_required'):
            state_text = '실험 횟수 한도 도달 · 예산 확대 필요'
        elif reason == 'worker_restart_backoff':
            state_text = '작업자 재시도 대기'
        elif reason == 'worker_retry_limit_reached':
            state_text = '작업자 반복 종료 · 원인 확인 후 시작 / 재개하세요'
        elif reason == 'hypothesis_template_missing':
            state_text = '자동 가설 기준 실험 등록 대기'
        elif reason == 'no_registered_hypotheses':
            state_text = '자동 가설 등록 대기'
        elif reason == 'hypothesis_space_exhausted':
            state_text = '등록된 자동 가설 검증 완료 · 새 가설 대기'
        elif reason == 'hypothesis_generation_policy_missing':
            state_text = '완료 가설 있음 · 허용값 설정 대기'
        elif reason == 'next_hypothesis_generation_ready':
            state_text = '완료 개발 근거에서 다음 가설 생성 대기'
        elif reason == 'hypothesis_generation_policy_invalid':
            state_text = '자동 가설 허용값 확인 필요'
        elif reason == 'hypothesis_limit_reached':
            state_text = '자동 가설 전체 개수 상한 도달'
        else:
            state_text = labels.get(state, state)
        self._campaign_status.setText(f"지속 연구 · {state_text} · 회차 {campaign.get('cycle_sequence', 0)}")
        self._campaign_run.setEnabled(not registering and not self._manager.is_running)
        self._campaign_pause.setEnabled(not registering and campaign.get('desired_state') == 'RUNNING')
        self._campaign_stop.setEnabled(not registering and campaign.get('desired_state') != 'STOPPED')
        self._campaign_budget.setEnabled(not registering and not self._manager.is_running and campaign.get('desired_state') != 'RUNNING')
        self._campaign_hypotheses.setEnabled(
            not registering and not self._manager.is_running
            and campaign.get('desired_state') == 'PAUSED'
        )

    def _edit_campaign_hypotheses(self) -> None:
        if self._campaign_selection is None or self._manager.is_running or self._registration is not None:
            return
        try:
            repository = self._campaign_repository()
            campaign_id = self._campaign_selection['campaign_id']
            campaign = repository.load_campaign(campaign_id)
            if campaign['desired_state'] != 'PAUSED':
                raise ValueError('먼저 캠페인을 일시정지하세요')
            jobs = tuple(
                job for job in repository.load_campaign_jobs(campaign_id)
                if job['source_kind'] != 'auto_hypothesis'
            )
            if not jobs:
                raise ValueError('기준 실험을 먼저 등록하세요')
            hypotheses = repository.load_campaign_registered_hypotheses(campaign_id)
            bindings = {
                row['hypothesis_id']: row
                for row in repository.load_campaign_hypothesis_bindings(campaign_id)
            }
            expansions = repository.load_campaign_hypothesis_expansions(campaign_id)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'자동 가설 설정 확인 필요 · {exc}')
            return

        editor = QDialog(self)
        editor.setWindowTitle('자동 가설 설정과 현재 근거')
        layout = QFormLayout(editor)
        enabled = QCheckBox('완료된 개발 결과에서 다음 가설 자동 생성', editor)
        enabled.setChecked(bool(campaign['policy'].get('auto_hypotheses', False)))
        family = QComboBox(editor)
        family_jobs: dict[str, Mapping[str, Any]] = {}
        for job in jobs:
            spec = ExperimentSpec.from_dict(job['request'])
            family_id = spec.family_allowlist[0]
            if family_id not in family_jobs:
                family_jobs[family_id] = job
                family.addItem(family_id, family_id)
        parameter = QComboBox(editor)
        values = QLineEdit(editor)
        values.setPlaceholderText('예: 300, 500, 700')
        seed, per_cycle, total = (QSpinBox(editor) for _ in range(3))
        seed.setRange(-2_147_483_648, 2_147_483_647)
        seed.setValue(int(campaign['policy'].get('hypothesis_seed', 0)))
        per_cycle.setRange(1, 1000)
        per_cycle.setValue(int(campaign['policy'].get('max_generated_hypotheses_per_cycle', 8)))
        total.setRange(1, 10000)
        total.setValue(int(campaign['policy'].get('max_hypotheses', 1000)))

        def select_family() -> None:
            family_id = str(family.currentData())
            parameter.clear()
            for name in sorted(get_research_family(family_id).searchable_parameters):
                parameter.addItem(name, name)
            configured = campaign['policy'].get('hypothesis_parameter_values', {}).get(family_id, {})
            selected = next(iter(configured), '')
            if selected:
                parameter.setCurrentIndex(parameter.findData(selected))
                values.setText(', '.join(str(value) for value in configured[selected]))
            else:
                values.clear()

        family.currentIndexChanged.connect(select_family)
        select_family()
        layout.addRow(enabled)
        layout.addRow('전략 Family', family)
        layout.addRow('한 번에 비교할 항목', parameter)
        layout.addRow('허용값 (쉼표 구분)', values)
        layout.addRow('고정 순서 seed', seed)
        layout.addRow('완료 부모 1개당 생성 상한', per_cycle)
        layout.addRow('캠페인 전체 가설 상한', total)
        note = QLabel(
            '현재 부모의 값과 같은 값은 건너뜁니다. 한 자식에서는 선택한 항목 하나만 바뀌며, '
            '최종평가 결과는 다음 가설 근거로 사용하지 않습니다.', editor,
        )
        note.setWordWrap(True)
        layout.addRow(note)

        table = QTableWidget(0, 6, editor)
        table.setHorizontalHeaderLabels(('Family', '종류', '변경', '상태', '개발 근거', '확장'))
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        expansion_by_parent: dict[str, list[Mapping[str, Any]]] = {}
        for row in expansions:
            expansion_by_parent.setdefault(row['parent_hypothesis_id'], []).append(row)
        for hypothesis in hypotheses:
            row = table.rowCount()
            table.insertRow(row)
            binding = bindings[hypothesis.hypothesis_id]
            changes = (
                f'{hypothesis.changed_parameter}: {hypothesis.changed_from} → {hypothesis.changed_to}'
                if hypothesis.changed_parameter else '기준 설정'
            )
            evidence = ', '.join(value[-12:] for value in hypothesis.evidence_refs)
            expansion = ', '.join(
                f"r{item['policy_revision']} {item['state']} {item['generated_count']}개"
                for item in expansion_by_parent.get(hypothesis.hypothesis_id, ())
            ) or '-'
            for column, text in enumerate((
                hypothesis.family_id, hypothesis.kind, changes, binding['state'], evidence, expansion,
            )):
                table.setItem(row, column, QTableWidgetItem(str(text)))
        layout.addRow(f'현재 가설 {len(hypotheses)}개 · 확장 기록 {len(expansions)}개', table)
        status = QLabel('', editor)
        status.setWordWrap(True)
        layout.addRow(status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, editor)

        def save() -> None:
            try:
                parsed_values = tuple(dict.fromkeys(
                    int(item.strip()) for item in values.text().split(',') if item.strip()
                ))
                if enabled.isChecked() and not parsed_values:
                    raise ValueError('자동 가설을 켜려면 허용값을 하나 이상 입력하세요')
                family_id = str(family.currentData())
                configured = dict(campaign['policy'].get('hypothesis_parameter_values', {}))
                if parsed_values:
                    configured[family_id] = {str(parameter.currentData()): parsed_values}
                document = dict(campaign['policy'])
                document.pop('version', None)
                document.update(
                    auto_hypotheses=enabled.isChecked(),
                    hypothesis_seed=seed.value(),
                    max_generated_hypotheses_per_cycle=per_cycle.value(),
                    max_hypotheses=total.value(),
                    hypothesis_parameter_values=configured,
                )
                policy = ResearchCampaignPolicy(**document)
                if not self._save_campaign_hypothesis_policy(
                    policy, campaign['revision'], family_jobs[family_id]['job_id'],
                ):
                    status.setText(self._campaign_status.text())
                    return
            except (TypeError, ValueError) as exc:
                status.setText(str(exc))
                return
            editor.accept()

        buttons.accepted.connect(save)
        buttons.rejected.connect(editor.reject)
        layout.addRow(buttons)
        editor.exec()
        editor.deleteLater()

    def _save_campaign_hypothesis_policy(
        self,
        policy: ResearchCampaignPolicy,
        expected_revision: int,
        template_job_id: str,
    ) -> bool:
        if self._campaign_selection is None or self._manager.is_running or self._registration is not None:
            return False
        try:
            repository = self._campaign_repository()
            campaign_id = self._campaign_selection['campaign_id']
            campaign = repository.load_campaign(campaign_id)
            if campaign['desired_state'] != 'PAUSED':
                raise ValueError('먼저 캠페인을 일시정지하세요')
            job = repository.load_campaign_job(campaign_id, template_job_id)
            if job is None or job['source_kind'] == 'auto_hypothesis':
                raise ValueError('등록된 기준 실험을 선택하세요')
            existing = repository.load_campaign_registered_hypotheses(campaign_id)
            if len(existing) > policy.max_hypotheses:
                raise ValueError('전체 가설 상한은 이미 등록된 가설 수보다 작을 수 없습니다')
            bootstrap = ()
            if policy.auto_hypotheses:
                spec = ExperimentSpec.from_dict(job['request'])
                family_id = spec.family_allowlist[0]
                allowed = policy.hypothesis_parameter_values.get(family_id)
                if not allowed:
                    raise ValueError('선택한 Family의 허용값을 입력하세요')
                baseline = spec.research_context.get('baseline_strategy')
                if not isinstance(baseline, Mapping):
                    raise ValueError('기준 실험에 정규화된 전략 설정이 없습니다')
                next_revision = expected_revision + int(campaign['policy'] != policy.to_dict())
                snapshot = build_development_evidence_snapshot(
                    f'campaign-policy:{campaign_id}:{next_revision}',
                    DevelopmentEvidence(
                        status='NOT_APPLICABLE',
                        reasons=('user_configured_campaign_baseline',),
                        fold_refs=(), net_pnl_won=None, max_drawdown_won=None,
                        closed_trade_count=0, active_day_count=0,
                    ),
                )
                bootstrap = generate_research_hypotheses(HypothesisGenerationRequest(
                    research_scope_id=f'campaign:{campaign_id}',
                    family_id=family_id,
                    factor_allowlist=tuple(spec.factor_allowlist),
                    baseline_parameters=dict(baseline),
                    allowed_parameter_values={key: tuple(values) for key, values in allowed.items()},
                    development_evidence_refs=(DevelopmentEvidenceRef(snapshot.evidence_id),),
                    seed=policy.hypothesis_seed,
                    max_variants=min(
                        policy.max_generated_hypotheses_per_cycle,
                        max(0, policy.max_hypotheses - 1),
                    ),
                ))
            revision = repository.revise_campaign_policy(
                campaign_id, policy, expected_revision=expected_revision,
            )
            if bootstrap:
                family_id = bootstrap[0].family_id
                if not any(item.family_id == family_id for item in existing):
                    repository.save_research_hypotheses(bootstrap)
                    repository.register_campaign_hypotheses(
                        campaign_id, tuple(item.hypothesis_id for item in bootstrap),
                    )
            self._display_campaign(repository.load_campaign(campaign_id))
            self._campaign_status.setToolTip(f'자동 가설 정책 revision {revision}')
            return True
        except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'자동 가설 설정 확인 필요 · {exc}')
            return False

    def _edit_campaign_budget(self) -> None:
        if self._campaign_selection is None or self._manager.is_running or self._registration is not None:
            return
        try:
            repository = self._campaign_repository()
            campaign_id = self._campaign_selection['campaign_id']
            if repository.load_campaign(campaign_id)['desired_state'] == 'RUNNING':
                raise ValueError('먼저 일시정지한 뒤 예산을 변경하세요')
            jobs = repository.load_campaign_jobs(campaign_id)
            if not jobs:
                raise ValueError('등록된 실험이 없습니다')
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'예산 확인 필요 · {exc}')
            return
        editor = QDialog(self)
        editor.setWindowTitle('실험 예산')
        layout = QFormLayout(editor)
        selector = QComboBox()
        for job in jobs:
            selector.addItem(f"#{job['accepted_sequence']} · {job['state']} · {job['job_id'][-8:]}")
        trials, seconds, memory, cpu = (QSpinBox() for _ in range(4))
        trials.setRange(0, 1000)
        seconds.setRange(1, max(86400, *(job['request']['max_seconds'] for job in jobs)))
        memory.setRange(128, 4096)
        cpu.setRange(10, 100)
        status = QLabel('완료된 결과는 유지하며 늘어난 한도만 이어서 실행합니다.')
        status.setWordWrap(True)
        for label, widget in (('실험', selector), ('누적 실험 횟수 한도', trials), ('한 회차 시간 한도(초)', seconds),
                              ('메모리 한도(MiB)', memory), ('CPU 사용 목표(%)', cpu)):
            layout.addRow(label, widget)
        layout.addRow(status)
        save = QPushButton('예산 저장')
        retry = QPushButton('차단 / 실패 실험 다시 대기')
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(save, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(retry, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(editor.reject)
        layout.addRow(buttons)

        def select(index):
            spec = ExperimentSpec.from_dict(jobs[index]['request'])
            trials.setMinimum(spec.max_trials)
            trials.setValue(spec.max_trials)
            seconds.setValue(max(1, spec.max_seconds))
            memory.setValue(int(spec.resource_budget.get('memory_mb', 512)))
            cpu.setValue(int(spec.resource_budget.get('cpu_duty_percent', 50)))
            retry.setEnabled(jobs[index]['state'] in ('RESOURCE_BLOCKED', 'NEEDS_ATTENTION'))

        def save_budget():
            job = jobs[selector.currentIndex()]
            spec = ExperimentSpec.from_dict(job['request'])
            updated = replace(spec, max_trials=trials.value(), max_seconds=seconds.value(),
                              resource_budget={**spec.resource_budget, 'memory_mb': memory.value(), 'cpu_duty_percent': cpu.value()})
            if self._save_campaign_budget(job['job_id'], updated, job['budget_revision']):
                editor.accept()
            else:
                status.setText(self._campaign_status.text())

        def retry_job():
            if self._retry_campaign_work(jobs[selector.currentIndex()]['job_id']):
                editor.accept()
            else:
                status.setText(self._campaign_status.text())

        selector.currentIndexChanged.connect(select)
        save.clicked.connect(save_budget)
        retry.clicked.connect(retry_job)
        select(0)
        editor.exec()
        editor.deleteLater()

    def _edit_campaign_inputs(self) -> None:
        if self._campaign_selection is None or self._manager.is_running or self._registration is not None:
            return
        try:
            repository = self._campaign_repository()
            campaign_id = self._campaign_selection['campaign_id']
            if repository.load_campaign(campaign_id)['desired_state'] == 'RUNNING':
                self._campaign_status.setText('일시정지 후 작업자 종료를 기다려 주세요')
                return
            jobs = repository.load_campaign_jobs(campaign_id)
            sources = repository.load_campaign_input_sources(campaign_id)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'새 자료 설정 확인 필요 · {exc}')
            return
        if not jobs:
            return
        editor = QDialog(self)
        editor.setWindowTitle('새 연구 자료 자동 등록')
        layout = QFormLayout(editor)
        template = QComboBox(editor)
        for job in jobs:
            template.addItem(job['job_id'], job['job_id'])
        folder = QLineEdit(editor)
        enabled = QCheckBox('자동 등록 켜기', editor)
        enabled.setChecked(True)
        nas_prepare = QCheckBox('NAS에서 새 자료 자동 준비', editor)
        rolling_daily = QCheckBox('새 거래일의 TRAIN/VALIDATION 평가 기간 확장', editor)
        storage_cap = QSpinBox(editor)
        storage_cap.setRange(0, 1000000)
        storage_cap.setSuffix(' GB')
        storage_cap.setSpecialValueText('무제한')
        storage_cap.setToolTip('이 상위 폴더의 파일 용량 상한입니다. 가득 차면 새 자료 준비를 보류하며 기존 파일은 삭제하지 않습니다.')
        browse = QPushButton('폴더 선택', editor)
        def choose_folder():
            selected = QFileDialog.getExistingDirectory(editor, '준비된 자료 폴더들이 들어 있는 상위 폴더', folder.text())
            if selected:
                folder.setText(selected)
        browse.clicked.connect(choose_folder)
        def select_template():
            source = next((item for item in sources if item['template_job_id'] == template.currentData()), None)
            folder.setText(source['root'] if source else '')
            enabled.setChecked(bool(source['enabled']) if source else True)
            nas_prepare.setChecked(bool(source['nas_auto_prepare']) if source else False)
            rolling_daily.setChecked(bool(source['rolling_daily']) if source else False)
            storage_cap.setValue((int(source['storage_cap_bytes']) + 1024 ** 3 - 1) // 1024 ** 3 if source else 0)
            status.setText(f"{source['state']} · {source['reason']}" if source else '')
        template.currentIndexChanged.connect(select_template)
        layout.addRow('기준 실험', template)
        layout.addRow('상위 폴더', folder)
        layout.addRow(browse)
        layout.addRow(enabled)
        layout.addRow(nas_prepare)
        layout.addRow(rolling_daily)
        layout.addRow('새 자료 폴더 용량 상한 (0=무제한)', storage_cap)
        layout.addRow(QLabel('거래일 확장과 NAS 자동 준비를 함께 켜면 완료된 새 날짜를 하루씩 준비합니다. FINAL/OOS는 확장하지 않습니다.', editor))
        status = QLabel('', editor)
        layout.addRow(status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, editor)
        def save():
            try:
                if not folder.text().strip():
                    raise ValueError('자료 폴더를 선택해 주세요')
                repository.save_campaign_input_source(campaign_id, template.currentData(), Path(folder.text()), enabled=enabled.isChecked(), nas_auto_prepare=nas_prepare.isChecked(), nas_config_path=self._state_dir.parent / 'data_source.json' if nas_prepare.isChecked() else None, storage_cap_bytes=storage_cap.value() * 1024 ** 3, rolling_daily=rolling_daily.isChecked())
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                status.setText(str(exc))
                return
            editor.accept()
        buttons.accepted.connect(save)
        buttons.rejected.connect(editor.reject)
        layout.addRow(buttons)
        select_template()
        editor.exec()
        editor.deleteLater()

    def _save_campaign_budget(self, job_id, spec: ExperimentSpec, expected_revision: int) -> bool:
        if self._registration is not None:
            return False
        try:
            repository = self._campaign_repository()
            campaign_id = self._campaign_selection['campaign_id']
            repository.revise_campaign_job_budget(campaign_id, job_id, spec, expected_revision=expected_revision)
            self._display_campaign(repository.load_campaign(campaign_id))
            return True
        except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'예산 변경 확인 필요 · {exc}')
            return False

    def _retry_campaign_work(self, job_id) -> bool:
        if self._registration is not None:
            return False
        try:
            repository = self._campaign_repository()
            campaign_id = self._campaign_selection['campaign_id']
            if repository.load_campaign(campaign_id)['desired_state'] == 'RUNNING':
                raise ValueError('먼저 일시정지한 뒤 재시도하세요')
            repository.retry_campaign_job(campaign_id, job_id)
            self._display_campaign(repository.load_campaign(campaign_id))
            return True
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'재시도 확인 필요 · {exc}')
            return False

    def _start_campaign(self) -> None:
        if self._manager.is_running or self._campaign_selection is None or self._registration is not None:
            return
        self._campaign_suspended = False
        self._worker_retry_timer.stop()
        try:
            repository = self._campaign_repository()
            if repository.load_campaign_worker(self._campaign_selection['campaign_id'])['state'] in ('FAILED', 'NEEDS_ATTENTION'):
                repository.retry_campaign_worker(self._campaign_selection['campaign_id'])
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'작업자 재개 확인 필요 · {exc}')
            return
        if self._set_campaign_state('RUNNING'):
            self._launch_campaign_worker()

    def _set_campaign_state(self, state: str) -> bool:
        if self._campaign_selection is None or self._registration is not None:
            return False
        try:
            if state != 'RUNNING':
                self._worker_retry_timer.stop()
            repository = self._campaign_repository()
            repository.set_campaign_desired_state(self._campaign_selection['campaign_id'], state)
            if state != 'RUNNING' and self._campaign_process and self._manager.is_running:
                self._cancel_path.parent.mkdir(parents=True, exist_ok=True)
                self._cancel_path.write_text('cancel\n', encoding='ascii')
            self._display_campaign(repository.load_campaign(self._campaign_selection['campaign_id']))
            return True
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'캠페인 상태 변경 확인 필요 · {exc}')
            return False

    def _launch_campaign_worker(self) -> None:
        if self._manager.is_running or self._campaign_selection is None or self._registration is not None:
            return
        try:
            repository = self._campaign_repository()
            claim = repository.claim_campaign_worker(self._campaign_selection['campaign_id'], owner_token=uuid.uuid4().hex)
            if claim is None:
                self._display_campaign(repository.load_campaign(self._campaign_selection['campaign_id']))
                self._schedule_worker_retry()
                return
            self._worker_claim = claim
            self._state_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'작업자 시작 확인 필요 · {exc}')
            if self._worker_claim is not None:
                self._finish_worker('FAILED', f'launch_directory_error: {exc}')
                self._schedule_worker_retry()
            return
        try:
            self._cancel_path.unlink(missing_ok=True)
            self._result_path.unlink(missing_ok=True)
        except OSError as exc:
            self._finish_worker('FAILED', f'launch_files_error: {exc}')
            self._schedule_worker_retry()
            return
        self._resume_timer.stop()
        selected = self._campaign_selection
        command = build_auxiliary_command('kiwoom_monitor.research_process', '--research-process', [
            '--campaign', selected['campaign_id'], '--database', selected['database'],
            '--runs-dir', selected['runs_dir'], '--result', str(self._result_path), '--cancel', str(self._cancel_path),
            '--worker-token', claim['owner_token'], '--worker-generation', str(claim['generation']),
        ])
        try:
            self._manager.start(command, Path.cwd(), below_normal_priority=True)
        except OSError as exc:
            self._campaign_status.setText(f'캠페인 실행 실패 · {exc}')
            self._finish_worker('FAILED', f'launch_error: {exc}')
            self._schedule_worker_retry()
            return
        self._campaign_process = True
        self._campaign_resume_pending = False
        self._last_campaign_document = ''
        self._run.setEnabled(False)
        self._continuous.setEnabled(False)
        self._campaign_run.setEnabled(False)
        self._campaign_budget.setEnabled(False)
        self._campaign_hypotheses.setEnabled(False)
        self._poll.start()

    def _finish_worker(self, outcome: str, reason: str, exit_code=None) -> None:
        claim = self._worker_claim
        if claim is None:
            return
        try:
            self._campaign_repository().finish_campaign_worker(claim['campaign_id'], owner_token=claim['owner_token'],
                                                               generation=claim['generation'], outcome=outcome,
                                                               reason=reason, exit_code=exit_code)
            self._worker_claim = None
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'작업자 종료 기록 확인 필요 · {exc}')

    def _schedule_worker_retry(self) -> None:
        if self._campaign_suspended or self._campaign_selection is None or self._manager.is_running or self._registration is not None:
            return
        try:
            repository = self._campaign_repository()
            campaign_id = self._campaign_selection['campaign_id']
            campaign = repository.load_campaign(campaign_id)
            self._display_campaign(campaign)
            worker = repository.load_campaign_worker(campaign_id)
            self._campaign_status.setToolTip(f"실패 {worker['failure_count']}회 · {worker['reason']} · 재시도 {worker['next_retry_at']}")
            delay = campaign_worker_retry_delay_ms(worker)
            if campaign['desired_state'] == 'RUNNING' and delay is not None:
                self._worker_retry_timer.start(delay)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self._campaign_status.setText(f'작업자 복구 확인 필요 · {exc}')

    def _browse_request(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "연구 요청 선택", self._request_path.text(), "JSON (*.json)",
        )
        if selected:
            self._request_path.setText(selected)
            self._preview_request()

    def _preview_request(self) -> bool:
        path = Path(self._request_path.text().strip())
        try:
            request = load_research_process_request(path)
        except (OSError, TypeError, ValueError) as exc:
            self._summary.setText(f"요청 확인 필요 · {exc}")
            self._run.setEnabled(False)
            return False
        folds = ", ".join(f"{fold.name}({fold.role})" for fold in request.evaluation.folds)
        cost = request.execution.cost_model
        search = request.search
        search_text = (
            f" · 생성 {search.generation_mode} · 실행 {search.execution_environment}"
            f" · CPU 목표 {request.resource_limits.cpu_duty_percent}%"
            if search is not None else ""
        )
        self._summary.setText(
            f"{request.mode} · {request.family} · 데이터 {request.dataset.name} · 구간 {folds}"
            f"{search_text} · "
            f"비용 {cost.rate_basis if cost else '없음'} / {cost.source if cost else ''}"
        )
        labels = {
            "rank_comparison": "기준선 비교 실행",
            "limited_search": "제한 탐색 실행",
            "single_run": "단일 연구 실행",
        }
        self._run.setText(labels[request.mode])
        self._run.setEnabled(not self._manager.is_running and self._registration is None)
        self._settings.setValue("request_path", str(path))
        return True

    def _start(self) -> None:
        if self._manager.is_running or self._registration is not None or not self._preview_request():
            return
        self._resume_timer.stop()
        self._worker_retry_timer.stop()
        self._campaign_process = False
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._result_path.unlink(missing_ok=True)
        self._cancel_path.unlink(missing_ok=True)
        command = build_auxiliary_command(
            "kiwoom_monitor.research_process",
            "--research-process",
            [
                "--request", self._request_path.text().strip(),
                "--result", str(self._result_path),
                "--cancel", str(self._cancel_path),
            ],
        )
        try:
            self._manager.start(
                command, Path.cwd(), below_normal_priority=True,
            )
        except OSError as exc:
            self._status.setText(f"실행 실패 · {exc}")
            self._schedule_worker_retry()
            return
        self._status.setText("별도 프로세스에서 연구 중…")
        self._run.setEnabled(False)
        self._campaign_budget.setEnabled(False)
        self._campaign_hypotheses.setEnabled(False)
        self._cancel.setEnabled(True)
        self._table.setRowCount(0)
        self._poll.start()

    def _request_cancel(self) -> None:
        if self._registration is not None:
            self._registration['cancel'].write_text('cancel\n', encoding='ascii')
            self._status.setText('캠페인 등록 취소 요청을 처리하는 중…')
            self._cancel.setEnabled(False)
            return
        if self._resume_timer.isActive():
            self._resume_timer.stop()
            self._cancel_path.parent.mkdir(parents=True, exist_ok=True)
            self._cancel_path.write_text("cancel\n", encoding="ascii")
            self._status.setText("남은 실험 자동 재개 취소")
            self._cancel.setEnabled(False)
            return
        if not self._manager.is_running:
            return
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._cancel_path.write_text("cancel\n", encoding="ascii")
        self._status.setText("취소 요청을 처리하는 중…")
        self._cancel.setEnabled(False)

    def _poll_process(self) -> None:
        if self._registration is not None:
            self._poll_campaign_registration()
            return
        process = self._manager.process
        if self._campaign_process:
            if self._result_path.is_file():
                try:
                    document = self._result_path.read_text(encoding='utf-8')
                    if document != self._last_campaign_document:
                        result = json.loads(document)
                        if result.get('kind') == 'campaign':
                            self._last_campaign_document = document
                            self._display_campaign(result['campaign'])
                            if result.get('last_result'):
                                self._apply_result(result['last_result'])
                            if result.get('cycle_outcome') in ('FAILED', 'RESOURCE_BLOCKED') and result.get('cycle_reason'):
                                self._status.setText(f"연구 실행 확인 · {result['cycle_reason']}")
                            if result.get('input_discovery', {}).get('errors'):
                                self._status.setText('새 자료 자동 등록 확인 필요 · ' + '; '.join(result['input_discovery']['errors']))
                        else:
                            self._campaign_status.setText(f"캠페인 실행 확인 필요 · {result.get('reason', '')}")
                except (OSError, ValueError, TypeError, AttributeError, KeyError):
                    pass
            if process is not None and process.poll() is not None:
                self._poll.stop()
                self._manager.clear()
                if self._worker_claim is not None:
                    try:
                        desired = self._campaign_repository().load_campaign(self._worker_claim['campaign_id'])['desired_state']
                        expected = self._campaign_suspended or self._cancel_path.is_file() or desired != 'RUNNING'
                        self._finish_worker('EXPECTED_EXIT' if expected else 'FAILED',
                                            'user_or_app_stop' if expected else f'unexpected_exit: {process.returncode}', process.returncode)
                    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                        self._campaign_status.setText(f'작업자 종료 확인 필요 · {exc}')
                self._run.setEnabled(True)
                self._continuous.setEnabled(True)
                self._campaign_run.setEnabled(True)
                if self._campaign_selection is not None:
                    try:
                        self._display_campaign(self._campaign_repository().load_campaign(self._campaign_selection['campaign_id']))
                    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                        self._campaign_status.setText(f'캠페인 상태 확인 필요 · {exc}')
                if process.returncode:
                    self._campaign_status.setText('캠페인 작업 프로세스 종료 · 원인 확인 후 재개하세요')
                elif self._campaign_resume_pending:
                    self._campaign_resume_pending = False
                    QTimer.singleShot(0, self._restore_campaign)
                self._schedule_worker_retry()
            return
        if process is None or process.poll() is None:
            return
        self._poll.stop()
        self._manager.clear()
        self._cancel.setEnabled(False)
        self._run.setEnabled(True)
        if self._campaign_selection is not None:
            self._display_campaign(self._campaign_repository().load_campaign(self._campaign_selection['campaign_id']))
        try:
            result = json.loads(self._result_path.read_text(encoding="utf-8"))
            if not isinstance(result, Mapping):
                raise ValueError("result is not an object")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._status.setText(f"결과 읽기 실패 · {exc}")
            self._schedule_worker_retry()
            return
        self._apply_result(result)
        if (
            self._continuous.isChecked()
            and result.get("kind") == "limited_search"
            and result.get("job_status") == "cancelled"
            and not self._cancel_path.is_file()
        ):
            self._status.setText("시간 예산 종료 · 남은 실험 자동 재개 대기")
            self._cancel.setEnabled(True)
            self._resume_timer.start()
        else:
            self._schedule_worker_retry()

    def _continuous_changed(self, checked: bool) -> None:
        self._settings.setValue("continuous_resume", checked)
        if not checked and self._resume_timer.isActive():
            self._resume_timer.stop()
            self._cancel.setEnabled(False)
            self._status.setText("남은 실험 자동 재개 중지")
            self._schedule_worker_retry()

    def _resume_after_slice(self) -> None:
        self._cancel.setEnabled(False)
        if self._continuous.isChecked() and not self._cancel_path.is_file():
            self._start()

    def _apply_result(self, result: Mapping[str, Any]) -> None:
        status = str(result.get("status", "unknown"))
        if status == 'resource_blocked' or result.get('job_status') == 'resource_blocked':
            self._status.setText('자원 한도 도달 · 메모리/CPU 예산을 확인한 뒤 다시 실행하세요')
            self._table.setRowCount(0)
            return
        reason = str(result.get("reason", ""))
        job_status = str(result.get("job_status", ""))
        notification = "후보 변화 있음" if result.get("notification_proposed") else ""
        market = format_market_regime_summary(result.get("market_regime"))
        details = tuple(value for value in (reason, job_status, notification, market) if value)
        self._status.setText(status + (f" · {' · '.join(details)}" if details else ""))
        rows = format_research_result_rows(result)
        self._table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                self._table.setItem(row_index, column, QTableWidgetItem(value))

    def stop(self) -> None:
        self._settings.setValue("geometry", self.saveGeometry())
        if self._historical_news_review_dialog is not None:
            self._historical_news_review_dialog.close()
        if self._final_holdout_dialog is not None:
            self._final_holdout_dialog.stop()
        if self._validation_dialog is not None:
            self._validation_dialog.stop()
        if self._comparison_dialog is not None:
            self._comparison_dialog.stop()
        self._campaign_suspended = True
        self._campaign_resume_pending = False
        self._poll.stop()
        self._resume_timer.stop()
        self._worker_retry_timer.stop()
        if self._manager.is_running:
            cancel_path = self._registration['cancel'] if self._registration is not None else self._cancel_path
            cancel_path.parent.mkdir(parents=True, exist_ok=True)
            cancel_path.write_text("cancel\n", encoding="ascii")
            self._manager.stop(graceful_timeout=3.0, terminate_timeout=1.0)
        if self._registration is not None:
            self._clear_campaign_registration()
        self._finish_worker('EXPECTED_EXIT', 'app_shutdown')
        self._cancel_path.unlink(missing_ok=True)

    def closeEvent(self, event) -> None:
        self._settings.setValue("geometry", self.saveGeometry())
        if self._historical_news_review_dialog is not None:
            self._historical_news_review_dialog.close()
        if self._final_holdout_dialog is not None:
            self._final_holdout_dialog.close()
        if self._validation_dialog is not None:
            self._validation_dialog.close()
        if self._comparison_dialog is not None:
            self._comparison_dialog.close()
        self._worker_retry_timer.stop()
        self._campaign_suspended = True
        if self._registration is not None:
            self._request_cancel()
        if self._campaign_process and self._manager.is_running:
            self._campaign_suspended = True
            self._cancel_path.parent.mkdir(parents=True, exist_ok=True)
            self._cancel_path.write_text('cancel\n', encoding='ascii')
        if self._resume_timer.isActive():
            self._resume_timer.stop()
            self._cancel.setEnabled(False)
        event.ignore()
        self.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._campaign_suspended:
            self._campaign_suspended = False
            self._campaign_resume_pending = True
            QTimer.singleShot(1100, self._restore_campaign)


def format_research_result_rows(result: Mapping[str, Any]) -> tuple[tuple[str, ...], ...]:
    if result.get("kind") == "limited_search":
        cards = result.get("candidate_cards", ())
        return tuple((
            f"#{int(card.get('ordinal', 0)) + 1} {card.get('variant', '')}",
            str(card.get("status", "")),
            _won(card.get("max_drawdown_won")),
            _won(card.get("net_pnl_won")),
            str(card.get("closed_trade_count", 0)),
            str(card.get("active_day_count", 0)),
            ", ".join(str(value) for value in card.get("reasons", ())),
        ) for card in cards if isinstance(card, Mapping))
    if result.get("kind") == "rank_comparison":
        comparison = result.get("comparison")
        if not isinstance(comparison, Mapping):
            return ()
        folds = comparison.get("fold_comparisons", ())
        return tuple((
            str(row.get("name", "")), str(row.get("status", "")),
            _won(row.get("baseline_net_pnl_won")), _won(row.get("variant_net_pnl_won")),
            _won(row.get("net_pnl_delta_won")), _won(row.get("avoided_loss_won")),
            _won(row.get("missed_profit_won")),
        ) for row in folds if isinstance(row, Mapping))
    report = result.get("report")
    if not isinstance(report, Mapping):
        return ()
    folds = report.get("fold_reports", ())
    return tuple((
        str(row.get("name", "")), str(row.get("status", "")), "",
        _won(row.get("net_realized_pnl_won")), "", "", "",
    ) for row in folds if isinstance(row, Mapping))


class DevelopmentValidationDialog(QDialog):
    """Owns one sequential validation child, independently of campaign/query lifetimes."""

    def __init__(self, state_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle('여러 구간 순차 검증')
        self.resize(1000, 520)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._state_dir = Path(state_dir)
        self._saved_request_path = self._state_dir / 'last_development_validation_request.json'
        self._manager = AuxiliaryProcessManager()
        self._operation = None
        self._last_request = None
        self._last_encoded = None
        self._request_path = QLineEdit()
        self._request_path.setPlaceholderText('순차 검증 JSON: 고정 전략과 검증할 개발 구간을 지정합니다')
        browse = QPushButton('검증 파일 선택')
        browse.clicked.connect(self._browse)
        self._run = QPushButton('검증 실행')
        self._run.clicked.connect(self._start)
        self._resume = QPushButton('같은 요청 이어서 실행')
        self._resume.setEnabled(False)
        self._resume.clicked.connect(lambda: self._launch(self._last_request))
        self._cancel = QPushButton('취소')
        self._cancel.setEnabled(False)
        self._cancel.clicked.connect(self._request_cancel)
        self._status = QLabel('대기 · 완료 결과는 재사용하고 취소한 구간은 다시 실행합니다.')
        self._status.setWordWrap(True)
        self._summary = QLabel('구간마다 초기 현금이 별개입니다. 적격은 수익성 승인이 아닙니다.')
        self._summary.setWordWrap(True)
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(('구간', '기간(KST) / 역할', '실행 상태', '판정 상태', '손익', '구간 MDD', '이유'))
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._groups = QTableWidget(0, 9)
        self._groups.setHorizontalHeaderLabels(('종목 그룹', '요청 구간', '실행 완료', '미시작', '적격 구간', '양수 구간', '구간 손익 중앙값', '최악 구간 MDD', '표본 판정'))
        self._groups.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._groups.horizontalHeader().setStretchLastSection(True)
        self._groups.hide()
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        for widget in (self._request_path, browse, self._run, self._resume, self._cancel):
            row.addWidget(widget)
        layout.addLayout(row)
        layout.addWidget(self._status)
        layout.addWidget(self._summary)
        layout.addWidget(self._table)
        layout.addWidget(self._groups)
        self._poll = QTimer(self)
        self._poll.setInterval(250)
        self._poll.timeout.connect(self._poll_process)
        self._restore_last_request()

    def _restore_last_request(self) -> None:
        if not self._saved_request_path.exists():
            return
        try:
            batch = load_development_validation_request(self._saved_request_path)
        except (OSError, TypeError, ValueError) as exc:
            self._status.setText('이전 순차 검증 요청 복원 실패: ' + str(exc))
            return
        self._last_request = batch
        self._request_path.setText(str(self._saved_request_path))
        self._resume.setEnabled(True)
        self._status.setText('이전 순차 검증 요청 복원됨 · 실행 상태는 연구 원장에서 재확인합니다.')

    def _browse(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, '순차 검증 요청', '', 'JSON (*.json)')
        if selected:
            self._request_path.setText(selected)

    def _start(self) -> None:
        if self._operation is not None or self._manager.is_running:
            return
        try:
            self._launch(load_development_validation_request(Path(self._request_path.text().strip())))
        except (OSError, TypeError, ValueError) as exc:
            self._status.setText('요청 확인 실패: ' + str(exc))

    def _launch(self, batch) -> None:
        if batch is None or self._operation is not None or self._manager.is_running:
            return
        try:
            operation_id = uuid.uuid4().hex
            files = {key: self._state_dir / f'development_validation_{operation_id}.{suffix}'
                     for key, suffix in (('request', 'json'), ('result', 'result.json'),
                                         ('cancel', 'cancel'), ('owner', 'owner.json'))}
            source = Path(self._request_path.text().strip()).resolve()
            for path in (*files.values(), self._saved_request_path):
                resolved = path.resolve()
                if ((resolved == source and path != self._saved_request_path)
                        or resolved == batch.request.database.resolve()
                        or resolved.is_relative_to(batch.request.dataset.resolve())):
                    raise ValueError('operation files must be outside the frozen input/source/database')
            self._state_dir.mkdir(parents=True, exist_ok=True)
            write_json_command(self._saved_request_path, batch.to_dict())
            self._last_request = batch
            self._operation = {**files, 'batch': batch, 'implementation_hash': None}
            files['request'].write_text(json.dumps(batch.to_dict(), ensure_ascii=False), encoding='utf-8')
            process = self._manager.start(build_auxiliary_command('kiwoom_monitor.research_process', '--research-process', [
                '--validate-partitions', str(files['request']), '--result', str(files['result']),
                '--cancel', str(files['cancel']),
                '--validation-owner-token', 'development-ui-owner-' + operation_id]),
                Path.cwd(), below_normal_priority=True)
            try:
                if not _record_research_operation_owner(files, process, 'development_validation'):
                    self._status.setToolTip('자식 프로세스 시작 토큰을 확인할 수 없어 비정상 종료 자동 판정은 보류합니다.')
            except OSError as exc:
                self._status.setToolTip('자식 프로세스 소유 기록 저장 실패: ' + str(exc))
            self._last_encoded = None
            self._run.setEnabled(False)
            self._resume.setEnabled(False)
            self._cancel.setEnabled(True)
            self._status.setText('입력 확인 중 · 구간을 순서대로 실행합니다.')
            self._summary.setText(f'선택 {len(batch.fold_names)}개 구간 · 시간 예산 {batch.max_seconds}초 · 초기 현금은 구간마다 별개')
            if batch.symbol_partitions:
                self._summary.setText(f'{len(batch.symbol_partitions)}개 종목 그룹 × {len(batch.fold_names)}개 시간 구간 · 시간 예산 {batch.max_seconds}초 · 초기 현금은 단계마다 별개')
            self._poll.start()
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            self._clear_operation()
            self._status.setText('실행 실패: ' + str(exc))

    def _request_cancel(self) -> None:
        if self._operation is not None:
            try:
                self._operation['cancel'].write_text('cancel\n', encoding='ascii')
                self._status.setText('취소 요청 중 · 완료된 결과는 보존합니다.')
                self._cancel.setEnabled(False)
            except OSError as exc:
                self._status.setText('취소 요청 실패: ' + str(exc))

    def _poll_process(self) -> None:
        process = self._manager.process
        if self._operation is None or process is None:
            return
        exit_code = process.poll()
        verified = False
        try:
            path = self._operation['result']
            if not path.exists():
                if exit_code is not None:
                    raise ValueError(f'결과 없이 종료됨 (종료 코드 {exit_code})')
                return
            with path.open('rb') as stream:
                encoded = stream.read(16 * 1024 * 1024 + 1)
            if len(encoded) > 16 * 1024 * 1024:
                raise ValueError('진행 결과가 16 MiB를 초과합니다')
            document = json.loads(encoded.decode('utf-8'))
            if exit_code is not None and (not isinstance(document, dict)
                    or {'ok': 0, 'cancelled': 2, 'resource_blocked': 3}.get(document.get('status')) != exit_code):
                raise ValueError(f'실행 종료/결과 불일치 (종료 코드 {exit_code}): {document.get("reason", "") if isinstance(document, dict) else ""}')
            if encoded != self._last_encoded or exit_code is not None:
                self._apply_progress(document, finished=exit_code is not None)
                self._last_encoded = encoded
            if exit_code is not None:
                verified = True
        except (OSError, TypeError, ValueError, KeyError, AttributeError) as exc:
            self._status.setText('진행 확인 실패: ' + str(exc))
        finally:
            if exit_code is not None:
                self._clear_operation(preserve_receipt=not verified)

    def _apply_progress(self, document, *, finished: bool) -> None:
        operation = self._operation
        batch = operation['batch']
        grouped = bool(batch.symbol_partitions)
        if (not isinstance(document, dict) or document.get('kind') != 'independent_development_validation'
                or document.get('database') != str(batch.request.database.resolve())
                or document.get('fold_names') != list(batch.fold_names)
                or document.get('comparison_scope') != ('per_symbol_bucket_identified_runs/v1' if grouped else 'identified_runs_only/v1')
                or document.get('status') not in ('ok', 'cancelled', 'resource_blocked')):
            raise ValueError('검증 결과 범위 불일치')
        code_hash = document.get('implementation_hash')
        if not isinstance(code_hash, str) or not code_hash or operation['implementation_hash'] not in (None, code_hash):
            raise ValueError('검증 구현 hash 불일치')
        steps = document['steps']
        expected_steps = [(name, policy.bucket) for name in batch.fold_names for policy in batch.symbol_partitions] if grouped else [(name, None) for name in batch.fold_names]
        if not isinstance(steps, list) or [(row['fold_name'], row.get('symbol_bucket')) for row in steps] != expected_steps:
            raise ValueError('검증 구간 목록 불일치')
        if grouped and (document.get('version') != batch.version
                or document.get('symbol_partition') != batch.symbol_partition_document()
                or type(document.get('requested_step_count')) is not int
                or document['requested_step_count'] != len(expected_steps)
                or document.get('comparison') is not None
                or any(type(row.get('symbol_bucket')) is not int or row.get('step_key') != f'{name}/bucket-{bucket}' for row, (name, bucket) in zip(steps, expected_steps))):
            raise ValueError('종목 그룹 요청 계약 불일치')
        ids = [row['run_id'] for row in steps if row['run_id']]
        if any(not isinstance(row['run_id'], str) for row in steps) or document['run_ids'] != ids or len(ids) != len(set(ids)):
            raise ValueError('검증 실행 ID 불일치')
        comparison = document.get('comparison')
        evidence = {}
        group_formatted = []
        if grouped:
            groups = document['group_comparisons']
            if not isinstance(groups, list) or [group['symbol_bucket'] for group in groups] != [policy.bucket for policy in batch.symbol_partitions]:
                raise ValueError('종목 그룹 비교 목록 불일치')
            for group in groups:
                rows = [row for row in steps if row['symbol_bucket'] == group['symbol_bucket']]
                group_ids = [row['run_id'] for row in rows if row['run_id']]
                counts = {'requested_step_count': len(rows), 'completed_step_count': sum(row['state'] in ('COMPLETED', 'CACHED') for row in rows),
                          'not_started_count': sum(row['state'] == 'NOT_STARTED' for row in rows)}
                if (type(group['symbol_bucket']) is not int or group['run_ids'] != group_ids
                        or group.get('comparison_scope') != 'identified_runs_only/v1'
                        or any(type(group.get(key)) is not int or group[key] != value for key, value in counts.items())):
                    raise ValueError('종목 그룹 진행 범위 불일치')
                result = group['comparison']
                if result is None:
                    if group_ids:
                        raise ValueError('종목 그룹 비교 결과 누락')
                    statistics = ('N/A', 'N/A', 'N/A', 'N/A', '미판정')
                else:
                    if (not isinstance(result, dict) or result.get('version') != 'independent_development_comparison/v1'
                            or result.get('requested_count') != len(group_ids)
                            or [row['run_id'] for row in result['partitions']] != group_ids
                            or result.get('status') not in ('COMPLETE', 'INCOMPLETE', 'INCOMPARABLE')
                            or any(type(result.get(key)) is not int for key in ('eligible_partition_count', 'positive_partition_count'))
                            or not 0 <= result['positive_partition_count'] <= result['eligible_partition_count'] <= len(group_ids)):
                        raise ValueError('종목 그룹 표본 범위 불일치')
                    evidence.update({row['run_id']: row for row in result['partitions']})
                    median = result['median_partition_pnl_won']
                    drawdown = result['worst_partition_drawdown_won']
                    if (median is not None and (type(median) not in (int, float) or not float('-inf') < median < float('inf'))
                            or drawdown is not None and (type(drawdown) is not int or drawdown < 0)):
                        raise ValueError('종목 그룹 통계 불일치')
                    statistics = (str(result['eligible_partition_count']), str(result['positive_partition_count']),
                        'N/A' if median is None else f'{median:,.1f}원', _won(drawdown),
                        {'COMPLETE': '식별 결과 적격', 'INCOMPLETE': '표본 / 상태 확인 필요', 'INCOMPARABLE': '조건이 달라 비교 불가'}[result['status']])
                group_formatted.append((f'그룹 {group["symbol_bucket"] + 1}', *(str(counts[key]) for key in counts), *statistics))
        elif comparison is not None:
            if (comparison.get('version') != 'independent_development_comparison/v1'
                    or comparison.get('requested_count') != len(ids)
                    or [row['run_id'] for row in comparison['partitions']] != ids):
                raise ValueError('부분 비교 범위 불일치')
            evidence = {row['run_id']: row for row in comparison['partitions']}
        states = {'NOT_STARTED': '미시작', 'RUNNING': '실행 중', 'COMPLETED': '실행 완료', 'CACHED': '완료 결과 재사용',
                  'BUSY': '소유자 / 복구 확인 필요', 'FAILED': '실패', 'CACHE_INVALID': '완료 결과 확인 필요',
                  'CANCELLED': '취소', 'BUDGET_EXHAUSTED': '시간 예산 종료', 'RESOURCE_BLOCKED': '자원 한도 차단'}
        folds = {fold.name: fold for fold in batch.request.evaluation.folds}
        formatted = []
        for row in steps:
            fold = folds[row['fold_name']]
            if (row['role'], row['start'], row['end']) != (fold.role, fold.start, fold.end) or row['state'] not in states:
                raise ValueError('검증 구간 계약 불일치')
            result = evidence.get(row['run_id'], {})
            period = ' ~ '.join(datetime.fromisoformat(row[key]).astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S') for key in ('start', 'end'))
            name = row['fold_name'] + (f' / 그룹 {row["symbol_bucket"] + 1}' if grouped else '')
            formatted.append((name, period + ' / ' + row['role'], states[row['state']],
                str(result.get('status', '미판정')), _won(result.get('net_pnl_won')), _won(result.get('max_drawdown_won')),
                str(row['reason']) or ', '.join(result.get('reasons', ()))))
        complete = sum(row['state'] in ('COMPLETED', 'CACHED') for row in steps)
        expected_status = ('CANCELLED' if document['status'] == 'cancelled' else 'RESOURCE_BLOCKED'
                           if document['status'] == 'resource_blocked' else 'COMPLETED' if complete == len(steps) else 'PARTIAL')
        if document['batch_status'] != expected_status or finished and any(row['state'] == 'RUNNING' for row in steps):
            raise ValueError('검증 전체 완료 상태 불일치')
        operation['implementation_hash'] = code_hash
        self._table.setRowCount(len(formatted))
        for index, values in enumerate(formatted):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip('실행: ' + steps[index]['run_id'])
                self._table.setItem(index, column, item)
        self._groups.setRowCount(len(group_formatted))
        for index, values in enumerate(group_formatted):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(f'bucket={batch.symbol_partitions[index].bucket} · 표본 판정은 식별된 실행만 대상 · 그룹 간 손익/MDD를 합산하지 않음')
                self._groups.setItem(index, column, item)
        self._groups.setVisible(grouped)
        labels = {'COMPLETED': '전체 구간 실행 완료', 'PARTIAL': '일부 구간 완료 · 남은 상태 확인 필요',
                  'CANCELLED': '취소됨', 'RESOURCE_BLOCKED': '자원 한도로 중단됨'}
        self._status.setText(labels[expected_status] if finished else ('취소 요청 중' if operation['cancel'].exists() else '순차 검증 진행 중'))
        self._status.setToolTip(str(document.get('reason', '')))
        self._summary.setText(f'전체 {len(steps)} / 완료 {complete} / 미시작 {sum(row["state"] == "NOT_STARTED" for row in steps)} · 초기 현금은 구간마다 별개 · 적격은 수익성 승인 아님')

    def _clear_operation(self, *, preserve_receipt: bool = False) -> None:
        operation, self._operation = self._operation, None
        self._poll.stop()
        self._manager.clear()
        errors = []
        if operation is not None:
            for key in (() if preserve_receipt else ('request', 'result', 'cancel', 'owner')):
                try:
                    path = operation.get(key)
                    if path is not None:
                        path.unlink(missing_ok=True)
                except OSError as exc:
                    errors.append(str(exc))
        if errors:
            self._status.setToolTip(self._status.toolTip() + '\n' + '\n'.join(errors))
        if preserve_receipt and operation is not None:
            self._status.setToolTip(self._status.toolTip() + '\n비정상 종료 기록 보존: ' + str(operation['owner']))
        self._run.setEnabled(True)
        self._resume.setEnabled(self._last_request is not None)
        self._cancel.setEnabled(False)

    def stop(self) -> None:
        if self._operation is not None:
            self._request_cancel()
        if self._manager.is_running:
            self._manager.stop(graceful_timeout=3.0, terminate_timeout=1.0)
        self._clear_operation(preserve_receipt=self._operation is not None)

    def closeEvent(self, event) -> None:
        self._request_cancel()
        event.ignore()
        self.hide()


class FinalHoldoutDialog(QDialog):
    """Runs one immutable final batch without exposing its storage to the UI process."""

    _RESULT_LIMIT = 16 * 1024 * 1024
    _TERMINAL_SUCCESS = frozenset(('COMPLETED', 'CACHED'))
    _RECOVERABLE = frozenset(('FAILED', 'CANCELLED'))
    _STATES = {
        'NOT_STARTED': '미시작',
        'RUNNING': '실행 중',
        'COMPLETED': '실행 완료',
        'CACHED': '완료 결과 재사용',
        'FAILED': '실패 · 명시 복구 필요',
        'CANCELLED': '취소 · 명시 복구 필요',
        'BUSY': '다른 실행 소유 중',
        'CACHE_INVALID': '완료 자료 확인 필요',
    }

    def __init__(self, state_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle('잠긴 후보 최종 평가')
        self.resize(1120, 540)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._state_dir = Path(state_dir)
        self._saved_request_path = self._state_dir / 'last_final_holdout_request.json'
        self._manager = AuxiliaryProcessManager()
        self._operation: dict[str, Any] | None = None
        self._last_encoded: bytes | None = None
        self._last_request = None
        self._last_states: dict[str, str] = {}
        self._displayed_hashes: list[str] = []
        self._can_resume = False
        self._exposure_available = False
        self._exposed = False

        self._request_path = QLineEdit()
        self._request_path.setPlaceholderText('최종 평가 JSON: 잠긴 후보·미사용 기간·접근 기록을 지정합니다')
        browse = QPushButton('평가 파일 선택')
        browse.clicked.connect(self._browse)
        self._run = QPushButton('최종 평가 실행')
        self._run.clicked.connect(self._start)
        self._resume = QPushButton('남은 후보 이어서 실행')
        self._resume.setEnabled(False)
        self._resume.setToolTip('완료 후보는 재사용하고 미시작 후보만 진행합니다. 실패·취소 후보는 명시 복구가 필요합니다.')
        self._resume.clicked.connect(lambda: self._launch(self._last_request))
        self._cancel = QPushButton('취소')
        self._cancel.setEnabled(False)
        self._cancel.clicked.connect(self._request_cancel)

        self._status = QLabel('대기 · 최종 기간은 한 번 열면 다시 미사용으로 되돌릴 수 없습니다.')
        self._status.setWordWrap(True)
        self._summary = QLabel('요청 파일을 선택하면 별도 프로세스가 입력·원장·결과를 검증합니다.')
        self._summary.setWordWrap(True)

        recovery_row = QHBoxLayout()
        self._recovery_reason = QLineEdit()
        self._recovery_reason.setPlaceholderText('실패/취소 원인을 확인한 내용을 입력하세요 (필수)')
        self._recover = QPushButton('선택 후보 명시 복구')
        self._recover.setEnabled(False)
        self._recover.clicked.connect(self._recover_selected)
        recovery_row.addWidget(QLabel('복구 근거'))
        recovery_row.addWidget(self._recovery_reason, 1)
        recovery_row.addWidget(self._recover)

        exposure_row = QHBoxLayout()
        self._exposure_reason = QLineEdit()
        self._exposure_reason.setPlaceholderText('최종 결과를 어떤 전략 개선에 사용하는지 입력하세요 (필수)')
        self._expose = QPushButton('결과를 개발에 사용한 기록 남기기')
        self._expose.setEnabled(False)
        self._expose.setToolTip('이 기록은 되돌릴 수 없으며 같은 기간을 다시 최종 검증으로 쓸 수 없습니다.')
        self._expose.clicked.connect(self._expose_to_development)
        exposure_row.addWidget(QLabel('개발 사용 근거'))
        exposure_row.addWidget(self._exposure_reason, 1)
        exposure_row.addWidget(self._expose)

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels((
            '후보 hash', '실행 상태', '실행 ID', '성과 판정',
            '보고서 판정', '결과 hash', '복구 요청', '사유',
        ))
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._selection_changed)
        self._recovery_reason.textChanged.connect(self._selection_changed)
        self._exposure_reason.textChanged.connect(self._selection_changed)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        for widget in (self._request_path, browse, self._run, self._resume, self._cancel):
            top.addWidget(widget)
        layout.addLayout(top)
        layout.addWidget(self._status)
        layout.addWidget(self._summary)
        layout.addLayout(recovery_row)
        layout.addLayout(exposure_row)
        layout.addWidget(self._table)

        self._poll = QTimer(self)
        self._poll.setInterval(250)
        self._poll.timeout.connect(self._poll_process)
        self._restore_last_request()

    def _restore_last_request(self) -> None:
        if not self._saved_request_path.exists():
            return
        try:
            request = load_final_holdout_execution_request(self._saved_request_path)
            if request.recoveries:
                raise ValueError('저장된 요청에 명시 복구가 포함돼 있습니다')
        except (OSError, TypeError, ValueError) as exc:
            self._status.setText('이전 최종 평가 요청 복원 실패: ' + str(exc))
            return
        self._last_request = request
        self._can_resume = True
        self._request_path.setText(str(self._saved_request_path))
        self._resume.setEnabled(True)
        self._status.setText('이전 최종 평가 요청 복원됨 · 최종 원장을 확인한 뒤 남은 후보를 수동 실행할 수 있습니다.')

    def _browse(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, '최종 평가 요청', '', 'JSON (*.json)')
        if selected:
            self._request_path.setText(selected)

    def _start(self) -> None:
        if self._operation is not None or self._manager.is_running:
            return
        try:
            source = Path(self._request_path.text().strip()).resolve()
            request = load_final_holdout_execution_request(source)
            self._launch(request, source_path=source)
        except (OSError, TypeError, ValueError) as exc:
            self._status.setText('요청 확인 실패: ' + str(exc))

    def _launch(self, request, *, source_path: Path | None = None) -> None:
        if request is None or self._operation is not None or self._manager.is_running:
            return
        try:
            operation_id = uuid.uuid4().hex
            if source_path is None or not request.recoveries:
                request = replace(request, owner_token='final-ui-owner-' + operation_id)
            files = {key: self._state_dir / f'final_holdout_{operation_id}.{suffix}'
                     for key, suffix in (('request', 'json'), ('result', 'result.json'),
                                         ('cancel', 'cancel'), ('owner', 'owner.json'))}
            first = request.candidates[0]
            for path in (*files.values(), self._saved_request_path):
                resolved = path.resolve()
                if (resolved == first.database.resolve()
                        or (source_path is not None and resolved == source_path.resolve()
                            and path != self._saved_request_path)
                        or resolved.is_relative_to(first.dataset.resolve())
                        or resolved.is_relative_to(first.runs_dir.resolve())):
                    raise ValueError('operation files must be outside the request, frozen input, database and run artifacts')
            self._state_dir.mkdir(parents=True, exist_ok=True)
            # A recovery needs a fresh explicit decision after restart; only the base batch is resumable.
            write_json_command(self._saved_request_path, replace(request, recoveries=()).to_dict())
            self._last_request = request
            self._can_resume = True
            self._operation = {'request': files['request'], 'result': files['result'],
                'cancel': files['cancel'], 'owner': files['owner'], 'snapshot': request}
            write_json_command(files['request'], request.to_dict())
            command = build_auxiliary_command('kiwoom_monitor.research_process', '--research-process', [
                '--evaluate-final', str(files['request']), '--result', str(files['result']),
                '--cancel', str(files['cancel']),
            ])
            process = self._manager.start(command, Path.cwd(), below_normal_priority=True)
            if source_path is None or not request.recoveries:
                try:
                    if not _record_research_operation_owner(files, process, 'final_holdout'):
                        self._status.setToolTip('자식 프로세스 시작 토큰을 확인할 수 없어 비정상 종료 자동 판정은 보류합니다.')
                except OSError as exc:
                    self._status.setToolTip('자식 프로세스 소유 기록 저장 실패: ' + str(exc))
            self._last_encoded = None
            self._can_resume = False
            self._exposure_available = False
            self._run.setEnabled(False)
            self._resume.setEnabled(False)
            self._cancel.setEnabled(True)
            self._recover.setEnabled(False)
            self._status.setText('잠긴 후보와 최종 기간을 검증하는 중…')
            self._summary.setText(
                f'배치 {request.batch.batch_id[:12]} · 후보 {len(request.batch.candidate_spec_hashes)}개 '
                f'· 종료된 후보는 자동 재실행하지 않음'
            )
            self._poll.start()
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            self._clear_operation()
            self._status.setText('최종 평가 시작 실패: ' + str(exc))

    def _recover_selected(self) -> None:
        if self._operation is not None or self._manager.is_running or self._last_request is None:
            return
        row = self._table.currentRow()
        reason = self._recovery_reason.text().strip()
        if not (0 <= row < len(self._displayed_hashes)):
            self._status.setText('복구할 실패 또는 취소 후보를 선택하세요.')
            return
        candidate_hash = self._displayed_hashes[row]
        if self._last_states.get(candidate_hash) not in self._RECOVERABLE:
            self._status.setText('실패 또는 취소 상태의 후보만 명시 복구할 수 있습니다.')
            return
        if not reason or len(reason) > 2000 or '\x00' in reason:
            self._status.setText('복구 근거를 1~2000자로 입력하세요.')
            return
        request_id = 'final-ui-recovery-' + uuid.uuid4().hex
        recovered = replace(self._last_request,
            owner_token='final-ui-owner-' + uuid.uuid4().hex,
            recoveries=((candidate_hash, request_id, reason),))
        self._launch(recovered)

    def _expose_to_development(self) -> None:
        if (self._operation is not None or self._manager.is_running or self._last_request is None
                or not self._exposure_available or self._exposed):
            return
        reason = self._exposure_reason.text().strip()
        if not reason or len(reason) > 2000 or '\x00' in reason:
            self._status.setText('개발 사용 근거를 1~2000자로 입력하세요.')
            return
        request = FinalHoldoutExposureRequest(
            self._last_request.candidates[0].database,
            self._last_request.batch,
            'final-ui-exposure-' + uuid.uuid4().hex,
            datetime.now(UTC).isoformat(timespec='microseconds'),
            reason,
        )
        try:
            operation_id = uuid.uuid4().hex
            files = {key: self._state_dir / f'final_exposure_{operation_id}.{suffix}'
                     for key, suffix in (('request', 'json'), ('result', 'result.json'), ('cancel', 'cancel'))}
            for path in files.values():
                if path.resolve() in (request.database.resolve(),):
                    raise ValueError('operation files must not overwrite the research database')
            self._state_dir.mkdir(parents=True, exist_ok=True)
            self._operation = {'request': files['request'], 'result': files['result'],
                'cancel': files['cancel'], 'snapshot': request, 'kind': 'exposure'}
            write_json_command(files['request'], request.to_dict())
            command = build_auxiliary_command('kiwoom_monitor.research_process', '--research-process', [
                '--expose-final', str(files['request']), '--result', str(files['result']),
                '--cancel', str(files['cancel']),
            ])
            self._manager.start(command, Path.cwd(), below_normal_priority=True)
            self._run.setEnabled(False)
            self._resume.setEnabled(False)
            self._cancel.setEnabled(True)
            self._recover.setEnabled(False)
            self._expose.setEnabled(False)
            self._status.setText('최종 결과의 개발 사용 기록을 저장하는 중…')
            self._poll.start()
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            self._clear_operation()
            self._status.setText('개발 사용 기록 시작 실패: ' + str(exc))

    def _request_cancel(self) -> None:
        if self._operation is None:
            return
        try:
            self._operation['cancel'].write_text('cancel\n', encoding='ascii')
            self._cancel.setEnabled(False)
            self._status.setText(('개발 사용 기록' if self._operation.get('kind') == 'exposure'
                                  else '최종 평가') + ' 취소 요청 중…')
        except OSError as exc:
            self._status.setText('취소 요청 실패: ' + str(exc))

    def _poll_process(self) -> None:
        process = self._manager.process
        if self._operation is None or process is None:
            return
        exit_code = process.poll()
        operation = self._operation
        if exit_code is None:
            if operation.get('kind') == 'exposure' or not operation['result'].is_file():
                return
            try:
                with operation['result'].open('rb') as stream:
                    encoded = stream.read(self._RESULT_LIMIT + 1)
                if len(encoded) > self._RESULT_LIMIT:
                    raise ValueError('최종 평가 진행 결과가 16 MiB를 초과합니다')
                if encoded == self._last_encoded:
                    return
                result = json.loads(encoded.decode('utf-8'))
                if not isinstance(result, Mapping) or result.get('status') != 'running':
                    return
                self._apply_result(result, operation['snapshot'], finished=False)
                self._last_encoded = encoded
            except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                self._status.setText('최종 평가 진행 확인 실패: ' + str(exc))
            return
        verified = False
        try:
            with operation['result'].open('rb') as stream:
                encoded = stream.read(self._RESULT_LIMIT + 1)
            if len(encoded) > self._RESULT_LIMIT:
                raise ValueError('최종 평가 결과가 16 MiB를 초과합니다')
            result = json.loads(encoded.decode('utf-8'))
            if not isinstance(result, Mapping):
                raise ValueError('최종 평가 결과가 JSON 객체가 아닙니다')
            expected_exit = {'ok': 0, 'cancelled': 2, 'resource_blocked': 3, 'failed': 1}.get(result.get('status'))
            if expected_exit != exit_code:
                raise ValueError(f'실행 종료/결과 불일치 (종료 코드 {exit_code})')
            if result.get('status') == 'failed':
                raise ValueError(str(result.get('reason', '알 수 없는 실행 오류')))
            if 'kind' not in result:
                if result.get('status') != 'cancelled':
                    raise ValueError('최종 평가 결과 범위가 없습니다')
                if operation.get('kind') == 'exposure':
                    self._status.setText('개발 사용 기록 전 취소됨 · 최종 결과 표 유지')
                else:
                    self._status.setText('입력 확인 전 취소됨 · 이전 결과 표 유지')
                    self._can_resume = True
            elif operation.get('kind') == 'exposure':
                self._apply_exposure_result(result, operation['snapshot'])
            else:
                self._apply_result(result, operation['snapshot'])
            verified = True
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            subject = '개발 사용 기록' if operation.get('kind') == 'exposure' else '최종 평가'
            self._status.setText(subject + ' 결과 확인 실패: ' + str(exc) + ' · 이전 표 유지')
        finally:
            self._clear_operation(preserve_receipt=operation.get('kind') != 'exposure' and not verified)

    def _apply_result(self, result: Mapping[str, Any], request, *, finished: bool = True) -> None:
        required = {'status', 'kind', 'version', 'batch_id', 'window_id', 'implementation_hash',
                    'batch_status', 'candidates', 'limitations'}
        if (set(result) != required or result.get('kind') != 'independent_final_holdout'
                or result.get('version') != 'independent_final_holdout_result/v1'
                or result.get('batch_id') != request.batch.batch_id
                or result.get('window_id') != request.batch.window_id
                or result.get('status') not in (('ok', 'cancelled', 'resource_blocked') if finished else ('running',))):
            raise ValueError('최종 평가 결과 범위 불일치')
        implementation_hash = result['implementation_hash']
        if (not isinstance(implementation_hash, str) or len(implementation_hash) != 64
                or any(character not in '0123456789abcdef' for character in implementation_hash)):
            raise ValueError('최종 평가 구현 hash 불일치')
        rows = result['candidates']
        expected_hashes = list(request.batch.candidate_spec_hashes)
        fields = {'candidate_spec_hash', 'run_id', 'state', 'reason', 'logical_result_hash',
                  'performance_status', 'report_status', 'recovery_request_id'}
        if (not isinstance(rows, list) or len(rows) != len(expected_hashes)
                or [row.get('candidate_spec_hash') for row in rows if isinstance(row, Mapping)] != expected_hashes
                or any(not isinstance(row, Mapping) or set(row) != fields for row in rows)):
            raise ValueError('최종 평가 후보 목록 불일치')
        recovery_ids = {candidate_hash: value['request_id']
                        for candidate_hash, value in request.recovery_mapping().items()}
        run_ids: list[str] = []
        formatted = []
        for row in rows:
            state = row['state']
            values = tuple(row[key] for key in (
                'run_id', 'reason', 'logical_result_hash', 'performance_status',
                'report_status', 'recovery_request_id'))
            if (state not in self._STATES or finished and state == 'RUNNING'
                    or any(not isinstance(value, str) for value in values)):
                raise ValueError('최종 평가 후보 상태 불일치')
            if row['run_id']:
                run_ids.append(row['run_id'])
            expected_recovery = recovery_ids.get(row['candidate_spec_hash'], '')
            if (row['recovery_request_id'] and row['recovery_request_id'] != expected_recovery
                    or expected_recovery and state != 'NOT_STARTED' and row['recovery_request_id'] != expected_recovery):
                raise ValueError('최종 평가 복구 요청 불일치')
            formatted.append((
                row['candidate_spec_hash'][:12], self._STATES[state], row['run_id'][:12],
                row['performance_status'] or 'N/A', row['report_status'] or 'N/A',
                row['logical_result_hash'][:12], row['recovery_request_id'][:12], row['reason'],
            ))
        if len(run_ids) != len(set(run_ids)):
            raise ValueError('최종 평가 실행 ID 중복')
        complete = sum(row['state'] in self._TERMINAL_SUCCESS for row in rows)
        expected_batch = 'COMPLETED' if complete == len(rows) else 'PARTIAL'
        if result['batch_status'] != expected_batch or not isinstance(result['limitations'], list):
            raise ValueError('최종 평가 전체 상태 불일치')
        if (result['status'] == 'resource_blocked'
                and not any(row['state'] == 'CANCELLED' and row['reason'].startswith('ResearchResourceBlocked:') for row in rows)):
            raise ValueError('자원 차단 결과 불일치')

        self._table.setRowCount(len(formatted))
        for index, values in enumerate(formatted):
            tooltip = (f"후보: {rows[index]['candidate_spec_hash']}\n"
                       f"실행: {rows[index]['run_id']}\n"
                       f"결과: {rows[index]['logical_result_hash']}")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(tooltip)
                self._table.setItem(index, column, item)
        if finished:
            self._displayed_hashes = expected_hashes
            self._last_states = {row['candidate_spec_hash']: row['state'] for row in rows}
        failed = sum(row['state'] == 'FAILED' for row in rows)
        cancelled = sum(row['state'] == 'CANCELLED' for row in rows)
        pending = sum(row['state'] == 'NOT_STARTED' for row in rows)
        if finished:
            self._can_resume = any(row['state'] == 'NOT_STARTED' for row in rows)
        label = ('최종 평가 진행 중' if not finished else
                 '최종 평가 완료' if expected_batch == 'COMPLETED' else
                 '자원 한도로 중단됨' if result['status'] == 'resource_blocked' else
                 '취소됨 · 종료 후보는 명시 복구 필요' if result['status'] == 'cancelled' else
                 '일부 후보 확인 필요')
        self._status.setText(label)
        self._summary.setText(
            f'전체 {len(rows)} / 완료 {complete} / 실행 중 {sum(row["state"] == "RUNNING" for row in rows)} '
            f'/ 실패 {failed} / 취소 {cancelled} / 미시작 {pending} '
            '· 실패·취소는 자동 재시도하지 않음'
        )
        self._summary.setToolTip('\n'.join(str(value) for value in result['limitations']))
        if finished:
            self._exposure_available = True
            self._exposed = False
        self._selection_changed()

    def _apply_exposure_result(self, result: Mapping[str, Any], request: FinalHoldoutExposureRequest) -> None:
        if (set(result) != {'status', 'kind', 'version', 'database', 'window_id', 'batch_id',
                            'request_id', 'state', 'recorded'}
                or result.get('status') != 'ok'
                or result.get('kind') != 'final_holdout_exposure'
                or result.get('version') != 'final_holdout_exposure_result/v1'
                or result.get('database') != str(request.database.resolve())
                or result.get('window_id') != request.batch.window_id
                or result.get('batch_id') != request.batch.batch_id
                or result.get('request_id') != request.request_id
                or result.get('state') != 'EXPOSED_DEVELOPMENT'
                or type(result.get('recorded')) is not bool):
            raise ValueError('개발 사용 기록 결과 범위 불일치')
        self._exposed = True
        self._exposure_available = False
        self._can_resume = False
        self._last_request = None
        try:
            self._saved_request_path.unlink(missing_ok=True)
        except OSError as exc:
            self._summary.setToolTip('저장된 최종 요청 정리 실패: ' + str(exc))
        self._status.setText('개발 사용 기록 완료 · 같은 기간은 다시 최종 검증으로 쓸 수 없음')
        self._summary.setText('이 최종 결과는 EXPOSED_DEVELOPMENT로 전환됐습니다. 다음 최종 평가는 새 미사용 기간이 필요합니다.')

    def _selection_changed(self) -> None:
        row = self._table.currentRow()
        recoverable = (self._operation is None and 0 <= row < len(self._displayed_hashes)
                       and self._last_states.get(self._displayed_hashes[row]) in self._RECOVERABLE
                       and bool(self._recovery_reason.text().strip()))
        self._recover.setEnabled(recoverable)
        self._expose.setEnabled(self._operation is None and self._exposure_available
                                and not self._exposed and bool(self._exposure_reason.text().strip()))

    def _clear_operation(self, *, preserve_receipt: bool = False) -> None:
        operation, self._operation = self._operation, None
        self._poll.stop()
        self._manager.clear()
        errors = []
        if operation is not None:
            for key in (() if preserve_receipt else ('request', 'result', 'cancel', 'owner')):
                try:
                    path = operation.get(key)
                    if path is not None:
                        path.unlink(missing_ok=True)
                except OSError as exc:
                    errors.append(str(exc))
        if errors:
            self._status.setToolTip(self._status.toolTip() + '\n' + '\n'.join(errors))
        if preserve_receipt and operation is not None:
            self._status.setToolTip(self._status.toolTip() + '\n비정상 종료 기록 보존: ' + str(operation['owner']))
        self._run.setEnabled(True)
        self._resume.setEnabled(self._last_request is not None and self._can_resume)
        self._cancel.setEnabled(False)
        self._selection_changed()

    def stop(self) -> None:
        if self._operation is not None:
            self._request_cancel()
        if self._manager.is_running:
            self._manager.stop(graceful_timeout=3.0, terminate_timeout=1.0)
        self._clear_operation(preserve_receipt=self._operation is not None
                              and self._operation.get('kind') != 'exposure')

    def closeEvent(self, event) -> None:
        self._request_cancel()
        event.ignore()
        self.hide()


class IndependentComparisonDialog(QDialog):
    """A separately cancellable, read-only result viewer; never owns campaign state."""

    def __init__(self, state_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle('독립 구간 결과 비교')
        self.resize(1000, 520)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._state_dir = Path(state_dir)
        self._manager = AuxiliaryProcessManager()
        self._operation: dict[str, Any] | None = None
        self._request_path = QLineEdit()
        self._request_path.setPlaceholderText('비교 요청 JSON: 비교할 저장 결과와 연구 DB를 지정합니다')
        browse = QPushButton('비교 파일 선택')
        browse.clicked.connect(self._browse)
        self._run = QPushButton('결과 비교')
        self._run.clicked.connect(self._start)
        self._cancel = QPushButton('취소')
        self._cancel.setEnabled(False)
        self._cancel.clicked.connect(self._request_cancel)
        self._status = QLabel('저장된 결과를 비교합니다. 시뮬레이션이나 주문을 새로 실행하지 않습니다.')
        self._status.setWordWrap(True)
        self._summary = QLabel('구간마다 초기 현금이 별개입니다. 연속 계좌 수익률로 합산하지 않습니다.')
        self._summary.setWordWrap(True)
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(('구간(KST) / 역할', '상태', '거래 / 활성일', '손익', '구간 MDD', '제외 사유', '판단 근거'))
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        for widget in (self._request_path, browse, self._run, self._cancel):
            row.addWidget(widget)
        layout.addLayout(row)
        layout.addWidget(self._status)
        layout.addWidget(self._summary)
        layout.addWidget(self._table)
        self._poll = QTimer(self)
        self._poll.setInterval(250)
        self._poll.timeout.connect(self._poll_process)

    def _browse(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, '독립 구간 비교 요청', '', 'JSON (*.json)')
        if selected:
            self._request_path.setText(selected)

    def _start(self) -> None:
        if self._operation is not None or self._manager.is_running:
            return
        try:
            request = load_independent_comparison_request(Path(self._request_path.text().strip()))
            self._state_dir.mkdir(parents=True, exist_ok=True)
            operation_id = uuid.uuid4().hex
            files = {key: self._state_dir / f'independent_comparison_{operation_id}.{suffix}'
                     for key, suffix in (('request', 'json'), ('result', 'result.json'), ('cancel', 'cancel'))}
            self._operation = {**files, 'database': str(request.database.resolve()), 'run_ids': list(request.run_ids)}
            files['request'].write_text(json.dumps({
                'version': 'independent_development_comparison_request/v1',
                'database': self._operation['database'], 'run_ids': self._operation['run_ids'],
            }, ensure_ascii=False), encoding='utf-8')
            self._manager.start(build_auxiliary_command('kiwoom_monitor.research_process', '--research-process', [
                '--compare-runs', str(files['request']), '--result', str(files['result']), '--cancel', str(files['cancel']),
            ]), Path.cwd(), below_normal_priority=True)
        except (OSError, TypeError, ValueError) as exc:
            self._clear_operation()
            self._status.setText(f'비교 시작 실패 · {exc}')
            return
        self._run.setEnabled(False)
        self._cancel.setEnabled(True)
        self._status.setText('별도 프로세스에서 저장 결과를 비교하는 중…')
        self._poll.start()

    def _request_cancel(self) -> None:
        if self._operation is not None:
            self._operation['cancel'].write_text('cancel\n', encoding='ascii')
            self._cancel.setEnabled(False)
            self._status.setText('결과 비교 취소 요청 중…')

    def _poll_process(self) -> None:
        process = self._manager.process
        if self._operation is None or process is None or process.poll() is None:
            return
        operation = self._operation
        exit_code = process.poll()
        try:
            with operation['result'].open('rb') as stream:
                encoded = stream.read(16 * 1024 * 1024 + 1)
            if len(encoded) > 16 * 1024 * 1024:
                raise ValueError('comparison result exceeds 16 MiB')
            result = json.loads(encoded.decode('utf-8'))
            if not isinstance(result, Mapping):
                raise ValueError('comparison result is not an object')
            if exit_code == 2 and result.get('status') == 'cancelled':
                self._status.setText('결과 비교 취소됨 · 이전 표 유지')
            elif exit_code != 0 or result.get('status') != 'ok':
                raise ValueError(str(result.get('reason', f'process exit {exit_code}')))
            elif (result.get('kind') != 'independent_development_comparison'
                  or result.get('database') != operation['database'] or result.get('run_ids') != operation['run_ids']):
                raise ValueError('comparison result scope mismatch')
            else:
                comparison = result['comparison']
                if (comparison['requested_count'] != len(operation['run_ids'])
                        or [row['run_id'] for row in comparison['partitions']] != operation['run_ids']):
                    raise ValueError('comparison partition scope mismatch')
                self._apply_comparison(comparison)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            self._status.setText(f'결과 비교 실패 · {exc} · 이전 표 유지')
        finally:
            self._clear_operation()

    def _apply_comparison(self, comparison: Mapping[str, Any]) -> None:
        if comparison.get('version') != 'independent_development_comparison/v1':
            raise ValueError('comparison result version mismatch')
        rows = comparison['partitions']
        # Build all rows before altering the previous display on malformed results.
        def period(row):
            if not row.get('start') or not row.get('end'):
                return str(row['run_id'])
            return ' ~ '.join(datetime.fromisoformat(row[key]).astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
                              for key in ('start', 'end')) + ' / ' + str(row.get('role', ''))
        formatted = tuple((period(row),
            str(row['status']), f"{row['closed_trade_count']} / {row['active_day_count']}",
            _won(row.get('net_pnl_won')), _won(row.get('max_drawdown_won')),
            str(row.get('excluded_reason', '')), ', '.join(row.get('reasons', ()))) for row in rows)
        summaries = ('같은 조건 비교 완료' if comparison['status'] == 'COMPLETE' else
                     '조건이 달라 통합 통계 없음' if comparison['status'] == 'INCOMPARABLE' else '누락·제외·표본 부족 확인 필요')
        median_pnl = comparison['median_partition_pnl_won']
        median_text = 'N/A' if median_pnl is None else f'{median_pnl:,.1f}원'
        limitations = '\n'.join(comparison.get('limitations', ()))
        summary = (f"요청 {comparison['requested_count']} / 고유 실행 {comparison['unique_run_count']} / "
                   f"적격 구간 {comparison['eligible_partition_count']} / 양수 구간 {comparison['positive_partition_count']} · "
                   f"구간 손익 중앙값 {median_text} · "
                   f"최악 구간 MDD {_won(comparison['worst_partition_drawdown_won'])}\n"
                   '독립 초기 현금 기준 · 연속 계좌 수익률 아님 · 적격은 수익성 승인 아님')
        dimension_names = {'by_symbol': '종목', 'by_date': '날짜', 'by_time_bucket': '시간대'}
        tooltips = tuple('실행: ' + str(row['run_id']) + '\n' + '\n'.join(
            f'{dimension_names.get(dimension, dimension)} {key}: 거래 {count}건, 손익 {_won(pnl)}'
            for dimension, key, count, pnl in row.get('strata', ())) for row in rows)
        self._table.setRowCount(len(formatted))
        for index, values in enumerate(formatted):
            tooltip = tooltips[index]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(tooltip)
                self._table.setItem(index, column, item)
        self._status.setText(summaries)
        self._summary.setText(summary)
        self._summary.setToolTip(limitations)

    def _clear_operation(self) -> None:
        operation, self._operation = self._operation, None
        self._poll.stop()
        self._manager.clear()
        cleanup_errors = []
        if operation is not None:
            for key in ('request', 'result', 'cancel'):
                try:
                    operation[key].unlink(missing_ok=True)
                except OSError as exc:
                    cleanup_errors.append(str(exc))
        self._status.setToolTip('\n'.join(cleanup_errors))
        self._run.setEnabled(True)
        self._cancel.setEnabled(False)

    def stop(self) -> None:
        if self._operation is not None:
            self._request_cancel()
        if self._manager.is_running:
            self._manager.stop(graceful_timeout=3.0, terminate_timeout=1.0)
        self._clear_operation()

    def closeEvent(self, event) -> None:
        self._request_cancel()
        event.ignore()
        self.hide()


def _won(value: object) -> str:
    return "N/A" if value is None else f"{int(value):,}원"


def format_market_regime_summary(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    payload = value.get("value")
    if not isinstance(payload, Mapping):
        return "시장 판정 자료 없음"
    market_type = str(payload.get("market_type", "UNKNOWN"))
    reasons = payload.get("reasons", ())
    reason_text = ", ".join(str(reason) for reason in reasons) if isinstance(reasons, (list, tuple)) else ""
    return f"시장 {market_type}" + (f" ({reason_text})" if reason_text else "")
