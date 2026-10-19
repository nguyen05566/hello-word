"""
cup_chat_bot.py — Bot nhắn tin sảnh cờ úp (mystery_xiangqi) trên gamevh.net
Dựa trên cup_bot_hoanchinh.py — protocol WebSocket nhị phân gamevh.net.

Tài khoản: ngan2
Chức năng:
 1. Lấy session (TOKEN + cookie) từ trang chơi mystery_xiangqi
 2. Kết nối WebSocket wss://gamevh.net/ws/gameServer
 3. Đăng nhập (LOGIN cmd 302), vào sảnh chính (ENTER_PLACE cmd 401)
 4. Gửi 100 tin nhắn chat "nguyên đây ,kết bạn nhé" vào sảnh (CHAT.SEND cmd 331)
 5. Giữ kết nối sống (PING keepalive)
"""
import os
import sys
import time
import json
import re
import struct
import threading
import traceback
import urllib.request, urllib.parse, http.cookiejar
import websocket

# ============================================================================
# CẤU HÌNH TÀI KHOẢN NGAN2 — CỜ ÚP (mystery_xiangqi)
# ============================================================================
USER = "ngan2"
WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/mystery_xiangqi/0"
GAME_ID = "mystery_xiangqi"
PLACE_PATH = "Lobby.mystery_xiangqi.0"   # sảnh chính (ngoài ván chơi)
CHAT_MESSAGE = "nguyên đây ,kết bạn nhé"
CHAT_COUNT = 100

# Cookies do người dùng cung cấp (xác thực game8.io / gamevh.net)
ACCESS_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6NTY2LCJmaXJzdE5hbWUiOiJob2FuZyBkdXkgaHVuZyIsImxhc3ROYW1lIjoiTmd1eWVuIiwiZW1haWwiOiJqYXZqcDYwNkBnbWFpbC5jb20iLCJwaG9uZSI6bnVsbCwiYmlydGhEYXRlIjpudWxsLCJhdmF0YXIiOiJvYXZhdGEvMjAyNTA5LzE3NTcwMzk2NjI2Nl9Ib3BzdGFydGVyLUJpb21hbi1CaW9tYW4tQmlvbWFuLUF2YXRhci0zX0JsdWVfNTEyLnBuZyIsImNvdmVyIjoiL2NvdmVyLzIwMjUwOS8xNzU2OTY5NTMwNzQ5XzktNC0yMDI1XzItMDUtMTVfUE0ucG5nIiwiYXV0aGVudGljYXRvciI6Imdvb2dsZSIsInN0YXR1cyI6IlBFUkRJTkciLCJpYXQiOjE3OTI0Mzk3NTAsImV4cCI6MTc5MjUyNjE1MH0uNjgzSC00dkNIdndBUFh0MjdmNGJxYkRLa2dxb2R4NHozN2lUM3g3V0g4MCJ9"
)
REFRESH_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6NTY2LCJmaXJzdE5hbWUiOiJob2FuZyBkdXkgaHVuZyIsImxhc3ROYW1lIjoiTmd1eWVuIiwiZW1haWwiOiJqYXZqcDYwNkBnbWFpbC5jb20iLCJwaG9uZSI6bnVsbCwiYmlydGhEYXRlIjpudWxsLCJhdmF0YXIiOiJvYXZhdGEvMjAyNTA5LzE3NTcwMzk2NjI2Nl9Ib3BzdGFydGVyLUJpb21hbi1CaW9tYW4tQmlvbWFuLUF2YXRhci0zX0JsdWVfNTEyLnBuZyIsImNvdmVyIjoiL2NvdmVyLzIwMjUwOS8xNzU2OTY5NTMwNzQ5XzktNC0yMDI1XzItMDUtMTVfUE0ucG5nIiwiYXV0aGVudGljYXRvciI6Imdvb2dsZSIsInN0YXR1cyI6IlBFUkRJTkciLCJpYXQiOjE3OTI0Mzk3NTAsImV4cCI6MTc5MzA0NDU1MH0uOTE1NTdlU0xwalNXTHhnZ1ppZ3ZlckNIRkVkbyplcml8SVAifQ=="
)
G_STATE = '{"i_l":0,"i_ll":1792292417842,"i_e":{"enable_itp_optimization":24},"i_et":1792292417842}'

# ============================================================================
# BIẾN TOÀN CỤC (được cập nhật sau khi lấy session)
# ============================================================================
COOKIE = ""
TOKEN = 0
CURRENT_PLAYER_NICKNAME = USER
CURRENT_PLAYER_ID = 0

