# research_desk/features/companies/ — 웹앱의 기업 목록과 관심 기업

## 맡는 일

- `GET /api/workspace`: 종목표 전체와 관심 기업 목록.
- `PUT /api/favorites/{code}`(`{"enabled": bool}`): 관심 기업 켜기·끄기.
- 관심 기업 파일 `~/.review_viewer/favorites.json`(`{"stocks": [코드…]}`). 이 파일은 웹앱만 읽고 쓴다.
- 공개 창구는 `router` 하나다. 다른 기능에 내놓는 이름은 없다.
- 폴더 이름을 `companies`로 한 것은 공용 기준의 종목표(`domain/stocks.py`)와 헷갈리지 않게 하려는 것이다.

## 맡지 않는 일

- 종목표 파일 읽기·머리줄 확인·지문·버전은 `domain.stocks`가 한다. 여기서 CSV를 직접 읽지 않는다.
- DB를 쓰지 않는다. 어떤 표도 다루지 않고(`R11 표 주인`), `core.db`도 다른 기능도 부르지 않는다. DB 설정이 하나도 없어도 이 두 주소는 동작해야 하므로, DB가 필요한 기능(reports·review 등)에 의존하게 만들지 않는다.
- 기업별 리포트 목록(`/api/stocks/{code}/reports`)은 `features/reports`, 기업별 발행 추이(`/api/stocks/{code}/activity`)는 `features/coverage`가 맡는다.
- `collector`·`tagger`·`web`·`cli`와 다른 기능의 하위 모듈은 import하지 않는다(`R5 기능`). `supabase`·`dotenv`·`pymupdf` 같은 외부 도구도 import하지 않는다(`R9 외부 도구`).

## 늘 지켜야 할 것

- `GET /api/workspace` → `{"stocks": [{code, name, sector_major, sector_minor}…], "favorites": [...]}`. 종목표 전체를 파일 순서대로 내고, 빈 업종은 `""`다. `favorites`는 파일에 저장된 순서 그대로다.
- `PUT /api/favorites/{code}`
  - 받은 코드 앞에 0을 채워 6자리로 만든 값(`zfill(6)`)으로 종목표에서 찾는다. 없으면 404 `종목을 찾을 수 없습니다.`이고 파일을 건드리지 않는다. 켜기와 끄기 모두 이 확인을 먼저 한다. 7자리 이상은 자르지 않으므로 404다.
  - 저장하는 것은 받은 코드 그대로다. `16360`으로 켜면 `"16360"`이 저장되고, 끌 때도 같은 문자열로 찾는다.
  - 켜기는 중복 없이 맨 뒤에 붙인다. 끄기는 목록에 없으면 아무것도 하지 않는다. 응답 `{"favorites": [...]}`는 쓴 뒤 파일을 다시 읽은 목록이다.
  - 본문이 없거나 `enabled`를 bool로 읽을 수 없으면 422이고 파일은 그대로다.
  - 알려진 문제, 그대로 둔다: 끄기도 종목표 확인을 하므로 종목표에서 사라진 코드는 관심 기업에서 뺄 수 없다(404). 바꾸려면 동작 변경으로 따로 정하고, 이것을 고정한 테스트와 함께 바꾼다.
- 관심 기업 파일
  - 쓰기는 같은 폴더의 임시 파일에 쓴 뒤 `os.replace`로 바꾼다. 실패하면 옛 파일이 그대로 있고 임시 파일도 남지 않는다. 폴더가 없으면 만든다.
  - JSON이 깨졌거나 모양이 틀리면(객체가 아님, `stocks`가 목록이 아님) 파일을 `favorites.json.bak`으로 옮기고 빈 목록으로 본다. `{}`처럼 `stocks`가 없는 객체는 빈 목록으로 보되 옮기지 않는다.
  - Windows에서는 `favorites.json.bak`이 이미 있으면 두 번째 옮기기가 `FileExistsError`로 실패한다. 사람이 `.bak`을 치울 때까지 두 주소가 일반 503이 된다. 이 경우를 시험하는 테스트는 없다. 손대려면 동작 변경으로 따로 다룬다.
  - 켜기·끄기는 프로세스 안 잠금 하나로 한 번에 하나만 한다. 읽고-고치고-쓰는 사이에 다른 변경을 잃지 않게 하려는 것이다. 읽기(`GET`)는 잠그지 않는다. 파일을 통째로 바꾸므로 옛 내용이나 새 내용 중 하나를 본다.
