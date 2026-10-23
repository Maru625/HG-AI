# 04. MCP 툴 명세

> 이 문서는 `uv run python -m mcp_server.export_spec`로 서버 코드에서 만든다. 손으로 고치지 않는다.

## 서버 지침 (instructions)

> 이 서버는 샘플테크(주)의 목업 전결 시스템이다. 전결 처리는 반드시 ① 규정 확인(search_rules, get_process_spec) → ② 증빙이 있으면 증빙 확인(check_evidence) → ③ create_plan으로 목록을 만들고 사용자 승인 → ④ save_drafts로 초안 작성 → ⑤ 사용자 재확인 → ⑥ submit_drafts 순서로 진행한다. 결재선·한도·중복 여부는 직접 판단하지 말고 서버 결과를 따른다. 사용자가 확인하지 않은 단계는 진행하지 않는다. 결재자가 결재할 문서를 물으면 list_pending_approvals와 get_review_brief로 중요도순 목록과 주의 항목을 전달하되, 서버 판정과 AI 의견을 구분하고 승인·반려는 하지 않는다(웹 화면에서만 가능). 모든 데이터는 시연용 더미다. 날짜는 실제 오늘이 아닌 whoami의 today 기준이다. 흐린 증빙도 원본을 다시 요구하지 말고 읽은 값과 낮은 confidence로 넣는다.

- 글자 수: 498자 (앞 512자 안에 핵심을 담는다, 설계서 6.1)
- 연결별로 끝에 연결 이름과 역할 안내 한 줄이 붙는다.

## 연결별 툴 범위

| 연결 | 툴 수 | 툴 |
|---|---|---|
| 기안자용 연결 | 14 | `whoami`, `list_processes`, `search_rules`, `get_process_spec`, `check_approval_line`, `get_status`, `check_evidence`, `check_room_availability`, `create_plan`, `approve_plan`, `cancel_plan`, `save_drafts`, `confirm_drafts`, `submit_drafts` |
| 결재자용 연결 | 8 | `whoami`, `list_processes`, `search_rules`, `get_process_spec`, `check_approval_line`, `get_status`, `list_pending_approvals`, `get_review_brief` |

- 공통 6개: `whoami`, `list_processes`, `search_rules`, `get_process_spec`, `check_approval_line`, `get_status`
- 승인·반려 툴은 어느 연결에도 없다. 결재 처리는 웹 화면에서만 한다(설계서 3.2).

## 오류 응답

툴이 실패하면 `isError: true`와 함께 아래 형식의 JSON 문자열을 돌려준다(앞에 SDK가 붙이는 `Error executing tool …:` 머리말이 있다).

```json
{"ok": false, "code": "PLAN_NOT_APPROVED", "message": "…", "fix_hint": "다음에 할 일", "details": {}}
```

- 전결 서버 판정 오류: 설계서 6.4의 11개 + `ALREADY_SUBMITTED`, `NOT_FOUND`, `FORBIDDEN`, `BAD_REQUEST`, `UNAUTHORIZED`
- MCP 서버가 붙이는 오류: `SERVER_UNAVAILABLE`(전결 서버에 연결할 수 없음), `SERVER_ERROR`(알 수 없는 응답)
- 입력 형식이 스키마와 다르면 SDK가 필드 이름과 함께 검증 오류를 돌려준다.

## 툴 상세

### `whoami` — 연결된 사용자 확인

- 연결: 공통 · 종류: 읽기 · 주석: `{"readOnlyHint": true, "openWorldHint": false}`

이럴 때 쓴다: 전결 작업을 시작할 때 맨 처음. 이 연결에 고정된 사용자(이름, 사번, 부서, 직급, 팀장)와 시연 날짜(today), 확인 방식(A=채팅 확인, B=화면 확인)을 돌려준다. '지난주', '다음 주 화요일' 같은 날짜는 실제 오늘이 아니라 이 today를 기준으로 계산한다.

입력 없음

### `list_processes` — 전결 프로세스 목록

- 연결: 공통 · 종류: 읽기 · 주석: `{"readOnlyHint": true, "openWorldHint": false}`

이럴 때 쓴다: 사용자의 지시와 자료를 보고 어떤 전결 처리가 필요한지 후보를 찾을 때. 프로세스 ID, 이름, 적용 상황, 결재 필요 여부를 돌려준다.

입력 없음

### `search_rules` — 전결 규정 검색

