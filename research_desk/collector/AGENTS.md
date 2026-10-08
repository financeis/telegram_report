# research_desk/collector/ — 텔레그램 채널의 PDF를 저장 폴더와 `reports` 행(`pending`)으로 모으는 수집기

## 맡는 일

- `collect` 명령. `cli.register(subparsers)`가 입구에 붙이고, `args.func`(`cli.collect`)가 종료 코드를 돌려준다. 옵션은 `--cutoff-days N`·`--backfill-days N`(같이 쓰면 argparse가 2로 끝냄), `--dry-run`, `-v/--verbose`.
- 텔레그램 접속(Telethon)과 로그인 세션 파일 `sessions/<TELEGRAM_SESSION_NAME>.session`(기본 이름 `samstudy`).
- PDF 파일: `STORAGE_BASE_DIR`(기본 `./reports`) 아래 `<메시지 번호>_<정리한 원래 이름>`.
- `reports` 표의 수집 몫: 새 행 쓰기, 채널 라벨별로 이미 받은 메시지 번호 전체와 가장 큰 번호 읽기.
- `failed_attempts` 표 전부(주인은 이 칸뿐): 실패 기록, 다음 실행의 재시도, 정리.
- 설정: 필수 `TELEGRAM_API_ID`·`TELEGRAM_API_HASH`·`TELEGRAM_CHANNEL`·`SUPABASE_URL`·`SUPABASE_SERVICE_KEY`, 선택 `TELEGRAM_CHANNEL_ID`·`TELEGRAM_SESSION_NAME`·`STORAGE_BASE_DIR`·`INITIAL_CUTOFF_DAYS`(30)·`MAX_CONCURRENT_DOWNLOADS`(4)·`LOG_LEVEL`(INFO).

## 맡지 않는 일

- **분류.** `reports`의 분류 칸(`tagging_*`, `tagged_at`, `tagger_version`, `taxonomy_version`, `report_type`, 종목·업종·제품 칸, `out_of_scope_reason` 등)은 쓰지 않는다. 새 행이 `pending`이고 `downloaded_at`이 찍히는 것은 DB 기본값이다. 분류는 `research_desk.tagger`, 수동 검토는 `research_desk.features.review`의 일이다.
- **import 경계**(구조 검사가 `R3 collector`·`R8 입구`로 보고): `research_desk.core`·`research_desk.domain`·`research_desk.collector`만 import한다. `research_desk.tagger`·`research_desk.features.*`·`research_desk.web`·`research_desk.cli`·`research_desk.__main__`은 import하지 않는다. 바깥에서 이 칸을 쓰는 곳은 입구(`research_desk/cli.py`)의 `register` 호출 하나다.
- **외부 도구**(`R9 외부 도구`): `supabase`·`asyncpg`·`openai`·`anthropic`·`dotenv`·`fitz`/`pymupdf`를 직접 import하지 않는다. Supabase 클라이언트는 `core.db.supabase_client`, `.env` 읽기는 `core.settings.load_env`로 받는다. `langgraph`는 분류기 것이고, `telethon`은 이 칸만 쓴다.
- **다른 표**(`R11 표 주인`): `report_summaries`(분석 기능 소유)는 다루지 않는다. `.table('<표>')` 호출과 docstring이 아닌 문자열 속 `FROM`/`UPDATE`/`INTO`/`JOIN <표>`를 검사가 잡는다.
- **옛 코드**(`R10 옛 코드`, 테스트 포함): `langgraph_tagger`와 옛 루트 모듈 `collector`·`config`·`storage`·`telegram_client`·`main`. `import collector`처럼 짧게 쓰면 옛 루트 모듈로 잡힌다 — 늘 `research_desk.collector…`로 쓴다.
- **PDF 내용.** 받은 바이트가 정말 PDF인지, 글자를 읽을 수 있는지는 보지 않는다. 읽기는 분류기와 웹이 `core.pdf`로 한다.
- **같은 내용 묶기.** 같은 PDF가 다른 메시지로 다시 올라오면 행이 따로 생긴다. SHA-256은 저장만 하고 묶지 않는다(집계가 부풀 수 있는 알려진 일).