- 종목표를 못 읽으면(파일 없음, 폴더임, UTF-8 아님, 빈 파일, 머리줄 다름) 두 주소 모두 `NotReady("기업 목록", "종목표 파일을 읽을 수 없습니다")` → 503 `기업 목록 기능을 지금 쓸 수 없습니다: 종목표 파일을 읽을 수 없습니다`. 응답에는 경로·원인을 넣지 않고, 원인은 경고 로그로만 남긴다. 이때 관심 기업 파일은 건드리지 않는다.
- 종목표 지문이 버전 정보와 다르거나 버전 정보 파일이 없거나 못 읽으면, 경고 로그 한 줄만 남기고 그대로 동작한다.
- DB·AI 설정(`SUPABASE_*`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`)이 하나도 없어도 두 주소가 동작한다.

## 이 칸의 방식

- `get_service()`가 프로세스에 하나인 `CompaniesService`를 만든다. 만들 때 아무것도 읽지 않는다. 라우터는 `Depends(get_service)`로 받는다.
- 종목표는 첫 요청에서 준비한다: `settings.load_env()` → `settings.krx_csv_path()` → `StockList.load()` → `verify()`. 성공하면 프로세스가 끝날 때까지 들고 있다. 그래서 종목표를 바꾸면 웹앱을 다시 켜야 목록이 바뀐다. 실패하면 다음 요청에서 다시 읽으므로, 파일을 채우거나 `.env`에 `KRX_CSV_PATH`를 더하면 재시작 없이 회복한다.
- `favorites.py`는 경로를 받아 파일 하나만 다루는 저장 부품이다. 설정도 종목표도 모른다. 종목표 확인과 잠금은 `service.py`가 한다.
- 관심 기업 파일 경로는 `CompaniesService(favorites_path=...)`로 받는다. 주지 않으면 `default_favorites_path()`(`Path.home()` 기준)다.
- 핸들러 이름(`bootstrap`, `favorite`)과 요청 본문 모델 `FavoriteBody`는 `/openapi.json` 항목을 그대로 두려고 정한 이름이다. 바꾸거나 docstring을 달지 않는다.

## 테스트

- 실행: 저장소 루트에서 `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider research_desk/features/companies`.
- `tests/conftest.py`의 `fake_home`(autouse)이 `USERPROFILE`과 `HOME`을 새 임시 폴더로 돌린다. Windows의 `Path.home()`은 `USERPROFILE`을 보므로 `HOME`만 바꾸면 실제 파일에 닿는다. 서비스는 `CompaniesService(favorites_path=<임시 경로>)`로 만들어 `app.dependency_overrides[get_service]`로 끼운다.
- 종목표는 임시 CSV와 `write_version()`으로 만든 버전 파일을 `KRX_CSV_PATH`로 준다. 첫 머리줄 칸에 따옴표 속 줄바꿈(`"종목\n코드"`)을 넣어 실제 파일 모양을 따른다.
- 꼭 지킬 검증: 0을 채운 존재 확인과 받은 그대로 저장, 사라진 코드는 끌 수 없음(알려진 문제 고정), 깨진 파일의 `.bak` 처리, 임시 파일 → 교체 쓰기와 실패 시 옛 파일 유지, 동시 변경은 한 번에 하나, 못 읽는 종목표 각 경우의 503 고정 문장, 다음 요청에서 다시 준비, `.env`에 더한 `KRX_CSV_PATH` 반영(`env_file`), 한 번 읽은 종목표 유지, 버전 문제는 경고만, DB·AI 설정 없이 동작, 실제 홈 파일 미접촉.
- 프로세스 전역 `_service`를 쓰는 테스트는 `monkeypatch.setattr(service_module, "_service", None)`으로 시작한다.
