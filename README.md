# gh-todoist-sync

GitHub에서 나한테 할당된 일감을 Todoist로 미러링한다.

동기화는 GitHub에서 Todoist 한 방향으로만 간다. 이슈가 닫히거나 어사인이 풀리면 Todoist
태스크도 완료된다. 반대는 안 된다. Todoist에서 태스크를 완료해도 GitHub 이슈는 그대로다.

```
GitHub                          <- 부모 프로젝트
├─ (개인 레포 섹션들)             <- owner가 User인 레포
├─ gsainfoteam                  <- owner가 Organization이면 서브프로젝트
│  ├─ account-fe                <- 섹션 = 레포
│  └─ ziggle-fe
└─ studio-void
   └─ campass-fe
```

태스크 이름은 `[#61](https://github.com/.../issues/61) 비밀번호 찾기 페이지` 꼴이다.
Todoist가 마크다운을 렌더링해서 `#61`을 누르면 이슈로 간다. 레포 이름은 섹션에 이미
있으니 빼놨다.

## 설치

```bash
uv sync
uv run gh-todoist-sync install   # launchd 등록, 120초마다 실행
```

`gh`와 `td`가 로그인돼 있으면 따로 설정할 게 없다. `GITHUB_TOKEN` / `TODOIST_API_TOKEN`
환경변수를 주면 그쪽을 먼저 쓴다.

설치는 체크아웃 한 곳만 유지한다. 상태 파일을 체크아웃 안에 두기 때문에, `uv tool
install`로 한 벌 더 깔면 상태 파일이 둘이 되고 Todoist에도 트리가 두 개 생긴다.

## 명령

```bash
uv run gh-todoist-sync                  # sync와 같음
uv run gh-todoist-sync sync --dry-run   # 실행 계획만 출력, 아무것도 안 씀
uv run gh-todoist-sync sync --force     # 대량 완료 가드 해제
uv run gh-todoist-sync sync --grace 14  # 빈 섹션 유예 기간 (기본 7일)
uv run gh-todoist-sync install          # launchd 등록 (--interval 로 주기 조절)
uv run gh-todoist-sync uninstall        # 해제
uv run gh-todoist-sync status           # 동작 여부, 마지막 종료 코드
```

launchd는 `.venv/bin/gh-todoist-sync`를 직접 부르니까 `uv run`이 붙지 않는다.
로그는 `~/Library/Logs/gh-todoist-sync.log`.

## 무엇을 가져오나

아래 세 곳에서 모아서 GitHub node id로 중복을 걸러낸다. 아카이브된 레포는 뺀다. 어차피
손댈 수 없는 작업이라 목록에 있어봐야 방해만 된다.

| 소스 | 엔드포인트 |
| --- | --- |
| 나에게 할당된 open 이슈·PR | `GET /issues?filter=assigned&state=open` |
| 리뷰 요청받은 PR | `GET /search/issues?q=is:pr is:open review-requested:@me` |
| 내가 연 open PR | `GET /search/issues?q=is:pr is:open author:@me` |

첫 줄에 search를 안 쓴 건 인덱스 지연 때문이기도 하고, REST 응답에 `repository.id` /
`repository.owner.id` / `repository.owner.type`이 같이 딸려 오기 때문이기도 하다. 전부
id로 매칭하는 구조라 이 값들이 꼭 필요한데 search는 안 준다. 그래서 search에서 새로 나온
레포만 `GET /repos/{owner}/{repo}`를 한 번 더 때린다. 실행하는 동안은 캐시한다.

| 필드 | 규칙 |
| --- | --- |
| 우선순위 | PR = p2, 이슈 = p4 |
| 라벨 | PR = `gh-pr`(보라), 이슈 = `gh-issue`(초록). 손으로 붙인 라벨은 남긴다 |
| 마감일 | GitHub milestone의 `due_on`. 없으면 비움 |

## 매핑을 어디에 저장하나

체크아웃 루트의 `state.json` 하나에 다 넣는다. gitignore 돼 있다.

```json
{
  "root": "6hRV382RrRFWWGm6",
  "orgs":     { "54899579": "6hRV38rc6xr6JW7H" },
  "sections": { "954621090": "6hRV3C3FR86wWWqq" },
  "tasks":    { "I_kwDOSpcotc8AAAABPpYuuQ": "6hRV3Mh4X85Xj5Fq" },
  "empty_since": { "6hRV3Frffr8qqPgf": "2026-09-08" }
}
```

전부 id로 찾으니까 이름이 바뀌어도 안 깨진다. GitHub에서 레포나 org 이름을 바꾸면
Todoist 쪽 이름을 따라 고치고, Todoist에서 섹션 이름을 손으로 바꿔놔도 다음 실행에
되돌린다. Todoist에서 뭘 손으로 지우면 상태 파일에서도 빠지고 새로 만든다.

description에는 사람이 읽을 것만 둔다.

| Todoist 객체 | description |
| --- | --- |
| 부모 프로젝트 | 없음 |
| org 서브프로젝트 | `[gsainfoteam](https://github.com/gsainfoteam)` |
| 레포 섹션 | `[account-fe](https://github.com/gsainfoteam/account-fe)` |
| 태스크 | 없음 (이슈 링크는 이름에 이미 있다) |