## 늘 지켜야 할 것

**종료 코드와 문구**

- `0` 성공(받을 것이 없어도 0), `1` 전체 실패, `2` 일부 실패. `collect`는 `4`를 쓰지 않는다 — 설정 누락도 1이다(`tag`·`stocks`의 "준비 문제 4"와 다르다).
- 필수 설정이 없거나 빈 값이면 stderr에 정확히 `Config error: Missing required env var: <이름>` 한 줄을 쓰고, stdout은 비운 채, 로그 설정·텔레그램·DB 접속 전에 1로 끝난다. 확인 순서는 `TELEGRAM_API_ID` → `TELEGRAM_API_HASH` → `TELEGRAM_CHANNEL` → `SUPABASE_URL` → `SUPABASE_SERVICE_KEY`이고, 처음 빠진 하나만 알린다.
- 숫자 설정 형식 오류(`INITIAL_CUTOFF_DAYS=thirty`, 빈 `MAX_CONCURRENT_DOWNLOADS=`, 숫자가 아닌 `TELEGRAM_API_ID`·`TELEGRAM_CHANNEL_ID`)는 지금 `ValueError`가 그대로 올라가 트레이스백과 함께 1로 끝난다. 이것을 `Config error` 문구로 바꾸는 것은 동작 변경이다.
- 텔레그램 인증·네트워크 실패와 그 밖의 예외는 로그 `Fatal error`(트레이스백 포함) 뒤 1, Ctrl+C는 로그 `Interrupted by user` 뒤 1.
- 2는 이번 실행에서 메시지가 하나라도 `failed_attempts`에 남은 경우다(새 메시지 실패 또는 재시도 실패). 재시도 대상이 지워졌거나 PDF가 아니어서 정리한 것은 실패로 치지 않는다.

**중복 방지와 채널 라벨**

- 메시지 하나 = 행 하나, 키는 `(chat_username, message_id)`(DB UNIQUE, `failed_attempts`도 같은 키). `chat_username`에는 언제나 `TELEGRAM_CHANNEL` 값을 넣고, 텔레그램 조회에만 `Config.channel_ref()`(`TELEGRAM_CHANNEL_ID`가 있으면 그 숫자 ID)를 쓴다. 비공개 채널은 사용자 이름이 없어 숫자 ID로만 조회되고, 라벨은 옛 행과 같은 이름공간을 지키려고 그대로 둔다.
- 숫자 ID를 DB에 넣거나 라벨 값을 바꾸면 안 된다. DB에서는 새 채널로 보여 첫 실행처럼 `INITIAL_CUTOFF_DAYS` 전부터 다시 받고, 같은 메시지가 라벨만 다른 행으로 겹친다.

**행 쓰기**

- `Storage.insert_report_metadata`는 `upsert(on_conflict='chat_username,message_id')`이고, 보내는 칸은 `message_id`, `chat_username`, `sent_at`(ISO 문자열로 바꿈), `file_name`, `file_path`, `file_size_bytes`, `file_hash_sha256`, `caption` 여덟 개뿐이다. INSERT가 시간 초과로 실패한 것처럼 보였지만 실제로는 들어간 경우에도, 재시도가 같은 값으로 덮고 끝나게 하려는 upsert다.
- 충돌하면 보낸 칸만 덮이고 분류 칸과 `downloaded_at`은 그대로 남는다. 그래서 분류 칸(`tagging_status` 등)이나 `downloaded_at`을 이 dict에 넣으면 안 된다 — 다시 받은 메시지가 이미 분류된 행을 되돌리거나, 분류기가 행을 가져가는 순서(`downloaded_at` 오름차순)를 바꾼다.
- `file_name`은 텔레그램 원래 이름 그대로다(이름이 없으면 `unnamed.pdf`). 분류기가 이 이름·`caption`·`sent_at`을 LLM 입력으로 쓰므로 정리한 이름으로 바꾸지 않는다.
- `file_path`는 `f"{message_id}_{sanitize_filename(원래 이름)}"`, 저장 폴더 기준 파일 이름만이다. 절대 경로를 넣으면 안 된다: 분류기는 상대 경로를 `STORAGE_BASE_DIR` 아래에서 찾고, 웹은 `core.pdf.resolve_in_storage`로 저장 폴더 밖이거나 `.pdf`가 아닌 경로를 404로 막는다. 이름만 두어야 저장 폴더를 옮겨도 행이 그대로 맞는다.
- `sanitize_filename` 규칙(이 순서): Windows 금지 문자 `<>:"/\|?*`와 제어 문자 → `_`, 앞뒤 점·공백 제거, 비면 `unnamed`, `.pdf`로 끝나지 않으면 붙임, 100자를 넘으면 `.pdf`를 남기고 자름. 메시지 번호는 정리한 뒤에 앞에 붙인다(원래 이름이 같은 메시지끼리 겹치지 않게).

