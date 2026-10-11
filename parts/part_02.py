
# ============================================================================
# HTTP SESSION (giữ nguyên từ cup_bot.py)
# ============================================================================
class _UrllibSession:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPRedirectHandler()
        )
        class _Headers:
            def __init__(self): self.d = {}
            def update(self, items): self.d.update(items)
            def __setitem__(self, k, v): self.d[k] = v
            def __getitem__(self, k): return self.d[k]
            def __contains__(self, k): return k in self.d
        self.headers = _Headers()
        self.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/139.0 Safari/537.36",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        })
        for k, v in self.headers.d.items():
            self.op.addheaders.append((k, v))
        self.last_url = ""
        self.cookies = self.cj

    def get(self, url, timeout=20, allow_redirects=True, **kw):
        h = list(self.op.addheaders)
        if 'headers' in kw and kw['headers']:
            for k, v in kw['headers'].items(): h.append((k, v))
        req = urllib.request.Request(url, headers=dict(h))
        r = self.op.open(req, timeout=timeout)
        self.last_url = r.geturl()
        class R:
            def __init__(self, r):
                self.text = r.read().decode("utf-8", "replace")
                self.url = r.geturl()
        return R(r)

    def post(self, url, data=None, timeout=20, headers=None, allow_redirects=True, **kw):
        body = urllib.parse.urlencode(data or {}).encode()
        h = dict(self.op.addheaders)
        if headers:
            for k, v in headers.items(): h[k] = v
        req = urllib.request.Request(url, data=body, headers=h)
        r = self.op.open(req, timeout=timeout)
        self.last_url = r.geturl()
        class R:
            def __init__(self, r):
                self.text = r.read().decode("utf-8", "replace")
                self.url = r.geturl()
        return R(r)

requests = type('R', (), {'Session': _UrllibSession})()

# ==================== TÀI KHOẢN ====================
CARO_USER_DIRECT = "ngan27"
CARO_PASSWD_DIRECT = "nhat123456"

def _clean_env(val, default):
    if val and str(val).strip():
        return str(val).strip()
    return default

USER = _clean_env(os.environ.get("CARO_USER19"), CARO_USER_DIRECT)
PASSWD = _clean_env(os.environ.get("CARO_PASSWD19"), CARO_PASSWD_DIRECT)

COOKIE = ""
WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/mystery_xiangqi/0"
CURRENT_PLAYER_NICKNAME = USER
CURRENT_PLAYER_ID = 0
TOKEN = 0
GAME_ID = 'mystery_xiangqi'
PLACE_PATH = 'Lobby.mystery_xiangqi.0'

# === ENGINE CONFIG ===
# Binary AB-JChess 0.2d build từ repo https://github.com/lxsgx23/AB-JChess
# (Pikafish-derived Jieqi NNUE engine, stronger than PikaJieQi ~90 Elo)
# Build cho Linux:  cd abjchess && make -j2 build ARCH=x86-64-bmi2 COMP=gcc
#   -> tạo ./src/AB-JChess (đặt cùng thư mục với file .py)
# Download cho Windows: 0.2d_bmi2.zip từ release v0.2d (chứa AB-JChess.exe + .nnue)
# Đặt cùng thư mục với file .py, hoặc override qua env MISTBOARD_ABJCHESS_ENGINE
ABJCHESS_BINARY_CANDIDATES = [
    os.environ.get("MISTBOARD_ABJCHESS_ENGINE", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "AB-JChess"
    )),
    # Tên binary build local từ source (Linux/macOS)
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "AB-JChess"),
    # Tên binary Windows trong 0.2d_bmi2.zip (chạy qua wine nếu cần)
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "AB-JChess.exe"),
    # Alias thường gặp trong mistboard builds
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ab-jchess"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "abjchess"),
    # Fallback tên cũ (nếu user đặt binary nhầm tên PikaJieQi)
    os.environ.get("MISTBOARD_JIEQI_ENGINE", ""),  # env cũ của PikaJieQi, dùng làm fallback
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "PikaJieQi"),
]
ABJCHESS_BINARY_CANDIDATES = [p for p in ABJCHESS_BINARY_CANDIDATES if p]

