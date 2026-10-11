"""
cup_bot_flipchess_abjchess.py — Cờ Úp Bot dùng engine AB-JChess 0.2d build từ mistboard
Source: https://github.com/lxsgx23/AB-JChess (release v0.2d, 2026-10-10)
Mistboard integration ref: https://github.com/brianhliou/mistboard/blob/main/ab-jchess.ref

Engine: AB-JChess 0.2d (NNUE-based, Pikafish-derived Jieqi engine)
  - Mạnh hơn PikaJieQi ~90 Elo @ 4s/move (mistboard benchmark)
  - Yêu cầu CPU có AVX2/BMI2 (build với ARCH=x86-64-bmi2)
  - Yêu cầu file NNUE (abjchess-20261010.nnue) — load qua setoption name EvalFile
Movetime: 2000ms (~2s/nước) — có thể tăng lên 3000ms để tận dụng NNUE
BAG updates: gửi kèm moves list để AB-JChess sync state (giống PikaJieQi)

[ĐẶC ĐIỂM]
- Binary: AB-JChess (build từ source cho Linux, hoặc download 0.2d_bmi2.zip cho Windows)
- KHÔNG gửi lệnh setflip (engine không hỗ trợ; bot tự track flip phía Python)
  (giống PikaJieQi — AB-JChess cùng encode dark state vào FEN BAG field)
- NNUE BẮT BUỘC: file abjchess-20261010.nnue phải nằm cùng thư mục với binary
  (PikaJieQi dùng classical eval, không cần NNUE; AB-JChess thì NNUE là cốt lõi)
- ws_frame_dump.py: parse WS frame từ server, track reveal piece qua raw_face byte
  → bot biết true piece type của quân úp (lợi thế so với người chơi thường)
- UCI tương thích ngược với PikaJieQi: cùng FEN BAG format, cùng reveal suffix
  (c3c4N = dark pawn c3→c4, reveals Knight). Code move-list giữ nguyên.

[ĐÃ BỎ so với cup_bot_mistboard.py gốc]
- Ally detection (is_family_bot): không né bạn cùng hệ thống
- Anti-software handler (is_block_software_message): bỏ detection chặn software
- Table tracking (ACTIVE_TABLES_FILE): bỏ hệ thống đăng ký bàn

[THAY ĐỔI so với cup_bot_flipchess.py bản PikaJieQi]
- PIKAJIEQI_BINARY_CANDIDATES  -> ABJCHESS_BINARY_CANDIDATES
- FlipchessJieqiEngine         -> FlipchessAbjchessEngine (giữ alias cho backward compat)
- Env MISTBOARD_JIEQI_ENGINE   -> MISTBOARD_ABJCHESS_ENGINE (env cũ vẫn fallback)
- NNUE file pikafish.nnue      -> abjchess-20261010.nnue (BẮT BUỘC, không còn optional)
- Nếu thiếu NNUE, engine sẽ KHÔNG khởi động được (khác PikaJieQi)
- Thêm setoption AggressiveLevel=3 và ChasingRule=skyrule_Jieqi (mặc định AB-JChess)

[BẢN NÀY] Bot KHÔNG tự kick/Thoát khi thua — luôn ở lại bàn và ready ván mới.
Mức cược: 2000 xu (BOT_BET_XU = 2000)
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
import shutil
import urllib.request, urllib.parse, http.cookiejar

# ============================================================================
# ws_frame_dump — EMBEDDED (file hoàn chỉnh, không cần ws_frame_dump.py rời)
# Giải mã WS frame gamevh cờ úp: START_MATCH pieces, MOVE reveal (display_sid,
# true_face_sid), stateful pos->sid. Nguồn: tái tạo từ frame thô + AB-JChess ref.
# ============================================================================
"""
ws_frame_dump.py — WS binary frame decoder for gamevh cờ úp (mystery_xiangqi).

Cung cấp 2 API cho cup_bot_flipchess_abjchess.py:
  - decode_frame(data: bytes) -> dict   : parse 1 frame đến, trả event dict
  - log_incoming_frame(data: bytes)     : ghi hex frame vào ws_frames.log

Wire format (giống InboundMessage trong bot chính):
  [cmd_len(1, signed)] [cmd ascii | cmd code 2B BE] [payload...]
  cmd_len < 0  -> tên ascii độ dài -cmd_len
  cmd_len >= 0 -> code = (cmd_len<<8) | byte_next, tra CMD_NAMES

Payload theo từng lệnh (byte = signed 1B):
  START_MATCH: player_count(1), {slot(1), id(4)}*N,
               piece_count(1), {sid(1), face(1), pos(1), is_open(1)}*M,
               x(1), mystery_count(1), {m(1)}*K, x(1), x(1),
               first_turn_slot(1), my_slot(1)
               NOTE: sid == face ở START_MATCH (không lộ mặt thật).
  MOVE:        source(1), target(1), reveal_count(1),
               reveal_count x (sid(1), face(1))
               - pair = (display_sid, true_face_sid) của 1 quân vừa bị lộ mặt
               - 1 move thường sinh 1 pair (quân đi là quân úp -> lật)
               - capture quân úp có thể sinh thêm pair (quân bị ăn)
               - frame sync (sau reconnect) có thể có nhiều pair (full map)