**파일 먼저, 행은 나중**

- `<이름>.partial`에 쓰고 `os.replace`로 바꾼다(같은 이름은 덮어쓰고, 지난번에 남은 `.partial`도 덮인다). 그다음 저장된 파일로 SHA-256(소문자 16진수)과 크기를 재고, 마지막에 행을 쓴다.
- 이 순서를 뒤집으면 안 된다. 행 없는 파일은 다음 재시도가 덮어쓰면 끝나지만, 파일 없는 행은 분류기에서 `first_page_unreadable`(검토 대기열), 웹에서 404가 된다.

**어디서부터 받나**

- 일반 실행: 이 라벨의 `reports ∪ failed_attempts` 중 가장 큰 `message_id` 다음부터 오래된 순으로 받는다. 따로 상태 파일을 두지 않고 DB가 기준이다. 가장 큰 번호가 0이면 첫 실행으로 보고 `INITIAL_CUTOFF_DAYS`(또는 `--cutoff-days`)일 전부터 받는다. 그래서 `--cutoff-days`는 첫 실행에만 효과가 있다.
- 일반 실행은 가장 큰 번호보다 작은 빈 번호를 다시 보지 않고, 전체 번호 목록(`get_all_message_ids`)도 읽지 않는다. 실행이 중간에 끊기면(Ctrl+C, 전체 실패) 동시에 받던 메시지 중 작은 번호는 빠지고 큰 번호만 들어갈 수 있다 — `--dry-run --backfill-days N`으로 확인하고 `--backfill-days N`으로 채운다.
- 되감기(`--backfill-days N`): N일 전부터 훑으며, A단계(재시도)가 **끝난 뒤** 읽은 `reports` 번호 ∪ 아직 남은 `failed_attempts` 번호를 건너뛴다. 재시도 전에 읽으면 같은 메시지를 한 실행에서 두 번 받는다.
- 번호 목록은 1000행씩 `.range()`로 끝까지 넘기며 읽는다. Supabase(PostgREST)는 한 요청에 1000행까지만 주므로, 페이지 넘김을 빼면 건너뛰기 목록이 잘려 이미 받은 PDF를 다시 받는다.

**실패 기록과 재시도**

- 일반·되감기 모두 A단계를 먼저 끝낸다: `failed_attempts`의 모든 번호를 다시 조회해, 메시지가 없거나 PDF가 아니면 실패 행을 지우고(정리), 받으면 지우고, 또 실패하면 `attempt_count`+1·`last_failed_at`·`error_message`를 고친다. B단계(새 메시지) 실패는 실패 행을 만든다(있으면 +1).
- 시도 횟수가 `ATTEMPT_WARN_THRESHOLD`(10) 이상이면 WARNING `msg_id=<번호> has failed <횟수> times — investigate manually`를 남긴다. 자동 차단은 없다. 고칠 수 없는 메시지면 사람이 그 실패 행을 지워 재시도를 멈추는데, 그 번호가 남은 행들(`reports` ∪ `failed_attempts`)의 가장 큰 번호보다 크면 다음 일반 실행의 "다음 번호부터" 범위에 다시 들어와 또 시도된다.
- 메시지 단위로 격리되는 것은 `_process_one_message` 안의 오류(받기·저장·SHA-256·행 쓰기)뿐이다. A단계의 메시지 조회(`get_message_by_id`), 메시지 목록 읽기, 실패 기록 쓰기 자체의 오류는 실행 전체를 1로 끝낸다.

