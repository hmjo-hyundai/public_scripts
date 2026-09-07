# public_scripts

Claude Code와 Codex에서 셔클라봇(ShuClientBot)을 사용하는 공개 설치 스크립트입니다.
현재 버전은 **v5**입니다.

설치 실행 (shucle-rider-ios 폴더에서 실행 !!)
```bash
curl -fsSL https://raw.githubusercontent.com/hmjo-hyundai/public_scripts/main/setup-skills-and-mcp.sh | bash
```

## 이 저장소의 역할

이 저장소는 비공개 AgentRAG 저장소에 접근할 수 없는 사용자를 위한 **공개 배포 창구**입니다.
설치 스크립트, 사용 안내, 회귀 테스트만 관리합니다. 스크립트를 받거나 실행할 때 GitHub
로그인이나 비공개 저장소 권한은 필요하지 않습니다. RAG 서버에 접속할 수 있는 네트워크와
서버 측 접근 권한은 별도로 필요합니다.

설치 스크립트의 기준본은 이 저장소의 `setup-skills-and-mcp.sh`입니다. 비공개 저장소는
서버 구현과 데이터소스·내부 문서를 관리합니다. 비공개 저장소의 같은 이름 파일을 이 파일에
덮어쓰는 방식으로 동기화하지 않습니다. 공개본의 서브에이전트 전용 연결과 제한시간 설정이
사라질 수 있기 때문입니다. 서버의 도구 계약이 바뀌면 필요한 설치 지침만 여기에서 수정하고
회귀 테스트를 함께 갱신합니다.

## 실행

설정할 프로젝트의 루트에서 실행합니다. 스크립트 파일 하나만 복사해도 사용할 수 있습니다.
`bash`, `curl`, **uv**가 필요합니다. Claude Code 설정은 `claude` CLI가 있을 때 설치합니다.

```bash
cd /path/to/your-project
bash /path/to/public_scripts/setup-skills-and-mcp.sh

# 같은 머신의 MCP 서버에 연결
bash /path/to/public_scripts/setup-skills-and-mcp.sh --local

# MCP URL 직접 지정
bash /path/to/public_scripts/setup-skills-and-mcp.sh --url https://example.com/mcp
```

현재 사용 중인 Mac Studio의 LAN 기본주소(`REMOTE_URL`)를 유지합니다. 해당 서버에 접근할 수
있는 LAN에서 인자 없이 실행하면 됩니다. 다른 접속 주소는 `--url` 또는
`SHUCLIENTBOT_MCP_URL` 환경 변수로 지정하세요. 명령행 옵션이 환경 변수보다 우선합니다.
`--local`은 `http://127.0.0.1:8848/mcp`를 사용합니다.
`--help`로 사용법과 버전을 확인할 수 있습니다. Tailscale 전용 주소·옵션은 포함하지 않습니다.

저장소를 clone하지 않고 인증 없이 내려받을 수도 있습니다. 아래 URL은 `main`의 최신 파일을
가리킵니다. 다운로드한 파일을 검토한 뒤 실행하세요.

```bash
cd /path/to/your-project
shuclientbot_installer="$(mktemp)"
curl -fsSL https://raw.githubusercontent.com/hmjo-hyundai/public_scripts/main/setup-skills-and-mcp.sh \
  -o "$shuclientbot_installer"
bash "$shuclientbot_installer" --help
bash "$shuclientbot_installer" --url https://example.com/mcp
rm -f "$shuclientbot_installer"
```

파일 내용을 확인한 뒤 한 줄로 설치하려면 다음 명령도 사용할 수 있습니다.

```bash
# LAN 기본주소
curl -fsSL https://raw.githubusercontent.com/hmjo-hyundai/public_scripts/main/setup-skills-and-mcp.sh | bash

# 같은 머신의 MCP 서버
curl -fsSL https://raw.githubusercontent.com/hmjo-hyundai/public_scripts/main/setup-skills-and-mcp.sh | bash -s -- --local
```

설치 과정에서 `uv run --no-project --with tomlkit==0.13.3 python`으로 TOML 편집 환경을
준비합니다. 최초 실행에는 Python 또는 라이브러리 다운로드가 필요할 수 있습니다.
대상 프로젝트의 Python 의존성이나 가상환경은 변경하지 않습니다.

## 설치 동작

MCP 연결은 `shuclientbot` 서브에이전트 설정에 넣습니다. 메인 세션은 질문을 위임하고
답과 출처를 받습니다. PR 조회 도구인 `lookup_pull_requests`와 저장소를 지정하는
`repository` 지침도 포함합니다.

| 대상 | 생성·갱신 내용 |
|---|---|
| Claude Code | `.claude/agents/shuclientbot.md`에 전용 `mcpServers`, `.claude/skills/shuclientbot/SKILL.md`에 호출 지침 |
| Claude 권한 | `.claude/settings.local.json`의 기존 설정을 유지하며 `mcp__ShucleRiderRAG__*` 허용 추가 |
| Codex | `.codex/agents/shuclientbot.toml`에 전용 MCP와 에이전트 지침 |
| 공통 지침 | `AGENTS.md`의 `SHUCLIENTBOT_START`/`SHUCLIENTBOT_END` 관리 블록 갱신 |