LUẬT GAME (xác nhận từ AB-JChess source + frame capture):
  - Quân úp đi theo MẶT NẠ (mảnh tiêu chuẩn tại vị trí của nó).
  - Khi quân úp đi, nó lật lộ MẶT THẬT (identity, lấy từ pool) và sau đó
    đi theo mặt thật. Thêm suffix identity vào UCI move (vd "c3c4N").
  - Engine AB-JChess tự track pool/BAG qua suffix — KHÔNG cần setflip.

decode_frame trả về:
  {"command": <tên lệnh>, ...}
  START_MATCH -> "pieces": [{"sid_decoded","face_decoded","position","is_open"}, ...]
  MOVE        -> "source","target","revealed_piece","captured_piece",
                 "mover_known","stale"
     revealed_piece  = ký tự FEN mặt thật của quân đi (None nếu không lật)
     captured_piece  = ký tự FEN mặt thật của quân bị ăn (None nếu không có/không lật)
     stale           = True nếu frame là replay/state-sync (ô nguồn trống)
"""
import os
import struct
import time

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
    """byte -> 'r4' / 'b72' ... (giống _decode_piece_id của bot chính)"""
    color = 'r'
    if encoded_id < 0:
        encoded_id = -encoded_id
        color = 'b'
    return f"{color}{encoded_id >> 3}{'' if (encoded_id & 7) == 0 else (encoded_id & 7)}"


def _face_to_fen_char(raw_face):
    """byte mặt thật -> ký tự FEN ('R','n','p'...) hoặc None"""
    v = raw_face
    if v == 0:
        return None
    ch = PIECE_TYPE_MAP.get(abs(v) >> 3)
    if not ch:
        return None
    return ch.upper() if v > 0 else ch


class _GameState:
    """Track pos->sid, quân đã lộ mặt, map identity — giữ qua các frame."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.pos_sid = {}       # position -> sid byte (display id)
        self.revealed = set()   # sid đã lộ mặt (hoặc open ngay từ đầu)
        self.identity = {}      # sid -> face byte (mặt thật)


_state = _GameState()


def decode_frame(data):
    """Parse 1 binary frame -> event dict. Không raise với frame lạ."""
    try:
        r = _Reader(data)
        cmd = _parse_command(r)
        event = {"command": cmd}

        if cmd == "START_MATCH":
            pieces = []
            player_count = r.read_byte()
            for _ in range(max(0, player_count)):
                r.read_byte()
                r.read_int()
            piece_count = r.read_byte()
            new_pos_sid = {}
            new_revealed = set()
            for _ in range(max(0, piece_count)):
                raw_sid = r.read_byte()
                raw_face = r.read_byte()
                pos = r.read_byte()
                is_open = r.read_byte()
                pieces.append({
                    "sid_decoded": _decode_piece_id(raw_sid),
                    "face_decoded": _decode_piece_id(raw_face),
                    "position": pos,
                    "is_open": is_open,
                })
                if 0 <= pos < 90:
                    new_pos_sid[pos] = raw_sid
                if is_open:
                    new_revealed.add(raw_sid)
            event["pieces"] = pieces
            _state.pos_sid = new_pos_sid
            _state.revealed = new_revealed
            _state.identity = {}

        elif cmd == "MOVE":
            if r.rem() >= 2:
                event["source"] = src = r.read_byte()
                event["target"] = tgt = r.read_byte()
            else:
                return event
            count = r.read_byte() if r.rem() >= 1 else 0
            if count < 0:
                count = 0
            pairs = []
            for _ in range(min(count, r.rem() // 2)):
                sid = r.read_byte()
                face = r.read_byte()
                pairs.append((sid, face))
                _state.identity[sid] = face

            mover_sid = _state.pos_sid.get(src)
            captured_sid = _state.pos_sid.get(tgt) if tgt != src else None
            revealed_char = None
            captured_char = None
            for sid, face in pairs:
                ch = _face_to_fen_char(face)
                if ch in ('k', 'K'):
                    ch = None
                if sid == mover_sid and sid not in _state.revealed:
                    if revealed_char is None:
                        revealed_char = ch
                elif sid == captured_sid and sid not in _state.revealed:
                    if captured_char is None:
                        captured_char = ch
                _state.revealed.add(sid)

            event["revealed_piece"] = revealed_char
            event["captured_piece"] = captured_char
            event["mover_known"] = mover_sid is not None
            event["stale"] = bool(_state.pos_sid) and mover_sid is None

            # Cập nhật pos map SAU khi đã match mover/captured
            if mover_sid is not None:
                if tgt != src:
                    _state.pos_sid.pop(src, None)
                    _state.pos_sid[tgt] = mover_sid
                # src == tgt (flip move): quân đứng yên, chỉ lật

        return event
    except Exception as exc:
        return {"command": "?", "decode_error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------- logging
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ws_frames.log")
_LOG_MAX_BYTES = 8 * 1024 * 1024


def log_incoming_frame(data):
    """Ghi hex frame (lazy, capped) để debug protocol."""
    try:
        if os.path.exists(_LOG_PATH) and os.path.getsize(_LOG_PATH) > _LOG_MAX_BYTES:
            return
        try:
            cmd = decode_frame(data).get("command", "?")
        except Exception:
            cmd = "?"
        line = f"{time.strftime('%H:%M:%S')} {cmd:<12} {data.hex(' ')}\n"
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
