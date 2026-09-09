# gh-todoist-sync

GitHub에서 로그인한 사용자에게 assign 된 issue와 PR을 Todoist로 옮깁니다.

동기화는 GitHub에서 Todoist로 향하는 한 방향으로만 이루어집니다. issue가 닫히거나
assign이 해제되면 Todoist task도 완료 처리됩니다. not planned나 duplicate로 닫힌
issue와 merge 되지 않고 닫힌 PR만 완료가 아니라 삭제합니다. 반대 방향으로는 동기화되지 않습니다. 즉, Todoist에서
task를 완료해도 GitHub issue는 그대로 남습니다.

```
GitHub                          <- 부모 project
├─ (개인 repo section들)         <- owner가 User인 repo
├─ gsainfoteam                  <- owner가 Organization이면 sub-project
│  ├─ account-fe                <- section = repo
│  └─ ziggle-fe
└─ studio-void
   └─ campass-fe
```

task 이름은 `[#61](https://github.com/.../issues/61) 비밀번호 찾기 페이지` 와 같은
형태입니다. Todoist가 markdown을 렌더링하므로 `#61`을 누르면 해당 issue로 이동합니다.
repo 이름은 section에 이미 표시되므로 task 이름에서는 제외했습니다.

## 설치

```bash
uv sync
uv run gh-todoist-sync install   # launchd 등록, 120초마다 실행
```

`gh`와 `td`에 로그인되어 있으면 따로 설정할 항목이 없습니다. 다만 `GITHUB_TOKEN`과
`TODOIST_API_TOKEN` 환경변수를 지정하면 그 값을 먼저 사용합니다.

설치는 checkout 한 위치 한 곳만 유지합니다. `uv tool install`로 한 벌 더 설치하면 state
파일이 두 개가 되고 Todoist에도 tree가 두 개 생성되기 때문입니다.

## 명령

```bash
uv run gh-todoist-sync                  # sync와 동일
uv run gh-todoist-sync sync --dry-run   # 실행 계획만 출력하고 아무것도 쓰지 않음
uv run gh-todoist-sync sync --force     # 대량 완료 가드 해제
uv run gh-todoist-sync sync --grace 14  # 빈 section 유예 기간 (기본 7일)
uv run gh-todoist-sync install          # launchd 등록 (--interval로 주기 조절)
uv run gh-todoist-sync uninstall        # 해제
uv run gh-todoist-sync status           # 동작 여부, 마지막 종료 코드
```

launchd는 `.venv/bin/gh-todoist-sync`를 직접 호출하므로 `uv run`이 붙지 않습니다.
로그는 `~/Library/Logs/gh-todoist-sync.log`에 기록됩니다.

## 수집 대상

| 소스 | endpoint |
| --- | --- |
| 로그인한 사용자에게 assign 된 open issue와 PR | `GET /issues?filter=assigned&state=open` |
| review 요청받은 PR | `GET /search/issues?q=is:pr is:open review-requested:@me` |
| 사용자가 연 open PR | `GET /search/issues?q=is:pr is:open author:@me` |
| 사용자가 열었고 아무도 맡지 않은 open issue | `GET /search/issues?q=is:issue is:open author:@me no:assignee` |

마지막 줄에 `no:assignee`가 붙어 있는 이유는, 내가 연 issue 라도 다른 사람이 assign 되어
있으면 그 사람의 작업이기 때문입니다. 내가 assign 된 issue는 위의 assign 목록으로 이미
들어옵니다.

archive 된 repo는 수집 대상에서 제외합니다. 어차피 손댈 수 없는 작업이므로 목록에 남아
있으면 방해가 되기 때문입니다.

| 필드 | 규칙 |
| --- | --- |
| 우선순위 | PR은 p2, issue는 p4 |
| label | PR은 `gh-pr` (보라), issue는 `gh-issue` (초록), 막힌 issue는 `gh-blocked` (빨강). 직접 붙인 label은 그대로 유지합니다 |
| 설명 | 열려 있는 의존 관계. `blocked by #39`, `blocks #30, #40` 꼴이고 다른 저장소는 `owner/repo#12` 로 적습니다 |
| 마감일 | GitHub milestone의 `due_on` 값을 사용합니다. 값이 없으면 비워 둡니다 |

## 정렬

같은 section 안에서 task는 이슈 번호가 아니라 의존 관계 순으로 놓입니다. 축이 셋입니다.

| 축 | 뜻 |
| --- | --- |
| depth | 앞을 막고 있는 열린 이슈의 사슬 길이. 0이면 지금 착수할 수 있습니다 |
| reach | 뒤에서 기다리는 이슈의 수. 사슬 전체를 세므로 셋을 연달아 푸는 일과 셋을 한꺼번에 푸는 일이 같은 무게가 됩니다 |
| 번호 | 나머지가 같을 때 쓰는 마지막 기준 |

