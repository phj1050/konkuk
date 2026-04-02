# Render 에 Flask 올리기 (처음부터 끝까지)

이 파일은 **PC 끄고·터미널 안 켜도** 폰 LTE 로 쓰려고 **인터넷에 백엔드를 띄울 때** 따라 하는 순서입니다.

---

## 0. 미리 알아둘 것

- **Render** = 무료(또는 유료)로 **24시간 서버**를 돌려 주는 사이트.
- **GitHub** = 코드를 인터넷 저장소에 올리는 곳. Render 가 여기서 코드를 가져갑니다.
- 배포가 끝나면 주소가 생깁니다. 예: `https://kkulib-xxxx.onrender.com`

---

## 1. GitHub 에 코드 올리기 (처음 한 번)

1. 브라우저에서 https://github.com 접속 → 로그인(없으면 가입).
2. 오른쪽 위 **+** → **New repository**.
3. Repository name 예: `konkuk-library-checkin` → **Public** 선택 → **Create repository**.
4. **네 PC**에서 폴더 `konkuk-library-checkin` 을 연다.
5. 그 안에 **`.env` 파일은 절대 GitHub 에 안 올라가게** 이미 `.gitignore` 에 들어 있음. **토큰은 GitHub 에 올리지 말 것.**

### Git 명령어 — 이렇게 실행하면 됨

- **줄이 여러 줄인 이유:** 명령이 **하나가 아니라 여러 개**라서 그래. 각 줄이 **한 번에 할 일**이야.
- **복붙 방법 (둘 중 편한 것):**
  - **A)** 아래 회색 박스 **전체**를 복사 → PowerShell 창에 **붙여넣기** → **Enter 한 번** → 위에서부터 **순서대로** 전부 실행됨.
  - **B)** 한 줄만 복사 → Enter → 다음 줄 복사 → Enter … 천천히 해도 됨.
- **한 줄로 이어 붙이면 안 되나?** 굳이 그럴 필요 없음. 여러 줄 그대로 두는 게 맞음.

VS Code / Cursor 를 쓰면: **터미널 → 새 터미널** 연 다음 같은 방식으로 붙여 넣으면 됨.

**1단계 — 로컬에서 Git 저장소 만들고 첫 커밋:**

```powershell
cd "c:\Users\hyenj\Documents\konkuk-library-checkin"
git init
git add app.py index.html requirements.txt .gitignore .env.example DEPLOY_RENDER.md
git commit -m "library proxy"
git branch -M main
```

**2단계 — GitHub 에 만든 빈 저장소랑 연결하고 올리기**

GitHub 웹에서 저장소 만든 뒤, **초록 Code 버튼** 눌러서 나오는 **HTTPS 주소**를 복사해.  
아래에서 `https://github.com/네아이디/konkuk-library-checkin.git` 부분만 **본인 주소로 바꿔서** 실행:

```powershell
git remote add origin https://github.com/네아이디/konkuk-library-checkin.git
git push -u origin main
```

- `git remote add origin ...` 은 **딱 한 번만** (이미 했는데 에러 나면 나중에 물어봐).
- 처음 `git push` 할 때 GitHub 로그인·토큰 입력 창이 뜰 수 있음.

---

## 2. Render 가입 · GitHub 연결

1. https://render.com 접속.
2. **Get Started for Free** 또는 **Sign Up** → **Sign up with GitHub** 추천(연동이 쉬움).
3. GitHub 접근 허용 질문이 나오면 **Authorize** 한다.

---

## 3. Web Service 만들기

1. Render 대시보드에서 **New +** → **Web Service**.
2. **Connect a repository** → 방금 만든 `konkuk-library-checkin` 선택. (안 보이면 **Configure account** 로 GitHub 전체 접근 허용.)
3. 설정 입력:

| 항목 | 넣을 값 |
|------|---------|
| **Name** | 아무거나 (예: `kkulib`) |
| **Region** | Singapore 또는 가까운 곳 |
| **Branch** | `main` |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn app:app --bind 0.0.0.0:$PORT` |

4. 아래로 스크롤 → **Environment Variables** → **Add Environment Variable**

| Key | Value |
|-----|--------|
| `LIBRARY_ID` | 건국대 통합/도서관 로그인 아이디 |
| `LIBRARY_PW` | 비밀번호 (**GitHub 에 절대 커밋하지 말 것**, Render 환경 변수만 사용 권장) |

5. **Create Web Service** 클릭.
6. **Logs** 가 돌아가며 빌드됨. 몇 분 걸릴 수 있음.
7. 상단에 **`https://이름.onrender.com`** 형태 주소가 보이면 성공.

---

## 4. 폰에서 쓰는 방법 (index.html)

1. `index.html` 을 메모장/VS Code 로 연다.
2. **백엔드 주소** 입력란에 관계없이, **브라우저에서 열었을 때** 맨 위 **백엔드 주소 칸**에 다음을 **직접 입력**한다:

   `https://Render가준주소.onrender.com`

   (끝에 `/` 있으면 지워도 됨.)

3. 로그인은 **서버**가 `LIBRARY_ID` / `LIBRARY_PW` 로 자동 처리한다. HTML 에 토큰을 넣을 필요 없음.

또는 URL 로 한 번에:

`file:///.../index.html?api=https://이름.onrender.com`

---

## 5. 무료 Render 특성

- **일정 시간 요청 없으면 서버가 잠듦(Sleep).** 첫 요청이 30초~1분 걸릴 수 있음.
- 그 다음 요청은 보통 빠름.

---

## 6. 비밀번호·아이디 변경 시

- Render 대시보드 → 해당 Web Service → **Environment** → `LIBRARY_ID` / `LIBRARY_PW` 수정 → **Save** → 자동 재배포.