**동시 다운로드와 속도 제한**

- 두 단계 모두 같은 세마포어(`MAX_CONCURRENT_DOWNLOADS`, 기본 4)로 동시 다운로드 수를 묶는다. A단계가 다 끝난 뒤에 B단계가 시작한다.
- Telethon `flood_sleep_threshold = 60`: 60초 미만 FloodWait은 기다리고, 그보다 길면 예외다(받는 중이면 그 메시지의 실패 기록, 목록을 읽는 중이면 전체 실패).

**미리 보기(`--dry-run`)**

- 파일·`reports`·`failed_attempts` 어느 것도 쓰지 않고, A단계도 하지 않는다. 그래도 텔레그램 로그인과 Supabase 읽기는 실제로 한다. 결과는 stderr 로그 줄이고, 되감기면 새로 받을 수와 이미 있어 건너뛸 수를 따로 센다.
- `cli._dry_run`은 `run()`의 받을 범위·건너뛰기 계산을 따로 갖고 있다(공유 함수 없음). 한쪽을 고치면 다른 쪽도 같이 고친다.

**가벼운 등록과 설정 읽기**

- 입구는 어떤 명령이든 모든 명령을 등록하므로 `collector.cli`의 최상위 import는 가볍게 둔다. `collector.cli` 자체는 `telethon`·`supabase`·`asyncpg`를 불러오지 않는다(테스트가 새 프로세스로 확인한다). Telethon은 `TelegramClient.__init__` 안에서, `core.db`는 `build_storage` 안에서 import한다. 지금은 분류기 쪽(`tagger.cli`)이 `core.db`를 통해 supabase·asyncpg를 이미 모든 명령에서 불러오지만, 텔레그램 라이브러리는 `collect`가 돌 때만 올라온다. 모듈 최상위로 올리면 테스트가 실패한다.
- 설정은 `collect`가 시작할 때 `load_config()`가 읽는다(`core.settings.load_env()`로 `.env`를 다시 읽되, 이미 있는 환경 변수가 이긴다). import 시점에는 읽지 않는다.
- `sessions/`와 `./reports` 같은 상대 경로는 현재 폴더 기준이다. 명령은 저장소 루트에서 실행한다.

## 이 칸의 방식

- **층.** `cli.collect`: 설정 → 로그 설정 → `asyncio.run(_amain)`. `_amain`만 진짜 `TelegramClient`(`async with`: `start()` → `disconnect()`)와 `build_storage(...)`를 만들어 `run.run(client, storage, config, backfill_days)` 또는 `_dry_run`에 넘긴다. `run.py`는 구체 클래스를 모르고 같은 이름의 메서드만 부른다 — 수집 규칙은 `run.py`에, 진짜 접속은 `_amain`에만 둔다.
- **설정.** `settings.load_config()` → 바꿀 수 없는 `Config`. 필수 값은 `core.settings.required`(빈 값도 누락 → `MissingSetting`), 숫자는 `core.settings.get_int`(빈 값도 `ValueError`), 선택 숫자 `TELEGRAM_CHANNEL_ID`는 `_optional_int`(비면 None). 이 칸의 준비 실패는 `MissingSetting` → `Config error` → 1이고, 웹 기능의 `core.settings.NotReady`나 종료 코드 4는 쓰지 않는다.
- **DB.** supabase-py 조회 사슬(`table().select().eq().order().range()`/`limit()`, `upsert`, `insert`, `update`, `delete`)은 `storage.Storage`에만 둔다. 서비스 키로 접속한다(RLS 우회). `upsert_failed_attempt`는 `attempt_count`를 +1 하려고 읽은 뒤 insert/update를 하는 두 번 왕복이다.
- **로그.** `setup_logging`이 stderr로 `basicConfig`를 건다(형식 `%(asctime)s %(levelname)-8s %(name)-10s %(message)s`). `-v`는 DEBUG, 모르는 `LOG_LEVEL`은 INFO. 설정 누락 문구는 로그 설정 전에 `print`로 나간다.
- **세션.** 세션 파일이 없거나 지워지면 `start()`가 터미널에서 전화번호와 인증 코드를 묻는다. 입력할 사람이 없는 실행(예약 작업 등)은 거기서 멈춘다. 세션 폴더는 `TelegramClient`가 만든다.
- **더할 때.** 수집 칸을 늘리면 `_process_one_message`의 dict, 새 `migrations/` 파일, 그리고 upsert가 충돌 때 그 칸을 덮는다는 점을 함께 본다. 새 설정은 `settings.py`와 테스트의 `COLLECTOR_ENV_VARS`에 같이 넣는다. 새 실행 방식은 `run()`과 `_dry_run()` 양쪽에 넣는다.

