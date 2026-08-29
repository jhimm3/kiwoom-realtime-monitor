# API 발급 가이드

이 앱은 키를 대신 발급하거나 개발자 계정의 사용료를 부담하지 않습니다. 필요한 기능만 선택해 각 공식 사이트에서 직접 발급하고 앱의 해당 설정 창에 입력하세요. 모든 비밀키는 현재 PC에 암호화해 저장하며 설정 백업과 Google Drive 동기화에는 포함하지 않습니다.

## 1. 키움 REST API

1. [키움 REST API](https://openapi.kiwoom.com/)에 키움증권 계정으로 로그인합니다.
2. 서비스 이용 신청과 약관 동의를 완료한 뒤 앱 키를 발급합니다.
3. 모의투자와 실전 환경을 구분해 `App Key`와 `Secret Key`를 확인합니다.
4. 앱의 `기본 설정 → 관리 → API 설정`에서 환경별 키를 입력하고 연결 테스트를 실행합니다.

키움 계좌와 API 이용 권한이 필요할 수 있습니다. 발급 화면과 호출 제한은 키움 정책에 따라 달라질 수 있으므로 [서비스 이용 안내](https://openapi.kiwoom.com/intro/serviceInfo)를 함께 확인하세요.

## 2. 네이버 뉴스 검색 API

1. [네이버 개발자센터 애플리케이션 등록](https://developers.naver.com/apps/#/register)을 엽니다.
2. 애플리케이션 이름을 정하고 사용 API에 `검색`을 추가합니다.
3. 등록 후 `내 애플리케이션`에서 `Client ID`와 `Client Secret`을 확인합니다.
4. 뉴스창 톱니바퀴의 뉴스 API 설정에 두 값을 입력합니다.

권한을 빠뜨리면 403 오류가 날 수 있습니다. 자세한 절차는 [네이버 공식 애플리케이션 등록 가이드](https://developers.naver.com/docs/common/openapiguide/appregister.md)를 따르세요.

## 3. OpenDART 공시 API

1. [OpenDART 인증키 신청](https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do)에서 회원가입·로그인 후 인증키를 신청합니다.
2. 이용 목적 등 필수 정보를 입력하고 신청 결과를 확인합니다.
3. 발급된 인증키를 뉴스 설정의 DART API 키에 입력하고 DART 사용을 켭니다.

DART 공시는 선택 기능입니다. 뉴스 양을 줄이고 싶으면 사용을 끌 수 있습니다.

## 4. Google Gemini API

1. [Google AI Studio API 키](https://aistudio.google.com/app/apikey)를 엽니다.
2. 사용할 Google Cloud 프로젝트를 선택하거나 새 프로젝트를 만듭니다.
3. `Create API key`로 키를 만들고 즉시 안전한 곳에 보관합니다.
4. 뉴스 AI 설정에서 공급자를 `Gemini`로 선택하고 키와 모델을 설정합니다.

무료 한도와 RPM·TPM·RPD는 모델과 프로젝트 상태에 따라 다릅니다. [Gemini API 키 가이드](https://ai.google.dev/gemini-api/docs/api-key?hl=ko)와 AI Studio의 Rate limits 화면에서 현재 값을 확인하세요.

## 5. OpenAI API

1. [OpenAI API 키 관리](https://platform.openai.com/api-keys)에 로그인합니다.
2. 사용할 프로젝트를 선택하고 새 비밀키를 만듭니다.
3. 키는 생성 직후 한 번만 완전히 표시되므로 안전하게 보관합니다.
4. 뉴스 AI 설정에서 공급자를 `OpenAI`로 선택하고 키와 모델을 설정합니다.

ChatGPT 구독과 API 사용료는 별도입니다. [OpenAI API 요금](https://openai.com/api/pricing/)과 프로젝트의 사용 한도를 확인하세요.

## 6. Anthropic Claude API

1. [Anthropic Console API Keys](https://console.anthropic.com/settings/keys)에 로그인합니다.
2. 워크스페이스를 선택하고 새 API 키를 생성합니다.
3. 필요한 경우 결제 수단이나 사용 크레딧을 설정합니다.
4. 뉴스 AI 설정에서 공급자를 `Claude`로 선택하고 키와 모델을 설정합니다.

키 생성과 첫 요청은 [Anthropic 공식 시작 가이드](https://docs.anthropic.com/en/api/getting-started)를 확인하세요.

## 7. Google Drive 설정 동기화 OAuth

1. [Google Cloud Console](https://console.cloud.google.com/)에서 프로젝트를 만들거나 선택합니다.
2. [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com)를 사용 설정합니다.
3. OAuth 동의 화면을 구성합니다. 개인 테스트 중이면 본인 Google 계정을 테스트 사용자로 추가합니다.
4. `API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → OAuth 클라이언트 ID`에서 애플리케이션 유형을 `데스크톱 앱`으로 선택합니다.
5. 받은 OAuth 클라이언트 JSON을 내려받습니다.
6. 앱의 관리 설정에서 `내 OAuth JSON 연결`로 파일을 선택한 뒤 Google Drive 연결을 진행합니다.

OAuth JSON과 로그인 토큰도 비밀정보입니다. GitHub에 올리거나 다른 사람과 공유하지 마세요. 앱은 설정·테마와 별도 AI 분석 캐시만 동기화하며 뉴스 기사 DB와 모든 API 키는 Drive에 올리지 않습니다.

## 오류가 날 때 먼저 확인할 것

- 키 앞뒤에 공백이 붙지 않았는지 확인합니다.
- 공급자와 모델 조합이 현재 계정에서 사용 가능한지 확인합니다.
- 일일 요청, 분당 요청, 토큰 또는 결제 한도에 도달하지 않았는지 확인합니다.
- 회사·학교 보안 프로그램의 자체 서명 인증서 때문에 HTTPS 인증 오류가 나면 Windows 인증서 저장소와 보안 프로그램 설정을 확인합니다. 인증서 검증을 끄는 방식은 사용하지 않습니다.
- 키가 노출됐다고 의심되면 해당 공급자 콘솔에서 즉시 폐기하고 새 키를 발급합니다.
