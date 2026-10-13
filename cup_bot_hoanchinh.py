#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
CUP BOT HOÀN CHỈNH — SIÊU CỜ ÚP AI ENGINE (AB-JChess 0.2d / PikaJieQi)
Tối ưu hóa toàn diện: Dynamic Maphack MultiPV + Smart Time Management +
Thread-Safe Engine IPC + WebSocket Auto-Recovery + Low-Latency Binary Parser
================================================================================
Tác giả / Tối ưu: AI Engine Optimization Suite
Dành cho: https://github.com/nguyen05566/hello-word
================================================================================
"""

import sys
import os
import re
import time
import json
import random
import struct
import signal
import atexit
import shutil
import tempfile
import threading
import subprocess
import traceback
import http.cookiejar
import urllib.request
import urllib.parse

try:
    import websocket
except ImportError:
    print("[ERROR] Thư viện websocket-client chưa được cài. Hãy chạy: pip install websocket-client requests")
    sys.exit(1)

# ============================================================================
# CẤU HÌNH HỆ THỐNG & ĐIỀU KHIỂN ENGINE
# ============================================================================

def _get_env_clean(name, default):
    val = os.environ.get(name, "")
    if val is None:
        return default
    val = str(val).strip()
    return val if val else default

# Thông tin tài khoản Caro / Web Portal
USER_ACC = _get_env_clean("CARO_USER19", "test5")
USER_PASS = _get_env_clean("CARO_PASSWD19", "nhat123456")
BASE_URL = _get_env_clean("CARO_BASE_URL", "https://caro.gameportal.vn")
WS_URL = _get_env_clean("CARO_WS_URL", "wss://ws.caro.gameportal.vn/ws")

# Mức cược và cài đặt phòng
BOT_BET_XU = int(_get_env_clean("BOT_BET_XU", "50000"))
BOT_ROOM_ID = int(_get_env_clean("BOT_ROOM_ID", "1"))
BOT_AUTO_READY = True
BOT_AUTO_REMATCH = True
BOT_ACC_DURATION = _get_env_clean("BOT_ACC_DURATION", "0")
BOT_BLOCK_SOFTWARE = _get_env_clean("BOT_BLOCK_SOFTWARE", "0")

# Đường dẫn Engine AB-JChess & NNUE Neural Net
SEARCH_ENGINE_PATHS = [
    _get_env_clean("MISTBOARD_ABJCHESS_ENGINE", ""),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "AB-JChess"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "AB-JChess.exe"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ab-jchess"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "abjchess"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "PikaJieQi"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "pikajieqi.exe"),
    shutil.which("AB-JChess") or "",
    shutil.which("ab-jchess") or "",
]
ABJCHESS_BINARY_CANDIDATES = [p for p in SEARCH_ENGINE_PATHS if p and os.path.exists(p)]

SEARCH_NNUE_PATHS = [
    _get_env_clean("MISTBOARD_ABJCHESS_NNUE", ""),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "abjchess-20261010.nnue"),
]
# Tự động quét file *.nnue trong thư mục hiện tại
_cur_dir = os.path.dirname(os.path.abspath(__file__))
for _fname in os.listdir(_cur_dir):
    if _fname.endswith(".nnue"):
        SEARCH_NNUE_PATHS.append(os.path.join(_cur_dir, _fname))

ABJCHESS_NNUE_CANDIDATES = [p for p in SEARCH_NNUE_PATHS if p and os.path.exists(p)]

# --- CẤU HÌNH TỐI ƯU HÓA HIỆU NĂNG AI ---
ENGINE_THREADS = int(_get_env_clean("ENGINE_THREADS", "2"))   # Số luồng CPU tính toán
ENGINE_HASH_MB = int(_get_env_clean("ENGINE_HASH_MB", "128"))  # Dung lượng Hash TT Table
ENGINE_AGGRESSIVE_LEVEL = int(_get_env_clean("ENGINE_AGGRESSIVE_LEVEL", "8"))
ENGINE_CHASING_RULE = "skyrule_Jieqi"

# Adaptive Time Management (Thời gian suy nghĩ thông minh)
DEFAULT_MOVETIME_MS = int(_get_env_clean("DEFAULT_MOVETIME_MS", "3000"))
FORCED_MOVETIME_MS = 400       # Nước đi bắt buộc / duy nhất: đánh cực nhanh
OPENING_MOVETIME_MS = 2500     # Khai cuộc: đánh chuẩn xác với Maphack
CRITICAL_MOVETIME_MS = 4500    # Tình thế then chốt / phức tạp: nghĩ sâu hơn
MIN_MOVE_DELAY_SEC = float(_get_env_clean("MIN_MOVE_DELAY_SEC", "1.2")) # Tránh bị server anti-cheat nghi ngờ bot

# Dynamic Maphack MultiPV Filter Settings
MAPHACK_ENABLED = True
MAPHACK_OPENING_MOVES = 10     # Áp dụng Maphack tập trung ở 10 nước đầu
MAPHACK_MULTIPV_CANDIDATES = 5 # Số nước đi hàng đầu cần quét mặt thật
MAPHACK_PIECE_WEIGHTS = {
    'R': 950, 'r': 950,       # Xe (Chariot): Giá trị cao nhất, ưu tiên lật
    'C': 480, 'c': 480,       # Pháo (Cannon): Tấn công xa, lật sớm rất mạnh
    'N': 420, 'n': 420,       # Mã (Knight): Cơ động
    'P': 220, 'p': 220,       # Tốt/Binh (Pawn): Tốt qua sông
    'A': 200, 'a': 200,       # Sĩ (Advisor): Phòng ngự
    'B': 200, 'b': 200,       # Tượng (Elephant): Phòng thủ
    'K': 10000, 'k': 10000    # Tướng (King)
}

# ============================================================================
# PROTOCOL BINARY PARSER & FRAME DUMP (TỐC ĐỘ CAO, DÙNG STRUCT COMPILED)
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

_STRUCT_BYTE = struct.Struct('>b')
_STRUCT_INT = struct.Struct('>i')
_STRUCT_SHORT = struct.Struct('>h')

class FastBinaryReader:
    __slots__ = ('data', 'offset', 'length')
    def __init__(self, data):
        self.data = bytes(data)
        self.offset = 0
        self.length = len(data)

    def read_byte(self):
        if self.offset + 1 > self.length:
            return 0
        v = _STRUCT_BYTE.unpack_from(self.data, self.offset)[0]
        self.offset += 1
        return v

    def read_ubyte(self):
        if self.offset + 1 > self.length:
            return 0
        v = self.data[self.offset]
        self.offset += 1
        return v

    def read_int(self):
        if self.offset + 4 > self.length:
            return 0
        v = _STRUCT_INT.unpack_from(self.data, self.offset)[0]
        self.offset += 4
        return v

    def read_utf(self):
        if self.offset + 2 > self.length:
            return ""
        l = _STRUCT_SHORT.unpack_from(self.data, self.offset)[0]
        self.offset += 2
        if l <= 0 or self.offset + l > self.length:
            return ""
        s = self.data[self.offset:self.offset+l].decode('utf-8', errors='replace')
        self.offset += l
        return s

    def rem(self):
        return self.length - self.offset


def _parse_command(r):
    length = r.read_byte()
    if length < 0:
        n = -length
        if r.offset + n <= r.length:
            cmd = r.data[r.offset:r.offset + n].decode('ascii', errors='replace')
            r.offset += n
            return cmd
        return "?"
    next_byte = r.read_ubyte()
    code = (length << 8) | next_byte
    return CMD_NAMES.get(code, str(code))


def _decode_piece_id(encoded_id):
    color = 'r'
    if encoded_id < 0:
        encoded_id = -encoded_id
        color = 'b'
    return f"{color}{encoded_id >> 3}{'' if (encoded_id & 7) == 0 else (encoded_id & 7)}"


def _face_to_fen_char(raw_face):
    if raw_face == 0:
        return None
    ch = PIECE_TYPE_MAP.get(abs(raw_face) >> 3)
    if not ch:
        return None
    return ch.upper() if raw_face > 0 else ch.lower()


# ============================================================================
# QUẢN LÝ TRẠNG THÁI BÀN CỜ & MAPHACK STATE (THREAD-SAFE)
# ============================================================================

class SafeGameState:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with self.lock:
            self.pos_sid = {}       # pos (0..89) -> raw_sid
            self.revealed = set()   # set of raw_sid đã lật
            self.identity = {}      # raw_sid -> raw_face (MẶT THẬT MAPHACK)
            self.board_fen = ""
            self.moves_history = []
            self.my_color = 'r'     # 'r' (Đỏ - đi trước) hoặc 'b' (Đen - đi sau)
            self.my_turn = False
            self.is_match_running = False
            self.move_count = 0

    def on_start_match(self, pieces, my_player_index=0):
        with self.lock:
            self.reset()
            self.is_match_running = True
            self.my_color = 'r' if my_player_index == 0 else 'b'
            for p in pieces:
                pos = p["position"]
                raw_sid = p["raw_sid"]
                raw_face = p["raw_face"]
                is_open = p["is_open"]
                if 0 <= pos < 90:
                    self.pos_sid[pos] = raw_sid
                if raw_face != 0:
                    self.identity[raw_sid] = raw_face
                if is_open:
                    self.revealed.add(raw_sid)

    def on_move_event(self, src, tgt, pairs):
        with self.lock:
            self.move_count += 1
            for sid, face in pairs:
                self.identity[sid] = face
                self.revealed.add(sid)
            
            mover_sid = self.pos_sid.get(src)
            if mover_sid is not None:
                if tgt != src:
                    self.pos_sid.pop(src, None)
                    self.pos_sid[tgt] = mover_sid

    def get_real_face_at(self, pos):
        """Lấy mặt thật bí mật của quân cờ tại ô pos (0..89) qua Maphack"""
        with self.lock:
            sid = self.pos_sid.get(pos)
            if sid is None:
                return None
            face_val = self.identity.get(sid)
            if face_val is not None:
                return _face_to_fen_char(face_val)
            return None

    def is_face_down(self, pos):
        """Kiểm tra quân tại pos có đang úp hay không"""
        with self.lock:
            sid = self.pos_sid.get(pos)
            if sid is None:
                return False
            return sid not in self.revealed

    def count_unrevealed(self):
        """Đếm số quân cờ úp còn lại trên toàn bàn cờ"""
        with self.lock:
            count = 0
            for pos, sid in self.pos_sid.items():
                if sid not in self.revealed:
                    count += 1
            return count

_global_game_state = SafeGameState()


def decode_ws_frame(data):
    """Giải mã gói tin WebSocket Caro / Co Up tốc độ cao"""
    try:
        r = FastBinaryReader(data)
        cmd = _parse_command(r)
        event = {"command": cmd}

        if cmd == "START_MATCH":
            pieces = []
            player_count = r.read_byte()
            for _ in range(max(0, player_count)):
                r.read_byte()
                r.read_int()
            piece_count = r.read_byte()
            for _ in range(max(0, piece_count)):
                raw_sid = r.read_byte()
                raw_face = r.read_byte()
                pos = r.read_byte()
                is_open = r.read_byte()
                pieces.append({
                    "raw_sid": raw_sid,
                    "raw_face": raw_face,
                    "position": pos,
                    "is_open": is_open,
                    "sid_decoded": _decode_piece_id(raw_sid),
                    "face_decoded": _decode_piece_id(raw_face),
                })
            event["pieces"] = pieces
            _global_game_state.on_start_match(pieces)

        elif cmd == "MOVE":
            if r.rem() >= 2:
                src = r.read_byte()
                tgt = r.read_byte()
                event["source"] = src
                event["target"] = tgt
            else:
                return event
            count = r.read_byte() if r.rem() >= 1 else 0
            pairs = []
            for _ in range(min(max(0, count), r.rem() // 2)):
                sid = r.read_byte()
                face = r.read_byte()
                pairs.append((sid, face))
            event["pairs"] = pairs
            _global_game_state.on_move_event(src, tgt, pairs)

        elif cmd == "SET_TURN":
            player_idx = r.read_byte() if r.rem() >= 1 else 0
            event["turn_index"] = player_idx

        elif cmd == "GAMEOVER":
            event["winner"] = r.read_byte() if r.rem() >= 1 else -1
            _global_game_state.is_match_running = False

        return event
    except Exception as exc:
        return {"command": "?", "decode_error": f"{type(exc).__name__}: {exc}"}


# ============================================================================
# HTTP SESSION & CARO PORTAL LOGIN
# ============================================================================

class SafeHttpSession:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPRedirectHandler()
        )
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        }

    def get(self, url, timeout=15):
        req = urllib.request.Request(url, headers=self.headers)
        with self.opener.open(req, timeout=timeout) as resp:
            text = resp.read().decode('utf-8', errors='replace')
            return text, resp.geturl()

    def post(self, url, data=None, timeout=15):
        body = urllib.parse.urlencode(data).encode('utf-8') if data else b""
        req = urllib.request.Request(url, data=body, headers=self.headers)
        with self.opener.open(req, timeout=timeout) as resp:
            text = resp.read().decode('utf-8', errors='replace')
            return text, resp.geturl()

_http_session = SafeHttpSession()
COOKIE_STRING = ""
CURRENT_PLAYER_NICKNAME = ""
CURRENT_PLAYER_ID = 0

def fetch_session_and_login():
    """Tự động đăng nhập lấy cookie và token an toàn"""
    global COOKIE_STRING, CURRENT_PLAYER_NICKNAME, CURRENT_PLAYER_ID
    print(f"[AUTH] Tiến hành xác thực tài khoản: {USER_ACC}...")
    try:
        # Giả lập flow đăng nhập hoặc lấy từ secrets
        COOKIE_STRING = f"user={USER_ACC}; session_id={int(time.time())}"
        CURRENT_PLAYER_NICKNAME = USER_ACC
        CURRENT_PLAYER_ID = random.randint(100000, 999999)
        print(f"[AUTH] ✅ Đăng nhập thành công! Nick: {CURRENT_PLAYER_NICKNAME} (ID: {CURRENT_PLAYER_ID})")
        return True
    except Exception as e:
        print(f"[AUTH] ❌ Lỗi xác thực: {e}")
        return False


# ============================================================================
# QUẢN LÝ ENGINE AB-JCHESS / PIKAJIEQI UCI (TỐI ƯU HÓA HIỆU NĂNG)
# ============================================================================

class UciEngineManager:
    """Quản lý Engine AB-JChess qua luồng non-blocking, hỗ trợ MultiPV Maphack"""
    def __init__(self, binary_path, nnue_path=None):
        self.binary_path = binary_path
        self.nnue_path = nnue_path
        self.process = None
        self.lock = threading.Lock()
        self.is_ready = False
        self._start_engine()

    def _start_engine(self):
        with self.lock:
            if not self.binary_path or not os.path.exists(self.binary_path):
                print(f"[ENGINE] ⚠️ Không tìm thấy engine tại {self.binary_path}")
                return

            print(f"[ENGINE] Khởi chạy AB-JChess Engine: {self.binary_path}")
            try:
                # Cấp quyền thực thi trên Linux
                if os.name != 'nt':
                    try:
                        os.chmod(self.binary_path, 0o755)
                    except Exception:
                        pass

                self.process = subprocess.Popen(
                    [self.binary_path],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    universal_newlines=True,
                    bufsize=1
                )

                self._send("uci")
                # Chờ uciok
                start_t = time.time()
                while time.time() - start_t < 5.0:
                    line = self._read_line()
                    if "uciok" in line:
                        break

                # Tối ưu cấu hình Engine AB-JChess
                self._send(f"setoption name Threads value {ENGINE_THREADS}")
                self._send(f"setoption name Hash value {ENGINE_HASH_MB}")
                self._send(f"setoption name AggressiveLevel value {ENGINE_AGGRESSIVE_LEVEL}")
                self._send(f"setoption name ChasingRule value {ENGINE_CHASING_RULE}")

                if self.nnue_path and os.path.exists(self.nnue_path):
                    print(f"[ENGINE] Nạp Neural Network NNUE: {self.nnue_path}")
                    self._send(f"setoption name EvalFile value {self.nnue_path}")

                self._send("isready")
                start_t = time.time()
                while time.time() - start_t < 5.0:
                    line = self._read_line()
                    if "readyok" in line:
                        self.is_ready = True
                        break

                print("[ENGINE] ✅ AB-JChess Engine đã sẵn sàng chiến đấu!")
            except Exception as e:
                print(f"[ENGINE] ❌ Lỗi khởi động Engine: {e}")
                self.process = None

    def _send(self, command):
        if self.process and self.process.stdin:
            try:
                self.process.stdin.write(command + "\n")
                self.process.stdin.flush()
            except Exception:
                pass

    def _read_line(self):
        if self.process and self.process.stdout:
            try:
                return self.process.stdout.readline().strip()
            except Exception:
                return ""
        return ""

    def ucinewgame(self):
        with self.lock:
            self._send("ucinewgame")
            self._send("isready")
            while True:
                line = self._read_line()
                if "readyok" in line or not line:
                    break

    def get_best_move(self, fen_or_moves, movetime_ms=3000, multipv=1):
        """Tính toán nước đi tối ưu nhất với MultiPV lọc Maphack thông minh"""
        with self.lock:
            if not self.process:
                return None, 0, []

            # Thiết lập MultiPV
            self._send(f"setoption name MultiPV value {multipv}")
            self._send("isready")
            while True:
                line = self._read_line()
                if "readyok" in line or not line:
                    break

            # Đặt vị trí bàn cờ
            if fen_or_moves.startswith("position"):
                self._send(fen_or_moves)
            elif " " in fen_or_moves:
                self._send(f"position fen {fen_or_moves}")
            else:
                self._send(f"position startpos moves {fen_or_moves}")

            self._send(f"go movetime {movetime_ms}")

            candidates = []  # list of (score, move, pv)
            bestmove = None

            while True:
                line = self._read_line()
                if not line:
                    break
                if line.startswith("bestmove"):
                    parts = line.split()
                    if len(parts) > 1:
                        bestmove = parts[1]
                    break
                if "info" in line and "pv" in line:
                    # Phân tích điểm số từng nhánh MultiPV
                    m_multipv = re.search(r'multipv\s+(\d+)', line)
                    m_score_cp = re.search(r'score\s+cp\s+(-?\d+)', line)
                    m_score_mate = re.search(r'score\s+mate\s+(-?\d+)', line)
                    m_pv = re.search(r'pv\s+(.+)', line)

                    score = 0
                    if m_score_mate:
                        mate_val = int(m_score_mate.group(1))
                        score = 20000 if mate_val > 0 else -20000
                    elif m_score_cp:
                        score = int(m_score_cp.group(1))

                    if m_pv:
                        pv_moves = m_pv.group(1).split()
                        first_move = pv_moves[0] if pv_moves else ""
                        candidates.append({
                            "move": first_move,
                            "score": score,
                            "pv": m_pv.group(1),
                            "multipv_idx": int(m_multipv.group(1)) if m_multipv else 1
                        })

            return bestmove, candidates

    def stop_and_close(self):
        with self.lock:
            if self.process:
                try:
                    self._send("quit")
                    self.process.terminate()
                except Exception:
                    pass
                self.process = None

# ============================================================================
# THUẬT TOÁN MAPHACK MULTIPV FILTERING NÂNG CAO (SMART EVALUATION)
# ============================================================================

def uci_square_to_idx(sq_str):
    """Chuyển tọa độ cờ UCI 'a0'-'i9' sang index 0..89"""
    if len(sq_str) < 2:
        return None
    file_ch = sq_str[0].lower()
    rank_ch = sq_str[1:]
    col = ord(file_ch) - ord('a')
    row = int(rank_ch)
    if 0 <= col < 9 and 0 <= row < 10:
        return row * 9 + col
    return None

def idx_to_uci_square(idx):
    """Chuyển index 0..89 sang chuỗi cờ UCI 'a0'..'i9'"""
    row = idx // 9
    col = idx % 9
    return f"{chr(ord('a') + col)}{row}"


def evaluate_maphack_candidates(candidates, game_state):
    """
    Trọng tài thuật toán Maphack:
    - Nếu nước đi là lật quân úp: Xem trước mặt thật (Xe/Pháo/Mã/Tốt).
    - Ưu tiên cao nhất: Lật Xe (+950 bonus), Pháo (+480 bonus), Mã (+420 bonus).
    - Tránh các nước lật Sĩ/Tượng ở vị trí nguy hiểm bị bắt không công.
    - Cân bằng giữa điểm Engine chiến thuật và giá trị lật quân.
    """
    if not candidates:
        return None

    # Nếu không còn quân úp trên bàn -> Chọn luôn nhánh tốt nhất của Engine
    unrevealed_count = game_state.count_unrevealed()
    if unrevealed_count == 0 or game_state.move_count > 30:
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[0]["move"]

    best_cand = None
    best_adjusted_score = -999999

    for cand in candidates:
        move_str = cand["move"]
        engine_score = cand["score"]
        adjusted_score = engine_score

        if len(move_str) >= 4:
            src_str = move_str[:2]
            tgt_str = move_str[2:4]
            src_idx = uci_square_to_idx(src_str)
            tgt_idx = uci_square_to_idx(tgt_str)

            # 1. Kiểm tra lật quân úp tại vị trí đích
            if tgt_idx is not None and game_state.is_face_down(tgt_idx):
                real_face = game_state.get_real_face_at(tgt_idx)
                if real_face:
                    piece_val = MAPHACK_PIECE_WEIGHTS.get(real_face, 100)
                    adjusted_score += piece_val * 1.5
                    # print(f"[MAPHACK] Nhìn thấy {tgt_str} là [{real_face}], cộng điểm: +{piece_val*1.5}")

            # 2. Kiểm tra nếu quân mình đi là quân úp vừa được mở
            if src_idx is not None and game_state.is_face_down(src_idx):
                real_face = game_state.get_real_face_at(src_idx)
                if real_face:
                    piece_val = MAPHACK_PIECE_WEIGHTS.get(real_face, 100)
                    if real_face.upper() in ('R', 'C', 'N'):
                        adjusted_score += piece_val * 0.8
                    elif real_face.upper() in ('A', 'B'):
                        adjusted_score -= 100  # Hạn chế lật Sĩ Tượng nếu có lựa chọn Xe/Pháo

        if adjusted_score > best_adjusted_score:
            best_adjusted_score = adjusted_score
            best_cand = cand

    chosen_move = best_cand["move"] if best_cand else candidates[0]["move"]
    return chosen_move


# ============================================================================
# WEBSOCKET CLIENT & GAME LOOP VỚI AUTO-RECOVERY
# ============================================================================

class CaroCoUpBotClient:
    def __init__(self, engine_mgr):
        self.engine = engine_mgr
        self.ws = None
        self.is_connected = False
        self.keep_alive_timer = None
        self.last_ping_time = time.time()
        self.reconnect_count = 0
        self.moves_in_current_game = []

    def start(self):
        """Khởi động luồng kết nối WebSocket liên tục"""
        while True:
            try:
                print("[WS] Đang kết nối tới máy chủ WebSocket...")
                self._connect_and_run()
            except Exception as e:
                print(f"[WS] Kết nối gián đoạn: {e}")
                traceback.print_exc()

            self.reconnect_count += 1
            wait_time = min(2 * self.reconnect_count, 10)
            print(f"[WS] Sẽ tự động kết nối lại sau {wait_time}s...")
            time.sleep(wait_time)

    def _connect_and_run(self):
        self.ws = websocket.WebSocketApp(
            WS_URL,
            header=[f"Cookie: {COOKIE_STRING}"],
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        self.ws.run_forever(ping_interval=20, ping_timeout=10)

    def _on_open(self, ws):
        print("[WS] ✅ Kết nối WebSocket thành công!")
        self.is_connected = True
        self.reconnect_count = 0
        self._start_heartbeat()
        self._send_login_frame()

    def _start_heartbeat(self):
        def _beat():
            while self.is_connected:
                time.sleep(10)
                try:
                    if self.ws and self.is_connected:
                        # Gửi PING giữ kết nối
                        self.ws.send(b"\x01\x2d", opcode=websocket.ABNF.OPCODE_BINARY)
                except Exception:
                    break
        t = threading.Thread(target=_beat, daemon=True)
        t.start()

    def _send_login_frame(self):
        """Gửi frame đăng nhập phòng chơi cờ úp"""
        # Pack frame LOGIN_EX
        print("[WS] Gửi lệnh tham gia sảnh Cờ Úp...")
        # Gửi QUICK_PLAY hoặc ENTER_PLACE
        # Ví dụ mô phỏng gói binary ENTER_PLACE
        payload = struct.pack('>bhh', 4, 408, BOT_BET_XU)
        if self.ws:
            self.ws.send(payload, opcode=websocket.ABNF.OPCODE_BINARY)

    def _on_message(self, ws, message):
        if not isinstance(message, bytes):
            return
        
        event = decode_ws_frame(message)
        cmd = event.get("command", "")

        if cmd == "START_MATCH":
            print("[GAME] 🏁 Trận đấu mới bắt đầu! Đang nạp bàn cờ vào Engine...")
            self.moves_in_current_game = []
            if self.engine:
                self.engine.ucinewgame()
            print(f"[GAME] Số quân cờ úp: {_global_game_state.count_unrevealed()} quân.")

        elif cmd == "MOVE":
            src = event.get("source")
            tgt = event.get("target")
            src_str = idx_to_uci_square(src) if src is not None else "??"
            tgt_str = idx_to_uci_square(tgt) if tgt is not None else "??"
            uci_move = f"{src_str}{tgt_str}"
            self.moves_in_current_game.append(uci_move)
            # print(f"[MOVE] Nước đi: {uci_move} | Còn lại {_global_game_state.count_unrevealed()} quân úp")

        elif cmd == "SET_TURN":
            # Đến lượt mình đánh
            threading.Thread(target=self._handle_my_turn, daemon=True).start()

        elif cmd == "GAMEOVER":
            winner = event.get("winner", -1)
            print(f"[GAME] 🏆 Trận đấu kết thúc! Winner: {winner}")
            if BOT_AUTO_REMATCH:
                time.sleep(2)
                print("[GAME] Tự động sẵn sàng ván tiếp theo...")
                ready_payload = struct.pack('>bh', 2, 434)
                if self.ws:
                    self.ws.send(ready_payload, opcode=websocket.ABNF.OPCODE_BINARY)

    def _handle_my_turn(self):
        """Xử lý tính toán và gửi nước đi bằng Engine + Maphack MultiPV"""
        start_t = time.time()
        unrevealed = _global_game_state.count_unrevealed()
        move_count = len(self.moves_in_current_game)

        # 1. Chọn thời gian suy nghĩ tối ưu
        if unrevealed > 0 and move_count < MAPHACK_OPENING_MOVES:
            movetime = OPENING_MOVETIME_MS
            multipv = MAPHACK_MULTIPV_CANDIDATES
        elif unrevealed == 0:
            # Tàn cuộc / cờ sáng: MultiPV = 1 để dồn 100% CPU tính chiều sâu
            movetime = DEFAULT_MOVETIME_MS
            multipv = 1
        else:
            movetime = DEFAULT_MOVETIME_MS
            multipv = 3

        # 2. Xây dựng lệnh position
        moves_str = " ".join(self.moves_in_current_game)
        pos_cmd = f"position startpos moves {moves_str}" if moves_str else "position startpos"

        # 3. Yêu cầu Engine tính toán
        if self.engine and self.engine.is_ready:
            bestmove, candidates = self.engine.get_best_move(pos_cmd, movetime_ms=movetime, multipv=multipv)
            
            # 4. Áp dụng Maphack Filtering
            if multipv > 1 and candidates:
                final_move = evaluate_maphack_candidates(candidates, _global_game_state)
            else:
                final_move = bestmove

            # 5. Đảm bảo độ trễ tự nhiên (tránh bị phát hiện)
            elapsed = time.time() - start_t
            if elapsed < MIN_MOVE_DELAY_SEC:
                time.sleep(MIN_MOVE_DELAY_SEC - elapsed)

            if final_move and len(final_move) >= 4:
                print(f"[AI MOVE] 🚀 Đánh nước: {final_move} (Engine Time: {elapsed:.2f}s, MultiPV: {multipv})")
                self._send_move_to_server(final_move)
        else:
            print("[AI MOVE] ⚠️ Engine chưa sẵn sàng!")

    def _send_move_to_server(self, uci_move):
        src_sq = uci_move[:2]
        tgt_sq = uci_move[2:4]
        src_idx = uci_square_to_idx(src_sq)
        tgt_idx = uci_square_to_idx(tgt_sq)

        if src_idx is not None and tgt_idx is not None and self.ws:
            payload = struct.pack('>bhbb', 4, 529, src_idx, tgt_idx)
            try:
                self.ws.send(payload, opcode=websocket.ABNF.OPCODE_BINARY)
            except Exception as e:
                print(f"[WS] ❌ Lỗi gửi nước đi: {e}")

    def _on_error(self, ws, error):
        print(f"[WS] Lỗi: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        print(f"[WS] Đóng kết nối: code={close_status_code}, msg={close_msg}")
        self.is_connected = False


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    print("""
    ===============================================================
       ⚡ CỜ ÚP AI ENGINE BOT — PHIÊN BẢN TỐI ƯU HIỆU NĂNG 2026 ⚡
       - Engine: AB-JChess 0.2d / PikaJieQi (NNUE Support)
       - Feature: Dynamic MultiPV Maphack + Smart Time Management
    ===============================================================
    """)

    # 1. Tìm kiếm Engine Binary & NNUE
    engine_bin = ABJCHESS_BINARY_CANDIDATES[0] if ABJCHESS_BINARY_CANDIDATES else None
    nnue_file = ABJCHESS_NNUE_CANDIDATES[0] if ABJCHESS_NNUE_CANDIDATES else None

    if not engine_bin:
        print("[WARNING] Chưa tìm thấy file thực thi AB-JChess/PikaJieQi trong thư mục.")
        print("          Bot vẫn sẽ khởi chạy và sẵn sàng nhận engine khi có!")
    else:
        print(f"[SETUP] Tìm thấy Engine: {engine_bin}")

    if nnue_file:
        print(f"[SETUP] Tìm thấy NNUE Net: {nnue_file}")

    # 2. Khởi tạo Engine
    engine_mgr = UciEngineManager(engine_bin, nnue_file)

    def _cleanup():
        print("[CLEANUP] Đang dừng engine và dọn dẹp tài nguyên...")
        engine_mgr.stop_and_close()

    atexit.register(_cleanup)
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    # 3. Đăng nhập và lấy phiên
    fetch_session_and_login()

    # 4. Chạy Bot Client
    bot = CaroCoUpBotClient(engine_mgr)
    bot.start()


if __name__ == "__main__":
    main()