## 테스트

- **확인할 것.** 종료 코드(0/1/2, 설정 누락 문구와 확인 순서, 비어 있는 stdout), 첫 실행·일반·되감기·미리 보기가 부르는 조회 함수와 인자, 건너뛰기 목록(A단계 뒤에 읽고 실패 번호 포함), A단계의 정리·성공·실패, 경고 기준 10, 파일 이름·원래 이름·SHA-256·크기, 라벨과 숫자 ID 분리, 세마포어 상한, 1000행 페이지, upsert 키와 칸, 가벼운 등록.
- **밀폐.**
  - `research_desk/conftest.py`가 모든 테스트에서 `.env` 읽기를 끈다(`core.settings.ENV_FILE_ENABLED`). `.env` 동작은 `env_file` 픽스처(임시 `.env`)로만 시험한다.
  - 수집기 `tests/conftest.py`의 자동 픽스처가 `cli.TelegramClient`·`cli.build_storage`·`telethon.TelegramClient`를 부르는 순간 테스트를 실패시키게 바꿔 둔다. 명령 전체를 돌릴 때는 `test_cli.py`의 `wired`처럼 그 자리에 기록용 가짜를 끼운다.
  - `clean_env`는 `COLLECTOR_ENV_VARS`를 모두 지우고, `required_env`는 필수 다섯 개만 테스트 값으로 둔다. 앞 테스트가 남긴 환경 변수에 기대지 않도록 새 변수는 이 목록에 넣는다.
  - 파일은 `tmp_path`에만 쓴다. 실제 `sessions/`·`reports/`·Supabase·텔레그램에 닿지 않는다.
- **가짜.**
  - `fakes.py`: `make_msg`, `FakeTelegramClient`(두 조회 함수가 범위와 상관없이 `new_messages`를 그대로 내놓고 호출을 `calls`에 남김), `TrackingFakeClient`(동시 다운로드 최대치 기록), `FakeStorage`(호출만 기록하고 상태는 안 바뀜: 넣어도 `_existing_ids`·`_max_seen`이 그대로, 지워도 `_failed_ids`가 그대로). 그래서 범위와 건너뛰기는 `calls`와 `inserted`로 확인한다.
  - `test_storage_supabase.py`의 `FakeSupabase`는 조회 사슬을 메서드·인자·순서까지 기록한다. `Storage`의 사슬을 바꾸면 이 테스트가 깨지는 것이 의도다.
  - `run()`·`_dry_run()` 테스트의 설정은 `SimpleNamespace`에 `channel_ref`를 붙인 가짜(`cfg`, `_make_cfg`, `_make_dry_run_config`)다. 이 함수들이 새 설정 속성을 읽게 하면 세 곳을 같이 고친다.
- **함정.**
  - 비동기 테스트는 `pytest.ini`의 `asyncio_mode = auto`로 표시 없이 돈다.
  - 미리 보기 줄과 경고 문구는 `caplog`로 글자 그대로 비교한다.
  - 가벼운 등록 테스트는 저장소 루트에서 새 파이썬 프로세스를 띄워 `sys.modules`를 본다.
