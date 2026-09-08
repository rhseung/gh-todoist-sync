# gh-todoist-sync

GitHub에서 나에게 할당된 작업을 Todoist로 미러링한다.

GitHub이 진실의 원천, Todoist는 거울. 이슈가 닫히거나 어사인이 풀리면 Todoist 태스크도
완료된다. 반대 방향은 없다 — Todoist에서 완료해도 GitHub 이슈는 그대로다.

```
GitHub                          <- 부모 프로젝트
├─ (개인 레포 섹션들)             <- owner가 User인 레포
├─ gsainfoteam                  <- owner가 Organization이면 서브프로젝트
│  ├─ account-fe                <- 섹션 = 레포
│  └─ ziggle-fe
└─ studio-void
   └─ campass-fe
```

태스크 이름은 `[#61](https://github.com/.../issues/61) 비밀번호 찾기 페이지`.
Todoist가 마크다운을 렌더링하므로 `#61`이 이슈로 가는 링크가 된다. 레포 이름은 섹션이
이미 말해주니 넣지 않는다.

## 설치

```bash
uv sync
uv run gh-todoist-sync install   # launchd 등록, 120초마다 실행
```

`gh`와 `td`가 로그인돼 있으면 설정할 게 없다. `GITHUB_TOKEN` / `TODOIST_API_TOKEN`
환경변수를 주면 그쪽이 우선한다.

체크아웃 안에서만 쓴다. 상태 파일이 체크아웃 옆에 살기 때문에 `uv tool install`로 한
벌 더 깔면 상태 파일이 둘로 갈리고 Todoist에 트리가 두 개 생긴다.

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

launchd는 체크아웃의 `.venv/bin/gh-todoist-sync`를 직접 실행하므로 `uv run`이 필요 없다.
로그는 `~/Library/Logs/gh-todoist-sync.log`.

## 무엇을 가져오나

세 소스의 합집합 (GitHub node id로 dedupe). 아카이브된 레포는 건너뛴다 — 손댈 수 없는
작업이라 띄워야 소음이다.

| 소스 | 엔드포인트 |
| --- | --- |
| 나에게 할당된 open 이슈·PR | `GET /issues?filter=assigned&state=open` |
| 리뷰 요청받은 PR | `GET /search/issues?q=is:pr is:open review-requested:@me` |
| 내가 연 open PR | `GET /search/issues?q=is:pr is:open author:@me` |

첫 번째에 search 대신 REST를 쓰는 이유는 두 가지다. search 인덱스 지연이 없고, 응답에
`repository.id` / `repository.owner.id` / `repository.owner.type`이 들어 있어 id 기반
매칭에 필요한 값이 한 번에 온다. search API는 이 id들을 안 주므로 거기서 새로 발견한
레포만 `GET /repos/{owner}/{repo}`로 한 번 더 조회한다 (실행 내 캐시).

| 필드 | 규칙 |
| --- | --- |
| 우선순위 | PR = p2, 이슈 = p4 |
| 라벨 | PR = `gh-pr`(보라), 이슈 = `gh-issue`(초록). 손으로 붙인 라벨은 남긴다 |
| 마감일 | GitHub milestone의 `due_on`. 없으면 비움 |

## 매핑을 어디에 저장하나

체크아웃 루트의 `state.json` 하나. gitignore 대상이다.

```json
{
  "root": "6hRV382RrRFWWGm6",
  "orgs":     { "54899579": "6hRV38rc6xr6JW7H" },
  "sections": { "954621090": "6hRV3C3FR86wWWqq" },
  "tasks":    { "I_kwDOSpcotc8AAAABPpYuuQ": "6hRV3Mh4X85Xj5Fq" },
  "empty_since": { "6hRV3Frffr8qqPgf": "2026-09-08" }
}
```

id로 찾으므로 이름이 바뀌어도 안 깨진다. GitHub에서 레포나 org 이름을 바꾸면 Todoist
쪽 이름을 맞춰 고치고, Todoist에서 섹션 이름을 손으로 바꿔놔도 되돌린다. Todoist에서
뭔가를 손으로 지우면 상태 파일에서도 지워지고 다음 실행에 다시 만든다.

Todoist description에는 사람이 볼 것만 남긴다.

| Todoist 객체 | description |
| --- | --- |
| 부모 프로젝트 | 없음 |
| org 서브프로젝트 | `[gsainfoteam](https://github.com/gsainfoteam)` |
| 레포 섹션 | `[account-fe](https://github.com/gsainfoteam/account-fe)` |
| 태스크 | 없음 (이슈 링크는 이름에 이미 있다) |