# ============================================================================
# PROTOCOL NHỊ PHÂN GAMEVH.NET (từ cup_bot_hoanchinh.py)
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

class _Reader:
    def __init__(self, data):
        self.data = bytes(data)
        self.offset = 0
    def read_byte(self):
        v = struct.unpack_from('>b', self.data, self.offset)[0]
        self.offset += 1
        return v

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

def now_ts():
    return time.strftime("%H:%M:%S", time.localtime())

def log(*args):
    print(f"[{now_ts()}] " + " ".join(str(a) for a in args), flush=True)

# ============================================================================
# SESSION: lấy TOKEN + cookie từ gamevh.net (mystery_xiangqi)
# ============================================================================
def fetch_session_info():
    global COOKIE, TOKEN, CURRENT_PLAYER_NICKNAME, CURRENT_PLAYER_ID, PLACE_PATH
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    base_headers = [
        ("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/139.0"),
        ("Accept", "text/html,application/xhtml+xml"),
        ("Accept-Language", "vi-VN,vi;q=0.9,en;q=0.7"),
        ("Origin", "https://gamevh.net"),
        ("Referer", LOGIN_URL),
    ]

    # Inject cookies do người dùng cung cấp
    cookie_line = (
        f'g_state={G_STATE}; '
        f'__Host-AccessToken={ACCESS_TOKEN}; '
        f'__Host-RefreshToken={REFRESH_TOKEN}'
    )
    for part in cookie_line.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        c = http.cookiejar.Cookie(
            version=0, name=name.strip(), value=value.strip(),
            port=None, port_specified=False,
            domain="gamevh.net", domain_specified=True, domain_initial_dot=False,
            path="/", path_specified=True,
            secure=True, expires=None, discard=True,
            comment=None, comment_url=None, rest={},
        )
        cj.set_cookie(c)

    opener.addheaders = list(base_headers)

    try:
        req = urllib.request.Request(GAME_URL, headers=dict(opener.addheaders))
        r = opener.open(req, timeout=20)
        html = r.read().decode("utf-8", "replace")
        log(f"[SESSION] GET {GAME_URL} -> {r.status}, len={len(html)}")

        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", html)
        if tm:
            TOKEN = int(tm.group(1))
            log(f"[SESSION] TOKEN = {TOKEN}")
        else:
            log("[SESSION] Không tìm thấy TOKEN trong HTML, dùng mặc định 566")
            TOKEN = 566

        nm = re.search(r'var\s+currentPlayerNickName\s*=\s*["\']([^"\']+)["\']', html)
        if nm:
            CURRENT_PLAYER_NICKNAME = nm.group(1).strip()
            log(f"[SESSION] Nickname = {CURRENT_PLAYER_NICKNAME}")
        else:
            CURRENT_PLAYER_NICKNAME = USER
            log(f"[SESSION] Không tìm thấy nickname, dùng {USER}")

        pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", html)
        if pid:
            CURRENT_PLAYER_ID = int(pid.group(1))
            log(f"[SESSION] Player ID = {CURRENT_PLAYER_ID}")

        pm = re.search(r'var\s+placePath\s*=\s*["\']([^"\']+)["\']', html)
        if pm:
            PLACE_PATH = pm.group(1)
            log(f"[SESSION] Place path = {PLACE_PATH}")
        else:
            log(f"[SESSION] Không tìm thấy placePath, dùng mặc định {PLACE_PATH}")

        cookie_parts = [f"{c.name}={c.value}" for c in cj if c.domain in ("gamevh.net", ".gamevh.net")]
        COOKIE = "; ".join(cookie_parts)
        log(f"[SESSION] Cookie OK (JSESSIONID presence: {'JSESSIONID' in COOKIE})")
        return True
    except Exception as e:
        log(f"[SESSION] Lỗi: {e}")
        TOKEN = 566
        COOKIE = cookie_line
        return False