기존 메인 MCP 등록은 다음 범위에서 정리합니다.

- Claude: 현재 프로젝트의 **local** scope에 있는 `agentrag`·`ShucleRiderRAG`만 삭제합니다.
  `user`·`project` scope 등록은 보존합니다. 해당 범위에도 등록되어 있다면 메인 세션에
  MCP가 계속 표시될 수 있습니다. 등록이 없는 경우는 정상 처리하고, 다른 삭제 오류는 중단합니다.
- Codex: 사용자 설정(`$CODEX_HOME/config.toml`, 기본 `~/.codex/config.toml`)과
  `.codex/config.toml`에서 두 이름의 MCP 등록만 제거합니다. 실제로 변경하는 파일마다
  `.bak.<시각>.<프로세스 ID>` 백업을 만들고, 다른 설정·주석·파일 권한은 보존합니다.
  없는 메인 설정 파일은 새로 만들지 않습니다.

사전 HTTP 도달 확인과 Codex MCP 초기 연결 제한시간은 3초입니다. 도달 확인이 실패해도
설정 설치는 계속 진행하므로, 완료 메시지만으로 서버 연결 성공을 판단하지 마세요.

기존 TOML·Claude JSON·AGENTS 관리 마커는 변경 전에 검증합니다. 문법 오류 또는 짝이 맞지
않는 마커가 있으면 원본을 보존한 채 중단합니다. TOML은 정규식 대신 파서로 편집하고,
완성된 내용을 검증한 뒤 같은 디렉터리의 임시 파일로 교체합니다.

## 사용 확인

Claude Code 또는 Codex를 재시작하고 다음과 같이 요청합니다.

> 셔클라봇으로 이 기능의 요구사항을 확인해줘.

서브에이전트가 `agent_handshake`를 호출하고, 조회 결과에 근거한 답과 출처를 반환하는지
확인합니다. Claude의 프로젝트 에이전트에 직접 정의된 MCP는 해당 프로젝트의 workspace
trust가 필요합니다. [Claude 공식 문서](https://code.claude.com/docs/en/sub-agents#scope-mcp-servers-to-a-subagent)와
[Codex 공식 문서](https://developers.openai.com/codex/subagents/)의 에이전트 형식을 사용합니다.

## 회귀 테스트

이 저장소의 루트에서 실행합니다.

```bash
uv run tests/test_setup_installer.py -q
bash -n setup-skills-and-mcp.sh
```

테스트는 임시 디렉터리와 CLI 대역을 사용합니다. 실제 사용자 설정이나 MCP 서버를 건드리지
않으며, TOML 보존, 오류 시 원본 유지, 재실행, Claude scope와 삭제 오류, 전용 MCP 설정,
PR 조회 지침, URL 인코딩, Tailscale 전용 주소 제외와 파이프 설치를 확인합니다.

## 권장 배포·업데이트 운영

1. 설치 동작을 변경할 때 `SCRIPT_VERSION`을 올리고 스크립트·테스트·README를 함께 수정합니다.
2. PR에서 회귀 테스트와 Bash 문법 검사를 통과시킵니다. 이 저장소의 GitHub Actions는
   push와 PR에 대해 이 검사를 실행합니다.
3. 검증한 커밋을 `main`에 반영합니다. 이 시점부터 위 익명 다운로드 URL의 내용이 바뀝니다.
4. 팀에 배포할 버전에는 `setup-v5` 같은 태그와 GitHub Release를 만듭니다. Release에는
   `setup-skills-and-mcp.sh`, 해당 파일의 SHA-256 체크섬, 변경점과 필요한 재설치 절차를 넣습니다.
5. 팀의 설치 안내에는 `main` 대신 검증한 태그나 커밋으로 고정한 URL을 사용합니다.
   기존 태그는 다른 커밋으로 옮기지 않고, 문제가 있으면 직전 태그를 안내하거나 새 버전을 배포합니다.

`main` URL은 편리한 최신본 확인용이며 내용이 바뀔 수 있습니다. 재현 가능한 설치에는
`https://raw.githubusercontent.com/hmjo-hyundai/public_scripts/<태그 또는 커밋>/setup-skills-and-mcp.sh`
형식을 사용합니다. GitHub Release 자산도 이 공개 저장소에서는 인증 없이 다운로드할 수 있습니다.
체크섬은 내려받은 파일이 해당 릴리스의 파일과 일치하는지 확인하는 용도입니다.

설치 스크립트는 사용자가 실행할 때만 갱신되며, 이미 설치된 소비 프로젝트의 파일을 자동으로
업데이트하지 않습니다. 서버 주소나 에이전트 지침이 바뀌면 버전과 재실행 명령을 팀에 안내합니다.
공개 변경 이력에는 설치 동작만 설명하고, 내부 기획 문서나 자격 증명은 포함하지 않습니다.
