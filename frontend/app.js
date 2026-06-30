// 프론트 → API 호출.
//
// ★ 학습 포인트: API_BASE 한 줄이 네트워크 경로를 결정한다.
//
//   Phase 1 (직결, CORS 체험):
//     const API_BASE = "http://localhost:8000";
//     → 프론트(:5173)와 다른 출처(:8000) → 브라우저가 CORS로 막음
//       (백엔드 ALLOW_CORS=1 켜야 통과 — 그 차이를 직접 본다)
//
//   Phase 2~3 (프록시 뒤, 같은 출처):
//     const API_BASE = "/api";
//     → Nginx가 /api/* 를 백엔드로 넘김. 같은 출처라 CORS 불필요.
//
// 아래 기본값은 Phase 2~3(프록시) 기준. Phase 1을 해볼 땐 위 주석대로 바꾼다.
// ── Phase 2~3: 프록시 뒤 같은 출처. 절대 URL이 아니라 /api 상대경로 ──
const API_BASE = "/api";

document.getElementById("base").textContent = API_BASE;

const listEl = document.getElementById("list");
const textEl = document.getElementById("text");

// ── Phase 4: JWT 토큰 보관 (localStorage = 새로고침해도 유지) ──
const TOKEN_KEY = "lab_token";
const getToken = () => localStorage.getItem(TOKEN_KEY);
const setToken = (t) => localStorage.setItem(TOKEN_KEY, t);
const clearToken = () => localStorage.removeItem(TOKEN_KEY);

// ── Phase 4: 토큰을 자동 첨부하는 fetch 래퍼 ──
// 매 요청마다 Authorization: Bearer <token> 를 붙인다.
// 401(만료/없음/위조)이 오면 토큰을 버리고 로그인 화면으로 되돌린다.
async function fetchWithAuth(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    clearToken();
    showLogin("세션이 만료되었거나 인증이 필요합니다. 다시 로그인하세요.");
    throw new Error("401 unauthorized");
  }
  return res;
}

// ── 화면 전환: 로그인 영역 vs 메모 영역 ──
const loginBox = document.getElementById("login-box");
const notesBox = document.getElementById("notes-box");
const loginMsg = document.getElementById("login-msg");

function showLogin(msg = "") {
  loginBox.hidden = false;
  notesBox.hidden = true;
  loginMsg.textContent = msg;
}

function showNotes() {
  loginBox.hidden = true;
  notesBox.hidden = false;
  // 토큰 페이로드에서 사용자명(sub)만 디코드해 표시 (서명검증은 서버 몫)
  try {
    const payload = JSON.parse(atob(getToken().split(".")[1]));
    document.getElementById("who").textContent = payload.sub;
  } catch {
    document.getElementById("who").textContent = "?";
  }
  load();
}

// ── 로그인: 아이디/비번 → 토큰 발급받아 저장 ──
async function login() {
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;
  const res = await fetch(`${API_BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    loginMsg.textContent = `로그인 실패: HTTP ${res.status} (아이디/비번 확인)`;
    return;
  }
  const data = await res.json();
  setToken(data.access_token);
  showNotes();
}

function logout() {
  clearToken();
  showLogin("로그아웃되었습니다.");
}

async function load() {
  try {
    const res = await fetchWithAuth("/notes");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const notes = await res.json();
    render(notes);
  } catch (e) {
    if (e.message !== "401 unauthorized") {
      listEl.innerHTML = `<li style="color:#c00">불러오기 실패: ${e.message}</li>`;
    }
  }
}

function render(notes) {
  listEl.innerHTML = "";
  for (const n of notes) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${escapeHtml(n.text)}</span>`;
    const del = document.createElement("button");
    del.textContent = "삭제";
    del.onclick = () => remove(n.id);
    li.appendChild(del);
    listEl.appendChild(li);
  }
}

async function add() {
  const text = textEl.value.trim();
  if (!text) return;
  await fetchWithAuth("/notes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  textEl.value = "";
  load();
}

async function remove(id) {
  await fetchWithAuth(`/notes/${id}`, { method: "DELETE" });
  load();
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// ── Phase 6: SSE 진행률 (EventSource) ──
// EventSource는 커스텀 헤더를 못 붙이므로, 토큰을 쿼리파라미터로 전달한다.
function startJob() {
  const bar = document.getElementById("bar");
  const pctEl = document.getElementById("pct");
  bar.style.width = "0";
  pctEl.textContent = "0%";

  // /api/stream?token=... 로 연결. 서버가 data: {progress} 를 0.3초마다 흘려보낸다.
  const es = new EventSource(`${API_BASE}/stream?token=${encodeURIComponent(getToken())}`);

  es.onmessage = (e) => {
    const { progress } = JSON.parse(e.data);
    bar.style.width = `${progress}%`;
    pctEl.textContent = `${progress}%`;
  };
  // 서버가 보낸 'done' 이벤트 → 스트림 닫기 (안 닫으면 EventSource가 재연결 시도)
  es.addEventListener("done", () => es.close());
  es.onerror = () => es.close();
}

// ── 이벤트 연결 ──
document.getElementById("start-job").onclick = startJob;
document.getElementById("login").onclick = login;
document.getElementById("logout").onclick = logout;
document.getElementById("add").onclick = add;
textEl.addEventListener("keydown", (e) => e.key === "Enter" && add());
document.getElementById("password").addEventListener(
  "keydown",
  (e) => e.key === "Enter" && login()
);

// ── 시작 시: 토큰 있으면 메모 화면, 없으면 로그인 화면 ──
if (getToken()) showNotes();
else showLogin();