그래서 남을 가장 많이 푸는 일이 맨 위에, 아무것도 막지 않고 막히지도 않은 일이 가운데,
막힌 일이 맨 아래에 옵니다. 막힌 task에는 `gh-blocked`가 붙으므로 Todoist 필터에
`!@gh-blocked`를 걸면 지금 손댈 수 있는 것만 남습니다.

닫힌 이슈는 더 이상 막지 않으므로 관계에서 빠집니다. 의존 관계는 GraphQL의 `blockedBy`
와 `blocking`으로 한 번에 100개씩 읽으므로 이슈가 늘어도 호출이 이슈 수만큼 늘지
않습니다.

REST 쪽에 순서를 세우는 자리가 없어서 재정렬만 sync 명령을 씁니다. 순서가 어긋난
section에만 한 번씩 나갑니다.

## 안전장치

GitHub 호출이 하나라도 실패하면 Todoist에는 아무것도 기록하지 않습니다. 빈 응답을 목표
상태로 잘못 인식해서 전부 완료 처리해 버리는 상황을 막기 위해서입니다.

한 번에 20개를 넘게 완료하려고 하면 실행을 중단합니다. 그 정도 수량이라면 org 권한이
누락되었거나 page가 잘려서 응답한 상황이지, 실제로 스무 개를 끝냈을 가능성은 낮기
때문입니다. 실제로 그런 경우라면 `--force`를 사용합니다.

state 파일에 없는 항목은 건드리지 않습니다. 따라서 GitHub project 안에 메모를 직접 적어
두어도 안전합니다.

GitHub에서 사라진 항목은 완료 처리합니다. 다만 not planned나 duplicate로 닫힌 issue,
merge 되지 않고 닫힌 PR은 완료가 아니라 삭제합니다. 하지 않기로 한 일을 완료 목록에
남기면 실제로 끝낸 작업과 섞여서 기록이 틀리기 때문입니다. 이 판정은 GraphQL로 issue의
`stateReason`과 PR의 `state`를 확인해서 내립니다. merge 된 PR은 `MERGED`라는 별도
state 이므로 완료로 남습니다.

비어 있는 section이나 sub-project는 곧바로 삭제하지 않고, 처음 비게 된 날짜를 기록해
둡니다. 그 상태로 7일 (`--grace`)이 지나야 삭제합니다. 오늘 issue가 모두 닫힌 repo를
내일 다시 열 수도 있는데, polling 할 때마다 삭제하고 다시 생성하면 곤란하기 때문입니다.
안에 항목이 하나라도 남아 있으면, 이 도구가 만든 항목이 아니더라도 삭제하지 않습니다.

## 상태 저장

GitHub id와 Todoist id를 짝지어 checkout 루트의 `state.json`에 저장합니다. 이 파일은
gitignore에 등록되어 있습니다.

이 파일을 잃어버리면 다음 실행에서 기존 tree를 인식하지 못하고 동일한 tree를 하나 더
생성합니다. 머신 한 대에서만 실행한다고 가정하고 있습니다.

## 제약

- webhook이 아니라 polling 방식이므로 최대 2분까지 지연됩니다.
- issue 본문과 comment는 가져오지 않고 link만 연결합니다.

## 개발

```bash
uv sync
uv run pytest        # 네트워크를 사용하지 않음
uv run ruff check .
uv run ruff format .
```

코드를 고치기 전에 launchd 를 먼저 내립니다.

```bash
launchctl bootout gui/$(id -u)/local.gh-todoist-sync   # 고치기 전
launchctl kickstart -k gui/$(id -u)/local.gh-todoist-sync   # 끝난 뒤
```

launchd 가 부르는 `.venv/bin/gh-todoist-sync` 는 이 저장소를 editable 로 가리킵니다.
그래서 파일을 저장하는 순간부터 120초마다 작성 중인 코드가 실제 Todoist에 적용되고,
`--dry-run` 으로 계획을 먼저 확인하려던 절차가 의미를 잃습니다.

`reconcile.py`가 순수 함수로 작성되어 있어서 test가 가볍습니다. 이렇게 구현한 이유는
각 모듈의 docstring에 적어 두었습니다.

## 문제 해결

```bash
uv run gh-todoist-sync status
uv run gh-todoist-sync sync --dry-run   # 실행 계획 확인
gh auth status                          # GitHub 인증
td auth status                          # Todoist 인증
tail -50 ~/Library/Logs/gh-todoist-sync.log
```

처음부터 다시 만들려면 Todoist의 "GitHub" project와 `state.json`을 둘 다 삭제한 뒤에
한 번 실행합니다. 둘 중 하나만 삭제하면 안 됩니다. project만 삭제하면 존재하지 않는
id를 가리키게 되고, `state.json`만 삭제하면 tree가 두 개로 늘어나기 때문입니다.

## 라이선스

MIT