- 연결: 공통 · 종류: 읽기 · 주석: `{"readOnlyHint": true, "openWorldHint": false}`

이럴 때 쓴다: 판단 근거가 되는 규정 조항이 필요할 때. 조항 번호, 제목, 본문, 관련 프로세스를 돌려준다. 결과가 없으면 다른 낱말로 다시 찾는다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `query` | 문자열 | 필수 | 찾을 낱말 (예: 출장, 숙박, 회의실, 중복) |

### `get_process_spec` — 프로세스 작성 명세

- 연결: 공통 · 종류: 읽기 · 주석: `{"readOnlyHint": true, "openWorldHint": false}`

이럴 때 쓴다: 초안을 쓰기 전에 반드시. 필수 입력 항목(키·이름·형식), 증빙 요건과 형식, 한도, 기한, 결재선 기준, 작성 예시, 근거 조항을 돌려준다. save_drafts의 values는 여기 fields의 키를 쓴다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `process_id` | 문자열 | 필수 | 프로세스 ID (예: EXP-02) |

### `check_approval_line` — 결재선 확인

- 연결: 공통 · 종류: 읽기 · 주석: `{"readOnlyHint": true, "openWorldHint": false}`

이럴 때 쓴다: 결재선을 사용자에게 보여줄 때. 결재자 순서와 합의 부서, 근거 조항을 서버가 계산해 돌려준다. 결재선은 직접 추측하지 않는다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `process_id` | 문자열 | 필수 | 프로세스 ID |
| `amount` | 정수 (선택) | 선택 | 금액(원). 금액 기준이 있는 프로세스일 때 |
| `dept` | 문자열 (선택) | 선택 | 부서 이름이나 코드. 비우면 연결된 사용자의 부서 |
| `values` | 객체 (선택) | 선택 | 합의 조건 판단용 입력값(회의실 등). 선택 |

### `get_status` — 진행 상황 조회

- 연결: 공통 · 종류: 읽기 · 주석: `{"readOnlyHint": true, "openWorldHint": false}`

이럴 때 쓴다: 진행 상황이나 반려 사유를 확인할 때. 계획 ID를 주면 계획과 문서들의 상태를, 문서 ID를 주면 그 문서의 상태·결재 진행·반려 사유를, 둘 다 비우면 최근 계획을 돌려준다. 화면 링크가 함께 온다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `plan_id` | 문자열 (선택) | 선택 | 작업 계획 ID (예: PLAN-001) |
| `doc_id` | 문자열 (선택) | 선택 | 문서 ID(DOC-…) 또는 문서번호(ST-…) |

### `check_evidence` — 증빙 확인

- 연결: 기안자용 연결 · 종류: 읽기 · 주석: `{"readOnlyHint": true, "openWorldHint": false}`

이럴 때 쓴다: 영수증 등 증빙을 모은 뒤, create_plan을 부르기 전. 흐린 영수증도 원본을 다시 달라고 하지 말고 읽은 값을 넣고 confidence를 낮게 적는다(판독 신뢰도 판정은 서버가 한다). 증빙별로 중복(이미 정산된 문서번호), 한도 초과, 휴일 사용, 판독 신뢰도 낮음을 서버가 판정하고, 제외를 제안하는 증빙과 사유가 필요한 항목(reason_key), 비목별 합계를 돌려준다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `process_id` | 문자열 | 필수 | 프로세스 ID (예: EXP-02) |
| `evidence` | EvidenceItem 목록 | 필수 | 자료에서 읽은 증빙 전체 (중복이 의심돼도 일단 모두 넣는다) |
| `reasons` | 객체 (선택) | 선택 | 이미 받은 사유 (reason_key → 사유). 선택 |

`EvidenceItem` 항목

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `date` | 문자열 | 필수 | 사용일 YYYY-MM-DD |
| `merchant` | 문자열 | 필수 | 상호 |
| `category` | 문자열 | 필수 | 비목. get_process_spec의 evidence.categories 중 하나 (예: 교통비, 숙박비, 식비, 회의비, 견적서) |
| `amount` | 정수 | 필수 | 금액(원, 정수) |
| `filename` | 문자열 (선택) | 선택 | AI가 읽은 원본 파일 이름 |
| `confidence` | 숫자 (선택) | 선택 | 판독 신뢰도 0~1. 흐리거나 확실하지 않으면 낮게 적는다 |
| `nights` | 정수 (선택) | 선택 | 숙박비일 때 박 수 |

