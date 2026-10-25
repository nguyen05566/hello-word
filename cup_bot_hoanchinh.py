"""
cup_bot_flipchess_abjchess.py — Cờ Úp Bot dùng engine AB-JChess 0.2d build từ mistboard
Tích hợp Dynamic Maphack: 
 - Khai cuộc (6 nước đầu): Kích hoạt Maphack (MultiPV=3), ưu tiên lật Xe, Pháo, Mã thật.
 - Trung/Tàn cuộc: Tắt Maphack (MultiPV=1), dồn 100% CPU cho engine tính toán chiều sâu chiến thuật.
"""
import struct
import threading
import time
import sys
import os
import re
import subprocess
import signal
import atexit
import tempfile
import json
import random
import traceback
import urllib.request, urllib.parse, http.cookiejar

# ============================================================================
# ws_frame_dump — EMBEDDED
# ============================================================================
CMD_NAMES = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT",
    311: "BROADCAST", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    331: "CHAT.SEND", 335: "CHAT.MSG",
    401: "ENTER_PLACE", 405: "CREATE_RULE", 406: "PLAYER_ENTERED", 407: "PLAYER_EXITED",
    408: "QUICK_PLAY", 410: "KICK_PLAYER", 412: "LIST_ZONE_ROOM", 413: "LIST_BET_AMT",
    414: "GET_TABLE_DATA", 416: "SLOT_IN_TABLE_CHANGED",
    417: "START_MATCH", 418: "GAMEOVER", 419: "ENTER_STATE",
    420: "SET_TURN", 434: "SET_READY",
    502: "PLAY", 529: "MOVE", 533: "ASK_DRAW", 534: "SURRENDER", 601: "LOGIN_EX",
}

PIECE_TYPE_MAP = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}

class _Reader:
    def __init__(self, data):
        self.data = bytes(data)
        self.offset = 0

    def read_byte(self):
        v = struct.unpack_from('>b', self.data, self.offset)[0]
        self.offset += 1
        return v

    def read_int(self):
        v = struct.unpack_from('>i', self.data, self.offset)[0]
        self.offset += 4
        return v

    def rem(self):
        return len(self.data) - self.offset

def _parse_command(r):
    length = r.read_byte()
    if length < 0:
        n = -length
        cmd = r.data[r.offset:r.offset + n].decode('ascii', errors='replace')
        r.offset += n
        return cmd
    next_byte = r.data[r.offset] & 0xFF
    r.offset += 1
    code = (length << 8) | next_byte
    return CMD_NAMES.get(code, str(code))

def _decode_piece_id(encoded_id):
    color = 'r'
    if encoded_id < 0:
        encoded_id = -encoded_id
        color = 'b'
    return f"{color}{encoded_id >> 3}{'' if (encoded_id & 7) == 0 else (encoded_id & 7)}"

def _face_to_fen_char(raw_face):
    v = raw_face
    if v == 0: return None
    ch = PIECE_TYPE_MAP.get(abs(v) >> 3)
    if not ch: return None
    return ch.upper() if v > 0 else ch

class _GameState:
    def __init__(self):
        self.reset()
    def reset(self):
        self.pos_sid = {}       
        self.revealed = set()   
        self.identity = {}      

_state = _GameState()