맨 URL이 아니라 마크다운 링크로 쓴다. 맨 URL을 넣으면 Todoist가 제목을 붙인 링크로
바꿔버려서 매 폴링마다 달라 보이고 description을 영원히 다시 쓴다.

전에는 이 매핑을 description에 `gh-id: I_kwDO...` 같은 마커로 심었다. 상태 파일이 필요
없다는 장점이 있었지만 태스크마다 그 줄이 눈에 걸렸다. 지금 방식의 대가는 두 가지다.

- **상태 파일을 잃으면** 다음 실행이 기존 트리를 못 알아보고 옆에 하나 더 만든다. 옛
  태스크는 시야 밖이라 완료 처리도 안 되고 그냥 남는다. 손으로 지워야 한다.
- **머신 한 대 전제다.** 두 곳에서 돌리면 각자 상태 파일을 들고 서로 다른 트리를 만든다.

## 안전장치

- **GitHub 호출이 하나라도 실패하면 아무것도 쓰지 않는다.** GitHub을 먼저, 따로 읽는
  이유가 이것이다. 빈 응답을 목표 상태로 착각해 전부 완료 처리하는 사고를 막는다.
- **완료 상한 20개.** 한 실행에서 완료 대상이 20개를 넘으면 중단한다. org 권한이 갑자기
  빠지거나 페이지가 잘려 온 상황이지 내가 그만큼 끝냈을 리는 없다. 의도한 대량 완료라면
  `--force`.
- **상태 파일이 모르는 항목은 건드리지 않는다.** GitHub 프로젝트 안에 손으로 메모를
  추가해도 안전하다.
- **태스크는 지우지 않는다.** GitHub에서 사라진 작업은 완료 처리만 한다.
- **빈 섹션·서브프로젝트는 유예 기간을 두고 지운다.** 처음 빈 날짜를 상태 파일에 적어
  두고, 그대로 7일(`--grace`)을 넘긴 것만 지운다. 오늘 이슈가 다 닫힌 레포를 매 폴링마다
  지웠다 만들었다 하지 않기 위해서다. 안에 뭐가 하나라도 남아 있으면 — 우리가 만든 게
  아니어도 — 지우지 않는다. 컨테이너를 지우면 내용물이 같이 날아간다.

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

SDK를 안 쓴다. `todoist-api-python`의 `Section` 모델에는 `description` 필드가 아예 없어서
섹션에 레포 링크를 달 수 없고, 그렇다고 섹션만 원시 REST로 처리하면 한 서비스에
클라이언트가 둘 생긴다. 양쪽 API가 base URL·인증 헤더·페이지네이션 방식만 다르므로
`rest.Client` 하나로 통일했다.

reconcile이 내는 op는 전부 frozen dataclass다. 프로젝트를 만들기 전에는 Todoist id가
존재하지 않으므로 op 안의 참조는 `NewOrg(owner_id)` 같은 심볼릭 형태로 남고,
`todoist.Applier`가 실행하면서 실제 id로 해소한다. 실행 도중 죽어도 그때까지 만든 id는
저장된다 — 안 그러면 다음 실행이 같은 태스크를 또 만든다.

## 알려진 한계

- **상태 파일이 단일 실패 지점이다.** 위 "매핑을 어디에 저장하나" 참고.
- **웹훅이 아니라 폴링이다.** 최대 2분 지연. 진짜 웹훅을 하려면 24/7 공개 HTTPS
  엔드포인트와 org admin 권한이 필요한데, 소속 org 중 일부는 member 권한뿐이라 어차피
  구멍이 남는다.
- 이슈 본문과 코멘트는 동기화하지 않는다. 링크만 건다.

## 개발

```bash
uv sync
uv run pytest        # 네트워크 안 탐
uv run ruff check .
uv run ruff format .
```

`reconcile.py`가 순수하다는 게 테스트가 싼 이유다. 멱등성, 신규 이슈, 레포·org rename,
제목 변경, 완료 처리, 대량 완료 가드, 태스크 이동, 유예 기간, 라벨 색과 교체, 상태 파일
왕복을 검증한다.

## 문제가 생기면

```bash
uv run gh-todoist-sync status
uv run gh-todoist-sync sync --dry-run   # 뭘 하려는 건지
gh auth status                          # GitHub 인증
td auth status                          # Todoist 인증
tail -50 ~/Library/Logs/gh-todoist-sync.log
```

Todoist 구조를 통째로 다시 만들려면 Todoist에서 "GitHub" 프로젝트를 지우고 `state.json`도
지운 뒤 한 번 실행한다. 둘 중 하나만 지우면 안 된다 — 프로젝트만 지우면 상태 파일이 없는
id를 가리키고, 상태 파일만 지우면 트리가 복제된다.

## 라이선스

MIT
