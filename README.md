# gh-todoist-sync

GitHub에서 나에게 할당된 작업을 Todoist로 미러링한다.

GitHub이 진실의 원천, Todoist는 거울. 이슈가 닫히거나 어사인이 풀리면 Todoist 태스크도
완료된다. 반대 방향은 없다 — Todoist에서 태스크를 완료해도 GitHub 이슈는 그대로다.

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
이미 말해주므로 넣지 않는다.

## 설치

```bash
uv tool install .
gh-todoist-sync install        # launchd 등록, 120초마다 실행
```

`gh`와 `td`가 로그인돼 있으면 설정할 게 없다. 토큰을 직접 주고 싶으면
`GITHUB_TOKEN` / `TODOIST_API_TOKEN` 환경변수가 우선한다.

## 명령

```bash
gh-todoist-sync                # sync와 같음
gh-todoist-sync sync --dry-run # 실행 계획만 출력, 아무것도 안 씀
gh-todoist-sync sync --force   # 대량 완료 가드 해제
gh-todoist-sync install        # launchd 등록 (--interval 로 주기 조절)
gh-todoist-sync uninstall      # 해제
gh-todoist-sync status         # 동작 여부, 마지막 종료 코드
```

로그는 `~/Library/Logs/gh-todoist-sync.log`.

## 무엇을 가져오나

세 소스를 합집합으로 모은다 (GitHub node id 기준 dedupe).

| 소스 | 엔드포인트 |
|---|---|
| 나에게 할당된 open 이슈·PR | `GET /issues?filter=assigned&state=open` |
| 리뷰 요청받은 PR | `GET /search/issues?q=is:pr is:open review-requested:@me` |
| 내가 연 open PR | `GET /search/issues?q=is:pr is:open author:@me` |

첫 번째에 search가 아니라 REST를 쓰는 이유는 두 가지다. search 인덱스 지연이 없고,
응답에 `repository.id` / `repository.owner.id` / `repository.owner.type`이 들어 있어
id 기반 매칭에 필요한 값이 한 번에 온다. search API는 이 id들을 안 주므로, 거기서 새로
발견한 레포만 `GET /repos/{owner}/{repo}`로 한 번 더 조회한다 (실행 내 캐시).

| 필드 | 규칙 |
|---|---|
| 우선순위 | PR = p2, 이슈 = p4 |
| 마감일 | GitHub milestone의 `due_on`. 없으면 비움 |

## 매핑을 어디에 저장하나

**로컬 상태 파일이 없다.** GitHub의 숫자 id를 Todoist description 첫 줄에 심는다.

| Todoist 객체 | description 마커 |
|---|---|
| 부모 프로젝트 | `gh-root: 1` |
| org 서브프로젝트 | `gh-org-id: 54899579` |
| 레포 섹션 | `gh-repo-id: 954621090` |
| 태스크 | `gh-id: I_kwDOSpcotc8AAAABPpYuuQ` + 둘째 줄에 이슈 URL |

이렇게 한 이유:

- **이름이 바뀌어도 안 깨진다.** GitHub에서 레포나 org 이름을 바꾸면 id로 찾아서 Todoist
  쪽 이름을 GitHub에 맞춰 고친다. 반대로 Todoist에서 섹션 이름을 손으로 바꿔놔도 다음
  실행에 되돌아온다.
- **머신을 갈아엎어도 그대로 동작한다.** 복구할 상태 파일이 없으니 드리프트라는 개념
  자체가 없다.
- **부모 프로젝트 이름도 하드코딩이 아니다.** `gh-root: 1` 마커로 찾으므로 Todoist에서
  "GitHub"을 다른 이름으로 바꿔도 계속 붙는다.

## 구조

| 모듈 | 역할 |
|---|---|
| `models.py` | Item·Snapshot 등 공용 타입. SDK를 import하지 않는다 |
| `reconcile.py` | 순수 함수. (목표, 현재) → op 리스트. 네트워크를 안 탄다 |
| `rest.py` | 양쪽 API가 공유하는 얇은 JSON 클라이언트 |
| `gh.py` | GitHub → `list[Item]` |
| `todoist.py` | Snapshot 조회 + op 실행 |
| `agent.py` | launchd plist 생성·등록 (`plistlib`) |
| `cli.py` | typer 명령 |

SDK를 안 쓴다. `todoist-api-python`의 `Section` 모델에는 `description` 필드가 아예
없어서 (읽기도 쓰기도) 마커를 심을 수 없고, 그렇다고 섹션만 원시 REST로 처리하면 한
서비스에 클라이언트가 둘 생긴다. 양쪽 API가 base URL·인증 헤더·페이지네이션 방식만
다르므로 `rest.Client` 하나로 통일했다.

reconcile이 내는 op는 전부 frozen dataclass다. 프로젝트를 만들기 전에는 Todoist id가
존재하지 않으므로 op 안의 참조는 `NewOrg(owner_id)` 같은 심볼릭 형태로 남고,
`todoist.Applier`가 실행하면서 실제 id로 해소한다.

## 안전장치

- **GitHub 호출이 하나라도 실패하면 아무것도 쓰지 않는다.** GitHub을 먼저, 따로 읽는
  이유가 이것이다. 빈 응답을 목표 상태로 착각해 전부 완료 처리하는 사고를 막는다.
- **완료 상한 20개.** 한 실행에서 완료 대상이 20개를 넘으면 중단한다. org 권한이 갑자기
  빠지거나 페이지가 잘려 온 상황이지, 내가 정말 그만큼 끝냈을 리는 없다. 의도한
  대량 완료라면 `--force`.
- **`gh-id:` 마커가 없는 항목은 절대 건드리지 않는다.** GitHub 프로젝트 안에 손으로
  메모를 추가해도 안전하다.
- 삭제는 하지 않는다. GitHub에서 사라진 작업은 완료 처리만 한다.

## 알려진 한계

- **레포가 org 사이를 이동하면** 옛 프로젝트의 섹션이 빈 채로 남는다. 태스크는 새 섹션으로
  따라가지만 껍데기는 손으로 지워야 한다. 섹션을 자동으로 지우면 그 안 태스크까지 날아갈
  위험이 있어 일부러 안 한다.
- **웹훅이 아니라 폴링이다.** 최대 2분 지연. 진짜 웹훅을 하려면 24/7 공개 HTTPS
  엔드포인트와 org admin 권한이 필요한데, 소속 org 중 일부는 member 권한뿐이라 어차피
  구멍이 남는다.
- 이슈 본문과 코멘트는 동기화하지 않는다. 링크만 건다.

## 개발

```bash
uv sync
uv run pytest        # reconcile 순수 로직, 네트워크 안 탐
uv run ruff check .
uv run ruff format .
```

테스트가 검증하는 것: 멱등성, 신규 이슈, 레포 rename이 섹션만 바꾸는지, 제목 변경,
org 서브프로젝트 생성, org rename, 완료 처리, 대량 완료 가드, 마커 없는 항목 무시,
엉뚱한 섹션에 있는 태스크 이동.

## 문제가 생기면

```bash
gh-todoist-sync status
gh-todoist-sync sync --dry-run       # 뭘 하려는 건지
gh auth status                       # GitHub 인증
td auth status                       # Todoist 인증
tail -50 ~/Library/Logs/gh-todoist-sync.log
```

Todoist 구조를 통째로 다시 만들고 싶으면 Todoist에서 "GitHub" 프로젝트를 지우고 한 번
실행하면 된다. 상태 파일이 없으므로 처음부터 다시 만든다.

## 라이선스

MIT
