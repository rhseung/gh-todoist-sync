# gh-todoist-sync

GitHub에서 나한테 assign된 issue와 PR을 Todoist로 옮긴다.

동기화는 GitHub에서 Todoist 한 방향으로만 간다. issue가 닫히거나 assign이 풀리면 Todoist
task도 완료된다. 반대는 안 된다. Todoist에서 task를 완료해도 GitHub issue는 그대로다.

```
GitHub                          <- 부모 project
├─ (개인 repo section들)         <- owner가 User인 repo
├─ gsainfoteam                  <- owner가 Organization이면 sub-project
│  ├─ account-fe                <- section = repo
│  └─ ziggle-fe
└─ studio-void
   └─ campass-fe
```

task 이름은 `[#61](https://github.com/.../issues/61) 비밀번호 찾기 페이지` 꼴이다.
Todoist가 markdown을 렌더링해서 `#61`을 누르면 issue로 간다. repo 이름은 section에 이미
있으니 뺐다.

## 설치

```bash
uv sync
uv run gh-todoist-sync install   # launchd 등록, 120초마다 실행
```

`gh`와 `td`가 로그인돼 있으면 따로 설정할 게 없다. `GITHUB_TOKEN` / `TODOIST_API_TOKEN`
환경변수를 주면 그쪽을 먼저 쓴다.

설치는 checkout 한 곳만 유지한다. `uv tool install`로 한 벌 더 깔면 state 파일이 둘이
되고 Todoist에도 tree가 두 개 생긴다.

## 명령

```bash
uv run gh-todoist-sync                  # sync와 같음
uv run gh-todoist-sync sync --dry-run   # 실행 계획만 출력, 아무것도 안 씀
uv run gh-todoist-sync sync --force     # 대량 완료 가드 해제
uv run gh-todoist-sync sync --grace 14  # 빈 section 유예 기간 (기본 7일)
uv run gh-todoist-sync install          # launchd 등록 (--interval 로 주기 조절)
uv run gh-todoist-sync uninstall        # 해제
uv run gh-todoist-sync status           # 동작 여부, 마지막 종료 코드
```

launchd는 `.venv/bin/gh-todoist-sync`를 직접 부르니까 `uv run`이 붙지 않는다.
로그는 `~/Library/Logs/gh-todoist-sync.log`.

## 수집 대상

| 소스 | endpoint |
| --- | --- |
| 나한테 assign된 open issue·PR | `GET /issues?filter=assigned&state=open` |
| review 요청받은 PR | `GET /search/issues?q=is:pr is:open review-requested:@me` |
| 내가 연 open PR | `GET /search/issues?q=is:pr is:open author:@me` |

archive된 repo는 뺀다. 어차피 손댈 수 없는 작업이라 목록에 있어봐야 방해만 된다.

| 필드 | 규칙 |
| --- | --- |
| 우선순위 | PR = p2, issue = p4 |
| label | PR = `gh-pr`(보라), issue = `gh-issue`(초록). 손으로 붙인 label은 남긴다 |
| 마감일 | GitHub milestone의 `due_on`. 없으면 비움 |

## 안전장치

GitHub 호출이 하나라도 실패하면 Todoist에 아무것도 안 쓴다. 빈 응답을 목표 상태로
착각해서 전부 완료 처리해버리면 큰일이다.

한 번에 20개 넘게 완료하려고 하면 멈춘다. 그 정도면 org 권한이 빠졌거나 page가 잘려 온
거지 내가 정말 스무 개를 끝냈을 리 없다. 진짜 그럴 일이면 `--force`.

state 파일에 없는 건 건드리지 않는다. GitHub project 안에 메모를 손으로 적어둬도 안전하다.
task는 지우지 않고 완료 처리만 한다.

빈 section이나 sub-project는 바로 안 지우고 처음 빈 날짜를 적어둔다. 그 상태로 7일
(`--grace`)을 넘겨야 지운다. 오늘 issue가 다 닫힌 repo를 내일 또 열 수도 있는데 polling
마다 지웠다 만들었다 하면 곤란해서다. 안에 뭐라도 하나 남아 있으면 우리가 만든 게
아니어도 안 지운다.

## 상태 저장

GitHub id와 Todoist id를 짝지어 checkout 루트의 `state.json`에 넣는다. gitignore 돼 있다.

이 파일을 잃으면 다음 실행이 기존 tree를 못 알아보고 옆에 똑같은 걸 하나 더 만든다.
머신 한 대에서만 돌린다고 가정한다.

## 제약

- webhook이 아니라 polling이라 최대 2분 늦는다.
- issue 본문이랑 comment는 안 가져온다. link만 건다.

## 개발

```bash
uv sync
uv run pytest        # 네트워크 안 탐
uv run ruff check .
uv run ruff format .
```

`reconcile.py`가 순수 함수라 test가 가볍다. 왜 이렇게 짰는지는 각 모듈 docstring에 적어뒀다.

## 문제 해결

```bash
uv run gh-todoist-sync status
uv run gh-todoist-sync sync --dry-run   # 뭘 하려는 건지
gh auth status                          # GitHub 인증
td auth status                          # Todoist 인증
tail -50 ~/Library/Logs/gh-todoist-sync.log
```

처음부터 다시 만들고 싶으면 Todoist의 "GitHub" project랑 `state.json`을 둘 다 지우고 한
번 돌린다. 하나만 지우면 안 된다. project만 지우면 없는 id를 가리키게 되고, `state.json`만
지우면 tree가 두 개가 된다.

## 라이선스

MIT