# AB-JChess 0.2d release ships NNUE file abjchess-20261010.nnue trong 0.2d_bmi2.zip.
# Đây là BẮT BUỘC — không có NNUE, engine không khởi động được (khác PikaJieQi).
# Tìm file theo pattern abjchess-*.nnue trong cùng thư mục binary.
ABJCHESS_NNUE_CANDIDATES = [
    os.environ.get("MISTBOARD_ABJCHESS_NNUE", ""),
    # Ưu tiên file mới nhất (sort theo tên giảm dần)
] + sorted(
    [os.path.join(d, f) for d in [os.path.dirname(os.path.abspath(__file__))]
     for f in os.listdir(d) if f.startswith("abjchess-") and f.endswith(".nnue")],
    reverse=True,
)
ABJCHESS_NNUE_CANDIDATES = [p for p in ABJCHESS_NNUE_CANDIDATES if p]

# Cấu hình engine (giống bản PikaJieQi, thêm 2 option riêng của AB-JChess)
ENGINE_USE_AGGRESSIVE_LEVEL = 3   # 0..10, default 3 (theo AB-JChess README)
ENGINE_CHASING_RULE = "skyrule_Jieqi"  # default của AB-JChess

ENGINE_MULTIPV = 1
MIN_MOVE_SECONDS = 3.0
MOVE_DEADLINE_SECONDS = 30.0
MAX_SAFE_MOVES = 250
TRUST_ENGINE_AFTER = 100
MAX_ENGINE_RESTARTS_PER_GAME = 2
MOVE_DEDUP_WINDOW = 0.1
# AB-JChess dùng NNUE nên nghĩ lâu hơn có lợi; đặt movetime default 3000ms
# (PikaJieQi cũ dùng 2000ms với classical eval)
DEFAULT_MOVETIME_MS = 3000

KICK_MODE = "never"
KICK_DELAY = 5.0
SIT_ALONE_TIMEOUT = 300.0

BOT_BET_XU = 2000
BOT_USE_CREATE_TABLE = True
BOT_MATCH_DURATION = '5'
BOT_TURN_DURATION = '30'
BOT_ACC_DURATION = '0'
BOT_BLOCK_SOFTWARE = '0'

VN_TEN_DAU = [
    "Tuấn ",  "Minh ",  "Đức ",  "Hoàng ",  "Huy ",  "Hùng ",  "Dũng ",  "Cường ",  "Long ",  "Nam ",
    "Sơn ",  "Hải ",  "Phong ",  "Thắng ",  "Trung ",  "Kiên ",  "Quân ",  "Thanh ",  "Đạt ",  "Khoa ",
]
VN_TEN_KHONG_DAU = [
    "Tuan ",  "Minh ",  "Duc ",  "Hoang ",  "Huy ",  "Hung ",  "Dung ",  "Cuong ",  "Long ",  "Nam ",
    "Son ",  "Hai ",  "Phong ",  "Thang ",  "Trung ",  "Kien ",  "Quan ",  "Thanh ",  "Dat ",  "Khoa ",
]

_IDENTITY_SYNCED = False

def generate_dotted_full_name():
    name = random.choice(VN_TEN_DAU if random.choice([True, False]) else VN_TEN_KHONG_DAU)
    if len(name) >= 2:
        pos = random.randint(1, len(name) - 1)
        name = name[:pos] + "." + name[pos:]
    return name

def sync_profile_name(session):
    try:
        edit_url = "https://gamevh.net/com/ftl/game/profile/update_profile.jsp"
        page = session.get(edit_url, timeout=15, allow_redirects=True)
        form_match = re.search(r'(?is)<form\b[^>]*name=["\']InputForm0["\'][^>]*>.*?</form\s*>', page.text)
        if not form_match: return
        form = form_match.group(0)
        open_tag = re.search(r'(?is)<form\b[^>]*>', form).group(0)
        action_match = re.search(r'action=["\']([^"\']+)["\']', open_tag)
        action = action_match.group(1) if action_match else edit_url
        if not action.startswith('http'):
            from urllib.parse import urljoin
            action = urljoin(edit_url, action)
        data = {}
        for tag in re.findall(r'(?is)<input\b[^>]*>', form):
            nm = re.search(r'name=["\']([^"\']+)["\']', tag)
            val = re.search(r'value=["\']([^"\']*)["\']', tag)
            if nm:
                k = nm.group(1)
                data[k] = val.group(1) if val else ''
        old_full_name = data.get('FULL_NAME', '')
        new_full_name = generate_dotted_full_name()
        data['FULL_NAME'] = new_full_name
        data['OLD_PASSWORD'] = PASSWD
        data['SAVE'] = '\uf046'
        session.post(action, timeout=15, data=data,
                     headers={'Origin': 'https://gamevh.net',
                              'Referer': page.url,
                              'Content-Type': 'application/x-www-form-urlencoded'},
                     allow_redirects=True)
        print(f"[PROFILE] 👤 '{old_full_name}' -> '{new_full_name}'")
    except Exception as e:
        print(f"[PROFILE] Lỗi: {e}")