### `check_room_availability` — 회의실 빈 시간 확인

- 연결: 기안자용 연결 · 종류: 읽기 · 주석: `{"readOnlyHint": true, "openWorldHint": false}`

이럴 때 쓴다: 회의실 사용 신청(MTG-01) 초안을 쓰기 전. 회의실별 수용 인원, 겹치는 예약(가예약 포함), 합의가 필요한 조건, 추천 회의실을 돌려준다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `date` | 문자열 | 필수 | 날짜 YYYY-MM-DD |
| `start` | 문자열 | 필수 | 시작 시각 HH:MM |
| `end` | 문자열 | 필수 | 종료 시각 HH:MM |
| `people` | 정수 | 필수 | 인원 |

### `create_plan` — 작업 계획 만들기

- 연결: 기안자용 연결 · 종류: 쓰기 · 주석: `{"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": false}`

이럴 때 쓴다: 규정과 증빙을 확인해 필요한 처리 목록이 정해졌을 때. 계획 ID, 항목 번호, 항목별 증빙 확인 결과, 화면 링크를 돌려준다. 만든 뒤에는 목록을 사용자에게 보여주고 승인(확인 ①)을 받는다. 승인 전에는 초안을 쓸 수 없다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `title` | 문자열 | 필수 | 계획 제목 (예: 대전 출장 정리) |
| `summary` | 문자열 | 필수 | 사용자 요청 요약 |
| `items` | PlanItem 목록 | 필수 | 처리할 항목 목록. 프로세스 하나당 한 항목 |

`EvidenceItem` 항목

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `date` | 문자열 | 필수 | 사용일 YYYY-MM-DD |
| `merchant` | 문자열 | 필수 | 상호 |
| `category` | 문자열 | 필수 | 비목. get_process_spec의 evidence.categories 중 하나 (예: 교통비, 숙박비, 식비, 회의비, 견적서) |
| `amount` | 정수 | 필수 | 금액(원, 정수) |
| `filename` | 문자열 (선택) | 선택 | AI가 읽은 원본 파일 이름 |
| `confidence` | 숫자 (선택) | 선택 | 판독 신뢰도 0~1. 흐리거나 확실하지 않으면 낮게 적는다 |
| `nights` | 정수 (선택) | 선택 | 숙박비일 때 박 수 |

`PlanItem` 항목

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `process_id` | 문자열 | 필수 | 프로세스 ID (list_processes 결과, 예: EXP-02) |
| `summary` | 문자열 | 필수 | 이 항목에서 할 일 한 줄 요약 |
| `sources` | 문자열 | 선택 | 근거 자료 (예: 영수증 5장, 출장 메모) |
| `evidence` | EvidenceItem 목록 | 선택 | 이 항목의 증빙 목록 (없으면 비움) |

### `approve_plan` — 작업 계획 승인 (확인 ①)

- 연결: 기안자용 연결 · 종류: 쓰기 · 주석: `{"readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}`

이럴 때 쓴다: 사용자가 대화에서 작업 계획 목록을 승인한다고 분명히 말했을 때만. 사용자 승인 없이 부르지 않는다. 확인 방식이 B(화면 확인형)면 CONFIRM_IN_UI_REQUIRED를 돌려주므로, 그때는 화면 링크를 안내한다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `plan_id` | 문자열 | 필수 | 작업 계획 ID |

### `cancel_plan` — 작업 계획 취소

- 연결: 기안자용 연결 · 종류: 쓰기 · 되돌리기 어려움 · 주석: `{"readOnlyHint": false, "destructiveHint": true, "idempotentHint": true, "openWorldHint": false}`

이럴 때 쓴다: 사용자가 작업 계획을 그만두겠다고 할 때. 상신 전 초안도 함께 취소된다. 이미 상신한 문서는 취소되지 않는다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `plan_id` | 문자열 | 필수 | 작업 계획 ID |
| `reason` | 문자열 | 선택 | 취소 사유 |

### `save_drafts` — 초안 저장

- 연결: 기안자용 연결 · 종류: 쓰기 · 주석: `{"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": false}`