def decode_frame(data):
    try:
        r = _Reader(data)
        cmd = _parse_command(r)
        event = {"command": cmd}

        if cmd == "START_MATCH":
            pieces = []
            player_count = r.read_byte()
            for _ in range(max(0, player_count)):
                r.read_byte(); r.read_int()
            piece_count = r.read_byte()
            new_pos_sid = {}; new_revealed = set()
            for _ in range(max(0, piece_count)):
                raw_sid = r.read_byte(); raw_face = r.read_byte()
                pos = r.read_byte(); is_open = r.read_byte()
                pieces.append({
                    "sid_decoded": _decode_piece_id(raw_sid),
                    "face_decoded": _decode_piece_id(raw_face),
                    "position": pos,
                    "is_open": is_open,
                })
                if 0 <= pos < 90: new_pos_sid[pos] = raw_sid
                if is_open: new_revealed.add(raw_sid)
            event["pieces"] = pieces
            _state.pos_sid = new_pos_sid
            _state.revealed = new_revealed
            _state.identity = {}

        elif cmd == "MOVE":
            if r.rem() >= 2:
                event["source"] = src = r.read_byte()
                event["target"] = tgt = r.read_byte()
            else: return event
            count = r.read_byte() if r.rem() >= 1 else 0
            if count < 0: count = 0
            pairs = []
            for _ in range(min(count, r.rem() // 2)):
                sid = r.read_byte(); face = r.read_byte()
                pairs.append((sid, face))
                _state.identity[sid] = face

            mover_sid = _state.pos_sid.get(src)
            captured_sid = _state.pos_sid.get(tgt) if tgt != src else None
            revealed_char = None; captured_char = None
            for sid, face in pairs:
                ch = _face_to_fen_char(face)
                if ch in ('k', 'K'): ch = None
                if sid == mover_sid and sid not in _state.revealed:
                    if revealed_char is None: revealed_char = ch
                elif sid == captured_sid and sid not in _state.revealed:
                    if captured_char is None: captured_char = ch
                _state.revealed.add(sid)

            event["revealed_piece"] = revealed_char
            event["captured_piece"] = captured_char
            event["mover_known"] = mover_sid is not None
            event["stale"] = bool(_state.pos_sid) and mover_sid is None

            if mover_sid is not None:
                if tgt != src:
                    _state.pos_sid.pop(src, None)
                    _state.pos_sid[tgt] = mover_sid
        return event
    except Exception as exc:
        return {"command": "?", "decode_error": f"{type(exc).__name__}: {exc}"}

# ============================================================================
# HTTP SESSION 
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
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/139.0",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        })
        for k, v in self.headers.d.items():
            self.op.addheaders.append((k, v))
        self.cookies = self.cj

    def get(self, url, timeout=20, allow_redirects=True, **kw):
        h = list(self.op.addheaders)
        if 'headers' in kw and kw['headers']:
            for k, v in kw['headers'].items(): h.append((k, v))
        req = urllib.request.Request(url, headers=dict(h))
        r = self.op.open(req, timeout=timeout)
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
        class R:
            def __init__(self, r):
                self.text = r.read().decode("utf-8", "replace")
                self.url = r.geturl()
        return R(r)

requests = type('R', (), {'Session': _UrllibSession})()

# ==================== CẤU HÌNH ====================
CARO_USER_DIRECT = "nguyen16"
CARO_PASSWD_DIRECT = "nhat123456"

def _clean_env(val, default):
    if val and str(val).strip(): return str(val).strip()
    return default

USER = CARO_USER_DIRECT
PASSWD = CARO_PASSWD_DIRECT

COOKIE = ""
WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/mystery_xiangqi/0"
CURRENT_PLAYER_NICKNAME = USER
CURRENT_PLAYER_ID = 0
TOKEN = 0
GAME_ID = 'mystery_xiangqi'
PLACE_PATH = 'Lobby.mystery_xiangqi.0'

ABJCHESS_BINARY_CANDIDATES = [
    os.environ.get("MISTBOARD_ABJCHESS_ENGINE", "/tmp/engines/pikafish-jieqi-wrapper"),
    "/tmp/engines/pikafish-jieqi-wrapper",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "AB-JChess"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "AB-JChess.exe"),
]
ABJCHESS_BINARY_CANDIDATES = [p for p in ABJCHESS_BINARY_CANDIDATES if p]

ABJCHESS_NNUE_CANDIDATES = [
    os.environ.get("MISTBOARD_ABJCHESS_NNUE", ""),
    "/tmp/engines/pikafish.nnue",
] + sorted(
    [os.path.join(d, f) for d in [os.path.dirname(os.path.abspath(__file__))]
     for f in os.listdir(d) if f.startswith("abjchess-") and f.endswith(".nnue")],
    reverse=True,
)
ABJCHESS_NNUE_CANDIDATES = [p for p in ABJCHESS_NNUE_CANDIDATES if p]

# --- Thông số Maphack & Engine ---
MAPHACK_MOVE_LIMIT = 0     # Tat Maphack - choi engine thuan
MAPHACK_MULTIPV = 2           # Tang MultiPV tu 4 len 6 - GIA TRI HỢP LÝ
ENGINE_USE_AGGRESSIVE_LEVEL = 3  # Tang muc do tan cong nhẹ hơn
ENGINE_CHASING_RULE = "skyrule_Jieqi"
MIN_MOVE_SECONDS = 1.0       # Giam thoi gian cho toi thieu
MOVE_DEADLINE_SECONDS = 35.0
MAX_ENGINE_RESTARTS_PER_GAME = 3
DEFAULT_MOVETIME_MS = 5000  # Tang thoi gian mac dinh cho engine suy nghi

# Thong so cho Dynamic Time Management
FAST_MATE_MOVETIME = 500      # 0.5s cho chieu chet
FAST_BIG_ADV_MOVETIME = 1200  # 1.2s cho uu the lon
FAST_MED_ADV_MOVETIME = 1800  # 1.8s cho uu the vua
FAST_SMALL_ADV_MOVETIME = 2500 # 2.5s cho uu the nho
NORMAL_MOVETIME = 2000       # 2.0s cho binh thuong

# Opening Book Configuration
USE_OPENING_BOOK = True  # Su dung opening book cho nhung nuoc di dau
OPENING_BOOK_MOVES = 8   # So nuoc di su dung opening book
# Dinh dang: {fen_start: move1 move2 move3 ...}
OPENING_BOOK = {
    # Cac khoi dau co ban
    "xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX": ["e3d3", "h3g3", "e3f3", "h3i3"],
    # Phong thu Ma
    "xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX": ["b2e3", "g2e3", "b2f3", "g2f3"],
}

KICK_MODE = "never"
SIT_ALONE_TIMEOUT = 300.0
BOT_BET_XU = 1000
BOT_USE_CREATE_TABLE = True
BOT_MATCH_DURATION = '5'
BOT_TURN_DURATION = '30'
BOT_ACC_DURATION = '0'
BOT_BLOCK_SOFTWARE = '0'

def fetch_session_info():
    global COOKIE, TOKEN, CURRENT_PLAYER_NICKNAME, CURRENT_PLAYER_ID, PLACE_PATH
    try:
        session = requests.Session()
        session.get(LOGIN_URL, timeout=20)
        session.post(
            LOGIN_URL, timeout=20,
            data={"redirect": "/", "USER_NAME": USER, "PASSWORD": PASSWD,
                  "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
            headers={"Origin": "https://gamevh.net",
                     "Referer": LOGIN_URL,
                     "Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=True)
        game_resp = session.get(GAME_URL, timeout=20)
        page_html = game_resp.text
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", page_html)
        if not tm: return False
        TOKEN = int(tm.group(1))
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
        print(f"[SESSION] Login OK | Nick: {CURRENT_PLAYER_NICKNAME}")
        return True
    except Exception as e:
        print(f"[SESSION] Lỗi: {e}")
        return False

class Conn:
    def pack(self, cmd, data=b''):
        result = bytearray()
        if isinstance(cmd, str):
            cmd_bytes = cmd.encode('ascii')
            result.append((-len(cmd_bytes)) & 0xFF)
            result.extend(cmd_bytes)
        elif isinstance(cmd, int):
            result.extend(struct.pack('>H', cmd))
        result.extend(data)
        return bytes(result)

    def pack_byte(self, value): return struct.pack('>b', value)
    def pack_int(self, value): return struct.pack('>i', value)
    def pack_ascii(self, value):
        encoded = value.encode('ascii')[:255]
        return struct.pack('>b', len(encoded)) + encoded
    def pack_string(self, value):
        encoded = value.encode('utf-16-be')
        return struct.pack('>h', len(encoded) // 2) + encoded

class InboundMessage:
    def __init__(self, data):
        self.data = bytes(data)
        self.offset = 0
        self.command = self._parse_command()

    def _parse_command(self):
        length = self.read_byte()
        if length < 0:
            cmd = self.data[self.offset:self.offset + (-length)].decode('ascii', errors='replace')
            self.offset += (-length)
            return cmd
        else:
            next_byte = self.data[self.offset] & 0xFF
            self.offset += 1
            return CMD_NAMES.get((length << 8) | next_byte, str((length << 8) | next_byte))

    def read_byte(self):
        val = struct.unpack_from('>b', self.data, self.offset)[0]
        self.offset += 1
        return val
    def read_short(self):
        val = struct.unpack_from('>h', self.data, self.offset)[0]
        self.offset += 2
        return val
    def read_int(self):
        val = struct.unpack_from('>i', self.data, self.offset)[0]
        self.offset += 4
        return val
    def read_long(self):
        val = struct.unpack_from('>q', self.data, self.offset)[0]
        self.offset += 8
        return val
    def read_ascii(self):
        length = self.read_byte()
        if length < 0: length += 256
        s = self.data[self.offset:self.offset + length].decode('ascii', errors='replace')
        self.offset += length
        return s
    def read_string(self):
        char_count = self.read_short()
        s = self.data[self.offset:self.offset + char_count * 2].decode('utf-16-be', errors='replace')
        self.offset += char_count * 2
        return s
    def rem(self):
        return len(self.data) - self.offset

INITIAL_BAG = {'A': 2, 'B': 2, 'N': 2, 'R': 2, 'C': 2, 'P': 5,
               'a': 2, 'b': 2, 'n': 2, 'r': 2, 'c': 2, 'p': 5}
BAG_ORDER = ['A', 'B', 'N', 'R', 'C', 'P', 'a', 'b', 'n', 'r', 'c', 'p']
UCI_MOVE_WITH_SUFFIX_RE = re.compile(r'^[a-i]\d[a-i]\d[a-zA-Z]{0,2}$')

class XiangqiBoardTracker:
    INITIAL_FEN = "xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX w"

    def __init__(self):
        self.reset()

    def reset(self):
        self.start_fen = self.INITIAL_FEN.split(' ')[0]
        self.start_side = 'w'
        self.uci_moves = []
        self.revealed_chars = []
        self.my_slot_id = -1
        self.first_turn_slot_id = 0
        self.is_my_turn = False
        self.is_playing = False
        self.is_red = None
        self.dark_positions = set()
        self.flip = False
        self.side_to_move = 'w'

    def pos_to_rc(self, pos):
        s_row, col = pos // 9, pos % 9
        return ((9 - s_row) if self.flip else s_row), col

    def rc_to_pos(self, fen_row, col):
        s_row = (9 - fen_row) if self.flip else fen_row
        return s_row * 9 + col

    def pos_to_engine_move(self, source_pos, target_pos):
        s_row, s_col = self.pos_to_rc(source_pos)
        t_row, t_col = self.pos_to_rc(target_pos)
        return (f"{chr(ord('a') + s_col)}{9 - s_row}"
                f"{chr(ord('a') + t_col)}{9 - t_row}")

    def engine_move_to_pos(self, engine_move):
        move = engine_move[:4]
        s_col, s_rank = ord(move[0]) - ord('a'), int(move[1])
        t_col, t_rank = ord(move[2]) - ord('a'), int(move[3])
        return (self.rc_to_pos(9 - s_rank, s_col),
                self.rc_to_pos(9 - t_rank, t_col))

    def bag_string(self):
        bag = dict(INITIAL_BAG)
        for ch in self.revealed_chars:
            if ch in bag: bag[ch] = max(0, bag[ch] - 1)
        return "".join(f"{k}{bag[k]}" for k in BAG_ORDER)

    def get_current_fen(self):
        fen = f"{self.start_fen} {self.bag_string()} {self.start_side} - - 0 1"
        moves = [m for m in self.uci_moves if UCI_MOVE_WITH_SUFFIX_RE.match(m)]
        return fen, moves

    def set_base(self, board_fen, side='w'):
        board_fen = board_fen.split(' ')[0] if ' ' in board_fen else board_fen
        self.start_fen = board_fen
        self.start_side = side
        self.side_to_move = side
        self.uci_moves = []
        self.revealed_chars = []
        self.dark_positions.clear()

    def record_move(self, mv, revealed_char=None):
        uci = mv + (revealed_char or "")
        self.uci_moves.append(uci)
        if revealed_char:
            for _ch in revealed_char:
                self.revealed_chars.append(_ch)
        self.side_to_move = 'b' if self.side_to_move == 'w' else 'w'
        return uci

    def set_my_slot(self, slot_id, first_turn_slot_id):
        self.my_slot_id = slot_id
        self.first_turn_slot_id = first_turn_slot_id
        self.is_red = (self.my_slot_id == self.first_turn_slot_id)

    def detect_flip(self, pieces):
        red_rows, black_rows = [], []
        red_king_row = black_king_row = None
        for sid, face, position, is_open in pieces:
            if position is None or position < 0 or position >= 90: continue
            row = position // 9
            color = face[0] if face else (sid[0] if sid else 'r')
            ptype = int(face[1]) if len(face) > 1 and str(face[1]).isdigit() else 0
            if color == 'r':
                red_rows.append(row)
                if ptype == 1: red_king_row = row
            else:
                black_rows.append(row)
                if ptype == 1: black_king_row = row
        if red_king_row is not None and black_king_row is not None:
            self.flip = red_king_row < black_king_row
        elif red_rows and black_rows:
            self.flip = (sum(red_rows) / len(red_rows)) < (sum(black_rows) / len(black_rows))
        else:
            self.flip = bool(self.is_red)
        return self.flip

    def sanity_check_fen(self, board_fen):
        rows = board_fen.split(' ')[0].split('/')
        if len(rows) != 10: return False, f"FEN có {len(rows)} hàng"
        K_row = k_row = None
        for i, r in enumerate(rows):
            if 'K' in r: K_row = i
            if 'k' in r: k_row = i
        if K_row is None or k_row is None: return False, "thiếu tướng"
        if K_row < 7 or k_row > 2: return False, "tướng sai chiều"
        return True, "ok"

class VisibleBoard:
    TYPE_TO_FEN = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}
    
    def __init__(self):
        self.cells = ['.'] * 90
        
    def set_from_pieces(self, pieces, flip):
        self.cells = ['.'] * 90
        for sid, face, position, is_open in pieces:
            if position < 0 or position >= 90: continue
            if len(face) >= 2:
                color = face[0]
                piece_type = int(face[1])
                fen_char = self.TYPE_TO_FEN.get(piece_type, '?')
                if color == 'r': fen_char = fen_char.upper()
                self.cells[position] = fen_char
    
    def apply_move(self, source_pos, target_pos):
        if 0 <= source_pos < 90 and 0 <= target_pos < 90:
            if source_pos != target_pos:
                self.cells[target_pos] = self.cells[source_pos]
                self.cells[source_pos] = '.'

class FlipchessAbjchessEngine:
    def __init__(self):
        self.proc = None
        self.binary_path = None
        self.nnue_path = None
        self.engine_lock = threading.Lock()
        self._readyok = False
        self._latest_bestmove = None
        self._engine_searching = False
        self._last_depth = "?"
        self._last_score = "?"
        self._restart_count = 0
        self._stdout_lines = []
        self._lines_lock = threading.Lock()
        self.multipv_info = {}  

        for path in ABJCHESS_BINARY_CANDIDATES:
            if path and os.path.isfile(path) and os.access(path, os.X_OK):
                self.binary_path = path; break
        if not self.binary_path:
            self.engine = False; return
            
        for path in ABJCHESS_NNUE_CANDIDATES:
            if path and os.path.isfile(path):
                self.nnue_path = path; break
        # NNUE is optional for pikafish-jieqi (uses its own default)

        self._init_engine()
        self.engine = self.proc is not None

    def _init_engine(self):
        if self.proc:
            try: self.proc.kill()
            except: pass
            self.proc = None
        try:
            self.proc = subprocess.Popen(
                [self.binary_path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
                cwd=os.path.dirname(self.binary_path),
            )
        except Exception:
            return

        def consume_stderr(proc):
            try:
                while proc.poll() is None:
                    line = proc.stderr.readline()
                    if not line: break
            except: pass
        threading.Thread(target=consume_stderr, args=(self.proc,), daemon=True).start()

        def consume_stdout(proc):
            while True:
                try: line = proc.stdout.readline()
                except: break
                if not line: break
                line = line.strip()
                if not line: continue
                with self._lines_lock:
                    self._stdout_lines.append(line)
                    if len(self._stdout_lines) > 200:
                        self._stdout_lines = self._stdout_lines[-100:]
                        
                if line == "readyok": self._readyok = True
                if line.startswith("bestmove"):
                    self._latest_bestmove = line
                    self._engine_searching = False
                
                if line.startswith("info") and " pv " in line:
                    try:
                        m_move = re.search(r' pv ([a-i]\d[a-i]\d)', line)
                        m_score = re.search(r'score (cp|mate) (-?\d+)', line)
                        if m_move and m_score:
                            mv = m_move.group(1)
                            stype = m_score.group(1)
                            sval = int(m_score.group(2))
                            if stype == "mate": sval = 90000 if sval > 0 else -90000
                            self.multipv_info[mv] = sval
                    except: pass
                        
        threading.Thread(target=consume_stdout, args=(self.proc,), daemon=True).start()

        with self.engine_lock:
            try:
                self.proc.stdin.write("uci\n")
                self.proc.stdin.flush()
            except: return
        if not self._wait_for_line("uciok", timeout=30): return

        with self.engine_lock:
            try:
                _threads = max(1, min(8, (os.cpu_count() or 4)))  # Tang luong tu 2 len 8
                self.proc.stdin.write(f"setoption name Threads value {_threads}\n")
                self.proc.stdin.write("setoption name Hash value 512\n")  # Tang Hash tu 256 len 512
                if self.nnue_path:
                    self.proc.stdin.write(f"setoption name EvalFile value {self.nnue_path}\n")
                self.proc.stdin.write("isready\n")
                self.proc.stdin.flush()

            except:
                return
        self._wait_for_readyok(timeout=30)

    def _wait_for_line(self, prefix, timeout=10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc and self.proc.poll() is not None: return False
            with self._lines_lock:
                for l in self._stdout_lines:
                    if l.startswith(prefix) or l == prefix: return True
            time.sleep(0.05)
        return False

    def _wait_for_readyok(self, timeout=10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._readyok: return True
            if self.proc and self.proc.poll() is not None: return False
            time.sleep(0.05)
        return False

    def alive(self): return self.proc is not None and self.proc.poll() is None

    def restart(self):
        if self._restart_count >= MAX_ENGINE_RESTARTS_PER_GAME: return False
        self._restart_count += 1
        self._init_engine()
        return self.alive()

    # Thêm tham số multipv để Bot linh hoạt thay đổi tùy giai đoạn ván đấu
    def get_best_move(self, fen, moves, movetime_ms=DEFAULT_MOVETIME_MS, searchmoves=None, multipv=1):
        if not self.alive():
            if not self.restart(): return None
        self._latest_bestmove = None
        self._engine_searching = True
        self.multipv_info.clear()  
        
        with self._lines_lock:
            self._stdout_lines.clear()
        try:
            cmd = "position startpos"
            if moves: cmd += " moves " + " ".join(moves)
            go_cmd = "go infinite"
            if searchmoves:
                sm_clean = [m for m in searchmoves if m and len(m) >= 4][:128]
                if sm_clean: go_cmd += " searchmoves " + " ".join(sm_clean)
            
            with self.engine_lock:
                # Gửi lệnh set MultiPV trước khi tính toán
                self.proc.stdin.write(f"setoption name MultiPV value {multipv}\n")
                self.proc.stdin.write(cmd + "\n")
                self.proc.stdin.flush()
                self.proc.stdin.write(go_cmd + "\n")
                self.proc.stdin.flush()
        except: return None
        
        time.sleep(movetime_ms / 1000.0)
        try:
            with self.engine_lock:
                self.proc.stdin.write("stop\n")
                self.proc.stdin.flush()
        except: pass
        
        t0 = time.time()
        while time.time() - t0 < 5.0:
            if self._latest_bestmove:
                self._engine_searching = False
                with self._lines_lock:
                    for l in reversed(self._stdout_lines):
                        if l.startswith("info") and "depth" in l:
                            m = re.search(r'depth (\d+)', l)
                            if m: self._last_depth = m.group(1)
                            sm = re.search(r'score (cp|mate) (-?\d+)', l)
                            if sm:
                                if sm.group(1) == "mate": self._last_score = f"M{sm.group(2)}"
                                else: self._last_score = f"{int(sm.group(2))/100:+.2f}"
                            break
                return self._latest_bestmove
            if not self.alive(): return None
            time.sleep(0.02)
        return self._latest_bestmove


class JieqiCupBot:
    def __init__(self):
        self.conn = Conn()
        self.board = XiangqiBoardTracker()
        self.visible_board = VisibleBoard()
        self.engine = None
        self.ws = None
        self.connected = False
        self.logged_in = False
        self.in_game = False
        self._joining_table = False
        self._last_quick_play_time = 0
        self._QUICK_PLAY_INTERVAL = 3.0
        self.ROOM_LIST = ["0", "1", "2", "3"]
        self._sit_alone_since = None
        self.bet_amts = []
        self._resolved_bet_id = None
        self._bet_amts_loaded = False
        self.last_action_timestamp = time.time()
        self.last_recv_timestamp = time.time()
        self.slot_players = {}
        self._reconnect_streak = 0
        self._connected_since = 0.0
        self._closing = False
        self._keep_alive_started = False
        self.player_names = {}
        self._thinking = False
        self._turn_started_at = 0.0
        self._turn_deadline = 0.0
        self._played_this_turn = False
        self._last_sent_move = None
        self._rejected_moves = set()
        self._play_reject_count = 0
        self._move_lock = threading.Lock()
        self._last_move_uci = None
        self._game_seq = 0
        self._bot_move_count = 0  # Trình theo dõi số nước đi của Bot
        self.engine = FlipchessAbjchessEngine()

    def _is_stale(self, ws):
        if self.ws is None: return False
        return ws is not self.ws

    def _disconnect(self, reason=""):
        if self._closing: return
        self._closing = True
        try:
            if reason: print(f"[WS] 🔌 {reason}")
            if self.ws:
                try: self.ws.close()
                except: pass
            self.connected = False; self.logged_in = False
        finally:
            self._closing = False

    def connect(self):
        import websocket
        if self.ws:
            try: self.ws.close()
            except: pass
        if getattr(self, "ws_thread", None) and self.ws_thread.is_alive():
            self.ws_thread.join(timeout=5)
        self.connected = False
        app = websocket.WebSocketApp(
            WS_URL, cookie=COOKIE,
            on_open=self._on_open, on_message=self._on_message,
            on_error=self._on_error, on_close=self._on_close,
            on_ping=self._on_ping, on_pong=self._on_pong,
            header={"Origin": "https://gamevh.net"})
        self.ws = app
        self.ws_thread = threading.Thread(
            target=lambda: app.run_forever(ping_interval=20, ping_timeout=None), daemon=True)
        self.ws_thread.start()
        for _ in range(75):
            if self.connected: break
            if not self.ws_thread.is_alive(): break
            time.sleep(0.2)
        return self.connected

    def _on_open(self, ws):
        if self._is_stale(ws):
            try: ws.close()
            except: pass
            return
        self.connected = True
        self.last_action_timestamp = time.time()
        self.last_recv_timestamp = time.time()
        self._connected_since = time.time()
        self._send_login()

    def _on_ping(self, ws, message):
        if self._is_stale(ws): return
        self.last_recv_timestamp = time.time()
        try: ws.pong(message)
        except: pass

    def _on_pong(self, ws, message):
        if self._is_stale(ws): return
        self.last_recv_timestamp = time.time()

    def _on_message(self, ws, message):
        if self._is_stale(ws): return
        self.last_recv_timestamp = time.time()
        if isinstance(message, bytes):
            self._decoded_ws_event = None
            try: self._decoded_ws_event = decode_frame(message)
            except: pass
            self._handle_binary_message(message)

    def _on_error(self, ws, error):
        if self._is_stale(ws): return
        self._disconnect(f"error: {error}")

    def _on_close(self, ws, code, msg):
        if self._is_stale(ws): return
        if self._connected_since and time.time() - self._connected_since < 60:
            self._reconnect_streak += 1
        else: self._reconnect_streak = 0
        self.connected = False; self.logged_in = False; self.in_game = False
        self._joining_table = False; self._bet_amts_loaded = False
        self._resolved_bet_id = None; self.bet_amts = []
        self._thinking = False; self._played_this_turn = False
        self.board.reset()

    def send_message(self, cmd, data=b''):
        if self.ws and self.connected:
            try:
                self.ws.send(self.conn.pack(cmd, data), opcode=0x2)
                return True
            except: self._disconnect(f"send fail")
        return False

    def _send_login(self):
        data = bytearray()
        data.extend(self.conn.pack_ascii(CURRENT_PLAYER_NICKNAME))
        data.extend(self.conn.pack_int(TOKEN))
        data.extend(self.conn.pack_ascii("5.0.2"))
        data.extend(self.conn.pack_ascii(""))
        data.extend(self.conn.pack_ascii(GAME_ID))
        data.extend(self.conn.pack_byte(1))
        self.send_message("LOGIN", bytes(data))

    def send_enter_place(self, path=None, mode=1):
        data = bytearray()
        data.extend(self.conn.pack_ascii(path or PLACE_PATH))
        data.extend(self.conn.pack_string(""))
        data.extend(self.conn.pack_byte(mode))
        self.send_message("ENTER_PLACE", bytes(data))

    def send_list_bet_amt(self):
        self.send_message("LIST_BET_AMT")

    def leave_table(self):
        if self.board.is_playing: return
        self.in_game = False; self._joining_table = False
        self.slot_players.clear(); self.board.reset()
        self.send_enter_place(PLACE_PATH)

    def resolve_bet_amt_id(self):
        if not self.bet_amts: return None
        exact = [ba for ba in self.bet_amts if ba["value"] == BOT_BET_XU]
        if exact: return exact[0]['id']
        above = [ba for ba in self.bet_amts if ba["value"] >= BOT_BET_XU]
        if above: return min(above, key=lambda x: x['value'])['id']
        return self.bet_amts[-1]['id'] if self.bet_amts else 0

    def send_create_table(self, bet_amt_id=None):
        now = time.time()
        if now - self._last_quick_play_time < self._QUICK_PLAY_INTERVAL: return
        self._last_quick_play_time = now
        if bet_amt_id is None:
            bet_amt_id = self._resolved_bet_id if self._resolved_bet_id else self.resolve_bet_amt_id()
        if bet_amt_id is None: return
        args = [("matchDuration", str(BOT_MATCH_DURATION)), ("turnDuration", str(BOT_TURN_DURATION)),
                ("accDuration", str(BOT_ACC_DURATION)), ("blockSoftware", str(BOT_BLOCK_SOFTWARE))]
        data = bytearray()
        data.extend(self.conn.pack_byte(bet_amt_id))
        data.extend(self.conn.pack_byte(len(args)))
        for k, v in args:
            data.extend(self.conn.pack_ascii(k))
            data.extend(self.conn.pack_string(v))
        self.send_message("CREATE_RULE", bytes(data))

    def send_play(self, source_pos, target_pos):
        self._played_this_turn = True
        data = bytearray()
        data.extend(self.conn.pack_byte(source_pos))
        data.extend(self.conn.pack_byte(target_pos))
        self.send_message("PLAY", bytes(data))

    def opponent_player_id(self):
        for sid, pid in self.slot_players.items():
            if pid and pid != CURRENT_PLAYER_ID and sid != self.board.my_slot_id: return pid
        return None

    def send_ready(self, is_ready=1):
        if self.board.is_playing: return
        data = bytearray()
        data.extend(self.conn.pack_byte(is_ready))
        self.send_message("SET_READY", bytes(data))

    def _handle_binary_message(self, data):
        try:
            msg = InboundMessage(data)
            cmd = msg.command
            if cmd == "PING": self.send_message("PONG")
            elif cmd == "LOGIN": self._handle_login_response(msg)
            elif cmd == "ENTER_PLACE": self._handle_enter_place_response(msg)
            elif cmd == "QUICK_PLAY" or cmd == "CREATE_RULE": self._handle_join_success(msg)
            elif cmd == "LIST_BET_AMT": self._handle_list_bet_amt_response(msg)
            elif cmd == "SLOT_IN_TABLE_CHANGED": self._handle_slot_changed(msg)
            elif cmd == "PLAYER_ENTERED": self._handle_player_entered(msg)
            elif cmd == "START_MATCH": self._handle_start_match(msg)
            elif cmd == "MOVE": self._handle_move(msg)
            elif cmd == "PLAY" or cmd == "502": self._handle_play_response(msg)
            elif cmd == "SET_TURN": self._handle_set_turn(msg)
            elif cmd == "GAMEOVER": self._handle_gameover(msg)
            elif cmd == "KICK_PLAYER": self._handle_kick_response(msg)
        except Exception: pass

    def _handle_login_response(self, msg):
        status = msg.read_byte()
        if status == 0:
            self.logged_in = True
            path = msg.read_string()
            if path == 'REFRESH':
                if fetch_session_info(): self._send_login()
                else: self._disconnect("session REFRESH failed")
                return
            self.send_enter_place()
        else:
            self._disconnect(f"login failed status={status}")

    def _handle_enter_place_response(self, msg):
        status = msg.read_byte()
        if status != 0:
            if self._joining_table:
                self._joining_table = False; self.in_game = True
                threading.Thread(target=lambda: (time.sleep(1.0), self.send_ready(1)), daemon=True).start()
            return
        if self._joining_table:
            self._joining_table = False; self.in_game = True
            threading.Thread(target=lambda: (time.sleep(1.0), self.send_ready(1)), daemon=True).start()
        elif not self.in_game:
            self.send_list_bet_amt()

    def _handle_join_success(self, msg):
        status = msg.read_byte()
        if status == 0:
            table_path = msg.read_ascii()
            self.in_game = True; self._joining_table = True
            self._sit_alone_since = time.time()
            threading.Thread(target=lambda: (time.sleep(0.2), self.send_enter_place(path=table_path, mode=1)), daemon=True).start()
        else: self._joining_table = False

    def _handle_list_bet_amt_response(self, msg):
        if msg.read_byte() != 0: return
        count = msg.read_byte()
        self.bet_amts = [{"id": i, "value": msg.read_int()} for i in range(count)]
        self._resolved_bet_id = self.resolve_bet_amt_id()
        self._bet_amts_loaded = True

    def _handle_player_entered(self, msg):
        try:
            msg.read_byte(); pid = msg.read_long(); name = msg.read_string()
            if pid > 0 and pid != CURRENT_PLAYER_ID: self.player_names[pid] = name
        except: pass

    def _handle_slot_changed(self, msg):
        try:
            msg.read_string(); slot_id = msg.read_byte()
            msg.read_long(); msg.read_long(); msg.read_byte(); msg.read_short()
            msg.read_ascii(); msg.read_byte(); msg.read_byte()
            player_id = msg.read_long()
            if player_id > 0: self.slot_players[slot_id] = player_id
            else: self.slot_players.pop(slot_id, None)
            
            if player_id == CURRENT_PLAYER_ID: self.board.my_slot_id = slot_id
            else:
                if player_id > 0:
                    self._sit_alone_since = None
                    if not self.board.is_playing:
                        threading.Thread(target=lambda: (time.sleep(1.0), self.send_ready(1)), daemon=True).start()
                else:
                    if not self.board.is_playing and self.opponent_player_id() is None:
                        self._sit_alone_since = time.time()
        except: pass

    def _handle_start_match(self, msg):
        self._game_seq += 1
        print(f"\n[GAME] 🎮 Match #{self._game_seq}")
        self._thinking = False; self._played_this_turn = False
        self._play_reject_count = 0; self._rejected_moves = set()
        self._bot_move_count = 0  # Reset Move Tracker
        self.board.reset()
        self.board.is_playing = True; self.in_game = True
        self._joining_table = False
        self.last_action_timestamp = time.time()
        
        if self.engine and self.engine.alive():
            try:
                with self.engine.engine_lock:
                    self.engine.proc.stdin.write("ucinewgame\n")
                    self.engine.proc.stdin.flush()
            except: pass
            
        try:
            player_count = msg.read_byte()
            for _ in range(player_count): msg.read_byte(); msg.read_int()
            piece_count = msg.read_byte(); board_pieces = []
            parsed_event = getattr(self, "_decoded_ws_event", None)
            parsed_pieces = (parsed_event or {}).get("pieces", [])
            for index in range(piece_count):
                raw_sid = msg.read_byte(); raw_face = msg.read_byte()
                pos = msg.read_byte(); is_open = msg.read_byte()
                if index < len(parsed_pieces):
                    p = parsed_pieces[index]
                    board_pieces.append((p["sid_decoded"], p["face_decoded"], p["position"], p["is_open"]))
                else:
                    board_pieces.append((self._decode_piece_id(raw_sid), self._decode_piece_id(raw_face), pos, is_open))
            
            msg.read_byte(); mystery_count = msg.read_byte()
            for _ in range(mystery_count): msg.read_byte()
            msg.read_byte(); msg.read_byte()
            first_turn_slot_id = msg.read_byte()
            my_slot_id = msg.read_byte()
            if my_slot_id < 0 or my_slot_id == 255:
                my_slot_id = self.board.my_slot_id if self.board.my_slot_id >= 0 else first_turn_slot_id
            
            self.board.set_my_slot(my_slot_id, first_turn_slot_id)
            self.board.detect_flip(board_pieces)
            fen = self._rebuild_fen_with_current_flip(board_pieces)
            
            _ok, _ = self.board.sanity_check_fen(fen)
            if not _ok:
                self.board.flip = not self.board.flip
                fen = self._rebuild_fen_with_current_flip(board_pieces)
                
            self.board.set_base(fen, 'w')
            self.visible_board.set_from_pieces(board_pieces, self.board.flip)
            
            for sid, face, position, is_open in board_pieces:
                if not is_open and 0 <= position < 90: self.board.dark_positions.add(position)
        except: pass

    def _rebuild_fen_with_current_flip(self, pieces):
        board = [['.' for _ in range(9)] for _ in range(10)]
        for sid, face, position, is_open in pieces:
            if position < 0 or position >= 90: continue
            fen_row, col = self.board.pos_to_rc(position)
            if len(face) > 1:
                color = face[0]; piece_type = int(face[1])
                type_to_fen = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}
                fen_char = type_to_fen.get(piece_type, '?')
                if color == 'r': fen_char = fen_char.upper()
            else:
                fen_char = 'X' if sid.startswith('r') else 'x'
            board[fen_row][col] = fen_char
        fen_rows = []
        for row in board:
            fen_row = ""; empty = 0
            for cell in row:
                if cell == '.': empty += 1
                else:
                    if empty > 0: fen_row += str(empty); empty = 0
                    fen_row += cell
            if empty > 0: fen_row += str(empty)
            fen_rows.append(fen_row)
        return '/'.join(fen_rows) + ' w'

    def _handle_move(self, msg):
        with self._move_lock:
            try:
                if not self.board.is_playing: return
                source_pos = msg.read_byte(); target_pos = msg.read_byte()
                if not (0 <= source_pos < 90 and 0 <= target_pos < 90): return
                
                engine_move = self.board.pos_to_engine_move(source_pos, target_pos)
                self.last_action_timestamp = time.time()
                
                event = getattr(self, "_decoded_ws_event", None) or {}
                
                revealed_char = event.get("revealed_piece")
                if revealed_char in ('k', 'K'): revealed_char = None
                captured_char = event.get("captured_piece")
                if captured_char in ('k', 'K'): captured_char = None
                
                self.board.dark_positions.discard(source_pos)
                self.board.dark_positions.discard(target_pos)
                
                full_uci = engine_move + (revealed_char or "") + (captured_char or "")
                self.board.record_move(engine_move, (revealed_char or "") + (captured_char or ""))
                
                src_pos, tgt_pos = self.board.engine_move_to_pos(engine_move)
                self.visible_board.apply_move(src_pos, tgt_pos)
                
                self._last_move_uci = full_uci
                self._played_this_turn = False
            except: pass

    def _handle_play_response(self, msg):
        status = msg.read_byte()
        if status != 0:
            self._play_reject_count += 1
            self.board.is_my_turn = True; self._played_this_turn = False
            if self._last_sent_move: self._rejected_moves.add(self._last_sent_move)
            if self._play_reject_count <= 3:
                threading.Thread(target=lambda: (time.sleep(0.2), self._make_auto_move()), daemon=True).start()
        else:
            self._play_reject_count = 0; self._rejected_moves.clear()

    def _handle_set_turn(self, msg):
        try:
            slot_id = msg.read_byte()
            try: turn_timeout = msg.read_short()
            except: turn_timeout = 0
            if slot_id == -2 or slot_id == -1 or not self.board.is_playing: return
            
            self.board.is_my_turn = (slot_id == self.board.my_slot_id)
            self.last_action_timestamp = time.time()
            if not self.board.is_my_turn: return
            
            self._turn_started_at = time.time()
            self._turn_deadline = self._turn_started_at + max(turn_timeout - 5, 10)
            self._played_this_turn = False
            threading.Thread(target=self._make_auto_move, daemon=True).start()
        except: pass

    def _handle_kick_response(self, msg):
        self.in_game = False; self._joining_table = False
        self.board.reset()

    def _handle_gameover(self, msg):
        self.board.is_playing = False; self.board.is_my_turn = False
        self.board.reset(); self.in_game = True; self._joining_table = False
        self.last_action_timestamp = time.time()
        self._thinking = False; self._played_this_turn = False
        
        if self.engine and self.engine.alive():
            try:
                with self.engine.engine_lock:
                    self.engine.proc.stdin.write("ucinewgame\n")
                    self.engine.proc.stdin.flush()
            except: pass
        threading.Thread(target=lambda: (time.sleep(1.0), self.send_ready(1) if self.connected else None), daemon=True).start()

    def _make_auto_move(self):
        if not self.board.is_my_turn or not self.board.is_playing: return
        if self._thinking: return
        self._thinking = True
        try: self._do_auto_move()
        finally: self._thinking = False


    def _do_auto_move(self):
        if not self.engine or not self.engine.alive(): return
        
        now = time.time()
        deadline = self._turn_deadline if self._turn_deadline > 0 else (now + MOVE_DEADLINE_SECONDS)
        remain = deadline - now
        if remain < 4.0: return
        
        fen, moves = self.board.get_current_fen()
        
        # =======================================================
        # OPENING BOOK: Su dung opening book cho nhung nuoc di dau
        # =======================================================
        if USE_OPENING_BOOK and self._bot_move_count < OPENING_BOOK_MOVES:
            fen_key = fen.split(' ')[0]  # Lay phan board cua FEN
            if fen_key in OPENING_BOOK:
                book_moves = OPENING_BOOK[fen_key]
                if book_moves:
                    # Chon nuoc di ngau nhien tu opening book
                    best_move = random.choice(book_moves)
                    print(f"][OPENING] Using book move: {best_move}")
                    
                    try:
                        source_pos, target_pos = self.board.engine_move_to_pos(best_move)
                        if not (self.board.is_my_turn and self.board.is_playing): return
                        if self._played_this_turn: return
                        self._last_sent_move = best_move
                        self.send_play(source_pos, target_pos)
                        self._bot_move_count += 1
                        return
                    except: pass
        
        # =======================================================
        # QUYẾT ĐỊNH SỬ DỤNG MAPHACK HAY KHÔNG
        # =======================================================
        movetime_ms = DEFAULT_MOVETIME_MS
        use_maphack = self._bot_move_count < MAPHACK_MOVE_LIMIT
        current_multipv = MAPHACK_MULTIPV if use_maphack else 1
        
        if use_maphack:
            print(f"[TACTIC] Bot nước thứ {self._bot_move_count + 1}: Kích hoạt Maphack (MultiPV={current_multipv})")
        else:
            print(f"[TACTIC] Bot nước thứ {self._bot_move_count + 1}: TẮT Maphack, dồn 100% tài nguyên tính toán (MultiPV=1)")

        raw = self.engine.get_best_move(fen, moves, movetime_ms=movetime_ms, multipv=current_multipv)
        
        if not raw: return
        parts = raw.split()
        if len(parts) < 2: return
        best_move = parts[1]
        
        # =======================================================
        # DYNAMIC MOVE TIME: Đi nhanh khi có ưu thế
        # =======================================================
        current_score = self.engine.multipv_info.get(best_move, 0)
        
        # Xac dinh thoi gian suy nghi dua tren diem so - Su dung thong so moi
        if current_score >= 90000:  # Forced mate
            dynamic_movetime = FAST_MATE_MOVETIME  # Su dung thong so tu cau hinh
            print(f"][FAST] Mate found! Score={current_score}, movetime={dynamic_movetime/1000:.1f}s")
        elif current_score >= 500:  # uu the lon (>5 pawns)
            dynamic_movetime = FAST_BIG_ADV_MOVETIME
            print(f"][FAST] Uu the lon! Score={current_score/100:+.1f}, movetime={dynamic_movetime/1000:.1f}s")
        elif current_score >= 300:  # uu the va (>3 pawns)
            dynamic_movetime = FAST_MED_ADV_MOVETIME
            print(f"][FAST] Uu the va! Score={current_score/100:+.1f}, movetime={dynamic_movetime/1000:.1f}s")
        elif current_score >= 150:  # uu the nho (>1.5 pawns)
            dynamic_movetime = FAST_SMALL_ADV_MOVETIME
            print(f"][FAST] Uu the nho! Score={current_score/100:+.1f}, movetime={dynamic_movetime/1000:.1f}s")
        else:  # Binh thuong
            dynamic_movetime = NORMAL_MOVETIME
            
        # Cap nhat movetime
        movetime_ms = min(movetime_ms, dynamic_movetime)
        
        # =======================================================
        # AP DUNG MAPHACK FILTER (Chi khi use_maphack = True)
        # =======================================================
        if use_maphack:
            candidates = dict(self.engine.multipv_info)
            if best_move not in candidates: candidates[best_move] = 0

            best_hacked_move = best_move
            best_hacked_score = -float('inf')

            # Gia tri quan co cho Maphack Filter
            PIECE_VALS = {'r': 900, 'c': 450, 'n': 350, 'b': 200, 'a': 200, 'p': 100, 'k': 0}
            EXPECTED_VAL = 200  # Gia tri trung binh du kien
            FLIP_BONUS = 50     # Thuong nho cho nuoc lat quan

            for mv, base_score in candidates.items():
                if mv in self._rejected_moves: continue
                hacked_score = base_score
                try:
                    src_pos, tgt_pos = self.board.engine_move_to_pos(mv)
                    mover_dark = src_pos in self.board.dark_positions
                    is_flip = (src_pos == tgt_pos)
                    captured_dark = (not is_flip) and (tgt_pos in self.board.dark_positions)

                    if mover_dark or is_flip:
                        true_char = self.visible_board.cells[src_pos]
                        if true_char != '.':
                            val = PIECE_VALS.get(true_char.lower(), EXPECTED_VAL)
                            hacked_score += (val - EXPECTED_VAL)
                        elif is_flip:
                            hacked_score += FLIP_BONUS

                    if captured_dark:
                        true_char = self.visible_board.cells[tgt_pos]
                        if true_char != '.':
                            val = PIECE_VALS.get(true_char.lower(), EXPECTED_VAL)
                            hacked_score += (val - EXPECTED_VAL)
                        else:
                            hacked_score += FLIP_BONUS // 2
                except: pass

                if hacked_score > best_hacked_score:
                    best_hacked_score = hacked_score
                    best_hacked_move = mv

            # Chi doi bestmove neu chenh lech diem khong qua lon (tranh override khi engine chac chan)
            score_diff = best_hacked_score - (self.engine.multipv_info.get(best_move, 0) if hasattr(self.engine, 'multipv_info') else 0)
            if best_hacked_move and best_hacked_move != best_move and abs(score_diff) < 300:
                print(f"][MAPHACK] Doi bestmove: {best_move} -> {best_hacked_move} (Diem tong hop: {best_hacked_score})")
            elif best_hacked_move and best_hacked_move != best_move:
                print(f"][MAPHACK] Giu bestmove: {best_move} (chenh lech qua lon: {score_diff})")
                best_hacked_move = best_move
            if best_hacked_move:
                best_move = best_hacked_move
        # =======================================================
        
        if best_move in ("(none)", "0000"):
            self.board.is_my_turn = False; return
            
        try:
            source_pos, target_pos = self.board.engine_move_to_pos(best_move)
            _turn_start = self._turn_started_at if self._turn_started_at > 0 else time.time()
            # Dynamic MIN_MOVE: di nhanh hon khi co uu the - Su dung thong so moi
            if current_score >= 90000:
                dynamic_min = 0.3  # Mate: cho 0.3s
            elif current_score >= 500:
                dynamic_min = 0.8  # uu the lon: cho 0.8s
            elif current_score >= 300:
                dynamic_min = 1.2  # uu the vua: cho 1.2s
            elif current_score >= 150:
                dynamic_min = 1.5  # uu the nho: cho 1.5s
            else:
                dynamic_min = MIN_MOVE_SECONDS  # binh thuong: cho toi thieu
            _remain_min = dynamic_min - (time.time() - _turn_start)
            if _remain_min > 0: time.sleep(_remain_min)
            
            if not (self.board.is_my_turn and self.board.is_playing): return
            if self._played_this_turn: return
            
            self._last_sent_move = best_move
            self.send_play(source_pos, target_pos)
            self._bot_move_count += 1  # Tang so nuoc di da di
        except: pass

    def start_keep_alive(self):
        if self._keep_alive_started: return
        self._keep_alive_started = True
        def loop():
            while True:
                time.sleep(10)
                if self.connected: self.send_message("PING")
        threading.Thread(target=loop, daemon=True).start()

    def run(self):
        while True:
            try:
                now_ts = time.time()
                if self.connected:
                    idle = now_ts - self.last_recv_timestamp
                    idle_limit = 300 if self.board.is_playing else 600
                    if idle > idle_limit: self._disconnect()
                    elif self._connected_since and not self.logged_in and now_ts - self._connected_since > 45:
                        self._disconnect()

                if not self.connected:
                    if self._reconnect_streak > 0:
                        time.sleep(min(60, 5 * (2 ** min(self._reconnect_streak - 1, 4))))
                    if not fetch_session_info(): time.sleep(5); continue
                    self.logged_in = False; self.in_game = False
                    self._joining_table = False; self._bet_amts_loaded = False
                    self.board.reset()
                    if not self.connect(): time.sleep(5); continue
                    self.start_keep_alive(); time.sleep(2)

                if (self.board.is_playing and self.board.is_my_turn and self._turn_deadline > 0
                        and time.time() > self._turn_deadline - 4.0 and not self._played_this_turn):
                    if not self._thinking:
                        threading.Thread(target=self._make_auto_move, daemon=True).start()

                if self.board.is_playing: self._sit_alone_since = None
                else:
                    if self.in_game and not self._joining_table:
                        if self.opponent_player_id() is None:
                            if self._sit_alone_since is None: self._sit_alone_since = time.time()
                            elif time.time() - self._sit_alone_since >= SIT_ALONE_TIMEOUT: self.leave_table()
                        else: self._sit_alone_since = None

                if self.connected and self.logged_in and not self.in_game and not self._joining_table:
                    if time.time() - self._last_quick_play_time >= self._QUICK_PLAY_INTERVAL:
                        if not self._bet_amts_loaded: self.send_list_bet_amt()
                        elif BOT_USE_CREATE_TABLE:
                            bid = self._resolved_bet_id if self._resolved_bet_id else self.resolve_bet_amt_id()
                            self.send_create_table(bet_amt_id=bid)
                time.sleep(1)
            except KeyboardInterrupt: break
            except: time.sleep(5)

    def cleanup(self):
        if self.engine:
            try:
                if self.engine.proc:
                    self.engine.proc.stdin.write("quit\n")
                    self.engine.proc.stdin.flush()
            except: pass
        if self.ws:
            try: self.ws.close()
            except: pass

def acquire_single_instance_lock():
    try:
        import fcntl
        path = os.path.join(tempfile.gettempdir(), f"xiangqi_bot_{USER}.lock")
        f = open(path, "w")
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except: sys.exit(1)
        f.write(str(os.getpid())); f.flush()
        atexit.register(lambda: (fcntl.flock(f, fcntl.LOCK_UN), f.close()))
        return f
    except: return None

if __name__ == "__main__":
    _lock = acquire_single_instance_lock()
    bot = JieqiCupBot()
    def signal_handler(sig, frame):
        bot.cleanup(); sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    try: bot.run()
    finally: bot.cleanup()