def sync_random_avatar(session):
    try:
        profile_url = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
        before = session.get(profile_url, timeout=15)
        m = re.search(r'/avatar/builtin(\d+).(?:webp|png|jpg)', before.text, re.I)
        old_avatar = int(m.group(1)) if m else None
        catalog = []
        seen = set()
        pattern = re.compile(
            r'''buyAvatar\(\s*(["']?)(\d+)\1\s*,\s*(["'])(.*?)\3\s*,\s*(["']?)([\d,.]+)\5\s*\)''',
            re.I | re.S)
        for category in range(1, 7):
            url = f"https://gamevh.net/com/ftl/game/profile/avatar_by_category.jsp?excludeLayout=true&category_id={category}"
            page = session.get(url, timeout=15)
            for match in pattern.finditer(page.text):
                avatar_id = int(match.group(2))
                if avatar_id not in seen:
                    seen.add(avatar_id)
                    catalog.append(avatar_id)
        choices = [a for a in catalog if a != old_avatar]
        if not choices: return
        selected = random.choice(choices)
        update_url = f"https://gamevh.net/com/ftl/game/profile/update_avatar.jsp?pk={selected}&redirect=/"
        session.post(update_url, timeout=20,
                     headers={"Origin": "https://gamevh.net",
                              "Referer": "https://gamevh.net/com/ftl/game/profile/avatar.jsp"},
                     allow_redirects=True)
    except Exception as e:
        print(f"[PROFILE] Lỗi avatar: {e}")

def fetch_session_info():
    global COOKIE, TOKEN, CURRENT_PLAYER_NICKNAME, CURRENT_PLAYER_ID, PLACE_PATH, _IDENTITY_SYNCED
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/139.0 Safari/537.36",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        })
        session.get(LOGIN_URL, timeout=20)
        session.post(
            LOGIN_URL, timeout=20,
            data={"redirect": "/", "USER_NAME": USER, "PASSWORD": PASSWD,
                  "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
            headers={"Origin": "https://gamevh.net",
                     "Referer": LOGIN_URL,
                     "Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=True)
        if not _IDENTITY_SYNCED:
            _IDENTITY_SYNCED = True
            sync_profile_name(session)
            sync_random_avatar(session)
        game_resp = session.get(GAME_URL, timeout=20)
        page_html = game_resp.text
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", page_html)
        if not tm: return False
        TOKEN = int(tm.group(1))
        # FIX: Dùng raw string với dấu nháy đơn để tránh lỗi cú pháp
        nm = re.search(r'var\s+currentPlayerNickName\s*=\s*["\']([^"\']+)["\']', page_html)
        if not nm: return False
        CURRENT_PLAYER_NICKNAME = nm.group(1).strip()
        pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", page_html)
        if pid: CURRENT_PLAYER_ID = int(pid.group(1))
        pm = re.search(r'var\s+placePath\s*=\s*["\']([^"\']+)["\']', page_html)
        if pm: PLACE_PATH = pm.group(1)
        try:
            cookie_parts = [f"{c.name}={c.value}" for c in session.cookies]
            COOKIE = "; ".join(cookie_parts)
        except Exception:
            COOKIE = "; ".join(f"{k}={v}" for k, v in session.cookies.items())
        print(f"[SESSION] Login OK | Token: {TOKEN} | Nick: {CURRENT_PLAYER_NICKNAME} | ID: {CURRENT_PLAYER_ID}")
        return True
    except Exception as e:
        print(f"[SESSION] Lỗi: {e}")
        return False