링크는 꼭 마크다운 형식으로 쓴다. 맨 URL을 넣으면 Todoist가 페이지 제목을 붙인 링크로
바꿔버린다. 그러면 우리가 쓴 값이랑 저장된 값이 매번 달라서, 폴링할 때마다 description을
계속 다시 쓰게 된다.

예전에는 이 매핑을 description에 `gh-id: I_kwDO...` 같은 마커로 박아뒀다. 상태 파일이
아예 필요 없다는 게 좋았는데 태스크마다 그 줄이 보이는 게 거슬려서 옮겼다. 대신 이런 걸
감수한다.

- 상태 파일을 잃으면 다음 실행이 기존 트리를 못 알아본다. 옆에 똑같은 걸 하나 더 만들고,
  원래 있던 태스크는 인식이 안 되니 완료 처리도 못 하고 그대로 남는다. 손으로 치워야 한다.
- 머신 한 대에서만 돌린다고 가정한다. 두 곳에서 돌리면 각자 상태 파일을 들고 서로 다른
  트리를 만든다.

## 안전장치

GitHub 호출이 하나라도 실패하면 Todoist에 아무것도 안 쓴다. GitHub을 먼저 따로 읽는 게
이것 때문이다. 빈 응답을 목표 상태로 착각해서 전부 완료 처리해버리면 큰일이다.

한 번에 20개 넘게 완료하려고 하면 멈춘다. 그 정도면 org 권한이 빠졌거나 페이지가 잘려
온 거지 내가 정말 스무 개를 끝냈을 리 없다. 진짜 그럴 일이면 `--force`.

상태 파일에 없는 건 건드리지 않는다. GitHub 프로젝트 안에 메모를 손으로 적어둬도 안전하다.
태스크는 지우지 않고 완료 처리만 한다.

빈 섹션이나 서브프로젝트는 바로 안 지우고 처음 빈 날짜를 적어둔다. 그 상태로 7일
(`--grace`)을 넘겨야 지운다. 오늘 이슈가 다 닫힌 레포를 내일 또 열 수도 있는데 폴링마다
지웠다 만들었다 하면 곤란해서다. 안에 뭐라도 하나 남아 있으면 우리가 만든 게 아니어도
안 지운다. 섹션을 지우면 안에 있던 것도 같이 날아간다.

## 구조

| 모듈 | 역할 |
| --- | --- |
| `models.py` | Item·Snapshot 등 공용 타입. SDK를 import하지 않는다 |
| `reconcile.py` | 순수 함수. (목표, 현재) → op 리스트. 네트워크를 안 탄다 |
| `state.py` | `state.json` 읽기·쓰기. GitHub id ↔ Todoist id |
| `rest.py` | 양쪽 API가 공유하는 얇은 JSON 클라이언트 |
| `gh.py` | GitHub → `list[Item]` |
| `todoist.py` | Snapshot 조회 + op 실행 |
| `agent.py` | launchd plist 생성·등록 (`plistlib`) |
| `cli.py` | typer 명령 |

SDK는 안 쓴다. `todoist-api-python`의 `Section` 모델에 `description` 필드가 없어서 섹션에
레포 링크를 달 방법이 없었다. 섹션만 REST로 따로 처리하자니 한 서비스에 클라이언트가 두
개 생긴다. 어차피 GitHub과 Todoist는 base URL, 인증 헤더, 페이지네이션 방식만 다르길래
`rest.Client` 하나로 합쳤다.

reconcile이 내놓는 op는 전부 frozen dataclass다. 프로젝트를 만들기 전에는 Todoist id가
없으니 op 안의 참조는 `NewOrg(owner_id)` 같은 기호로 남겨두고, `Applier`가 실행하면서
진짜 id로 바꾼다. 중간에 죽어도 그때까지 만든 id는 저장한다. 안 그러면 다음 실행이 같은
태스크를 또 만든다.

## 알려진 한계

- 상태 파일 하나에 전부 걸려 있다. 위 "매핑을 어디에 저장하나" 참고.
- 웹훅이 아니라 폴링이라 최대 2분 늦는다. 웹훅을 제대로 하려면 24시간 떠 있는 공개 HTTPS
  엔드포인트에 org admin 권한까지 있어야 하는데, member 권한만 가진 org가 섞여 있어서
  어차피 다 못 덮는다.
- 이슈 본문이랑 코멘트는 안 가져온다. 링크만 건다.

## 개발

```bash
uv sync
uv run pytest        # 네트워크 안 탐
uv run ruff check .
uv run ruff format .
```

`reconcile.py`가 순수 함수라 테스트가 가볍다. 멱등성, 신규 이슈, 레포·org rename, 제목
변경, 완료 처리, 대량 완료 가드, 태스크 이동, 유예 기간, 라벨 색과 교체, 상태 파일 왕복을
본다.

## 문제가 생기면

```bash
uv run gh-todoist-sync status
uv run gh-todoist-sync sync --dry-run   # 뭘 하려는 건지
gh auth status                          # GitHub 인증
td auth status                          # Todoist 인증
tail -50 ~/Library/Logs/gh-todoist-sync.log
```

처음부터 다시 만들고 싶으면 Todoist의 "GitHub" 프로젝트랑 `state.json`을 둘 다 지우고 한
번 돌린다. 하나만 지우면 안 된다. 프로젝트만 지우면 상태 파일이 없는 id를 가리키게 되고,
상태 파일만 지우면 트리가 두 개가 된다.

## 라이선스

MIT