# ============================================================================
# WEBSOCKET CLIENT
# ============================================================================
class ChatBot:
    def __init__(self):
        self.ws = None
        self.ws_thread = None
        self.connected = False
        self.logged_in = False
        self._closing = False
        self.conn = Conn()
        self.chat_sent = 0
        self._stop = False

    def connect(self):
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
            header={"Origin": "https://gamevh.net"})
        self.ws = app
        self.ws_thread = threading.Thread(
            target=lambda: app.run_forever(ping_interval=20, ping_timeout=None), daemon=True)
        self.ws_thread.start()
        for _ in range(75):
            if self.connected:
                break
            if not self.ws_thread.is_alive():
                break
            time.sleep(0.2)
        return self.connected

    def _on_open(self, ws):
        self.connected = True
        log("[WS] Đã mở kết nối WebSocket")
        self._send_login()

    def _on_message(self, ws, message):
        if isinstance(message, bytes):
            try:
                r = _Reader(message)
                length = r.read_byte()
                if length < 0:
                    n = -length
                    cmd = message[r.offset:r.offset + n].decode('ascii', errors='replace')
                else:
                    nb = message[r.offset] & 0xFF
                    code = (length << 8) | nb
                    cmd = CMD_NAMES.get(code, str(code))
                log(f"[WS] <- {cmd} ({len(message)} bytes)")
                if cmd == "LOGIN":
                    self.logged_in = True
                    log("[WS] Đăng nhập thành công")
                    self._send_enter_place()
            except Exception as e:
                log(f"[WS] Lỗi parse binary: {e}")
        else:
            log(f"[WS] <- text: {message[:200]}")

    def _on_error(self, ws, error):
        log(f"[WS] Error: {error}")

    def _on_close(self, ws, code, msg):
        self.connected = False
        self.logged_in = False
        log(f"[WS] Đóng (code={code}, msg={msg})")

    def send_message(self, cmd, data=b''):
        if self.ws and self.connected:
            try:
                self.ws.send(self.conn.pack(cmd, data), opcode=0x2)
                return True
            except Exception as e:
                log(f"[WS] Lỗi gửi {cmd}: {e}")
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
        log(f"[WS] -> LOGIN (nick={CURRENT_PLAYER_NICKNAME}, token={TOKEN}, game={GAME_ID})")

    def _send_enter_place(self):
        data = bytearray()
        data.extend(self.conn.pack_ascii(PLACE_PATH))
        data.extend(self.conn.pack_string(""))
        data.extend(self.conn.pack_byte(1))
        self.send_message("ENTER_PLACE", bytes(data))
        log(f"[WS] -> ENTER_PLACE ({PLACE_PATH})")

    def send_chat(self, message):
        """Gửi tin nhắn chat CHAT.SEND (cmd 331) vào sảnh."""
        data = bytearray()
        data.extend(self.conn.pack_string(message))
        ok = self.send_message("CHAT.SEND", bytes(data))
        if ok:
            self.chat_sent += 1
            log(f"[WS] -> CHAT.SEND #{self.chat_sent}: {message}")
        return ok

    def close(self):
        self._stop = True
        if self.ws:
            try: self.ws.close()
            except: pass

# ============================================================================
# MAIN
# ============================================================================
def main():
    log("=" * 60)
    log("CHAT BOT — gamevh.net mystery_xiangqi (tài khoản ngan2)")
    log("=" * 60)

    # 1. Lấy session (TOKEN + cookie)
    if not fetch_session_info():
        log("[MAIN] Cảnh báo: lấy session không thành công, vẫn thử kết nối...")

    # 2. Kết nối WebSocket
    bot = ChatBot()
    if not bot.connect():
        log("[MAIN] Không kết nối được WebSocket. Thoát.")
        return 1

    # 3. Đợi đăng nhập và vào sảnh
    t0 = time.time()
    while not bot.logged_in and time.time() - t0 < 10:
        time.sleep(0.2)

    # 4. Gửi 100 tin nhắn chat vào sảnh (ngoài ván chơi)
    log(f"[MAIN] Bắt đầu gửi {CHAT_COUNT} tin nhắn chat vào sảnh...")
    for i in range(CHAT_COUNT):
        if bot._stop or not bot.connected:
            log("[MAIN] Mất kết nối, dừng gửi.")
            break
        bot.send_chat(CHAT_MESSAGE)
        time.sleep(0.5)

    log(f"[MAIN] Đã gửi {bot.chat_sent}/{CHAT_COUNT} tin nhắn.")

    # 5. Giữ kết nối sống cho đến hết 6 giờ (hoặc Ctrl+C)
    try:
        while bot.connected and not bot._stop:
            time.sleep(5)
    except KeyboardInterrupt:
        log("[MAIN] Nhận Ctrl+C, tắt bot...")
    finally:
        bot.close()
        log("[MAIN] Bot đã dừng.")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