이럴 때 쓴다: 작업 계획이 승인된 뒤 항목별 초안을 쓰거나 고칠 때(같은 항목 번호로 다시 부르면 고친다). 반려된 문서의 항목을 다시 저장하면 원문서를 잇는 재기안이 된다. 문서별 상태, 오류·경고, 필요한 사유(missing_reasons), 결재선 미리보기, 화면 링크를 돌려준다. 담당자·사유처럼 사람이 정해야 하는 값은 지어내지 말고 사용자에게 묻는다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `plan_id` | 문자열 | 필수 | 승인된 작업 계획 ID |
| `drafts` | Draft 목록 | 필수 | 항목별 초안. 여러 건을 한 번에 넣는다 |

`Draft` 항목

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `item_no` | 정수 | 필수 | create_plan 결과의 항목 번호 |
| `values` | 객체 | 필수 | get_process_spec의 fields 키에 맞춘 입력값 |
| `evidence` | EvidenceItem 목록 | 선택 | 증빙 목록. check_evidence에서 제외 제안된 증빙은 빼고 넣는다 |
| `reasons` | 객체 | 선택 | 사유. 검증 결과의 reason_key를 키로, 사용자에게 받은 사유 문장을 값으로 넣는다 |
| `memo` | 문자열 | 선택 | 메모 (선택) |

`EvidenceItem` 항목

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `date` | 문자열 | 필수 | 사용일 YYYY-MM-DD |
| `merchant` | 문자열 | 필수 | 상호 |
| `category` | 문자열 | 필수 | 비목. get_process_spec의 evidence.categories 중 하나 (예: 교통비, 숙박비, 식비, 회의비, 견적서) |
| `amount` | 정수 | 필수 | 금액(원, 정수) |
| `filename` | 문자열 (선택) | 선택 | AI가 읽은 원본 파일 이름 |
| `confidence` | 숫자 (선택) | 선택 | 판독 신뢰도 0~1. 흐리거나 확실하지 않으면 낮게 적는다 |
| `nights` | 정수 (선택) | 선택 | 숙박비일 때 박 수 |

### `confirm_drafts` — 초안 확인 (확인 ②)

- 연결: 기안자용 연결 · 종류: 쓰기 · 주석: `{"readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}`

이럴 때 쓴다: 사용자가 초안 내용(화면 링크 또는 대화에서 보여준 요약)을 보고 맞다고 분명히 말했을 때만. 확인 방식이 B면 CONFIRM_IN_UI_REQUIRED를 돌려주므로 임시저장함 화면에서 확인해 달라고 안내한다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `doc_ids` | 문자열 목록 | 필수 | 확인할 문서 ID 목록 |

### `submit_drafts` — 상신 (확인 ③)

- 연결: 기안자용 연결 · 종류: 쓰기 · 주석: `{"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": false}`

이럴 때 쓴다: 확인완료된 초안을 사용자가 올려 달라고 했을 때. 문서별 문서번호, 결재선, 회의실 가예약 결과, 결재자에게 간 알림을 돌려준다. 확인 뒤 내용이 바뀌었으면 MODIFIED_AFTER_CONFIRM을 돌려준다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `doc_ids` | 문자열 목록 | 필수 | 상신할 문서 ID 목록 (확인완료 상태) |

### `list_pending_approvals` — 결재할 문서 목록

- 연결: 결재자용 연결 · 종류: 읽기 · 주석: `{"readOnlyHint": true, "openWorldHint": false}`

이럴 때 쓴다: 결재자가 결재할 문서를 묻거나 결재 알림을 받은 뒤. 연결된 사용자가 결재할 차례인 문서 전체를 중요도(긴급·보통·낮음)순으로, 중요도 이유·대기 기간·주의 항목 수·화면 링크와 함께 돌려준다. 승인·반려는 웹 화면에서만 할 수 있다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `sort` | importance | submitted | 선택 | 정렬: importance(중요도순, 기본) 또는 submitted(상신순) |
| `only` | 긴급 | 보통 | 낮음 (선택) | 선택 | 이 중요도만 보기. 선택 |

### `get_review_brief` — 결재 검토 요약

- 연결: 결재자용 연결 · 종류: 읽기 · 주석: `{"readOnlyHint": true, "openWorldHint": false}`

이럴 때 쓴다: 결재자가 문서 하나를 검토할 때. 입력값·증빙 요약, 서버가 판정한 주의 항목(규칙, 해당 항목, 근거 조항, 기안자 사유), 결재선 진행, 재상신이면 달라진 점, 화면 링크를 돌려준다. 주의 항목은 서버 판정이므로 AI 의견과 구분해 전달한다.

| 입력 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `doc_id` | 문자열 | 필수 | 문서 ID(DOC-…) 또는 문서번호(ST-…) |
